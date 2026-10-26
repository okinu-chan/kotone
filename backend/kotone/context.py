"""アプリケーション全体で共有するオブジェクトの束。

FastAPI の ``app.state.ctx`` に置き、依存性注入で各ルーターから取り出す。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kotone.bluetooth import BluetoothManager
from kotone.config import Config
from kotone.db import Database, apply_migrations
from kotone.editing import EditingService
from kotone.events import EventBus
from kotone.library import LibraryScanner, ScanResult
from kotone.mpdclient import MPDConnection
from kotone.outputs import OutputController
from kotone.playback import PlaybackController
from kotone.playlists import PlaylistService
from kotone.repository import LibraryRepository
from kotone.settings import SettingsStore
from kotone.stats import StatsStore
from kotone.storage import StorageMonitor
from kotone.text import get_reading_generator

log = logging.getLogger(__name__)


class ScanManager:
    """ライブラリスキャンをバックグラウンドスレッドで 1 本だけ走らせる。"""

    def __init__(self, ctx: AppContext) -> None:
        self._ctx = ctx
        self._task: asyncio.Task[ScanResult] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, *, full: bool = False) -> None:
        if self.running:
            raise RuntimeError("すでにスキャンが実行中です")
        # ドライブが読めない間にスキャンすると、全曲が「消えた」ことになって
        # ライブラリから削除されてしまう。読めないなら始めない
        await self._ctx.storage.ensure_available()
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(self._run(full=full), name="library-scan")

    async def start_loudness(self, *, remeasure: bool = False) -> None:
        """ラウドネス計測だけをバックグラウンドで走らせる。"""
        if self.running:
            raise RuntimeError("すでにスキャンが実行中です")
        await self._ctx.storage.ensure_available()
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.create_task(
            self._run_loudness(remeasure=remeasure), name="loudness-scan"
        )

    async def _run_loudness(self, *, remeasure: bool) -> ScanResult:
        ctx = self._ctx
        ctx.events.publish("scan", {"status": "running", "phase": "ラウドネスを計測中",
                                    "processed": 0, "total": 0})
        try:
            scanner = LibraryScanner(
                db=ctx.db,
                config=ctx.config,
                settings=ctx.settings,
                progress=self._publish_progress,
            )
            result = await asyncio.to_thread(scanner.measure_loudness, remeasure=remeasure)
        except Exception as exc:
            log.exception("ラウドネス計測が失敗しました")
            ctx.events.publish("scan", {"status": "error", "error": str(exc)})
            raise
        ctx.events.publish("scan", {"status": "idle", **result.as_dict()})
        return result

    async def _run(self, *, full: bool) -> ScanResult:
        ctx = self._ctx
        ctx.events.publish("scan", {"status": "running", "phase": "開始", "processed": 0,
                                    "total": 0})
        try:
            # MPD 側の DB も更新しておく（add がパスを解決できるようにするため）
            if ctx.mpd.connected:
                try:
                    await ctx.mpd.update_database()
                    await ctx.mpd.wait_for_update()
                except Exception as exc:
                    log.warning("MPD のデータベース更新に失敗しました: %s", exc)

            scanner = LibraryScanner(
                # スキャンは専用スレッドで動くため、DB もそのスレッドの接続を使う
                db=ctx.db,
                config=ctx.config,
                settings=ctx.settings,
                progress=self._publish_progress,
            )
            result = await asyncio.to_thread(scanner.scan, full=full)
        except Exception as exc:
            log.exception("ライブラリスキャンが失敗しました")
            ctx.events.publish("scan", {"status": "error", "error": str(exc)})
            raise
        ctx.events.publish("scan", {"status": "idle", **result.as_dict()})
        # 再生中の曲のアートワークが差し替わっていれば、再生画面にも新しいハッシュを届ける
        try:
            await ctx.player.broadcast_state()
        except Exception as exc:
            log.warning("スキャン後の再生状態の通知に失敗しました: %s", exc)
        return result

    def _publish_progress(self, phase: str, processed: int, total: int) -> None:
        """スキャンスレッドから安全にイベントを流す。"""
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            self._ctx.events.publish,
            "scan",
            {"status": "running", "phase": phase, "processed": processed, "total": total},
        )

    async def result(self) -> ScanResult | None:
        if self._task is None:
            return None
        return await self._task

    def state(self) -> dict[str, Any]:
        state = dict(self._ctx.repo.scan_state())
        state["running"] = self.running
        return state


class AppContext:
    def __init__(self, config: Config) -> None:
        self.config = config
        config.ensure_dirs()

        self.db = Database(config.db_path)
        if config.auto_migrate:
            applied = apply_migrations(self.db.conn)
            if applied:
                log.info("マイグレーションを %d 件適用しました", len(applied))

        self.settings = SettingsStore(self.db)
        self.stats = StatsStore(self.db)
        self.repo = LibraryRepository(self.db)
        self.playlists = PlaylistService(self.db, self.settings)
        self.editing = EditingService(self.db, self.settings)
        self.events = EventBus()
        self.storage = StorageMonitor(config.music_dir, self.events)
        self.mpd = MPDConnection(
            host=config.mpd_host, port=config.mpd_port, password=config.mpd_password
        )
        # 再生中画面に「どこへ出しているか」「何のコーデックか」を出すため、
        # 出力と Bluetooth は再生コントローラより先に作って渡す
        self.outputs = OutputController(self.mpd)
        self.bluetooth = BluetoothManager()
        self.player = PlaybackController(
            mpd=self.mpd, db=self.db, repo=self.repo,
            settings=self.settings, events=self.events,
            outputs=self.outputs, bluetooth=self.bluetooth,
        )
        self.scan = ScanManager(self)

    @property
    def reading_available(self) -> bool:
        """形態素解析器（fugashi + unidic-lite）が使えるか。"""
        return get_reading_generator().available

    async def startup(self) -> None:
        # スキャンより先に見張りを始める（読めないドライブでスキャンしないため）
        await self.storage.start()
        await self.mpd.start()
        await self.player.start()
        if self.settings.get("scan_on_start"):
            try:
                await self.scan.start()
            except RuntimeError as exc:
                log.warning("起動時のスキャンを見送りました: %s", exc)

    async def shutdown(self) -> None:
        await self.storage.stop()
        await self.mpd.close()
        self.db.close()
