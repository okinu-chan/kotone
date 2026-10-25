"""DSD の出力方式（ネイティブ / DoP / PCM 変換）と音量バイパスのテスト。

実機（Raspberry Pi 4B + Topping E50 II、MPD 0.24.4）で 2026-10-25 に確かめた
挙動を前提にしている。

* MPD のソフトウェア音量は DSD を素通しにする（音量 50 でも出力が 100 と同じ）。
* USB DAC (PCM) の ``dsd2pcm`` フィルタを通すと、音量より前で PCM になる。
* 同じ機器を指す 2 出力は、鳴っている最中には入れ替えられない。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kotone import outputs as outputs_module
from kotone.events import EventBus
from kotone.outputs import OutputController, read_alsa_hw_params
from kotone.playback import (
    PlaybackController,
    _parse_audio_format,
    describe_output_format,
)
from kotone.settings import DEFAULTS
from tests.test_playback_volume import FakeMPD, FakeRepo, QueueMPD


class _Outputs:
    """出力先のフェイク。active() の中身だけを決める。"""

    def __init__(self, *, bluetooth: bool = False, dsd_pcm: bool = False, dop: bool = False,
                 swap: bool = False) -> None:
        self.item = {"id": 0, "name": "Bluetooth" if bluetooth else "USB DAC",
                     "enabled": True, "bluetooth": bluetooth, "dsd_pcm": dsd_pcm, "dop": dop}
        self.swap = swap
        self.applied: list[str] = []

    async def active(self) -> dict:
        return dict(self.item)

    async def dsd_mode_needs_swap(self, mode: str) -> bool:
        return self.swap

    async def apply_dsd_mode(self, mode: str) -> bool:
        self.applied.append(mode)
        return self.swap


def _gains(paths: list[str], gain: float = -6.0) -> FakeRepo:
    return FakeRepo({
        path: {"track_gain_db": gain, "track_peak": 0.5, "album_gain_db": gain,
               "album_peak": 0.5}
        for path in paths
    })


def _ctrl(db, settings, mpd, outputs, paths=("a.dsf", "b.flac")) -> PlaybackController:
    return PlaybackController(mpd, db, _gains(list(paths)), settings, EventBus(),
                              outputs=outputs)


# ------------------------------------------------------------ 音量バイパス


async def test_dsd_on_the_bitperfect_usb_output_is_full_scale(db, settings) -> None:
    """ネイティブ / DoP では音量 100、ラウドネス補正なし。"""
    mpd = FakeMPD()
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    ctrl.user_volume = 30

    await ctrl._apply_loudness(await mpd.status(), fade=0.35)

    assert mpd.volumes == [100]          # フェードせず一度で入れる
    assert ctrl._bypassing


@pytest.mark.parametrize("outputs", [_Outputs(dsd_pcm=True), _Outputs(bluetooth=True)])
async def test_converted_dsd_keeps_volume_and_loudness(db, settings, outputs) -> None:
    """PCM 変換・Bluetooth では、DSD の曲にも音量と補正が効く。"""
    mpd = FakeMPD()
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, outputs)
    ctrl.user_volume = 80

    await ctrl._apply_loudness(await mpd.status())

    assert mpd.volumes == [40]           # 80 × 10^(-6/20)
    assert not ctrl._bypassing


async def test_pcm_track_is_untouched_by_the_bypass(db, settings) -> None:
    mpd = FakeMPD()
    mpd.song = {"file": "b.flac"}
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    ctrl.user_volume = 80

    await ctrl._apply_loudness(await mpd.status())

    assert mpd.volumes == [40]


async def test_leaving_dsd_goes_straight_to_the_pcm_volume(db, settings) -> None:
    """DSD → PCM ではフェードしない（PCM の頭が最大音量から下りてこない）。"""
    mpd = FakeMPD()
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    ctrl.user_volume = 80
    await ctrl._apply_loudness(await mpd.status())

    mpd.song = {"file": "b.flac"}
    await ctrl._apply_loudness(await mpd.status(), fade=0.35)

    assert mpd.volumes == [100, 40]
    assert ctrl._fade_task is None
    assert not ctrl._bypassing


async def test_slider_does_not_move_the_volume_during_dsd(db, settings) -> None:
    mpd = FakeMPD()
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    await ctrl._apply_loudness(await mpd.status())

    await ctrl.set_volume(20)

    assert mpd.volume == 100
    assert ctrl.user_volume == 20        # 値は覚えておき、PCM に戻ったら使う


async def test_external_change_is_ignored_during_dsd(db, settings) -> None:
    mpd = FakeMPD()
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    ctrl.user_volume = 30
    await ctrl._apply_loudness(await mpd.status())

    mpd.volume = 55
    await ctrl._reconcile_external_volume()

    assert ctrl.user_volume == 30


async def test_unknown_output_does_not_raise_the_volume(db, settings) -> None:
    """出力が分からなければバイパスしない（100 に上げるより大きくならない）。"""
    mpd = FakeMPD()
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, None)
    ctrl.user_volume = 80

    await ctrl._apply_loudness(await mpd.status())

    assert mpd.volumes == [40]


class _EndingMPD(QueueMPD):
    """曲の残り時間を返すフェイク（DSD の終わり際の受け渡しを試す）。"""

    def __init__(self, remaining: float) -> None:
        super().__init__(position=0, length=2)
        self.duration = 20.0
        self.elapsed = self.duration - remaining

    async def status(self) -> dict:
        base = await super().status()
        base.update({"duration": self.duration, "elapsed": self.elapsed})
        return base


@pytest.mark.parametrize(("following", "expected"), [("b.flac", [100, 40]), ("c.dsf", [100])])
async def test_dsd_hands_over_to_the_next_pcm_volume_before_it_ends(
    db, settings, following, expected
) -> None:
    """自然な変わり目で DSD → PCM のとき、DSD の終わり際に PCM の音量へ下げる。

    曲が変わってから下げると、開き直した出力のバッファが 100 のまま埋まる。
    次も DSD なら 100 のまま。
    """
    mpd = _EndingMPD(remaining=1.5)
    mpd.queue_items = [{"file": "a.dsf"}, {"file": following}]
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, _Outputs(), paths=("a.dsf", "b.flac", "c.dsf"))
    ctrl.user_volume = 80

    await ctrl._apply_loudness(await mpd.status())
    await ctrl._handover_task

    assert mpd.volumes == expected


async def test_handover_waits_while_the_dsd_has_time_left(db, settings) -> None:
    mpd = _EndingMPD(remaining=30.0)
    mpd.queue_items = [{"file": "a.dsf"}, {"file": "b.flac"}]
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    ctrl.user_volume = 80

    await ctrl._apply_loudness(await mpd.status())
    await asyncio.sleep(0.05)

    assert mpd.volumes == [100]
    # PCM の曲へ移ったら見張りはやめる
    mpd.song = {"file": "b.flac"}
    await ctrl._apply_loudness(await mpd.status())
    assert ctrl._handover_task is None


# ------------------------------------------------- 曲送り・再生開始での先回り


async def test_skipping_to_dsd_does_not_preset_full_volume(db, settings) -> None:
    """次が DSD でも先に 100 にしない（捨てる前の今の曲が最大音量で鳴る）。"""
    mpd = QueueMPD(position=0, length=2)
    mpd.queue_items = [{"file": "b.flac"}, {"file": "a.dsf"}]
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    ctrl.user_volume = 80
    ctrl._expected_volume = 40

    await ctrl.next()

    assert mpd.calls == [("next", None)]


async def test_skipping_from_dsd_presets_the_pcm_volume(db, settings) -> None:
    """DSD から PCM へは先に下げてよい（DSD は音量で変わらない）。"""
    mpd = QueueMPD(position=0, length=2)
    mpd.queue_items = [{"file": "a.dsf"}, {"file": "b.flac"}]
    mpd.song = {"file": "a.dsf"}
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    ctrl.user_volume = 80
    await ctrl._apply_loudness(await mpd.status())
    mpd.calls.clear()

    await ctrl.next()

    assert mpd.calls == [("setvol", 40), ("next", None)]


async def test_no_warmup_for_dsd_sent_as_is(db, settings) -> None:
    """setvol 0 が DSD に効かないので、ウォームアップすると曲頭が 2 回鳴る。"""
    mpd = FakeMPD(state="stop")
    mpd.queue_items = [{"file": "a.dsf"}]
    ctrl = _ctrl(db, settings, mpd, _Outputs())
    settings.set("startup_warmup_ms", 50)

    await ctrl._start_with_warmup(0)

    assert ("seek", 0) not in mpd.calls
    assert ("setvol", 0) not in mpd.calls
    assert ("play", 0) in mpd.calls


async def test_warmup_still_runs_for_converted_dsd(db, settings) -> None:
    mpd = FakeMPD(state="stop")
    mpd.queue_items = [{"file": "a.dsf"}]
    ctrl = _ctrl(db, settings, mpd, _Outputs(dsd_pcm=True))
    settings.set("startup_warmup_ms", 50)

    await ctrl._start_with_warmup(0)

    assert ("seek", 0) in mpd.calls


# -------------------------------------------------------- 出力方式の反映


@pytest.mark.parametrize(("state", "song", "swap", "reopened"), [
    ("play", "a.dsf", False, True),     # dop 属性は開き直すまで効かない
    ("play", "b.flac", True, True),     # 同じ機器の 2 出力は鳴りながら入れ替えられない
    ("play", "b.flac", False, False),   # PCM の曲で入れ替えも無ければ止めない
    ("pause", "a.dsf", True, False),    # 一時停止中は機器が閉じている
])
async def test_applying_the_mode_pauses_only_when_needed(
    db, settings, state, song, swap, reopened
) -> None:
    mpd = FakeMPD(state=state)
    mpd.song = {"file": song}
    outputs = _Outputs(swap=swap)
    ctrl = _ctrl(db, settings, mpd, outputs)
    settings.set("dsd_output", "dop")

    await ctrl.apply_dsd_output()

    assert outputs.applied == ["dop"]
    kinds = [c for c in mpd.calls if c[0] == "pause"]
    assert kinds == ([("pause", True), ("pause", False)] if reopened else [])
    assert mpd.state == state


def test_default_is_pcm_conversion() -> None:
    """既定は PCM 変換（音量 100 固定は利用者が選んだときだけ。2026-10-25 決定）。"""
    assert DEFAULTS["dsd_output"] == "pcm"


# ------------------------------------------------------ MPD の出力の扱い


class OutputsMPD:
    """outputs / enableoutput / disableoutput / outputset だけを持つ MPD のフェイク。"""

    def __init__(self, *, variant: bool = True, active: str = "USB DAC") -> None:
        names = ["USB DAC"] + (["USB DAC (PCM)"] if variant else []) + ["Bluetooth"]
        self.items = [
            {"outputid": str(i), "outputname": name,
             "outputenabled": "1" if name == active else "0",
             "attribute": ["allowed_formats=", "dop=0"]}
            for i, name in enumerate(names)
        ]
        self.log: list[tuple] = []

    def _find(self, output_id: int) -> dict:
        return next(item for item in self.items if int(item["outputid"]) == int(output_id))

    async def command(self, name: str, *args):  # noqa: ANN201
        await asyncio.sleep(0)   # 実際の MPD と同じく、ここで他の処理が割り込める
        if name == "outputs":
            return [dict(item) for item in self.items]
        self.log.append((name, *args))
        if name == "enableoutput":
            self._find(args[0])["outputenabled"] = "1"
        elif name == "disableoutput":
            self._find(args[0])["outputenabled"] = "0"
        return None

    async def set_output_attribute(self, output_id: int, name: str, value: str) -> None:
        self.log.append(("outputset", output_id, name, value))
        item = self._find(output_id)
        item["attribute"] = [a for a in item["attribute"] if not a.startswith(f"{name}=")]
        item["attribute"].append(f"{name}={value}")

    def enabled(self) -> list[str]:
        return [item["outputname"] for item in self.items if item["outputenabled"] == "1"]


async def test_the_pcm_variant_is_folded_into_usb_dac() -> None:
    mpd = OutputsMPD(active="USB DAC (PCM)")
    items = await OutputController(mpd).list()

    assert [item["name"] for item in items] == ["USB DAC", "Bluetooth"]
    assert items[0]["enabled"] and items[0]["dsd_pcm"]


@pytest.mark.parametrize(("mode", "expected"), [
    ("pcm", "USB DAC (PCM)"), ("native", "USB DAC"), ("dop", "USB DAC"),
])
async def test_selecting_usb_dac_follows_the_mode(mode, expected) -> None:
    mpd = OutputsMPD(active="Bluetooth")

    await OutputController(mpd).select(0, dsd_mode=mode)

    assert mpd.enabled() == [expected]


async def test_the_variant_cannot_be_selected_directly() -> None:
    with pytest.raises(ValueError):
        await OutputController(OutputsMPD()).select(1)


async def test_swapping_closes_the_old_usb_output_first() -> None:
    """同じ機器なので、先に閉じないと新しいほうが開けない。"""
    mpd = OutputsMPD(active="USB DAC")

    swapped = await OutputController(mpd).apply_dsd_mode("pcm")

    assert swapped
    assert mpd.log == [("disableoutput", 0), ("enableoutput", 1)]


async def test_dop_attribute_follows_the_mode() -> None:
    mpd = OutputsMPD(active="USB DAC")
    controller = OutputController(mpd)

    await controller.apply_dsd_mode("dop")
    assert ("outputset", 0, "dop", "1") in mpd.log
    assert (await controller.active())["dop"]

    mpd.log.clear()
    await controller.apply_dsd_mode("dop")
    assert mpd.log == []                 # 同じなら送らない

    await controller.apply_dsd_mode("native")
    assert mpd.log == [("outputset", 0, "dop", "0")]


async def test_concurrent_applies_swap_only_once(db, settings) -> None:
    """起動時は start() と接続のイベントから同時に呼ばれる（実機のログで 2 回入れ替えていた）。"""
    mpd = OutputsMPD(active="USB DAC")
    player_mpd = FakeMPD(state="pause")
    ctrl = _ctrl(db, settings, player_mpd, OutputController(mpd))

    await asyncio.gather(ctrl.apply_dsd_output(), ctrl.apply_dsd_output())

    assert mpd.log == [("disableoutput", 0), ("enableoutput", 1)]
    assert mpd.enabled() == ["USB DAC (PCM)"]


async def test_bluetooth_is_left_alone() -> None:
    mpd = OutputsMPD(active="Bluetooth")

    swapped = await OutputController(mpd).apply_dsd_mode("pcm")

    assert not swapped
    assert mpd.enabled() == ["Bluetooth"]


async def test_without_the_variant_pcm_mode_stays_on_usb_dac() -> None:
    """古い mpd.conf（USB DAC (PCM) が無い）でも鳴らなくはならない。"""
    mpd = OutputsMPD(variant=False, active="Bluetooth")
    controller = OutputController(mpd)

    assert not await controller.apply_dsd_mode("pcm")
    await controller.select(0, dsd_mode="pcm")

    assert mpd.enabled() == ["USB DAC"]
    assert not (await controller.active())["dsd_pcm"]


# ------------------------------------------------------------ 形式の表示


def test_mpd_reports_dsd_as_rate_and_channels() -> None:
    audio = _parse_audio_format("dsd64:2")

    assert audio["is_dsd"] and audio["dsd_rate"] == 64
    assert audio["samplerate"] == 2822400 and audio["channels"] == 2


def test_pcm_audio_is_not_dsd() -> None:
    audio = _parse_audio_format("44100:16:2")

    assert not audio["is_dsd"] and audio["bit_depth"] == 16


USB = {"id": 0, "name": "USB DAC", "bluetooth": False, "dsd_pcm": False, "dop": False}


@pytest.mark.parametrize(("output", "hw", "kind", "label"), [
    # E50 II の実測値
    (USB, {"format": "DSD_U32_BE", "rate": 88200, "channels": 2}, "dsd_native",
     "DSD64 ネイティブ"),
    ({**USB, "dop": True}, {"format": "S32_LE", "rate": 176400, "channels": 2}, "dop",
     "DSD64 DoP"),
    ({**USB, "dsd_pcm": True}, {"format": "S32_LE", "rate": 352800, "channels": 2},
     "pcm_converted", "PCM 352.8kHz に変換"),
    # 一時停止中（機器が閉じていて hw_params が取れない）は設定から推定する
    ({**USB, "dop": True}, None, "dop", "DSD64 DoP"),
    ({**USB, "dsd_pcm": True}, None, "pcm_converted", "PCM 352.8kHz に変換"),
    ({**USB, "name": "Bluetooth", "bluetooth": True}, None, "pcm_converted",
     "PCM に変換（Bluetooth）"),
])
def test_describes_what_reaches_the_dac(output, hw, kind, label) -> None:
    result = describe_output_format(_parse_audio_format("dsd64:2"), output, hw)

    assert result["kind"] == kind
    assert result["label"] == label
    assert result["verified"] == (hw is not None)


def test_pcm_track_is_described_by_its_rate() -> None:
    result = describe_output_format(
        _parse_audio_format("44100:16:2"), USB,
        {"format": "S32_LE", "rate": 44100, "channels": 2},
    )
    assert result == {"kind": "pcm", "label": "PCM 44.1kHz", "verified": True,
                      "hw": {"format": "S32_LE", "rate": 44100, "channels": 2}}


def test_reads_hw_params_of_the_usb_card(tmp_path: Path, monkeypatch) -> None:
    # card0 は内蔵（stream0 が無い）。開いていても USB DAC ではない
    builtin = tmp_path / "card0" / "pcm0p" / "sub0"
    builtin.mkdir(parents=True)
    (builtin / "hw_params").write_text("access: RW_INTERLEAVED\nformat: S16_LE\n")
    usb = tmp_path / "card1"
    (usb / "pcm0p" / "sub0").mkdir(parents=True)
    (usb / "stream0").write_text("Topping E50 II\n")
    (usb / "pcm0p" / "sub0" / "hw_params").write_text(
        "access: RW_INTERLEAVED\nformat: DSD_U32_BE\nsubformat: STD\nchannels: 2\n"
        "rate: 88200 (88200/1)\nperiod_size: 2205\nbuffer_size: 8820\n"
    )
    monkeypatch.setattr(outputs_module, "ASOUND_DIR", tmp_path)

    assert read_alsa_hw_params() == {"format": "DSD_U32_BE", "rate": 88200, "channels": 2}

    (usb / "pcm0p" / "sub0" / "hw_params").write_text("closed\n")
    assert read_alsa_hw_params() is None
