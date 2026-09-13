"""音楽用ドライブの見張り。

2026-09-13、Pi の USB ポートが過電流で給電を止め、音楽用 SSD が一瞬外れた。
SSD はすぐ別の名前（sda → sdb）で戻ったが、``/mnt/music`` は外れた古い
デバイスに付いたままで、どの曲を開いても読み込みエラーになった。KOTONE
からは「再生が始まらない」としか見えず、原因の手がかりが無かった。

ここではドライブの状態を定期的に確かめ、読めなくなったら画面へ知らせる。
見るのは次の 3 つ。

* マウント元のデバイスが消えていないか（今回はこれ。sda1 が消えていた）
* ファイルシステムが緊急停止していないか（ext4 の ``shutdown``）
* 実際に音楽ディレクトリを読めるか

復旧そのものは root の権限が要るので、別のサービス
（``scripts/kotone-storage-recover``）が受け持つ。その進み具合は
:data:`RECOVERY_STATUS_FILE` から読み取って一緒に知らせる。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kotone.events import EventBus

log = logging.getLogger(__name__)

#: 見張りの間隔（秒）
POLL_SECONDS = 10.0
#: 読み取りを試す時間の上限（秒）。応答の無いデバイスを待ち続けない
PROBE_TIMEOUT_SECONDS = 3.0
MOUNTINFO = Path("/proc/self/mountinfo")
#: 復旧サービスが書く状態ファイル（root が書き、KOTONE は読むだけ）
RECOVERY_STATUS_FILE = Path("/run/kotone-storage/status.json")
#: 復旧サービスの記録を「いまの話」として扱う長さ（秒）
RECOVERY_FRESH_SECONDS = 15 * 60

MESSAGES = {
    "missing": "音楽用ドライブが見つかりません。接続を確認してください。",
    "device_missing": "音楽用ドライブが外れました。",
    "fs_shutdown": "音楽用ドライブで読み書きのエラーが起き、使えなくなっています。",
    "unreadable": "音楽用ドライブを読み込めません。",
    "unresponsive": "音楽用ドライブが応答しません。",
}


class StorageUnavailable(RuntimeError):
    """音楽用ドライブが読めないので、再生やスキャンを受け付けない。"""


# ------------------------------------------------------------ マウント情報


@dataclass(frozen=True)
class MountEntry:
    mountpoint: str
    source: str
    fstype: str
    options: frozenset[str]


def _unescape(value: str) -> str:
    """mountinfo の 8 進エスケープ（空白は ``\\040`` など）を戻す。"""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def parse_mountinfo(text: str) -> list[MountEntry]:
    """``/proc/self/mountinfo`` を読む。

    1 行は ``ID 親ID 番号 root マウント先 オプション [任意項目...] - 種類 元 FSオプション``。
    ext4 の ``shutdown`` は末尾の FS オプション側に出るので、両方を合わせて持つ。
    """
    entries: list[MountEntry] = []
    for line in text.splitlines():
        left, separator, right = line.partition(" - ")
        if not separator:
            continue
        fields = left.split()
        tail = right.split()
        if len(fields) < 6 or len(tail) < 2:
            continue
        options = set(fields[5].split(","))
        if len(tail) >= 3:
            options |= set(tail[2].split(","))
        entries.append(
            MountEntry(
                mountpoint=_unescape(fields[4]),
                source=_unescape(tail[1]),
                fstype=tail[0],
                options=frozenset(options),
            )
        )
    return entries


def mount_for(path: Path, entries: list[MountEntry]) -> MountEntry | None:
    """``path`` を含むマウントのうち、いちばん深いもの。

    同じ場所に重ねてマウントされていれば、後から付いた（下の行の）ほうが見える。
    """
    target = Path(path).as_posix()
    best: MountEntry | None = None
    for entry in entries:
        prefix = entry.mountpoint.rstrip("/") + "/"
        if target == entry.mountpoint or target.startswith(prefix):
            if best is None or len(entry.mountpoint) >= len(best.mountpoint):
                best = entry
    return best


# -------------------------------------------------------------- 状態

@dataclass
class StorageStatus:
    available: bool
    reason: str | None = None
    message: str | None = None
    mountpoint: str | None = None
    device: str | None = None
    #: 復旧サービスの最新の記録（新しいものだけ。無ければ None）
    recovery: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "reason": self.reason,
            "message": self.message,
            "mountpoint": self.mountpoint,
            "device": self.device,
            "recovery": self.recovery,
        }


def _down(reason: str, entry: MountEntry | None = None) -> StorageStatus:
    return StorageStatus(
        available=False,
        reason=reason,
        message=MESSAGES[reason],
        mountpoint=entry.mountpoint if entry else None,
        device=entry.source if entry else None,
    )


def _has_device_node(entry: MountEntry) -> bool:
    """デバイスの存在を確かめられるマウントか。

    ルートは ``/dev/root`` のような実在しない名前で出ることがあるので確かめない。
    """
    return (
        entry.mountpoint != "/"
        and entry.source.startswith("/dev/")
        and entry.source != "/dev/root"
    )


def inspect(
    music_dir: Path,
    *,
    mountinfo_text: str | None = None,
    exists: Callable[[str], bool] = os.path.exists,
) -> StorageStatus:
    """音楽ディレクトリが使えるかを確かめる（同期。スレッドで呼ぶ）。"""
    if mountinfo_text is None:
        try:
            mountinfo_text = MOUNTINFO.read_text()
        except OSError:
            mountinfo_text = ""
    entry = mount_for(music_dir, parse_mountinfo(mountinfo_text))

    if entry is not None:
        # マウント先がキャッシュで読めてしまっても、元のデバイスが消えていれば
        # 中身はもう読めない（今回の障害はこの形だった）
        if _has_device_node(entry) and not exists(entry.source):
            return _down("device_missing", entry)
        if "shutdown" in entry.options:
            return _down("fs_shutdown", entry)

    try:
        with os.scandir(music_dir) as it:
            next(it, None)
    except FileNotFoundError:
        return _down("missing", entry)
    except OSError:
        return _down("unreadable", entry)

    return StorageStatus(
        available=True,
        mountpoint=entry.mountpoint if entry else None,
        device=entry.source if entry else None,
    )


def read_recovery(
    path: Path = RECOVERY_STATUS_FILE, *, now: Callable[[], float] = time.time
) -> dict[str, Any] | None:
    """復旧サービスの記録を読む。古いもの・壊れたものは無いものとして扱う。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    at = data.get("at")
    if not isinstance(at, (int, float)) or now() - at > RECOVERY_FRESH_SECONDS:
        return None
    return {"state": data.get("state"), "message": data.get("message"), "at": at}


