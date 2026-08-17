"""所要時間の見積もりと、実測スループットの記録のテスト。

曲数の多いライブラリではラウドネス計測に十数時間かかるため、実行前に
「どれくらいかかるか」を提示できる必要がある（利用者への事前確認用）。
"""

from __future__ import annotations

import shutil

import pytest

from kotone.db import Database
from kotone.stats import (
    DEFAULT_LOUDNESS_THROUGHPUT,
    DEFAULT_LOUDNESS_THROUGHPUT_RSGAIN,
    KEY_LOUDNESS_THROUGHPUT,
    KEY_RECENT_BASELINE,
    StatsStore,
    default_throughput,
    estimate_loudness,
    format_duration_ja,
)


def _add_track(db: Database, path: str, duration: float, measured: bool) -> None:
    """計測対象はアルバムに紐づく曲なので、アルバムも用意しておく。"""
    album_id = db.scalar("SELECT id FROM albums LIMIT 1")
    if album_id is None:
        album_id = db.execute(
            "INSERT INTO albums (album_key, name) VALUES ('k', 'テストアルバム')"
        ).lastrowid
    db.execute(
        "INSERT INTO tracks (path, title, duration, album_id, gain_scanned_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (path, path, duration, album_id, "2026-01-01 00:00:00" if measured else None),
    )
    db.commit()


@pytest.fixture
def stats(db: Database) -> StatsStore:
    return StatsStore(db)


def test_default_throughput_when_never_measured(stats: StatsStore) -> None:
    # 既定値は rsgain の有無で変わるので、実行環境に合わせて比べる
    assert stats.loudness_throughput == default_throughput()


def test_default_throughput_matches_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    """rsgain がある環境では、その分だけ速い既定値を使うこと。"""
    def only(tool: str):
        return lambda name: f"/usr/bin/{name}" if name == tool else None

    monkeypatch.setattr(shutil, "which", only("rsgain"))
    assert default_throughput() == DEFAULT_LOUDNESS_THROUGHPUT_RSGAIN

    monkeypatch.setattr(shutil, "which", only("ffmpeg"))
    assert default_throughput() == DEFAULT_LOUDNESS_THROUGHPUT


def test_records_observed_throughput(stats: StatsStore) -> None:
    # 3600 秒の音源を 60 秒で処理 = 60x
    stats.record_loudness_throughput(audio_seconds=3600, elapsed_seconds=60)
    assert stats.loudness_throughput == pytest.approx(60.0)


def test_throughput_is_smoothed_across_runs(stats: StatsStore) -> None:
    stats.record_loudness_throughput(3600, 60)     # 60x
    stats.record_loudness_throughput(3600, 30)     # 120x
    # 直近を重めに混ぜる（60*0.4 + 120*0.6 = 96）
    assert stats.loudness_throughput == pytest.approx(96.0)


def test_invalid_measurements_are_ignored(stats: StatsStore) -> None:
    stats.record_loudness_throughput(0, 60)
    stats.record_loudness_throughput(3600, 0)
    assert stats.loudness_throughput == default_throughput()


def test_tiny_sample_does_not_pollute_estimate(stats: StatsStore) -> None:
    """数曲だけの計測で見積もりが崩れないこと。

    実機で 4 秒の曲 6 曲を計測したとき、プロセス起動の時間が支配的になって
    121x と記録され、実力（約 350x）の見積もりが 280x から 184x へ落ちた。
    """
    stats.record_loudness_throughput(3600, 12)                  # 300x を記録
    stats.record_loudness_throughput(24, 0.2)                   # 24 秒 = 短すぎる
    assert stats.loudness_throughput == pytest.approx(300.0)    # 影響を受けない


def test_estimate_counts_only_unmeasured_tracks(db: Database, stats: StatsStore) -> None:
    _add_track(db, "a.flac", 300, measured=True)
    _add_track(db, "b.flac", 300, measured=False)
    _add_track(db, "c.flac", 600, measured=False)

    estimate = estimate_loudness(db, stats)
    assert estimate.total_tracks == 3
    assert estimate.measured_tracks == 1
    assert estimate.remaining_tracks == 2
    assert estimate.remaining_audio_seconds == pytest.approx(900)
    # 既定 80x なら 900/80 = 11.25 秒
    assert estimate.estimated_seconds == pytest.approx(900 / DEFAULT_LOUDNESS_THROUGHPUT)
    assert estimate.is_calibrated is False


def test_estimate_for_remeasure_covers_everything(db: Database, stats: StatsStore) -> None:
    _add_track(db, "a.flac", 300, measured=True)
    _add_track(db, "b.flac", 300, measured=False)

    estimate = estimate_loudness(db, stats, remeasure=True)
    assert estimate.remaining_tracks == 2
    assert estimate.remaining_audio_seconds == pytest.approx(600)


def test_estimate_uses_measured_throughput(db: Database, stats: StatsStore) -> None:
    _add_track(db, "a.flac", 3600, measured=False)
    stats.record_loudness_throughput(3600, 90)  # 40x

    estimate = estimate_loudness(db, stats)
    assert estimate.throughput == pytest.approx(40.0)
    assert estimate.estimated_seconds == pytest.approx(90.0)
    assert estimate.is_calibrated is True


def test_estimate_dict_includes_readable_text(db: Database, stats: StatsStore) -> None:
    _add_track(db, "a.flac", 3600 * 20, measured=False)
    payload = estimate_loudness(db, stats).as_dict()
    assert payload["estimated_text"].endswith("分") or payload["estimated_text"].endswith("時間")
    assert payload["remaining"] == 1


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "0 秒"),
        (45, "45 秒"),
        (600, "10 分"),
        (5400, "1.5 時間"),
        (3600 * 30, "1.2 日"),
    ],
)
def test_format_duration_ja(seconds: float, expected: str) -> None:
    assert format_duration_ja(seconds) == expected


def test_runtime_stats_table_exists(db: Database) -> None:
    """マイグレーション 0002 が適用されていること。"""
    row = db.query_one(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'runtime_stats'"
    )
    assert row is not None
    stats = StatsStore(db)
    stats.set_float(KEY_LOUDNESS_THROUGHPUT, 12.5)
    assert stats.get_float(KEY_LOUDNESS_THROUGHPUT, 0.0) == pytest.approx(12.5)


# --------------------------------------------- 「最近追加したアルバム」の起点


def test_recent_baseline_is_unset_at_first(stats: StatsStore) -> None:
    assert stats.recent_baseline is None


def test_ensure_sets_the_baseline_once(stats: StatsStore) -> None:
    """初回スキャンで起点が置かれ、以降のスキャンでは動かないこと。

    毎回いまに更新すると、直前に足したアルバムまで新着から外れてしまう。
    """
    stats.ensure_recent_baseline()
    first = stats.recent_baseline
    assert first is not None

    stats.ensure_recent_baseline()
    assert stats.recent_baseline == first


def test_mark_moves_the_baseline(stats: StatsStore) -> None:
    """明示的に印を付け直すと起点が更新されること。"""
    stats.mark_recent_baseline()
    before = stats.recent_baseline
    stats.set_text(KEY_RECENT_BASELINE, "2000-01-01 00:00:00")
    assert stats.recent_baseline == "2000-01-01 00:00:00"
    assert before != stats.recent_baseline
