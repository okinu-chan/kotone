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
    def __init__(self, state: str = "play") -> None:
        self.volume = 0
        self.song = {"file": "a.flac"}
        self.connected = True
        self.state = state
        self.calls: list[tuple] = []
        self.elapsed = 0.0
        self.song_id = "1"
        #: 再開時に進んでしまう秒数（実機で 1〜3 秒観測された）
        self.resume_drift = 0.0

    def on_event(self, cb) -> None:  # noqa: ANN001
        pass

    async def status(self) -> dict:
        return {
            "volume": self.volume,
            "state": self.state,
            "elapsed": self.elapsed,
            "songid": self.song_id,
        }

    async def current_song(self) -> dict:
        return dict(self.song)

    async def set_volume(self, v: int) -> None:
        self.volume = max(0, min(100, int(v)))
        self.calls.append(("setvol", self.volume))

    async def play(self, position: int | None = None) -> None:
        self.state = "play"
        self.calls.append(("play", position))

    async def seek(self, seconds: float) -> None:
        self.elapsed = seconds
        self.calls.append(("seek", seconds))

    async def pause(self, paused: bool | None = None) -> None:
        self.state = "pause" if paused else "play"
        self.calls.append(("pause", paused))
        # 再開時にバッファ分だけ位置が進む MPD の挙動を模す
        if paused is False:
            self.elapsed += self.resume_drift


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


# ------------------------------------------------ 外部アンプで音量を取る構成


async def test_external_mode_keeps_mpd_at_full_scale(controller) -> None:
    """アンプ側で音量を取る場合、MPD は最大のままにする（デジタル減衰を避ける）。"""
    ctrl, mpd = controller
    ctrl.settings.set("volume_control", "external")
    ctrl.settings.set("loudness_enabled", False)
    ctrl.user_volume = 30  # UI 側の値は無視される
    await ctrl._apply_loudness(await mpd.status())
    assert mpd.volume == 100


async def test_external_mode_still_attenuates_for_loudness(controller) -> None:
    """外部音量でもラウドネス補正の減衰は効く（正方向はクランプされる）。"""
    ctrl, mpd = controller
    ctrl.settings.set("volume_control", "external")
    ctrl.repo.tracks["a.flac"]["track_gain_db"] = -6.0
    ctrl.repo.tracks["a.flac"]["track_peak"] = 1.0
    await ctrl._apply_loudness(await mpd.status())
    # 100 * 10^(-6/20) = 50.1 -> 50
    assert mpd.volume == 50


# ------------------------------------------- 再生開始時の欠落対策（ウォームアップ）


async def test_warmup_mutes_then_seeks_back_to_start(db, settings) -> None:
    """停止状態からの再生では、無音で DAC を起こしてから曲頭へ戻す（要件 3.1）。"""
    mpd = FakeMPD(state="stop")
    mpd.volume = 40
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())
    ctrl.user_volume = 45
    settings.set("startup_warmup_ms", 50)

    await ctrl._start_with_warmup(3)

    kinds = [c[0] for c in mpd.calls]
    assert kinds == ["setvol", "play", "seek", "setvol"]
    assert mpd.calls[0][1] == 0        # まず無音にする
    assert mpd.calls[1][1] == 3        # 指定位置から再生
    assert mpd.calls[2][1] == 0        # 曲頭へ戻す
    # 最後は「元の音量」ではなく、いまの曲に対する実効音量を計算し直した値
    assert mpd.calls[3][1] == 45


async def test_warmup_does_not_clobber_loudness_applied_meanwhile(db, settings) -> None:
    """ウォームアップ中に idle 経由で適用された補正値を上書きしない（実機で発覚）。"""
    mpd = FakeMPD(state="stop")
    repo = FakeRepo(
        {"a.flac": {"track_gain_db": -4.83, "track_peak": 0.9,
                    "album_gain_db": -4.83, "album_peak": 0.9}}
    )
    ctrl = PlaybackController(mpd, db, repo, settings, EventBus())
    settings.set("startup_warmup_ms", 30)
    settings.set("volume_control", "external")

    await ctrl._start_with_warmup(0)

    # 100 * 10^(-4.83/20) = 57
    assert mpd.volume == 57


async def test_warmup_skipped_when_already_playing(db, settings) -> None:
    """再生中はデバイスが開いているのでウォームアップ不要（曲間は gapless に任せる）。"""
    mpd = FakeMPD(state="play")
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())
    settings.set("startup_warmup_ms", 500)

    await ctrl._start_with_warmup(2)

    assert [c[0] for c in mpd.calls] == ["play"]


async def test_warmup_disabled_by_setting(db, settings) -> None:
    mpd = FakeMPD(state="stop")
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())
    settings.set("startup_warmup_ms", 0)

    await ctrl._start_with_warmup(1)

    assert [c[0] for c in mpd.calls] == ["play"]


# ------------------------------------------ 一時停止からの再開（位置ズレの補正）


async def test_resume_returns_to_paused_position(db, settings) -> None:
    """再開時にバッファ分だけ進んでしまう位置を、止めた位置へ戻す。"""
    mpd = FakeMPD(state="play")
    mpd.elapsed = 42.0
    mpd.resume_drift = 3.2          # 実機で観測された飛び
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())

    await ctrl.pause()
    assert ctrl._paused_at == pytest.approx(42.0)

    await ctrl.play()
    # 45.2 まで進んだあと 42.0 へ戻る
    assert ("seek", pytest.approx(42.0)) in mpd.calls
    assert mpd.elapsed == pytest.approx(42.0)


async def test_resume_without_drift_does_not_seek(db, settings) -> None:
    """ズレが無ければ余計なシークをしない（不要な音の途切れを避ける）。"""
    mpd = FakeMPD(state="play")
    mpd.elapsed = 10.0
    mpd.resume_drift = 0.05
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())

    await ctrl.pause()
    await ctrl.play()
    assert not any(call[0] == "seek" for call in mpd.calls)


async def test_manual_seek_discards_paused_position(db, settings) -> None:
    """一時停止中に手動シークしたら、控えていた位置は使わない。"""
    mpd = FakeMPD(state="play")
    mpd.elapsed = 30.0
    mpd.resume_drift = 2.0
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())

    await ctrl.pause()
    await ctrl.seek(90.0)
    assert ctrl._paused_at is None

    mpd.calls.clear()
    await ctrl.play()
    # 30 秒へ戻されたりしない
    assert not any(call == ("seek", 30.0) for call in mpd.calls)


async def test_resume_after_song_change_does_not_seek(db, settings) -> None:
    """一時停止中に曲が変わった場合は、前の曲の位置へ戻さない。"""
    mpd = FakeMPD(state="play")
    mpd.elapsed = 20.0
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())

    await ctrl.pause()
    mpd.song_id = "2"           # 別の曲になった
    mpd.elapsed = 0.0
    mpd.calls.clear()
    await ctrl.play()
    assert not any(call[0] == "seek" for call in mpd.calls)


async def test_external_mode_ignores_external_volume_events(controller) -> None:
    """アンプ側で取る構成では、MPD の音量変化に追随しない。"""
    ctrl, mpd = controller
    ctrl.settings.set("volume_control", "external")
    ctrl.user_volume = 55
    ctrl._expected_volume = 100
    mpd.volume = 20
    await ctrl._reconcile_external_volume()
    assert ctrl.user_volume == 55  # 変化しない
