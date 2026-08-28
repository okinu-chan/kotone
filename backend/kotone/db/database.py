"""SQLite 接続の管理。

スレッドごとに接続を持つ（``sqlite3`` の接続はスレッド間で共有できないため）。
FastAPI の同期エンドポイントはスレッドプールで実行されるので、この方式で
イベントループをブロックせずに読み書きできる。

スレッドは使い回されるとは限らない。FastAPI（anyio）のワーカースレッドは
一定時間使われないと終了し、次の要求では別のスレッドが立つ。終わった
スレッドの接続を閉じずに持ち続けると、ファイルディスクリプタが開いたまま
積み上がる。実機で 500 本以上たまり、``Too many open files`` でアートワークの
配信も画面の読み込みも落ちた。そのためスレッドの終了に合わせて必ず閉じる。
"""

from __future__ import annotations

import sqlite3
import threading
import weakref
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


class _ThreadConnection:
    """スレッドローカルに置く接続の器。

    ``threading.local`` に入れた値は、そのスレッドが終わると解放される。
    接続そのものを入れて別の場所から参照し続けると、解放されないまま
    ファイルディスクリプタだけが残る。器を挟んで、解放されたその場で閉じる。
    """

    __slots__ = ("conn", "__weakref__")

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:      # インタプリタ終了中は sqlite3 以外も飛びうる
            pass

    def __del__(self) -> None:
        self.close()


class Database:
    """スレッドローカルな接続プール。"""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self._local = threading.local()
        # 弱参照で持つ。強く持つとスレッドが終わっても接続が生き続ける
        self._all: list[weakref.ref[_ThreadConnection]] = []
        self._lock = threading.Lock()
        # インメモリ DB はスレッド間で共有できないため、テスト用に単一接続を使う
        self._shared: sqlite3.Connection | None = None
        if self.path == ":memory:":
            self._shared = connect(self.path)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._shared is not None:
            return self._shared
        holder = getattr(self._local, "holder", None)
        if holder is None:
            holder = _ThreadConnection(connect(self.path))
            self._local.holder = holder
            with self._lock:
                # 終わったスレッドのぶんを落としてから足す（一覧を伸ばさない）
                self._all = [ref for ref in self._all if ref() is not None]
                self._all.append(weakref.ref(holder))
        return holder.conn

    @property
    def live_connections(self) -> int:
        """開いたままの接続の本数（漏れの監視用）。"""
        if self._shared is not None:
            return 1
        with self._lock:
            return sum(1 for ref in self._all if ref() is not None)

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
            for ref in self._all:
                holder = ref()
                if holder is not None:
                    holder.close()
            self._all.clear()
        if self._shared is not None:
            self._shared.close()
            self._shared = None
        self._local = threading.local()
