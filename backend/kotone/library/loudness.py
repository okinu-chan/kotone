"""ラウドネス計測（EBU R128 / ReplayGain 2.0 相当）— 要件 3.1。

計測値は SQLite に保存し、**元ファイルへのタグ書き込みは行わない**。
そのため rsgain は tag mode = skip（``-s s``）で実行し、標準出力の
スキャンデータだけを利用する。

rsgain が無い環境では FFmpeg の ``ebur128`` フィルタにフォールバックする。
どちらも無ければ計測はスキップされ、補正が効かないだけで再生は可能。

大規模ライブラリ向けの性能について
----------------------------------
Raspberry Pi 4B (Cortex-A72 4コア) での実測値（3分の FLAC 1 曲あたり）:

===================  ==========  ==============
方式                   所要時間    実時間比
===================  ==========  ==============
loudnorm (旧実装)        20.0 秒      9.1x
**ebur128 (現実装)**    **4.0 秒**  **45.8x**
デコードのみ（上限）        1.1 秒     171.3x
===================  ==========  ==============

``loudnorm`` は正規化用フィルタで計測以上の処理を行うため遅い。計測目的には
``ebur128`` で十分かつ 5 倍速い。さらにコア数ぶん並列化するため、18,000 曲規模
でも一晩（6〜7 時間程度）で完了する見込み。

なお、ファイルに既存の ReplayGain タグがある場合はそもそも計測せずに
それを使う（:mod:`kotone.library.tags` / :mod:`kotone.library.scanner` 参照）。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: ReplayGain 2.0 の基準ラウドネス
TARGET_LUFS = -18.0

#: 計測結果が無音とみなされる閾値
SILENCE_LUFS = -70.0


@dataclass
class LoudnessResult:
    gain_db: float
    peak: float
    loudness_lufs: float | None = None


@dataclass
class AlbumLoudness:
    tracks: dict[str, LoudnessResult]           # 絶対パス -> 計測値
    album: LoudnessResult | None = None


#: 並列数の既定上限。
#:
#: Raspberry Pi 4B での実測（3分の FLAC、ebur128 peak=sample）:
#:
#: ===== ============== ==================
#: 並列   合計スループット  18,000 曲の所要
#: ===== ============== ==================
#: 1       30.9x          34.5 時間
#: **2**   **49.9x**      **21.4 時間**
#: 3       51.7x          20.6 時間
#: 4       51.3x          20.8 時間
#: ===== ============== ==================
#:
#: コア数を増やしてもメモリ帯域で頭打ちになるため、3 を超えて並列化しても
#: 速くならない。むしろ再生や UI に回すコアを残したほうがよい。
MAX_AUTO_CONCURRENCY = 3


def default_concurrency() -> int:
    """既定の並列数（CPU コア数と :data:`MAX_AUTO_CONCURRENCY` の小さい方）。"""
    return max(1, min(os.cpu_count() or 2, MAX_AUTO_CONCURRENCY))


class LoudnessScanner:
    """rsgain / FFmpeg をラップしたラウドネス計測。"""

    def __init__(
        self,
        *,
        enabled: bool = True,
        timeout: float = 900.0,
        concurrency: int | None = None,
        true_peak: bool = False,
    ) -> None:
        self.enabled = enabled
        self.timeout = timeout
        self.concurrency = concurrency if concurrency and concurrency > 0 else default_concurrency()
        # トゥルーピークは 4 倍オーバーサンプリングを伴うため約 1.5 倍遅い
        # （Pi 4B 実測: peak=true 21.7x / peak=sample 31.0x）。
        # ピークはクリッピング防止にしか使わないので既定はサンプルピーク。
        self.true_peak = true_peak
        self._rsgain = shutil.which("rsgain")
        self._ffmpeg = shutil.which("ffmpeg")

    @property
    def available(self) -> bool:
        return self.enabled and bool(self._rsgain or self._ffmpeg)

    @property
    def backend(self) -> str:
        if not self.enabled:
            return "disabled"
        if self._rsgain:
            return "rsgain"
        if self._ffmpeg:
            return f"ffmpeg x{self.concurrency}"
        return "unavailable"

    def scan_album(self, files: list[Path]) -> AlbumLoudness:
        """1 アルバム分をまとめて計測する（アルバムゲインの算出に必要）。"""
        if not files or not self.available:
            return AlbumLoudness(tracks={})
        if self._rsgain:
            result = self._scan_with_rsgain(files)
            if result.tracks:
                return result
            log.debug("rsgain が結果を返さなかったため FFmpeg にフォールバックします")
        if self._ffmpeg:
            return self._scan_with_ffmpeg(files)
        return AlbumLoudness(tracks={})

    # --- rsgain ---------------------------------------------------------
    def _scan_with_rsgain(self, files: list[Path]) -> AlbumLoudness:
        cmd = [self._rsgain, "custom", "-a", "-s", "s", "-O", *[str(f) for f in files]]
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                # ロケール依存のデコードを避ける。ファイル名に非 ASCII が含まれると
                # 既定のエンコーディングでは復号に失敗することがある。
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("rsgain の実行に失敗しました: %s", exc)
            return AlbumLoudness(tracks={})

        if proc.returncode != 0 and not proc.stdout:
            log.warning("rsgain がエラーを返しました: %s", proc.stderr.strip()[:400])
            return AlbumLoudness(tracks={})

        return _parse_rsgain_output(proc.stdout, files)

    # --- FFmpeg ---------------------------------------------------------
    def _scan_with_ffmpeg(self, files: list[Path]) -> AlbumLoudness:
        """アルバム内の各曲を並列に計測する。

        1 曲あたり 1 プロセスなので、コア数ぶん同時に走らせると
        ほぼコア数倍のスループットが出る（GIL は subprocess 待ちで解放される）。
        """
        tracks: dict[str, LoudnessResult] = {}
        workers = min(self.concurrency, len(files))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for path, result in zip(
                files, pool.map(self._measure_with_ffmpeg, files), strict=True
            ):
                if result is not None:
                    tracks[str(path)] = result

        return AlbumLoudness(tracks=tracks, album=compute_album_gain(tracks))

    def _measure_with_ffmpeg(self, path: Path) -> LoudnessResult | None:
        peak_mode = "true" if self.true_peak else "sample"
        cmd = [
            self._ffmpeg, "-v", "info", "-nostats", "-i", str(path),
            "-map", "a:0", "-af", f"ebur128=peak={peak_mode}:framelog=quiet",
            "-f", "null", "-",
        ]
        try:
            proc = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            log.warning("FFmpeg のラウドネス計測に失敗しました: %s (%s)", path, exc)
            return None

        return parse_ebur128_output(proc.stderr)


# --- 出力パーサ ---------------------------------------------------------

# ebur128 のサマリ:
#     Integrated loudness:
#       I:         -15.0 LUFS
#     True peak:
#       Peak:       -0.1 dBFS
_EBUR128_I_RE = re.compile(r"^\s*I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", re.MULTILINE)
_EBUR128_PEAK_RE = re.compile(r"^\s*Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", re.MULTILINE)


def parse_ebur128_output(stderr: str) -> LoudnessResult | None:
    """``ebur128`` フィルタのサマリ出力から積分ラウドネスとピークを取り出す。"""
    if not stderr:
        return None
    match = _EBUR128_I_RE.search(stderr)
    if not match:
        return None
    try:
        loudness = float(match.group(1))
    except ValueError:
        return None
    if loudness <= SILENCE_LUFS:  # 無音扱い
        return None

    # True peak セクションの値を使う（複数出る場合は最後＝True peak）
    peaks = _EBUR128_PEAK_RE.findall(stderr)
    if peaks:
        try:
            peak = 10 ** (float(peaks[-1]) / 20)
        except ValueError:
            peak = 1.0
    else:
        peak = 1.0

    return LoudnessResult(
        gain_db=TARGET_LUFS - loudness, peak=peak, loudness_lufs=loudness
    )


def compute_album_gain(tracks: dict[str, LoudnessResult]) -> LoudnessResult | None:
    """収録曲のラウドネスからアルバムゲインを求める（エネルギー平均）。"""
    values = [r.loudness_lufs for r in tracks.values() if r.loudness_lufs is not None]
    if not values:
        return None
    mean_energy = sum(10 ** (v / 10) for v in values) / len(values)
    album_lufs = _log10(mean_energy) * 10
    return LoudnessResult(
        gain_db=TARGET_LUFS - album_lufs,
        peak=max(r.peak for r in tracks.values()),
        loudness_lufs=album_lufs,
    )


_HEADER_ALIASES = {
    "filename": "filename",
    "file": "filename",
    "loudness (lufs)": "loudness",
    "loudness": "loudness",
    "gain (db)": "gain",
    "gain": "gain",
    "peak": "peak",
    "peak (db)": "peak_db",
}


def _parse_rsgain_output(stdout: str, files: list[Path]) -> AlbumLoudness:
    """rsgain の ``-O``（タブ区切り）出力を解析する。

    列の並びはバージョンによって変わるため、ヘッダー行から列位置を決める。
    """
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return AlbumLoudness(tracks={})

    header = [_HEADER_ALIASES.get(c.strip().lower()) for c in lines[0].split("\t")]
    if "filename" not in header or "gain" not in header:
        log.warning("rsgain の出力形式を解釈できませんでした")
        return AlbumLoudness(tracks={})

    by_name = {Path(f).name: Path(f) for f in files}
    tracks: dict[str, LoudnessResult] = {}
    album: LoudnessResult | None = None

    for line in lines[1:]:
        cells = line.split("\t")
        row = {
            key: cells[i].strip()
            for i, key in enumerate(header)
            if key and i < len(cells)
        }
        gain = _to_float(row.get("gain"))
        if gain is None:
            continue
        peak = _to_float(row.get("peak"))
        if peak is None:
            peak_db = _to_float(row.get("peak_db"))
            peak = 10 ** (peak_db / 20) if peak_db is not None else 1.0
        result = LoudnessResult(
            gain_db=gain, peak=peak, loudness_lufs=_to_float(row.get("loudness"))
        )

        name = row.get("filename", "")
        if name.strip().lower() == "album":
            album = result
            continue
        path = by_name.get(Path(name).name)
        if path is not None:
            tracks[str(path)] = result

    return AlbumLoudness(tracks=tracks, album=album)


_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def _parse_loudnorm_json(stderr: str) -> dict[str, str] | None:
    """``loudnorm`` の JSON 出力（旧実装の互換用パーサ）。"""
    match = _JSON_BLOCK_RE.search(stderr or "")
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    cleaned = value.replace("dB", "").replace("LUFS", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _log10(value: float) -> float:
    import math  # noqa: PLC0415

    return math.log10(value) if value > 0 else -7.0
