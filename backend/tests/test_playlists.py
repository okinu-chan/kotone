"""プレイリスト管理のテスト（要件 3.4）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from kotone.db import Database
from kotone.playlists import PlaylistError, PlaylistService
from kotone.repository import LibraryRepository
from kotone.settings import SettingsStore


@pytest.fixture
def tracks(db: Database) -> list[int]:
    ids = []
    for index in range(1, 6):
        cursor = db.execute(
            "INSERT INTO tracks (path, title, duration) VALUES (?, ?, ?)",
            (f"album/{index:02d}.flac", f"曲 {index}", 100.0 + index),
        )
        ids.append(int(cursor.lastrowid))
    db.commit()
    return ids


@pytest.fixture
def service(db: Database, settings: SettingsStore) -> PlaylistService:
    return PlaylistService(db, settings)


def test_create_empty_and_add_tracks(service: PlaylistService, db: Database,
                                     tracks: list[int]) -> None:
    playlist_id = service.create("お気に入り")
    assert LibraryRepository(db).get_playlist(playlist_id)["track_count"] == 0

    service.add_tracks(playlist_id, tracks[:3])
    playlist = LibraryRepository(db).get_playlist(playlist_id)
    assert [t["id"] for t in playlist["tracks"]] == tracks[:3]
    assert [t["position"] for t in playlist["tracks"]] == [0, 1, 2]


def test_create_with_tracks(service: PlaylistService, db: Database,
                            tracks: list[int]) -> None:
    playlist_id = service.create("通勤用", tracks)
    assert LibraryRepository(db).get_playlist(playlist_id)["track_count"] == len(tracks)


def test_duplicate_name_is_rejected(service: PlaylistService) -> None:
    service.create("同じ名前")
    with pytest.raises(PlaylistError):
        service.create("同じ名前")


def test_insert_at_position(service: PlaylistService, db: Database,
                            tracks: list[int]) -> None:
    playlist_id = service.create("挿入テスト", tracks[:3])
    service.add_tracks(playlist_id, [tracks[4]], position=1)
    playlist = LibraryRepository(db).get_playlist(playlist_id)
    assert [t["id"] for t in playlist["tracks"]] == [
        tracks[0], tracks[4], tracks[1], tracks[2]
    ]


def test_remove_and_positions_are_renumbered(service: PlaylistService, db: Database,
                                             tracks: list[int]) -> None:
    playlist_id = service.create("削除テスト", tracks)
    service.remove_at(playlist_id, [0, 2])
    playlist = LibraryRepository(db).get_playlist(playlist_id)
    assert [t["id"] for t in playlist["tracks"]] == [tracks[1], tracks[3], tracks[4]]
    assert [t["position"] for t in playlist["tracks"]] == [0, 1, 2]


def test_move(service: PlaylistService, db: Database, tracks: list[int]) -> None:
    playlist_id = service.create("並び替え", tracks[:4])
    service.move(playlist_id, 0, 2)
    playlist = LibraryRepository(db).get_playlist(playlist_id)
    assert [t["id"] for t in playlist["tracks"]] == [
        tracks[1], tracks[2], tracks[0], tracks[3]
    ]


def test_reorder(service: PlaylistService, db: Database, tracks: list[int]) -> None:
    playlist_id = service.create("一括並び替え", tracks[:3])
    service.reorder(playlist_id, [tracks[2], tracks[0], tracks[1]])
    playlist = LibraryRepository(db).get_playlist(playlist_id)
    assert [t["id"] for t in playlist["tracks"]] == [tracks[2], tracks[0], tracks[1]]


def test_delete_removes_entries(service: PlaylistService, db: Database,
                                tracks: list[int]) -> None:
    playlist_id = service.create("消す", tracks)
    service.delete(playlist_id)
    assert LibraryRepository(db).get_playlist(playlist_id) is None
    assert db.scalar("SELECT COUNT(*) FROM playlist_tracks") == 0


def test_export_m3u(service: PlaylistService, tracks: list[int], tmp_path: Path) -> None:
    playlist_id = service.create("書き出し", tracks[:2])
    content = service.export_m3u(playlist_id, tmp_path)

    lines = content.splitlines()
    assert lines[0] == "#EXTM3U"
    assert lines[1] == "#PLAYLIST:書き出し"
    assert "album/01.flac" in lines
    assert any(line.startswith("#EXTINF:101") for line in lines)


def test_import_m3u_roundtrip(service: PlaylistService, db: Database,
                              tracks: list[int], tmp_path: Path) -> None:
    source_id = service.create("元", tracks[:3])
    exported = service.export_m3u(source_id, tmp_path)

    result = service.import_m3u("取り込み", exported, tmp_path)
    assert result["imported"] == 3
    assert result["unresolved_count"] == 0
    imported = LibraryRepository(db).get_playlist(result["playlist_id"])
    assert [t["id"] for t in imported["tracks"]] == tracks[:3]


def test_import_reports_unresolved_paths(service: PlaylistService,
                                         tmp_path: Path) -> None:
    content = "#EXTM3U\nalbum/01.flac\nどこにもない/曲.flac\n"
    result = service.import_m3u("一部だけ", content, tmp_path)
    assert result["imported"] == 0  # tracks フィクスチャを使っていないので 0 件
    assert result["unresolved_count"] == 2


def test_import_avoids_name_collision(service: PlaylistService, tmp_path: Path) -> None:
    service.create("重複名")
    result = service.import_m3u("重複名", "#EXTM3U\n", tmp_path)
    from kotone.repository import LibraryRepository as Repo

    playlist = Repo(service.db).get_playlist(result["playlist_id"])
    assert playlist["name"] == "重複名 (2)"


# ------------------------------------------------- プレイリスト自体の並び順


def test_new_playlists_go_to_the_end(service: PlaylistService, db: Database) -> None:
    """作った順に末尾へ積まれること（読み順に割り込まない）。"""
    service.create("さくら")
    service.create("あさひ")
    service.create("ゆうひ")

    names = [p["name"] for p in LibraryRepository(db).list_playlists()]
    assert names == ["さくら", "あさひ", "ゆうひ"]


def test_reorder_playlists(service: PlaylistService, db: Database) -> None:
    first = service.create("A")
    second = service.create("B")
    third = service.create("C")

    service.reorder_playlists([third, first, second])

    assert [p["name"] for p in LibraryRepository(db).list_playlists()] == ["C", "A", "B"]


def test_reorder_keeps_unlisted_playlists(service: PlaylistService, db: Database) -> None:
    """画面に出ていなかったプレイリストが消えないこと。

    別の端末で増えた分を渡し忘れても、末尾に残るだけで落ちない。
    """
    first = service.create("A")
    second = service.create("B")
    service.create("C")

    service.reorder_playlists([second, first])

    assert [p["name"] for p in LibraryRepository(db).list_playlists()] == ["B", "A", "C"]


def test_reorder_ignores_unknown_ids(service: PlaylistService, db: Database) -> None:
    first = service.create("A")
    service.reorder_playlists([9999, first])
    assert [p["name"] for p in LibraryRepository(db).list_playlists()] == ["A"]
