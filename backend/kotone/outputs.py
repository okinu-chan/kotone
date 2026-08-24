"""再生の出力先（USB DAC / Bluetooth）の切り替え。

MPD は有効になっている出力すべてへ同時に音を流す。KOTONE では「いま聴く
場所」を 1 つ選ぶ形にしたいので、1 つだけ有効にして残りを無効にする。

出力の定義自体は ``mpd.conf`` に置いてある。Bluetooth の出力は bluealsa の
``bluealsa`` PCM（＝最後に接続した BT 機器）を指しているので、機器を替えても
設定を書き換える必要はない（:mod:`kotone.bluetooth` の冒頭を参照）。
"""

from __future__ import annotations

import logging
from typing import Any

from kotone.mpdclient import MPDConnection

log = logging.getLogger(__name__)

#: mpd.conf に書いてある Bluetooth 出力の名前
BLUETOOTH_OUTPUT_NAME = "Bluetooth"


class OutputController:
    def __init__(self, mpd: MPDConnection) -> None:
        self.mpd = mpd

    async def list(self) -> list[dict[str, Any]]:
        outputs = await self.mpd.command("outputs")
        return [
            {
                "id": int(item.get("outputid", 0)),
                "name": item.get("outputname", ""),
                "enabled": item.get("outputenabled") == "1",
                "bluetooth": item.get("outputname") == BLUETOOTH_OUTPUT_NAME,
            }
            for item in outputs or []
        ]

    async def select(self, output_id: int) -> list[dict[str, Any]]:
        """指定した出力だけを有効にする。

        先に有効化してから他を無効にする。逆にすると、切り替えの一瞬だけ
        出力がゼロになり、MPD が再生を止めてしまうことがある。
        """
        outputs = await self.list()
        if not any(item["id"] == output_id for item in outputs):
            raise ValueError(f"出力が見つかりません: {output_id}")

        await self.mpd.command("enableoutput", output_id)
        for item in outputs:
            if item["id"] != output_id and item["enabled"]:
                await self.mpd.command("disableoutput", item["id"])
        return await self.list()

    async def active(self) -> dict[str, Any] | None:
        for item in await self.list():
            if item["enabled"]:
                return item
        return None