# -------------------------------------------------------------- 見張り


class StorageMonitor:
    """音楽用ドライブの状態を見張り、変わったら ``storage`` イベントで知らせる。"""

    def __init__(
        self,
        music_dir: Path,
        events: EventBus,
        *,
        interval: float = POLL_SECONDS,
        probe_timeout: float = PROBE_TIMEOUT_SECONDS,
        recovery_file: Path = RECOVERY_STATUS_FILE,
    ) -> None:
        self.music_dir = music_dir
        self.events = events
        self.interval = interval
        self.probe_timeout = probe_timeout
        self.recovery_file = recovery_file
        #: 確かめる前は使えるものとして扱う（起動直後に再生を止めない）
        self.status = StorageStatus(available=True)
        self._published = False
        self._task: asyncio.Task[None] | None = None
        #: 応答の無いデバイスを確かめているスレッド。終わるまで重ねて投げない
        self._inflight: asyncio.Future[StorageStatus] | None = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        await self.check()
        self._task = asyncio.create_task(self._run(), name="storage-monitor")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.check()
            except Exception:
                log.exception("音楽用ドライブの確認でエラーが発生しました")

    async def check(self) -> StorageStatus:
        """いまの状態を確かめ直す（再生やスキャンの直前にも呼ぶ）。"""
        async with self._lock:
            status = await self._inspect()
            status.recovery = await asyncio.to_thread(read_recovery, self.recovery_file)
            self._update(status)
            return status

    async def ensure_available(self) -> None:
        """読めなければ :class:`StorageUnavailable` を投げる。"""
        status = await self.check()
        if not status.available:
            raise StorageUnavailable(status.message or MESSAGES["unreadable"])

    async def _inspect(self) -> StorageStatus:
        if self._inflight is not None and not self._inflight.done():
            return _down("unresponsive")
        loop = asyncio.get_running_loop()
        self._inflight = loop.run_in_executor(None, inspect, self.music_dir)
        try:
            # 待ちきれなくても、確かめているスレッド自体は止めない（止められない）
            return await asyncio.wait_for(asyncio.shield(self._inflight), self.probe_timeout)
        except TimeoutError:
            return _down("unresponsive")

    def _update(self, status: StorageStatus) -> None:
        previous = self.status
        self.status = status
        key = (status.available, status.reason, (status.recovery or {}).get("state"))
        old_key = (previous.available, previous.reason, (previous.recovery or {}).get("state"))
        if self._published and key == old_key:
            return
        self._published = True
        if not status.available:
            log.warning("音楽用ドライブが使えません: %s (%s)", status.reason, status.device)
        elif not previous.available:
            log.info("音楽用ドライブが読めるようになりました (%s)", status.device)
        self.events.publish("storage", status.as_dict())
