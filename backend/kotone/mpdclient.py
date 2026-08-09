"""MPD への非同期接続ラッパー。

python-mpd2 の asyncio クライアントを使い、以下を担当する。

* 接続の確立と、切断時の指数バックオフによる再接続
* ``idle`` によるサーバー側イベントの購読とコールバック配信
* よく使う操作の型付きラッパー（GPT プロトタイプのラッパーロジックを踏襲）

MPD が落ちている間もバックエンド自体は起動し続け、API は 503 を返す。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

from mpd.asyncio import MPDClient

log = logging.getLogger(__name__)

EventCallback = Callable[[set[str]], Awaitable[None]]

#: 購読する MPD サブシステム
IDLE_SUBSYSTEMS = ("player", "mixer", "options", "playlist", "update", "database")


class MPDUnavailable(RuntimeError):
    """MPD へ接続できていない。"""


class MPDConnection:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 6600,
        password: str | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.password = password
        self._client = MPDClient()
        self._connected = asyncio.Event()
        self._callbacks: list[EventCallback] = []
        self._task: asyncio.Task[None] | None = None
        self._closing = False
        self._lock = asyncio.Lock()

    # --- ライフサイクル -------------------------------------------------
    async def start(self) -> None:
        self._closing = False
        self._task = asyncio.create_task(self._run(), name="mpd-idle")

    async def close(self) -> None:
        """接続を閉じる。

        （MPD の ``stop`` コマンドは :meth:`stop` なので、名前が衝突しないよう
        ライフサイクル側を ``close`` にしている。）
        """
        self._closing = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._disconnect()

    def _disconnect(self) -> None:
        self._connected.clear()
        try:
            self._client.disconnect()
        except Exception:  # pragma: no cover - 切断済みなら何もしない
            pass

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def wait_connected(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except TimeoutError:
            return False

    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    # --- 接続 + idle ループ ---------------------------------------------
    async def _run(self) -> None:
        backoff = 1.0
        while not self._closing:
            try:
                await self._connect()
                backoff = 1.0
                log.info("MPD に接続しました (%s:%s)", self.host, self.port)
                await self._emit({"connection", "player", "mixer", "options", "playlist"})
                async for subsystems in self._client.idle(IDLE_SUBSYSTEMS):
                    if self._closing:
                        break
                    await self._emit(set(subsystems))
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._closing:
                    break
                log.warning("MPD 接続が切れました (%s)。%.0f 秒後に再接続します。", exc, backoff)
            self._disconnect()
            if self._closing:
                break
            await self._emit({"connection"})
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)

    async def _connect(self) -> None:
        await self._client.connect(self.host, self.port)
        if self.password:
            await self._client.password(self.password)
        self._connected.set()

    async def _emit(self, subsystems: set[str]) -> None:
        for callback in list(self._callbacks):
            try:
                await callback(subsystems)
            except Exception:
                log.exception("MPD イベントハンドラでエラーが発生しました")

    # --- コマンド -------------------------------------------------------
    async def command(self, name: str, *args: Any) -> Any:
        """任意の MPD コマンドを実行する。"""
        if not self.connected:
            raise MPDUnavailable("MPD に接続されていません")
        method = getattr(self._client, name, None)
        if method is None:
            raise AttributeError(f"未知の MPD コマンドです: {name}")
        try:
            return await method(*args)
        except Exception as exc:
            if _is_connection_error(exc):
                self._disconnect()
                raise MPDUnavailable(f"MPD との通信に失敗しました: {exc}") from exc
            raise

    # --- 状態取得 -------------------------------------------------------
    async def status(self) -> dict[str, Any]:
        return await self.command("status")

    async def current_song(self) -> dict[str, Any]:
        return await self.command("currentsong")

    async def queue(self) -> list[dict[str, Any]]:
        return await self.command("playlistinfo")

    async def stats(self) -> dict[str, Any]:
        return await self.command("stats")

    # --- 再生操作 -------------------------------------------------------
    async def play(self, position: int | None = None) -> None:
        if position is None:
            await self.command("play")
        else:
            await self.command("play", position)

    async def play_id(self, song_id: int) -> None:
        await self.command("playid", song_id)

    async def pause(self, paused: bool | None = None) -> None:
        if paused is None:
            await self.command("pause")
        else:
            await self.command("pause", 1 if paused else 0)

    async def stop(self) -> None:
        await self.command("stop")

    async def next(self) -> None:
        await self.command("next")

    async def previous(self) -> None:
        await self.command("previous")

    async def seek(self, seconds: float) -> None:
        await self.command("seekcur", f"{seconds:.3f}")

    async def set_volume(self, volume: int) -> None:
        await self.command("setvol", max(0, min(100, int(volume))))

    # --- キュー操作 -----------------------------------------------------
    async def clear(self) -> None:
        await self.command("clear")

    async def add_id(self, uri: str, position: int | None = None) -> int:
        if position is None:
            result = await self.command("addid", uri)
        else:
            result = await self.command("addid", uri, position)
        return int(result)

    async def add_many(self, uris: Iterable[str]) -> list[int]:
        """複数曲をコマンドリストでまとめて追加する（大量投入時の往復を減らす）。"""
        uris = list(uris)
        if not uris:
            return []
        ids: list[int] = []
        # python-mpd2 の asyncio クライアントは command_list をサポートしないため、
        # 逐次投入する。数千曲でもローカル接続なら十分高速。
        for uri in uris:
            try:
                ids.append(await self.add_id(uri))
            except MPDUnavailable:
                raise
            except Exception as exc:
                log.warning("キューに追加できませんでした: %s (%s)", uri, exc)
        return ids

    async def delete_id(self, song_id: int) -> None:
        await self.command("deleteid", song_id)

    async def move_id(self, song_id: int, to: int) -> None:
        await self.command("moveid", song_id, to)

    # --- オプション -----------------------------------------------------
    async def set_repeat(self, enabled: bool) -> None:
        await self.command("repeat", 1 if enabled else 0)

    async def set_single(self, enabled: bool) -> None:
        await self.command("single", 1 if enabled else 0)

    async def set_random(self, enabled: bool) -> None:
        await self.command("random", 1 if enabled else 0)

    async def set_consume(self, enabled: bool) -> None:
        await self.command("consume", 1 if enabled else 0)

    async def set_crossfade(self, seconds: int) -> None:
        await self.command("crossfade", max(0, int(seconds)))

    # --- データベース ---------------------------------------------------
    async def update_database(self, uri: str | None = None) -> int:
        """MPD 側のデータベース更新を開始し、job id を返す。

        こちらのタグ DB とは別に、MPD は ``add`` の解決に自前の DB を使うため
        ライブラリスキャンの前に必ず更新しておく必要がある。
        """
        result = await self.command("update", uri) if uri else await self.command("update")
        return int(result)

    async def wait_for_update(self, timeout: float = 3600.0) -> None:
        """進行中の MPD データベース更新が終わるまで待つ。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            status = await self.status()
            if "updating_db" not in status:
                return
            await asyncio.sleep(0.5)
        raise TimeoutError("MPD のデータベース更新がタイムアウトしました")


def _is_connection_error(exc: Exception) -> bool:
    return isinstance(exc, (ConnectionError, OSError, EOFError)) or "not connected" in str(
        exc
    ).lower()
