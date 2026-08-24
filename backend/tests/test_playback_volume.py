"""再生コントローラの音量ロジック（ラウドネス補正・外部音量の追随）のテスト。

実機テストで見つかった「音量ドリフト」の回帰防止（要件 3.1）。
MPD への依存は最小限のフェイクで差し替える。
"""

from __future__ import annotations

import asyncio

import pytest

from kotone import playback
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
        #: キューの中身（先回りの音量設定が引く）
        self.queue_items: list[dict] = [{"file": "a.flac"}]
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

    async def song_at(self, position: int) -> dict | None:
        if position < 0 or position >= len(self.queue_items):
            return None
        return dict(self.queue_items[position])

    async def set_volume(self, v: int) -> None:
        self.volume = max(0, min(100, int(v)))
        self.calls.append(("setvol", self.volume))

    @property
    def volumes(self) -> list[int]:
        """送られた setvol の値だけを順に取り出す。"""
        return [v for name, v in self.calls if name == "setvol"]

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


# ------------------------------------- 曲の変わり目のノイズ対策（音量フェード）


@pytest.fixture
def fast_fade(monkeypatch: pytest.MonkeyPatch) -> None:
    """テストを待たせないため、フェードの刻みを詰める。"""
    monkeypatch.setattr(playback, "VOLUME_FADE_STEP_SECONDS", 0.001)


async def test_song_change_moves_volume_gradually(db, settings, fast_fade) -> None:
    """曲が変わったときの音量変化を段階的にする。

    一気に setvol すると出力に段差ができ、「プツッ」というノイズになる。
    ギャップレス再生でつながっているライブ盤などではそのまま聴こえる。
    """
    mpd = FakeMPD(state="play")
    mpd.volume = 100
    repo = FakeRepo({"a.flac": {"track_gain_db": -7.69, "track_peak": 0.9,
                                "album_gain_db": -7.69, "album_peak": 0.9}})
    ctrl = PlaybackController(mpd, db, repo, settings, EventBus())
    settings.set("volume_control", "external")

    mpd.song_id = "2"                       # 次の曲へ進んだ
    await ctrl._on_song_changed()
    await ctrl._fade_task

    volumes = mpd.volumes
    # 100 -> 41 を一気にではなく刻んで下ろす
    assert len(volumes) > 10
    assert volumes[-1] == 41
    assert volumes == sorted(volumes, reverse=True)
    # どの一歩も小さいこと（段差が残っていない）
    assert max(abs(b - a) for a, b in zip(volumes, volumes[1:], strict=False)) <= 3


async def test_song_change_uses_the_fixed_fade(db, settings, fast_fade) -> None:
    """曲の変わり目のフェード時間は一定（クロスフェードは提供しない）。"""
    mpd = FakeMPD(state="play")
    mpd.volume = 100
    repo = FakeRepo({"a.flac": {"track_gain_db": -6.0, "track_peak": 1.0,
                                "album_gain_db": -6.0, "album_peak": 1.0}})
    ctrl = PlaybackController(mpd, db, repo, settings, EventBus())
    settings.set("volume_control", "external")

    mpd.song_id = "2"
    await ctrl._on_song_changed()
    assert ctrl._fading
    await ctrl._fade_task
    assert mpd.volumes[-1] == 50            # 100 * 10^(-6/20)


async def test_new_song_change_continues_from_current_level(db, settings, fast_fade) -> None:
    """フェードの途中で次の曲へ進んでも、音量が飛ばずに続きから動くこと。"""
    mpd = FakeMPD(state="play")
    mpd.volume = 100
    repo = FakeRepo({"a.flac": {"track_gain_db": -20.0, "track_peak": 0.5,
                                "album_gain_db": -20.0, "album_peak": 0.5}})
    ctrl = PlaybackController(mpd, db, repo, settings, EventBus())
    settings.set("volume_control", "external")

    mpd.song_id = "2"
    await ctrl._apply_loudness(await mpd.status(), fade=0.4)
    await asyncio.sleep(0.05)               # フェードの途中
    interrupted_at = mpd.volume
    assert 10 < interrupted_at < 100        # 実際に下がっている最中

    # まだ下がりきらないうちに次の曲へ
    repo.tracks["a.flac"]["track_gain_db"] = 0.0
    mpd.song_id = "3"
    await ctrl._apply_loudness(await mpd.status(), fade=0.1)
    await ctrl._fade_task

    volumes = mpd.volumes
    assert volumes[-1] == 100
    # 打ち切った地点から上げ直す（100 へ飛んだり、いったん元へ戻ったりしない）
    assert max(abs(b - a) for a, b in zip(volumes, volumes[1:], strict=False)) <= 3


