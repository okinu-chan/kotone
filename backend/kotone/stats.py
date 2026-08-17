"""実行時に観測した統計値の読み書きと、それに基づく所要時間の見積もり。

ラウドネス計測は曲数が多いと十数時間かかるため、実行前に「どれくらいかかるか」を
利用者へ提示できるようにする。見積もりは前回の実測スループットに基づき、
実測値がまだ無い場合は既定値（Raspberry Pi 4B での計測値）を使う。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from kotone.db import Database

log = logging.getLogger(__name__)

#: ラウドネス計測のスループット（実時間比）。値が大きいほど速い。
#:
#: 既定値は Raspberry Pi 4B・3 並列での実測。実際に計測を走らせると
#: その結果で更新され、以降は実機に即した見積もりになる。
#: rsgain は ebur128 の約 1.8 倍速いため、どちらが使われるかで分ける。
DEFAULT_LOUDNESS_THROUGHPUT = 180.0
DEFAULT_LOUDNESS_THROUGHPUT_RSGAIN = 335.0

KEY_LOUDNESS_THROUGHPUT = "loudness_throughput"

#: 「最近追加したアルバム」の起点（この時刻より後に追加されたものを新着とする）。
#:
#: 最初のスキャンではライブラリ全体が取り込まれるので、そのまま日付で見ると
#: 全部が新着になってしまう。初回スキャンが済んだ時点を起点として控えておき、
#: それ以降に増えたぶんだけを新着として扱う。
KEY_RECENT_BASELINE = "recent_baseline"

#: スループットの記録に必要な最小の音源長（秒）。
#: これを下回るサンプルは起動時間の影響が大きすぎて実力を表さない。
MIN_THROUGHPUT_SAMPLE_SECONDS = 300.0


def default_throughput() -> float:
    """このマシンで使われるバックエンドに応じた既定スループット。"""
    from kotone.library.loudness import LoudnessScanner  # noqa: PLC0415  循環 import 回避

    return (
        DEFAULT_LOUDNESS_THROUGHPUT_RSGAIN
        if LoudnessScanner().backend.startswith("rsgain")
        else DEFAULT_LOUDNESS_THROUGHPUT
    )


class StatsStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get_float(self, key: str, default: float) -> float:
        row = self._db.query_one("SELECT value FROM runtime_stats WHERE key = ?", (key,))
        if row is None:
            return default
        try:
            value = float(row["value"])
        except (TypeError, ValueError):
            return default
        return value if value > 0 else default

    def set_float(self, key: str, value: float) -> None:
        if value <= 0:
            return
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO runtime_stats (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT (key) DO UPDATE
                    SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, f"{value:.4f}"),
            )

    def get_text(self, key: str) -> str | None:
        row = self._db.query_one("SELECT value FROM runtime_stats WHERE key = ?", (key,))
        return str(row["value"]) if row and row["value"] else None

    def set_text(self, key: str, value: str) -> None:
        with self._db.transaction() as conn:
            conn.execute(
                """
                INSERT INTO runtime_stats (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT (key) DO UPDATE
                    SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, value),
            )

    # --- 新着アルバムの起点 ---------------------------------------------

    @property
    def recent_baseline(self) -> str | None:
        """この時刻より後に追加されたアルバムを新着として扱う（未設定なら None）。"""
        return self.get_text(KEY_RECENT_BASELINE)

    def mark_recent_baseline(self) -> str:
        """いまを起点にする。ここまでに入っているものは新着から外れる。"""
        self._db.execute(
            """
            INSERT INTO runtime_stats (key, value, updated_at)
            VALUES (?, datetime('now'), datetime('now'))
            ON CONFLICT (key) DO UPDATE
                SET value = datetime('now'), updated_at = datetime('now')
            """,
            (KEY_RECENT_BASELINE,),
        )
        self._db.commit()
        return self.recent_baseline or ""

    def ensure_recent_baseline(self) -> None:
        """まだ起点が無ければ、いまを起点にする。

        初回スキャンの直後に呼ぶ。取り込んだライブラリ全体が新着になって
        しまうのを防ぐ（利用者が「最近追加した」と感じるのは、そのあとで
        足したぶんだけ）。
        """
        if self.recent_baseline is None:
            self.mark_recent_baseline()

    # --- ラウドネス計測 -------------------------------------------------

    @property
    def loudness_throughput(self) -> float:
        return self.get_float(KEY_LOUDNESS_THROUGHPUT, default_throughput())

    def record_loudness_throughput(self, audio_seconds: float, elapsed_seconds: float) -> None:
        """計測 1 回分の実績からスループットを更新する。

        1 回の値をそのまま採用すると曲の長さの偏りで振れるため、
        これまでの値と混ぜて滑らかにする（指数移動平均）。
        """
        if audio_seconds <= 0 or elapsed_seconds <= 0:
            return
        if audio_seconds < MIN_THROUGHPUT_SAMPLE_SECONDS:
            # 数曲だけの計測ではプロセス起動の時間が支配的になり、実力より
            # 大幅に遅い値が出る（4 秒の曲 6 曲で 121x、実際は 350x 前後）。
            # 見積もりを歪めるだけなので採用しない。
            log.debug("サンプルが短いためスループットを更新しません (%.0f 秒)", audio_seconds)
            return
        observed = audio_seconds / elapsed_seconds
        previous = self.get_float(KEY_LOUDNESS_THROUGHPUT, 0.0)
        smoothed = observed if previous <= 0 else previous * 0.4 + observed * 0.6
        self.set_float(KEY_LOUDNESS_THROUGHPUT, smoothed)
        log.info(
            "ラウドネス計測のスループットを更新: %.1fx (今回 %.1fx)", smoothed, observed
        )


