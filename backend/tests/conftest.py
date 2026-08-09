from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kotone.config import Config, set_config  # noqa: E402
from kotone.db import Database, apply_migrations  # noqa: E402
from kotone.settings import SettingsStore  # noqa: E402


@pytest.fixture
def config(tmp_path: Path) -> Config:
    music = tmp_path / "music"
    music.mkdir()
    config = Config(
        data_dir=tmp_path / "data",
        music_dir=music,
        static_dir=tmp_path / "static",
        mpd_host="127.0.0.1",
        mpd_port=16600,  # 何も動いていないポート（MPD 未接続時の挙動を確認する）
        log_level="WARNING",
    )
    config.ensure_dirs()
    set_config(config)
    yield config
    set_config(Config())


@pytest.fixture
def db(config: Config) -> Database:
    database = Database(config.db_path)
    apply_migrations(database.conn)
    yield database
    database.close()


@pytest.fixture
def settings(db: Database) -> SettingsStore:
    return SettingsStore(db)