async def test_fade_ignores_its_own_mixer_events(db, settings, fast_fade) -> None:
    """フェード中の mixer イベントを外部からの音量変更と誤認しないこと。

    setvol は 1 段ごとに mixer イベントを生む。観測値は 1 つ前の段の値である
    ことがあり、期待値との一致では見分けられない。
    """
    mpd = FakeMPD(state="play")
    mpd.volume = 100
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())
    ctrl.user_volume = 80
    ctrl._expected_volume = 100

    ctrl._fade_task = asyncio.create_task(ctrl._fade_volume(100, 40, 0.05))
    await asyncio.sleep(0.01)
    await ctrl._reconcile_external_volume()
    assert ctrl.user_volume == 80           # 自分のフェードでは変わらない
    await ctrl._fade_task

    # フェードが終われば、外部からの変更にはこれまでどおり追随する
    mpd.volume = 25
    await ctrl._reconcile_external_volume()
    assert ctrl.user_volume == 25


async def test_volume_slider_still_lands_on_the_requested_value(db, settings, fast_fade) -> None:
    """スライダー操作もフェードするが、最終的な値はずれないこと。"""
    mpd = FakeMPD(state="play")
    mpd.volume = 20
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())

    await ctrl.set_volume(70)
    await ctrl._fade_task
    assert mpd.volume == 70
    assert ctrl.user_volume == 70


async def test_fade_does_not_flood_clients_with_state(db, settings, fast_fade) -> None:
    """フェード中の mixer イベントで状態を配信しないこと。

    setvol は 1 段ごとに mixer イベントを生む。そのたびに配信すると、
    UI に出る音量は動いていないのに 3 秒間で数十回流れることになる。
    """
    mpd = FakeMPD(state="play")
    mpd.volume = 100
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())

    broadcasts = 0

    async def count() -> None:
        nonlocal broadcasts
        broadcasts += 1

    ctrl.broadcast_state = count

    ctrl._fade_task = asyncio.create_task(ctrl._fade_volume(100, 40, 0.05))
    await ctrl._on_mpd_event({"mixer"})
    assert broadcasts == 0
    await ctrl._fade_task

    # フェードが終われば、音量の変化はこれまでどおり配信する
    await ctrl._on_mpd_event({"mixer"})
    assert broadcasts == 1


async def test_song_change_is_still_broadcast_during_a_fade(db, settings, fast_fade) -> None:
    """曲が変わったことは、フェード中でも必ず配信すること。"""
    mpd = FakeMPD(state="play")
    ctrl = PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())

    broadcasts = 0

    async def count() -> None:
        nonlocal broadcasts
        broadcasts += 1

    ctrl.broadcast_state = count
    ctrl._fade_task = asyncio.create_task(ctrl._fade_volume(100, 40, 0.05))
    await ctrl._on_mpd_event({"player", "mixer"})
    assert broadcasts == 1
    await ctrl._fade_task


# ------------------------------------------ 終端での曲送り（スコープからの脱落防止）


class QueueMPD(FakeMPD):
    """キューの位置と長さを持つフェイク（曲送りの可否を試すため）。"""

    def __init__(self, position: int = 0, length: int = 3) -> None:
        super().__init__(state="play")
        self.position = position
        self.length = length
        self.nexted = 0
        self.previoused = 0
        self.queue_items = [{"file": f"{i}.flac"} for i in range(length)]

    async def status(self) -> dict:
        base = await super().status()
        base.update({"song": self.position, "playlistlength": self.length})
        if self.position + 1 < self.length:
            base["nextsong"] = self.position + 1
        return base

    async def next(self) -> None:
        self.nexted += 1
        self.position += 1
        self.calls.append(("next", None))

    async def previous(self) -> None:
        self.previoused += 1
        self.position = max(0, self.position - 1)
        self.calls.append(("previous", None))


