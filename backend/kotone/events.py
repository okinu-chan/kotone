"""WebSocket 配信用の簡易イベントバス。

再生状態やスキャン進捗の変化をバックエンド内から publish し、接続中の
ブラウザへ push する。購読者ごとに上限つきキューを持ち、詰まった購読者が
全体を止めないようにしている（遅い購読者は古いイベントを落とす）。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

log = logging.getLogger(__name__)

MAX_QUEUE = 64


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._last: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=MAX_QUEUE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        message = {"type": event_type, "payload": payload or {}}
        self._last[event_type] = message
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # 遅い購読者の最古のイベントを捨てて最新を入れる
                try:
                    queue.get_nowait()
                    queue.put_nowait(message)
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                    log.debug("イベントを配信できませんでした: %s", event_type)

    def snapshot(self) -> list[dict[str, Any]]:
        """接続直後の購読者へ渡す、各種類の最新イベント。"""
        return list(self._last.values())

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
