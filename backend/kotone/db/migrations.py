"""バージョン管理された前方向マイグレーション（要件 5 章）。

``kotone/db/migrations/NNNN_name.sql`` を番号順に適用し、適用済みバージョンを
``schema_version`` テーブルに記録する。アップデート時は未適用分だけが走る。
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME_RE = re.compile(r"^(\d{4})_([A-Za-z0-9_\-]+)\.sql$")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")


def discover(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        m = _NAME_RE.match(path.name)
        if not m:
            log.warning("マイグレーションの命名規則に合わないファイルを無視します: %s", path.name)
            continue
        migrations.append(Migration(version=int(m.group(1)), name=m.group(2), path=path))

    versions = [m.version for m in migrations]
    if len(set(versions)) != len(versions):
        raise RuntimeError(f"マイグレーション番号が重複しています: {versions}")
    return migrations


def _ensure_version_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()


def current_version(conn: sqlite3.Connection) -> int:
    _ensure_version_table(conn)
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(row["v"]) if row and row["v"] is not None else 0


def pending(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    version = current_version(conn)
    return [m for m in discover(directory) if m.version > version]


def apply_migrations(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """未適用のマイグレーションを順に適用し、適用したものを返す。"""
    todo = pending(conn, directory)
    applied: list[Migration] = []
    for migration in todo:
        log.info("マイグレーション適用: %04d_%s", migration.version, migration.name)
        try:
            # 外部キー制約はテーブル再構築を伴う移行を壊すことがあるため一時的に外す
            conn.execute("PRAGMA foreign_keys = OFF")
            conn.executescript(migration.sql)
            conn.execute(
                "INSERT INTO schema_version (version, name) VALUES (?, ?)",
                (migration.version, migration.name),
            )
            conn.commit()
        except Exception:
            conn.rollback()
            log.error(
                "マイグレーション %04d_%s の適用に失敗しました",
                migration.version, migration.name,
            )
            raise
        finally:
            conn.execute("PRAGMA foreign_keys = ON")
        applied.append(migration)
    return applied