def _controller(mpd, db, settings) -> PlaybackController:
    return PlaybackController(mpd, db, FakeRepo({}), settings, EventBus())


async def test_next_is_ignored_at_the_end_of_the_scope(db, settings) -> None:
    """リピートもシャッフルも無い状態では、終端で曲送りを受け付けない。

    受け付けると MPD がキューを使い切って停止し、画面は「再生していません」に
    なる。聴いていたアルバムへ戻る手がかりまで消えてしまう。
    """
    mpd = QueueMPD(position=2, length=3)
    ctrl = _controller(mpd, db, settings)

    await ctrl.next()

    assert mpd.nexted == 0
    assert mpd.position == 2


async def test_next_works_before_the_end(db, settings) -> None:
    mpd = QueueMPD(position=1, length=3)
    ctrl = _controller(mpd, db, settings)

    await ctrl.next()

    assert mpd.nexted == 1


@pytest.mark.parametrize("repeat", ["one", "range"])
async def test_repeat_still_advances_at_the_end(db, settings, repeat: str) -> None:
    """リピート中は巻き戻るので、終端でも曲送りできる。"""
    mpd = QueueMPD(position=2, length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl.repeat_mode = repeat

    await ctrl.next()

    assert mpd.nexted == 1


@pytest.mark.parametrize("order", ["shuffle", "random"])
async def test_shuffle_and_random_still_advance(db, settings, order: str) -> None:
    """シャッフル・ランダムでは次の曲が用意されるので受け付ける。"""
    mpd = QueueMPD(position=2, length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl.order_mode = order

    await ctrl.next()

    assert mpd.nexted == 1


async def test_next_allowed_while_stopped(db, settings) -> None:
    """停止中は終端と見なさない（再生の開始を邪魔しない）。"""
    mpd = QueueMPD(position=2, length=3)
    mpd.state = "stop"
    ctrl = _controller(mpd, db, settings)

    await ctrl.next()

    assert mpd.nexted == 1


async def test_single_track_scope_does_not_advance(db, settings) -> None:
    """1 曲だけのスコープでも同じ（position 0 が終端になる）。"""
    mpd = QueueMPD(position=0, length=1)
    ctrl = _controller(mpd, db, settings)

    await ctrl.next()

    assert mpd.nexted == 0


# --------------------------- 範囲を鳴らし終えたら先頭で待機する（NW-ZX300 に倣う）


class EndingMPD(QueueMPD):
    """最後まで鳴らし終えて停止した状態を作れるフェイク。"""

    def __init__(self, length: int = 3) -> None:
        super().__init__(position=length - 1, length=length)
        self.song_id = str(length - 1)

    def finish_queue(self) -> None:
        """MPD がキューを使い切って止まった状態にする。"""
        self.state = "stop"

    async def play(self, position: int | None = None) -> None:
        await super().play(position)
        if position is not None:
            self.position = position
            self.song_id = str(position)
        self.elapsed = 0.0

    async def stop(self) -> None:
        self.state = "stop"
        self.calls.append(("stop", None))


async def test_finishing_the_scope_waits_at_the_top(db, settings) -> None:
    """鳴らし終えたら、範囲の先頭で一時停止して操作待ちにする。

    MPD はキューを使い切ると止まるだけで、画面から曲もアルバムも消える。
    聴き終えたアルバムを聴き直すのにライブラリから探し直すことになる。
    """
    mpd = EndingMPD(length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl._last_play_state = "play"
    mpd.finish_queue()

    handled = await ctrl._return_to_scope_top(await mpd.status())

    assert handled
    assert mpd.position == 0            # 先頭の曲に構える
    assert mpd.state == "pause"         # 鳴らさずに待つ
    assert mpd.elapsed == 0.0


async def test_returning_to_the_top_is_silent(db, settings) -> None:
    """先頭へ構え直す間に音が出ないこと（いったん無音にしてから止める）。"""
    mpd = EndingMPD(length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl.user_volume = 55
    ctrl._last_play_state = "play"
    mpd.finish_queue()

    await ctrl._return_to_scope_top(await mpd.status())

    volumes = mpd.volumes
    # play より前に 0 まで落としている
    assert volumes[0] == 0
    assert [c[0] for c in mpd.calls].index("setvol") < [c[0] for c in mpd.calls].index("play")
    # 待機中の音量は先頭の曲に合わせた値へ戻す
    assert mpd.volume == 55
    assert ctrl._expected_volume == 55   # 自分で下げた 0 を外部変更と誤認しない


async def test_stop_button_does_not_rewind(db, settings) -> None:
    """停止ボタンで止めたときは先頭へ戻さない（意図した停止のため）。"""
    mpd = EndingMPD(length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl._last_play_state = "play"

    await ctrl.stop()
    mpd.state = "stop"
    handled = await ctrl._return_to_scope_top(await mpd.status())

    assert not handled
    assert mpd.position == 2


async def test_stop_flag_does_not_leak_into_the_next_end(db, settings) -> None:
    """停止ボタンの印が、次に鳴らし終えたときまで残らないこと。"""
    mpd = EndingMPD(length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl._last_play_state = "play"

    await ctrl.stop()
    mpd.state = "stop"
    await ctrl._return_to_scope_top(await mpd.status())

    # もう一度再生して、今度は最後まで鳴らし終えた
    mpd.state = "play"
    await ctrl._return_to_scope_top(await mpd.status())
    mpd.position = 2
    mpd.state = "stop"
    handled = await ctrl._return_to_scope_top(await mpd.status())

    assert handled
    assert mpd.position == 0


@pytest.mark.parametrize(
    ("repeat", "order"), [("range", "normal"), ("one", "normal"), ("off", "random")]
)
async def test_repeat_or_random_never_finishes(db, settings, repeat, order) -> None:
    """リピート中やランダムでは鳴らし終えないので、先頭へ戻す処理も走らない。"""
    mpd = EndingMPD(length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl.repeat_mode = repeat
    ctrl.order_mode = order
    ctrl._last_play_state = "play"
    mpd.finish_queue()

    assert not await ctrl._return_to_scope_top(await mpd.status())


async def test_pause_is_not_mistaken_for_the_end(db, settings) -> None:
    """一時停止は停止ではないので、先頭へ戻さない。"""
    mpd = EndingMPD(length=3)
    ctrl = _controller(mpd, db, settings)
    ctrl._last_play_state = "play"
    mpd.state = "pause"

    assert not await ctrl._return_to_scope_top(await mpd.status())
    assert mpd.position == 2


# ------------------------------- Bluetooth 出力ではウォームアップを行わない


class _Outputs:
    """出力先のフェイク（bluetooth かどうかだけ返す）。"""

    def __init__(self, bluetooth: bool) -> None:
        self._bluetooth = bluetooth

    async def active(self) -> dict:
        return {"id": 0, "name": "Bluetooth" if self._bluetooth else "USB DAC",
                "enabled": True, "bluetooth": self._bluetooth}


async def test_no_warmup_on_bluetooth(db, settings) -> None:
    """Bluetooth では曲頭へのシークをしないこと。

    A2DP にサンプリングレートを追従させる必要は無く、シークがストリームの
    取り直し（bluealsa の "PCM drop"）を招いて鳴り始めが途切れる。
    """
    mpd = FakeMPD(state="stop")
    ctrl = PlaybackController(
        mpd, db, FakeRepo({}), settings, EventBus(), outputs=_Outputs(True)
    )
    settings.set("startup_warmup_ms", 600)

    await ctrl._start_with_warmup(2)

    assert [c[0] for c in mpd.calls] == ["play"]   # 無音化も seek もしない


async def test_warmup_still_runs_on_usb_dac(db, settings) -> None:
    """USB DAC ではこれまでどおりウォームアップする（曲頭の欠け対策）。"""
    mpd = FakeMPD(state="stop")
    mpd.volume = 40
    ctrl = PlaybackController(
        mpd, db, FakeRepo({}), settings, EventBus(), outputs=_Outputs(False)
    )
    ctrl.user_volume = 45
    settings.set("startup_warmup_ms", 50)

    await ctrl._start_with_warmup(3)

    assert [c[0] for c in mpd.calls] == ["setvol", "play", "seek", "setvol"]


# ------------------------- 出力バッファが埋まる前に、次の曲の音量を入れておく


def _repo_with_gains(gains: dict[str, float]) -> FakeRepo:
    return FakeRepo(
        {
            path: {"track_gain_db": gain, "track_peak": 0.5,
                   "album_gain_db": gain, "album_peak": 0.5}
            for path, gain in gains.items()
        }
    )


async def test_next_sets_the_new_volume_before_skipping(db, settings) -> None:
    """曲送りでは、next を送る前に次の曲の音量を入れること。

    MPD は曲送りで出力バッファを捨て、新しい曲で一気に埋め直す。埋め直しは
    一瞬なので、鳴り始めてから setvol しても間に合わない。実機（Bluetooth、
    バッファ約 1.1 秒）では「1 秒ほど前の曲の音量で鳴ってから補正値へ滑る」
    形になっていた。
    """
    mpd = QueueMPD(position=0, length=3)
    ctrl = PlaybackController(
        mpd, db, _repo_with_gains({"1.flac": -6.0}), settings, EventBus()
    )
    ctrl.user_volume = 80
    ctrl._expected_volume = 80

    await ctrl.next()

    # 80 * 10^(-6/20) = 40.1 -> 40
    assert mpd.calls == [("setvol", 40), ("next", None)]


async def test_previous_sets_the_new_volume_before_skipping(db, settings) -> None:
    mpd = QueueMPD(position=2, length=3)
    ctrl = PlaybackController(
        mpd, db, _repo_with_gains({"1.flac": -6.0}), settings, EventBus()
    )
    ctrl.user_volume = 80
    ctrl._expected_volume = 80

    await ctrl.previous()

    assert mpd.calls == [("setvol", 40), ("previous", None)]


async def test_previous_within_three_seconds_only_rewinds(db, settings) -> None:
    """3 秒以上聴いていたら曲頭へ戻すだけ。同じ曲なので音量は動かさない。"""
    mpd = QueueMPD(position=2, length=3)
    mpd.elapsed = 10.0
    ctrl = PlaybackController(
        mpd, db, _repo_with_gains({"1.flac": -6.0}), settings, EventBus()
    )
    ctrl.user_volume = 80
    ctrl._expected_volume = 80

    await ctrl.previous()

    assert mpd.calls == [("seek", 0)]


async def test_shuffle_previous_does_not_guess_the_target(db, settings) -> None:
    """シャッフルでは MPD がどの曲へ戻すか分からないので先回りしない。"""
    mpd = QueueMPD(position=2, length=3)
    ctrl = PlaybackController(
        mpd, db, _repo_with_gains({"1.flac": -6.0}), settings, EventBus()
    )
    ctrl.order_mode = "shuffle"
    ctrl.user_volume = 80
    ctrl._expected_volume = 80

    await ctrl.previous()

    assert mpd.calls == [("previous", None)]


async def test_next_at_the_end_touches_nothing(db, settings) -> None:
    """終端では曲送り自体を受け付けないので、音量も動かさない。"""
    mpd = QueueMPD(position=2, length=3)
    ctrl = PlaybackController(
        mpd, db, _repo_with_gains({"1.flac": -6.0}), settings, EventBus()
    )
    ctrl.user_volume = 80
    ctrl._expected_volume = 80

    await ctrl.next()

    assert mpd.calls == []


async def test_playback_start_sets_the_volume_before_play(db, settings) -> None:
    """再生開始も同じ。play より先に、その曲の音量を入れること。"""
    mpd = QueueMPD(position=0, length=3)
    mpd.state = "stop"
    ctrl = PlaybackController(
        mpd, db, _repo_with_gains({"2.flac": -6.0}), settings, EventBus(),
        outputs=_Outputs(True),
    )
    ctrl.user_volume = 80
    ctrl._expected_volume = 80
    settings.set("startup_warmup_ms", 600)

    await ctrl._start_with_warmup(2)

    assert mpd.calls == [("setvol", 40), ("play", 2)]


async def test_preset_is_silent_when_the_volume_already_matches(db, settings) -> None:
    """同じ音量なら setvol を送らない（無駄な mixer イベントを増やさない）。"""
    mpd = QueueMPD(position=0, length=3)
    ctrl = PlaybackController(
        mpd, db, _repo_with_gains({"1.flac": 0.0}), settings, EventBus()
    )
    ctrl.user_volume = 80
    ctrl._expected_volume = 80

    await ctrl.next()

    assert mpd.calls == [("next", None)]
