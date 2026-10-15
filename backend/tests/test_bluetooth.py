"""Bluetooth 機器の「音を出せるか」の判定。

値は Pi の ``bluetoothctl info`` で実際に見えたもの（2026-10-15）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kotone.bluetooth import A2DP_SINK_UUID, _to_device


@dataclass
class Variant:
    """dbus_fast の Variant の代わり（Windows の開発環境には dbus_fast が無い）。"""

    signature: str
    value: Any


def _props(**kwargs) -> dict:
    return {"Address": Variant("s", "BC:3E:0B:35:5C:61"), **kwargs}


def test_audio_by_a2dp_sink_uuid() -> None:
    """A2DP Sink を広告している機器（JBL Tour Pro 3 など）。"""
    device = _to_device(_props(
        Class=Variant("u", 0x00244404),
        UUIDs=Variant("as", [A2DP_SINK_UUID]),
    ))
    assert device.audio


def test_audio_by_class_without_uuids() -> None:
    """探索の応答に UUID を載せない機器（Technics EAH-AZ100）。種別で判定する。"""
    device = _to_device(_props(
        Name=Variant("s", "Technics EAH-AZ100"),
        Class=Variant("u", 0x00240404),
    ))
    assert device.audio
    assert not device.paired


def test_not_audio_without_class_or_uuid() -> None:
    """種別も A2DP Sink も無い機器（BLE だけのタグ・充電器など）は隠す。"""
    device = _to_device(_props(
        UUIDs=Variant("as", ["0000ff09-0000-1000-8000-00805f9b34fb"]),
    ))
    assert not device.audio


def test_not_audio_for_other_major_class() -> None:
    """大分類が Audio/Video 以外（ここでは Phone）なら音を出せる機器とみなさない。"""
    device = _to_device(_props(Class=Variant("u", 0x005A020C)))
    assert not device.audio
