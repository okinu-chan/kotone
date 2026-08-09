"""ラウドネス計測（EBU R128 / ReplayGain 2.0 相当）— 要件 3.1。

計測値は SQLite に保存し、**元ファイルへのタグ書き込みは行わない**。
そのため rsgain は tag mode = skip（``-s s``）で実行し、標準出力の
スキャンデータだけを利用する。

rsgain が無い環境では FFmpeg の ``ebur128`` フィルタにフォールバックする。
どちらも無ければ計測はスキップされ、補正が効かないだけで再生は可能。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: ReplayGain 2.0 の基準ラウドネス
TARGET_LUFS = -18.0


@dataclass
class LoudnessResult:
    gain_db: float
    peak: float
    loudness_lufs: float | None = None


@dataclass
class AlbumLoudness:
    tracks: dict[str, LoudnessResult]           # 絶対パス -> 計測値
    album: LoudnessResult | None = None


class LoudnessScanner:
    """rsgain / FFmpeg をラップしたラウドネス計測。"""

    def __init__(self, *, enabled: bool = True, timeout: float = 900.0) -> None:
        self.enabled = enabled
        self.timeout = timeout
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
            return "ffmpeg"
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
        tracks: dict[str, LoudnessResult] = {}
        for path in files:
            result = self._measure_with_ffmpeg(path)
            if result is not None:
                tracks[str(path)] = result

        album: LoudnessResult | None = None
        if tracks:
            # 簡易的なアルバムゲイン: 各曲のラウドネスのエネルギー平均
            values = [r.loudness_lufs for r in tracks.values() if r.loudness_lufs is not None]
            if values:
                mean_energy = sum(10 ** (v / 10) for v in values) / len(values)
                album_lufs = 10 * _log10(mean_energy)
                album = LoudnessResult(
                    gain_db=TARGET_LUFS - album_lufs,
                    peak=max(r.peak for r in tracks.values()),
                    loudness_lufs=album_lufs,
                )
        return AlbumLoudness(tracks=tracks, album=album)

    def _measure_with_ffmpeg(self, path: Path) -> LoudnessResult | None:
        cmd = [
            self._ffmpeg, "-v", "info", "-nostats", "-i", str(path),
            "-map", "a:0", "-af", "loudnorm=print_format=json", "-f", "null", "-",
        ]
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
            log.warning("FFmpeg のラウドネス計測に失敗しました: %s (%s)", path, exc)
            return None

        data = _parse_loudnorm_json(proc.stderr)
        if data is None:
            return None
        try:
            loudness = float(data["input_i"])
            true_peak_db = float(data["input_tp"])
        except (KeyError, TypeError, ValueError):
            return None
        if loudness <= -70:  # 無音扱い
            return None
        peak = 10 ** (true_peak_db / 20)
        return LoudnessResult(
            gain_db=TARGET_LUFS - loudness, peak=peak, loudness_lufs=loudness
        )


# --- 出力パーサ ---------------------------------------------------------

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

    return math.log10(value) if value > 0 else -70.0
