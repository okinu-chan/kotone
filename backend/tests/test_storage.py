"""音楽用ドライブの見張りのテスト。

2026-09-13 の障害（USB の過電流で SSD が外れ、sda → sdb で戻ったのに
/mnt/music は消えた sda1 に付いたまま）を、そのままの mountinfo で再現する。
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest

from kotone import storage
from kotone.events import EventBus
from kotone.storage import (
    StorageMonitor,
    StorageUnavailable,
    inspect,
    mount_for,
    parse_mountinfo,
    read_recovery,
)

ROOT_LINE = "25 1 179:2 / / rw,noatime shared:1 - ext4 /dev/mmcblk0p2 rw"
#: 障害のときの実機の行（ホスト側）
STALE_LINE = "59 25 8:1 / /mnt/music rw,noatime shared:132 - ext4 /dev/sda1 rw"
HEALTHY_LINE = "59 25 8:17 / /mnt/music rw,noatime shared:132 - ext4 /dev/sdb1 rw"


def _mountinfo(*lines: str) -> str:
    return "\n".join(lines) + "\n"


def _only(*present: str):
    return lambda path: path in present


def _line(mountpoint: Path, source: str, fs_options: str = "rw") -> str:
    """``mountpoint`` に ``source`` が付いている mountinfo の 1 行。"""
    return f"59 25 8:17 / {mountpoint.as_posix()} rw,noatime - ext4 {source} {fs_options}"


# ------------------------------------------------------------ mountinfo


def test_parse_mountinfo_reads_source_and_both_option_lists() -> None:
    line = "59 25 8:17 / /mnt/music rw,noatime shared:1 - ext4 /dev/sdb1 rw,shutdown"
    [entry] = parse_mountinfo(line + "\n")
    assert entry.mountpoint == "/mnt/music"
    assert entry.source == "/dev/sdb1"
    assert entry.fstype == "ext4"
    # ext4 の shutdown は末尾の FS オプション側に出る
    assert {"noatime", "shutdown"} <= entry.options


def test_parse_mountinfo_unescapes_spaces() -> None:
    [entry] = parse_mountinfo(r"60 25 8:17 / /mnt/my\040music rw - ext4 /dev/sdb1 rw" + "\n")
    assert entry.mountpoint == "/mnt/my music"


def test_mount_for_picks_the_deepest_mount() -> None:
    entries = parse_mountinfo(_mountinfo(ROOT_LINE, HEALTHY_LINE))
    assert mount_for(Path("/mnt/music/Music"), entries).mountpoint == "/mnt/music"
    assert mount_for(Path("/var/lib/kotone/music"), entries).mountpoint == "/"
    # 名前の前方一致だけで選ばない（/mnt/musicbox は /mnt/music の中ではない）
    assert mount_for(Path("/mnt/musicbox"), entries).mountpoint == "/"


def test_mount_for_prefers_the_later_overmount() -> None:
    entries = parse_mountinfo(_mountinfo(ROOT_LINE, STALE_LINE, HEALTHY_LINE))
    assert mount_for(Path("/mnt/music/Music"), entries).source == "/dev/sdb1"


# -------------------------------------------------------------- 状態の判定


def test_the_2026_09_13_incident_is_detected(tmp_path: Path) -> None:
    """マウント先はキャッシュで読めても、元のデバイスが消えていれば使えない。"""
    status = inspect(
        tmp_path,   # 中身は読めてしまう状況を作る
        mountinfo_text=_mountinfo(ROOT_LINE, _line(tmp_path, "/dev/sda1")),
        exists=_only("/dev/sdb1"),   # 戻ってきたのは sdb1。sda1 はもう無い
    )
    assert not status.available
    assert status.reason == "device_missing"
    assert status.device == "/dev/sda1"
    assert "外れました" in status.message


def test_filesystem_shutdown_is_detected(tmp_path: Path) -> None:
    mountinfo = _mountinfo(ROOT_LINE, _line(tmp_path, "/dev/sdb1", "rw,shutdown"))
    status = inspect(tmp_path, mountinfo_text=mountinfo, exists=_only("/dev/sdb1"))
    assert status.reason == "fs_shutdown"


def test_healthy_drive_is_available(tmp_path: Path) -> None:
    mountinfo = _mountinfo(ROOT_LINE, _line(tmp_path, "/dev/sdb1"))
    status = inspect(tmp_path, mountinfo_text=mountinfo, exists=_only("/dev/sdb1"))
    assert status.available
    assert status.device == "/dev/sdb1"


def test_music_on_the_root_filesystem_is_not_flagged(tmp_path: Path) -> None:
    """ルートは /dev/root のような実在しない名前で出ることがある。"""
    line = "25 1 179:2 / / rw - ext4 /dev/root rw"
    status = inspect(tmp_path, mountinfo_text=_mountinfo(line), exists=_only())
    assert status.available


def test_missing_directory(tmp_path: Path) -> None:
    status = inspect(tmp_path / "nope", mountinfo_text=_mountinfo(ROOT_LINE))
    assert status.reason == "missing"


def test_unreadable_directory(tmp_path: Path, monkeypatch) -> None:
    def broken(path):  # noqa: ANN001
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(storage.os, "scandir", broken)
    status = inspect(tmp_path, mountinfo_text=_mountinfo(ROOT_LINE))
    assert status.reason == "unreadable"


# ---------------------------------------------------------- 復旧サービスの記録


def test_read_recovery_returns_fresh_records(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"state": "recovering", "message": "…", "at": 1000}))
    assert read_recovery(path, now=lambda: 1060)["state"] == "recovering"


def test_read_recovery_ignores_stale_or_broken_records(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text(json.dumps({"state": "failed", "at": 1000}))
    assert read_recovery(path, now=lambda: 1000 + 16 * 60) is None
    path.write_text("{壊れている")
    assert read_recovery(path) is None
    assert read_recovery(tmp_path / "none.json") is None


# -------------------------------------------------------------------- 見張り


class _Recorder(EventBus):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[dict] = []

    def publish(self, event_type: str, payload: dict | None = None) -> None:
        self.messages.append({"type": event_type, "payload": payload})
        super().publish(event_type, payload)


def _monitor(tmp_path: Path, events: EventBus, **kwargs) -> StorageMonitor:
    return StorageMonitor(tmp_path, events, recovery_file=tmp_path / "none.json", **kwargs)


async def test_monitor_publishes_only_on_change(tmp_path: Path, monkeypatch) -> None:
    events = _Recorder()
    monitor = _monitor(tmp_path, events)
    states = iter([
        storage.StorageStatus(available=True),
        storage.StorageStatus(available=True),
        storage._down("device_missing"),
        storage._down("device_missing"),
        storage.StorageStatus(available=True),
    ])
    monkeypatch.setattr(storage, "inspect", lambda path: next(states))

    for _ in range(5):
        await monitor.check()

    published = [m["payload"]["available"] for m in events.messages if m["type"] == "storage"]
    # 最初の 1 回（接続中の画面に初期状態を渡す）と、変わった 2 回だけ
    assert published == [True, False, True]


async def test_ensure_available_raises_with_the_reason(tmp_path: Path, monkeypatch) -> None:
    monitor = _monitor(tmp_path, EventBus())
    monkeypatch.setattr(storage, "inspect", lambda path: storage._down("device_missing"))

    with pytest.raises(StorageUnavailable, match="外れました"):
        await monitor.ensure_available()


async def test_unresponsive_drive_does_not_block(tmp_path: Path, monkeypatch) -> None:
    """応答の無いデバイスを待ち続けず、確かめ中のものに重ねて投げない。"""
    calls = []

    def slow(path):  # noqa: ANN001
        calls.append(path)
        time.sleep(0.4)
        return storage.StorageStatus(available=True)

    monkeypatch.setattr(storage, "inspect", slow)
    monitor = _monitor(tmp_path, EventBus(), probe_timeout=0.05)

    first = await monitor.check()
    second = await monitor.check()

    assert first.reason == "unresponsive"
    assert second.reason == "unresponsive"
    assert len(calls) == 1          # 2 回目は投げていない
    await asyncio.sleep(0.5)        # 後片付け（スレッドの終了を待つ）


async def test_recovery_record_is_attached(tmp_path: Path, monkeypatch) -> None:
    record = tmp_path / "status.json"
    record.write_text(json.dumps({"state": "recovering", "message": "復旧中", "at": time.time()}))
    monkeypatch.setattr(storage, "inspect", lambda path: storage._down("device_missing"))
    monitor = StorageMonitor(tmp_path, EventBus(), recovery_file=record)

    status = await monitor.check()

    assert status.recovery["state"] == "recovering"
