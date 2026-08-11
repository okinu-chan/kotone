"""再生コントローラの音量ロジック（ラウドネス補正・外部音量の追随）のテスト。

実機テストで見つかった「音量ドリフト」の回帰防止（要件 3.1）。
MPD への依存は最小限のフェイクで差し替える。
"""

from __future__ import annotations

import pytest

from kotone.db import Database
from kotone.events import EventBus
from kotone.playback import PlaybackController
from kotone.settings import SettingsStore


class FakeMPD:
    def __init__(self) -> None:
        self.volume = 0
        self.song = {"file": "a.flac"}
        self.connected = True

    def on_event(self, cb) -> None:  # noqa: ANN001
        pass

    async def status(self) -> dict:
        return {"volume": self.volume, "state": "play"}

    async def current_song(self) -> dict:
        return dict(self.song)

    async def set_volume(self, v: int) -> None:
        self.volume = max(0, min(100, int(v)))


class FakeRepo:
    def __init__(self, tracks: dict[str, dict]) -> None:
        self.tracks = tracks

    def get_track_by_path(self, path: str) -> dict | None:
        return self.tracks.get(path)

    def scope_label(self, kind: str, scope_id: str | None) -> str:
        return "test"


@pytest.fixture
def controller(db: Database, settings: SettingsStore):
    mpd = FakeMPD()
    repo = FakeRepo(
        {
            # +3.95dB のトラック（実機テストでドリフトを誘発したのと同じゲイン）
            "a.flac": {
                "track_gain_db": 3.95,
                "track_peak": 0.5,
                "album_gain_db": 3.95,
                "album_peak": 0.5,
            }
        }
    )
    return PlaybackController(mpd, db, repo, settings, EventBus()), mpd


async def test_apply_loudness_boosts_by_gain(controller) -> None:
    ctrl, mpd = controller
    ctrl.user_volume = 35
    await ctrl._apply_loudness(await mpd.status())
    # 35 * 10^(3.95/20) = 55.2 -> 55（ピーク0.5なので上限係数2.0以内、クリップ回避OK）
    assert mpd.volume == 55
    assert ctrl._expected_volume == 55


async def test_expected_volume_is_recorded_before_setvol(controller) -> None:
    """レース対策: setvol より先に期待値が記録されること。"""
    ctrl, mpd = controller
    ctrl.user_volume = 40
    await ctrl._apply_loudness(await mpd.status())
    # 期待値と実際の MPD 音量が一致 = 自分の setvol を外部変更と誤認しない土台
    assert ctrl._expected_volume == mpd.volume


async def test_reconcile_adopts_external_value_without_dividing(controller) -> None:
    """外部音量変更はゲインで割り戻さず、そのまま user_volume に採用する。"""
    ctrl, mpd = controller
    ctrl.user_volume = 35
    ctrl._expected_volume = 55  # 直前に自分が設定した実効音量
    mpd.volume = 20  # 他クライアントが 20 に変更
    await ctrl._reconcile_external_volume()
    # 割り戻すと 20/1.58≈13 になっていた。今は 20 をそのまま採用する。
    assert ctrl.user_volume == 20
    assert ctrl._expected_volume == 20


async def test_reconcile_ignores_own_setvol_event(controller) -> None:
    """自分の setvol が生む mixer イベント（観測値=期待値）では変化しない。"""
    ctrl, mpd = controller
    ctrl.user_volume = 40
    ctrl._expected_volume = 60
    mpd.volume = 60
    await ctrl._reconcile_external_volume()
    assert ctrl.user_volume == 40  # 変わらない


async def test_no_drift_over_repeated_events(controller) -> None:
    """setvol→mixerイベント→reconcile を繰り返しても値がドリフトしない。"""
    ctrl, mpd = controller
    ctrl.user_volume = 50
    for _ in range(5):
        await ctrl._apply_loudness(await mpd.status())
        # 自分の setvol による mixer イベントを模擬
        await ctrl._reconcile_external_volume()
    assert ctrl.user_volume == 50  # ドリフトしない
    assert mpd.volume == round(50 * (10 ** (3.95 / 20)))  # 79


async def test_loudness_disabled_is_passthrough(controller) -> None:
    ctrl, mpd = controller
    ctrl.settings.set("loudness_enabled", False)
    ctrl.user_volume = 42
    await ctrl._apply_loudness(await mpd.status())
    assert mpd.volume == 42  # 補正なし
