"""WebSocket による状態 push。

接続直後に現在の再生状態とスキャン状態を 1 度送り、以降は差分イベントを流す。
クライアントは ``{"type": "player" | "scan" | "settings", "payload": {...}}``
という形のメッセージを受け取る。
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from kotone.api.deps import get_ctx

log = logging.getLogger(__name__)

router = APIRouter(tags=["ws"])

#: この間隔で ping を送り、切断を検知する
PING_INTERVAL = 25.0


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    ctx = get_ctx(websocket)  # type: ignore[arg-type]
    await websocket.accept()

    try:
        async with ctx.events.subscribe() as queue:
            # 初期状態を送る
            try:
                await websocket.send_json(
                    {"type": "player", "payload": await ctx.player.build_state()}
                )
            except Exception:  # pragma: no cover - MPD 未接続時
                await websocket.send_json({"type": "player", "payload": {"connected": False}})
            await websocket.send_json({"type": "scan", "payload": ctx.scan.state()})

            receiver = asyncio.create_task(_drain(websocket))
            try:
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL)
                    except TimeoutError:
                        await websocket.send_json({"type": "ping", "payload": {}})
                        continue
                    await websocket.send_json(message)
            finally:
                receiver.cancel()
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("WebSocket が切断されました", exc_info=True)
    finally:
        try:
            await websocket.close()
        except Exception:  # pragma: no cover
            pass


async def _drain(websocket: WebSocket) -> None:
    """クライアントからのメッセージを読み捨てる（切断検知のため）。"""
    try:
        while True:
            await websocket.receive_text()
    except Exception:
        return
