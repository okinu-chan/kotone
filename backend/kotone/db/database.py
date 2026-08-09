"""SQLite 接続の管理。

スレッドごとに接続を持つ（``sqlite3`` の接続はスレッド間で共有できないため）。
FastAPI の同期エンドポイントはスレッドプールで実行されるので、この方式で
イベントループをブロックせずに読み書きできる。
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # SSD/USB メディア上での読み取り性能を優先（要件 4.2 / 4.6）
    conn.execute("PRAGMA temp_store = MEMORY")
    conn.execute("PRAGMA cache_size = -32000")  # 約 32MB
    conn.execute("PRAGMA busy_timeout = 5000")


def connect(path: Path | str) -> sqlite3.Connection:
    """単発の接続を開く（CLI・マイグレーション・テスト用）。"""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    _configure(conn)
    return conn


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


class Database:
    """スレッドローカルな接続プール。"""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._local = threading.local()
        self._all: list[sqlite3.Connection] = []
        self._lock = threading.Lock()
        # インメモリ DB はスレッド間で共有できないため、テスト用に単一接続を使う
        self._shared: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._shared = connect(self.path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = connect(self.path)
            self._local.conn = conn
            with self._lock:
                self._all.append(conn)
        return conn

    # --- クエリヘルパ ---------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        return self.conn.executemany(sql, [tuple(p) for p in seq])

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in self.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        return row_to_dict(self.execute(sql, params).fetchone())

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.execute(sql, params).fetchone()
        return row[0] if row is not None else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self.conn
        try:
            yield conn
        except Exception:
            conn.rollback()
            raise
        else:
            conn.commit()

    def commit(self) -> None:
        self.conn.commit()

    def close(self) -> None:
        with self._lock:
            for conn in self._all:
                try:
                    conn.close()
                except sqlite3.Error:
                    pass
            self._all.clear()
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        self._local = threading.local()
