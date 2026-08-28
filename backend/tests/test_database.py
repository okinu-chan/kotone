"""SQLite 接続の後始末のテスト。

実機で ``Too many open files`` が起きた回帰防止。FastAPI（anyio）の
ワーカースレッドは一定時間使われないと終了し、次の要求では別のスレッドが
立つ。終わったスレッドの接続を持ち続けると、ファイルディスクリプタだけが
積み上がっていく（実機で 500 本以上）。
"""

from __future__ import annotations

import gc
import sqlite3
import threading

import pytest

from kotone.db import Database


@pytest.fixture
def file_db(tmp_path):
    # スレッドごとの接続を試すので、テスト用の共有接続ではなくファイルを使う
    db = Database(tmp_path / "t.sqlite3")
    db.execute("CREATE TABLE t (v INTEGER)")
    db.commit()
    yield db
    db.close()


def test_connection_is_reused_within_a_thread(file_db) -> None:
    first = file_db.conn
    assert file_db.conn is first
    assert file_db.live_connections == 1


def test_thread_connections_are_closed_when_the_thread_ends(file_db) -> None:
    """終わったスレッドの接続を残さないこと（fd の積み上がり防止）。"""
    for _ in range(20):
        thread = threading.Thread(target=lambda: file_db.query("SELECT * FROM t"))
        thread.start()
        thread.join()
    gc.collect()

    # 呼び出し元スレッドのぶんだけが残る
    assert file_db.live_connections == 1


def test_dead_thread_connection_is_really_closed(tmp_path) -> None:
    """参照が残っていても、スレッドの終了時に閉じられていること。"""
    db = Database(tmp_path / "t.sqlite3")
    captured: list[sqlite3.Connection] = []

    thread = threading.Thread(target=lambda: captured.append(db.conn))
    thread.start()
    thread.join()
    gc.collect()

    with pytest.raises(sqlite3.ProgrammingError):
        captured[0].execute("SELECT 1")
    db.close()


def test_close_shuts_down_the_current_thread_connection(tmp_path) -> None:
    db = Database(tmp_path / "t.sqlite3")
    conn = db.conn
    db.close()

    assert db.live_connections == 0
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
