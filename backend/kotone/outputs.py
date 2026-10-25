"""再生の出力先（USB DAC / Bluetooth）の切り替え。

MPD は有効になっている出力すべてへ同時に音を流す。KOTONE では「いま聴く
場所」を 1 つ選ぶ形にしたいので、1 つだけ有効にして残りを無効にする。

出力の定義自体は ``mpd.conf`` に置いてある。Bluetooth の出力は bluealsa の
``bluealsa`` PCM（＝最後に接続した BT 機器）を指しているので、機器を替えても
設定を書き換える必要はない（:mod:`kotone.bluetooth` の冒頭を参照）。

DSD の出力方式
--------------
USB DAC には、同じ機器を指す出力が 2 つある。

* ``USB DAC`` … ビットパーフェクト。DSD はネイティブか DoP（出力属性 ``dop``）
  で送る。MPD のソフトウェア音量は DSD に効かない（素通しになる）。
* ``USB DAC (PCM)`` … ``dsd2pcm`` フィルタ（FFmpeg の ``anull``）を通す。
  フィルタの手前で MPD が DSD を PCM に変換するので、その後ろの音量が効く。

出力側の形式指定（``format`` / ``allowed_formats``）で PCM にしても、変換は
音量より後ろで行われるため、音量は効かないままになる（2026-10-25、MPD 0.24.4
で実測）。そのため出力ごと分けてある。画面には 1 つの「USB DAC」として見せ、
設定「DSD の出力方式」に合わせて裏でどちらかを有効にする。

同じ機器なので、2 つを同時に開くことはできない。再生中に切り替えると、新しい
ほうが開けずに一時停止する（実測）。切り替えは一時停止中か停止中に行う
（:meth:`kotone.playback.PlaybackController.apply_dsd_output`）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kotone.mpdclient import MPDConnection

log = logging.getLogger(__name__)

#: mpd.conf に書いてある Bluetooth 出力の名前
BLUETOOTH_OUTPUT_NAME = "Bluetooth"

#: mpd.conf に書いてある USB DAC 出力の名前（ビットパーフェクト）
USB_OUTPUT_NAME = "USB DAC"

#: DSD を PCM に変換して送る出力の名前。USB DAC と同じ機器を指す
USB_PCM_OUTPUT_NAME = "USB DAC (PCM)"

#: 設定「DSD の出力方式」の値
DSD_OUTPUT_MODES: tuple[str, ...] = ("pcm", "native", "dop")


class OutputController:
    def __init__(self, mpd: MPDConnection) -> None:
        self.mpd = mpd

    async def _raw(self) -> list[dict[str, Any]]:
        """MPD の出力を、隠している USB DAC (PCM) も含めてそのまま返す。"""
        outputs = await self.mpd.command("outputs")
        return [
            {
                "id": int(item.get("outputid", 0)),
                "name": item.get("outputname", ""),
                "enabled": item.get("outputenabled") == "1",
                "bluetooth": item.get("outputname") == BLUETOOTH_OUTPUT_NAME,
                "attributes": _attributes(item.get("attribute")),
            }
            for item in outputs or []
        ]

    async def list(self) -> list[dict[str, Any]]:
        """画面に出す出力の一覧。

        USB DAC (PCM) は USB DAC に畳み込む。どちらかが有効なら「USB DAC が
        有効」と見せ、``dsd_pcm`` でどちらが鳴っているかを添える。
        """
        raw = await self._raw()
        variant = next((item for item in raw if item["name"] == USB_PCM_OUTPUT_NAME), None)
        result = []
        for item in raw:
            if item is variant:
                continue
            entry = {key: item[key] for key in ("id", "name", "enabled", "bluetooth")}
            if item["name"] == USB_OUTPUT_NAME:
                entry["dsd_pcm"] = bool(variant and variant["enabled"])
                entry["enabled"] = item["enabled"] or entry["dsd_pcm"]
                entry["dop"] = item["attributes"].get("dop") == "1"
            result.append(entry)
        return result

    async def select(self, output_id: int, *, dsd_mode: str = "native") -> list[dict[str, Any]]:
        """指定した出力だけを有効にする。

        先に有効化してから他を無効にする。逆にすると、切り替えの一瞬だけ
        出力がゼロになり、MPD が再生を止めてしまうことがある。

        USB DAC を選んだときは、``dsd_mode`` に合わせて 2 つのうちどちらを
        有効にするか決める。
        """
        raw = await self._raw()
        target = next((item for item in raw if item["id"] == output_id), None)
        if target is None or target["name"] == USB_PCM_OUTPUT_NAME:
            raise ValueError(f"出力が見つかりません: {output_id}")
        if target["name"] == USB_OUTPUT_NAME:
            target = _usb_physical(raw, dsd_mode) or target
            # USB DAC どうしは同じ機器なので、開いているほうを先に閉じる
            for item in raw:
                if item["enabled"] and item["id"] != target["id"] and _is_usb(item):
                    await self.mpd.command("disableoutput", item["id"])
                    item["enabled"] = False

        await self.mpd.command("enableoutput", target["id"])
        for item in raw:
            if item["id"] != target["id"] and item["enabled"]:
                await self.mpd.command("disableoutput", item["id"])
        return await self.list()

    async def active(self) -> dict[str, Any] | None:
        for item in await self.list():
            if item["enabled"]:
                return item
        return None

    async def pcm_available(self) -> bool:
        """mpd.conf に USB DAC (PCM) があるか（無ければ PCM 変換は使えない）。"""
        return any(item["name"] == USB_PCM_OUTPUT_NAME for item in await self._raw())

    async def apply_dsd_mode(self, mode: str) -> bool:
        """USB DAC 側の出力を「DSD の出力方式」に合わせる。

        * ``dop`` 属性を合わせる（native なら 0、dop なら 1）。出力を開き直した
          ときから効く（実測）。
        * USB DAC が出力先になっていれば、USB DAC と USB DAC (PCM) のうち
          方式に合うほうへ切り替える。同じ機器なので、先に今のほうを閉じる。

        出力を切り替えたときだけ True を返す。再生中に切り替えると開けずに
        止まるので、呼ぶ側で一時停止しておくこと。
        """
        raw = await self._raw()
        usb = next((item for item in raw if item["name"] == USB_OUTPUT_NAME), None)
        if usb is None:
            return False
        want_dop = "1" if mode == "dop" else "0"
        if usb["attributes"].get("dop") != want_dop:
            await self.mpd.set_output_attribute(usb["id"], "dop", want_dop)

        variant = next((item for item in raw if item["name"] == USB_PCM_OUTPUT_NAME), None)
        if variant is None:
            if mode == "pcm":
                log.warning("mpd.conf に「%s」が無いため、DSD を PCM に変換できません",
                            USB_PCM_OUTPUT_NAME)
            return False
        current = usb if usb["enabled"] else variant if variant["enabled"] else None
        if current is None:
            return False   # Bluetooth で聴いている。USB DAC へ戻すときに select が合わせる
        target = _usb_physical(raw, mode)
        if target is None or target["id"] == current["id"]:
            return False
        await self.mpd.command("disableoutput", current["id"])
        await self.mpd.command("enableoutput", target["id"])
        log.info("DSD の出力方式に合わせて「%s」へ切り替えました", target["name"])
        return True

    async def dsd_mode_needs_swap(self, mode: str) -> bool:
        """:meth:`apply_dsd_mode` が USB DAC の 2 出力を入れ替えることになるか。"""
        raw = await self._raw()
        current = next((item for item in raw if item["enabled"] and _is_usb(item)), None)
        if current is None:
            return False
        target = _usb_physical(raw, mode)
        return target is not None and target["id"] != current["id"]

    async def pin_format(self, output_id: int, audio_format: str) -> None:
        """出力へ送る形式を固定する（例 ``48000:*:2``。MPD が変換して合わせる）。

        MPD の ``allowed_formats`` を使う。開き直したときから効く。
        """
        await self.mpd.set_output_attribute(output_id, "allowed_formats", audio_format)


def _usb_physical(raw: list[dict[str, Any]], mode: str) -> dict[str, Any] | None:
    """DSD の出力方式に合う USB DAC 側の出力（PCM 変換なら USB DAC (PCM)）。"""
    name = USB_PCM_OUTPUT_NAME if mode == "pcm" else USB_OUTPUT_NAME
    found = next((item for item in raw if item["name"] == name), None)
    if found is None and mode == "pcm":
        # USB DAC (PCM) の無い mpd.conf では、ビットパーフェクトのほうで鳴らす
        found = next((item for item in raw if item["name"] == USB_OUTPUT_NAME), None)
    return found


#: ALSA の状態を読む場所（試験で差し替える）
ASOUND_DIR = Path("/proc/asound")


def read_alsa_hw_params() -> dict[str, Any] | None:
    """いま開いている USB オーディオ機器の hw_params（format / rate / channels）。

    MPD の status の ``audio`` は MPD が扱っている形式で、DoP やネイティブ
    DSD のように ALSA へ渡す段で形が変わるものは分からない。DAC へ実際に
    送っている形式は、カーネルが ``/proc/asound/cardN/pcmMp/subS/hw_params``
    に出す。USB オーディオ（``stream0`` がある）カードだけを見る。

    機器が閉じていれば（停止・一時停止中）``closed`` と書かれていて None を返す。
    """
    try:
        cards = sorted(ASOUND_DIR.glob("card[0-9]*"))
    except OSError:
        return None
    for card in cards:
        if not (card / "stream0").exists():
            continue
        for path in sorted(card.glob("pcm*p/sub*/hw_params")):
            try:
                params = _parse_hw_params(path.read_text(encoding="ascii", errors="replace"))
            except OSError:
                continue
            if params:
                return params
    return None


def _parse_hw_params(text: str) -> dict[str, Any] | None:
    values: dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            values[key.strip()] = value.strip()
    if "format" not in values:
        return None
    rate = values.get("rate", "").split(" ", 1)[0]
    channels = values.get("channels", "")
    return {
        "format": values["format"],
        "rate": int(rate) if rate.isdigit() else None,
        "channels": int(channels) if channels.isdigit() else None,
    }


def _is_usb(item: dict[str, Any]) -> bool:
    return item["name"] in (USB_OUTPUT_NAME, USB_PCM_OUTPUT_NAME)


def _attributes(value: Any) -> dict[str, str]:
    """``outputs`` の ``attribute: dop=0`` を辞書にする（1 つなら文字列で来る）。"""
    if value is None:
        return {}
    items = value if isinstance(value, list) else [value]
    result = {}
    for item in items:
        key, _, val = str(item).partition("=")
        result[key] = val
    return result
