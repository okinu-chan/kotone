"""マイグレーションのテスト。

既存の DB を持つ利用者が更新したときに、再スキャンなしで正しい状態へ
移行できることを確かめる。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kotone.db import Database
from kotone.db.migrations import MIGRATIONS_DIR, apply_migrations, current_version, discover
from kotone.text import KANA_INITIALS


def test_versions_are_sequential() -> None:
    versions = [m.version for m in discover()]
    assert versions == sorted(versions)
    assert versions == list(range(1, len(versions) + 1))


def _apply_up_to(conn: sqlite3.Connection, version: int, tmp_path: Path) -> None:
    """指定バージョンまでだけ適用する（更新前の DB を再現するため）。"""
    staging = tmp_path / "staging"
    staging.mkdir(exist_ok=True)
    for migration in discover():
        if migration.version > version:
            continue
        (staging / migration.path.name).write_text(migration.sql, encoding="utf-8")
    apply_migrations(conn, staging)


def test_0003_repacks_kana_initials_without_rescan(tmp_path: Path) -> None:
    """カナの頭文字が行の代表から 1 文字ずつへ詰め替わること。

    更新前は「きようと」も「かんだ」も initial が「か」だった。五十音を
    並べるには 1 文字そのものが要るが、18,000 曲の再スキャンは避けたい。
    """
    db = Database(tmp_path / "library.sqlite3")
    _apply_up_to(db.conn, 2, tmp_path)

    # 更新前の形（行の代表）でデータを入れる
    rows = [
        ("京都", "きようと", "か"),
        ("神田", "かんだ", "か"),
        ("宇多田", "うただ", "あ"),
    ]
    for name, sort_key, initial in rows:
        db.execute(
            "INSERT INTO artists (name, reading, sort_key, initial, initial_group) "
            "VALUES (?, ?, ?, ?, 'kana')",
            (name, sort_key, sort_key, initial),
        )
    # ラテン文字の行は触られないこと（比較用）
    db.execute(
        "INSERT INTO artists (name, reading, sort_key, initial, initial_group) "
        "VALUES ('Radiohead', 'Radiohead', 'radiohead', 'R', 'latin')"
    )
    db.commit()

    apply_migrations(db.conn, MIGRATIONS_DIR)

    got = {
        r["name"]: r["initial"]
        for r in db.query("SELECT name, initial FROM artists")
    }
    assert got == {"京都": "き", "神田": "か", "宇多田": "う", "Radiohead": "R"}
    assert all(got[name] in KANA_INITIALS for name in ("京都", "神田", "宇多田"))
    db.close()


@pytest.mark.parametrize("table", ["artists", "genres", "albums", "tracks", "playlists"])
def test_0003_covers_every_table_with_initials(table: str) -> None:
    """頭文字を持つテーブルを取りこぼしていないこと。

    1 つでも漏れるとそのタブだけ旧形式のまま残り、押しても 0 件になる。
    """
    sql = (MIGRATIONS_DIR / "0003_kana_initials.sql").read_text(encoding="utf-8")
    assert f"UPDATE {table}" in sql


def test_migrations_are_idempotent_once_applied(db: Database) -> None:
    """適用済みの DB にもう一度かけても何も走らないこと。"""
    version = current_version(db.conn)
    assert apply_migrations(db.conn) == []
    assert current_version(db.conn) == version