@dataclass
class LoudnessEstimate:
    """ラウドネス計測の残量と所要時間の見積もり。"""

    total_tracks: int
    measured_tracks: int
    remaining_tracks: int
    remaining_audio_seconds: float
    throughput: float
    estimated_seconds: float
    is_calibrated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": self.total_tracks,
            "measured": self.measured_tracks,
            "remaining": self.remaining_tracks,
            "remaining_audio_seconds": round(self.remaining_audio_seconds),
            "throughput": round(self.throughput, 1),
            "estimated_seconds": round(self.estimated_seconds),
            "estimated_text": format_duration_ja(self.estimated_seconds),
            # 実測に基づく見積もりか、既定値による概算か
            "is_calibrated": self.is_calibrated,
        }


def estimate_loudness(
    db: Database, stats: StatsStore, *, remeasure: bool = False
) -> LoudnessEstimate:
    # 計測対象はアルバムに紐づく曲のみ（LibraryScanner._scan_loudness と条件を揃える）。
    # 見積もりと実際の対象がずれると、残り時間が合わなくなる。
    target = "FROM tracks WHERE album_id IS NOT NULL"
    total = int(db.scalar(f"SELECT COUNT(*) {target}") or 0)
    measured = int(db.scalar(f"SELECT COUNT(*) {target} AND gain_scanned_at IS NOT NULL") or 0)

    if remeasure:
        remaining = total
        audio_seconds = float(db.scalar(f"SELECT COALESCE(SUM(duration), 0) {target}") or 0)
    else:
        remaining = max(0, total - measured)
        audio_seconds = float(
            db.scalar(f"SELECT COALESCE(SUM(duration), 0) {target} AND gain_scanned_at IS NULL")
            or 0
        )

    throughput = stats.loudness_throughput
    calibrated = stats.get_float(KEY_LOUDNESS_THROUGHPUT, 0.0) > 0
    return LoudnessEstimate(
        total_tracks=total,
        measured_tracks=measured,
        remaining_tracks=remaining,
        remaining_audio_seconds=audio_seconds,
        throughput=throughput,
        estimated_seconds=audio_seconds / throughput if throughput > 0 else 0.0,
        is_calibrated=calibrated,
    )


def format_duration_ja(seconds: float) -> str:
    """所要時間を日本語で読みやすく表す。"""
    if seconds <= 0:
        return "0 秒"
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分"
    hours = seconds / 3600
    if hours < 24:
        return f"{hours:.1f} 時間"
    return f"{hours / 24:.1f} 日"
