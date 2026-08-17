"""API の疎通テスト。

MPD は起動していない前提（テスト用 config は空きポートを指す）なので、
ライブラリ系は 200、再生系は 503 を返すことを確認する。
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from kotone.config import Config
from kotone.db import Database, apply_migrations
from kotone.library.scanner import LibraryScanner
from kotone.main import create_app
from kotone.settings import SettingsStore
from kotone.text import KANA_INITIALS
from tests.test_scanner import LIBRARY, library  # noqa: F401 - fixture の再利用


@pytest.fixture
def client(library: Config) -> Iterator[TestClient]:  # noqa: F811
    db = Database(library.db_path)
    apply_migrations(db.conn)
    settings = SettingsStore(db)
    settings.set("rsgain_enabled", False)
    LibraryScanner(db, library, settings).scan()
    db.close()

    with TestClient(create_app(library)) as test_client:
        yield test_client


# ------------------------------------------------------------------ 参照系


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_library_stats(client: TestClient) -> None:
    payload = client.get("/api/library/stats").json()
    assert payload["library"]["tracks"] == len(LIBRARY)
    assert payload["library"]["albums"] == 3


def test_list_albums_with_sorts(client: TestClient) -> None:
    for sort in ("album", "artist", "year", "added", "genre"):
        response = client.get("/api/library/albums", params={"sort": sort})
        assert response.status_code == 200, sort
        assert response.json()["total"] == 3


def test_album_detail_includes_tracks(client: TestClient) -> None:
    albums = client.get("/api/library/albums", params={"sort": "album"}).json()["items"]
    first_love = next(a for a in albums if a["name"] == "First Love")

    album = client.get(f"/api/library/albums/{first_love['id']}").json()
    assert [t["title"] for t in album["tracks"]] == ["Automatic", "First Love"]


def test_artist_detail_is_direct_search_entry_point(client: TestClient) -> None:
    """アーティストを選ぶと全曲 + 収録アルバムが返る（要件 3.6）。"""
    artists = client.get("/api/library/artists").json()["items"]
    utada = next(a for a in artists if a["name"] == "宇多田ヒカル")

    detail = client.get(f"/api/library/artists/{utada['id']}").json()
    titles = {t["title"] for t in detail["tracks"]}
    assert titles == {"Automatic", "First Love", "Duet"}
    # 客演のみの Compilation もアルバム一覧に含まれる
    assert {a["name"] for a in detail["albums"]} == {"First Love", "Compilation"}


def test_initials_endpoint(client: TestClient) -> None:
    payload = client.get("/api/library/initials/artists", params={"group": "kana"}).json()
    assert [item["initial"] for item in payload["items"]] == KANA_INITIALS


def test_search_matches_name_and_reading(client: TestClient) -> None:
    payload = client.get("/api/library/search", params={"q": "First"}).json()
    assert any(a["name"] == "First Love" for a in payload["albums"])
    assert any(t["title"] == "First Love" for t in payload["tracks"])


def test_formats_endpoint_lists_only_present_formats(client: TestClient) -> None:
    """絞り込みの選択肢は、実際にライブラリへ入っているものだけを返す。"""
    payload = client.get("/api/library/formats").json()
    codecs = {c["codec"] for c in payload["codecs"]}
    assert codecs == {"flac"}
    # デモ音源は 44.1kHz/16bit のみなので CD 品質だけが出る
    assert {q["key"] for q in payload["qualities"]} == {"cd"}


def test_filter_albums_by_codec(client: TestClient) -> None:
    assert client.get("/api/library/albums", params={"codec": "flac"}).json()["total"] == 3
    assert client.get("/api/library/albums", params={"codec": "mp3"}).json()["total"] == 0


def test_filter_tracks_by_quality(client: TestClient) -> None:
    cd = client.get("/api/library/tracks", params={"quality": "cd"}).json()
    assert cd["total"] == len(LIBRARY)
    hires = client.get("/api/library/tracks", params={"quality": "hires"}).json()
    assert hires["total"] == 0


def test_artist_albums_sorted_by_name(client: TestClient) -> None:
    """アーティスト詳細のアルバムはアルバム名順（リリース年順ではない）。"""
    artists = client.get("/api/library/artists").json()["items"]
    utada = next(a for a in artists if a["name"] == "宇多田ヒカル")
    detail = client.get(f"/api/library/artists/{utada['id']}").json()
    names = [a["name"] for a in detail["albums"]]
    keys = {a["name"]: a["sort_key"] for a in detail["albums"]}
    assert names == sorted(names, key=lambda n: keys[n])


def test_genres_and_years(client: TestClient) -> None:
    genres = client.get("/api/library/genres").json()["items"]
    assert {g["name"] for g in genres} == {"J-Pop", "Alternative"}

    years = client.get("/api/library/years").json()["items"]
    assert {y["year"] for y in years} == {1999, 1997, 2005}


# ------------------------------------------------------------------ 編集系


def test_edit_track_reading_locks_it(client: TestClient) -> None:
    tracks = client.get("/api/library/tracks", params={"q": "Automatic"}).json()["items"]
    track_id = tracks[0]["id"]

    response = client.patch(
        f"/api/edit/tracks/{track_id}", json={"reading": "オートマチック"}
    )
    assert response.status_code == 200
    assert response.json()["reading"] == "オートマチック"
    assert response.json()["reading_locked"] == 1


def test_edit_track_artists(client: TestClient) -> None:
    tracks = client.get("/api/library/tracks", params={"q": "Duet"}).json()["items"]
    track_id = tracks[0]["id"]

    response = client.patch(
        f"/api/edit/tracks/{track_id}", json={"artist_names": ["椎名林檎"]}
    )
    assert response.status_code == 200
    assert [a["name"] for a in response.json()["artists"]] == ["椎名林檎"]
    assert response.json()["artists_locked"] == 1


def test_reading_suggest(client: TestClient) -> None:
    response = client.post("/api/edit/reading/suggest", json={"text": "Radiohead"})
    assert response.status_code == 200
    assert response.json()["reading"] == "Radiohead"


# ---------------------------------------------------------------- 設定・状態


def test_settings_roundtrip(client: TestClient) -> None:
    payload = client.get("/api/settings").json()
    assert payload["values"]["gapless_enabled"] is True
    assert any(spec["key"] == "startup_warmup_ms" for spec in payload["specs"])

    updated = client.patch(
        "/api/settings", json={"values": {"gapless_enabled": False, "startup_warmup_ms": 800}}
    )
    assert updated.status_code == 200
    assert updated.json()["values"]["startup_warmup_ms"] == 800


def test_settings_rejects_unknown_key(client: TestClient) -> None:
    response = client.patch("/api/settings", json={"values": {"存在しない": 1}})
    assert response.status_code == 400


def test_settings_clamps_out_of_range(client: TestClient) -> None:
    response = client.patch("/api/settings", json={"values": {"startup_warmup_ms": 99999}})
    assert response.json()["values"]["startup_warmup_ms"] == 3000


def test_crossfade_settings_are_gone(client: TestClient) -> None:
    """クロスフェードは提供しない（理由は kotone.settings の冒頭）。"""
    payload = client.get("/api/settings").json()
    assert "crossfade_enabled" not in payload["values"]
    assert not any(spec["key"].startswith("crossfade") for spec in payload["specs"])

    # 古いクライアントが送ってきても弾く（黙って無視しない）
    response = client.patch("/api/settings", json={"values": {"crossfade_enabled": True}})
    assert response.status_code == 400


def test_analyze_artists_endpoint(client: TestClient) -> None:
    payload = client.get("/api/scan/analyze-artists").json()
    assert "candidates" in payload
    assert payload["current"] == [";", " / ", "／", "、"]


def test_system_info(client: TestClient) -> None:
    payload = client.get("/api/system").json()
    assert payload["mpd"]["connected"] is False
    assert payload["schema_version"] >= 1


# ------------------------------------------------------------ 再生系（MPD 無し）


def test_player_state_reports_disconnected(client: TestClient) -> None:
    payload = client.get("/api/player").json()
    assert payload["connected"] is False


def test_player_controls_return_503_without_mpd(client: TestClient) -> None:
    for path in ("/api/player/play", "/api/player/toggle", "/api/player/next"):
        assert client.post(path).status_code == 503

    response = client.post("/api/player/scope", json={"kind": "all"})
    assert response.status_code == 503


# -------------------------------------------------------------- プレイリスト


def test_playlist_crud_via_api(client: TestClient) -> None:
    tracks = client.get("/api/library/tracks").json()["items"]
    track_ids = [t["id"] for t in tracks[:2]]

    created = client.post(
        "/api/playlists", json={"name": "テスト", "track_ids": track_ids}
    )
    assert created.status_code == 201
    playlist_id = created.json()["id"]
    assert created.json()["track_count"] == 2

    moved = client.post(
        f"/api/playlists/{playlist_id}/move", json={"from_position": 0, "to_position": 1}
    )
    assert [t["id"] for t in moved.json()["tracks"]] == list(reversed(track_ids))

    exported = client.get(f"/api/playlists/{playlist_id}/export")
    assert exported.status_code == 200
    assert exported.text.startswith("#EXTM3U")

    assert client.delete(f"/api/playlists/{playlist_id}").status_code == 204
    assert client.get(f"/api/playlists/{playlist_id}").status_code == 404


def test_playlist_add_album(client: TestClient) -> None:
    albums = client.get("/api/library/albums").json()["items"]
    album = next(a for a in albums if a["name"] == "First Love")

    playlist_id = client.post("/api/playlists", json={"name": "アルバム追加"}).json()["id"]
    response = client.post(
        f"/api/playlists/{playlist_id}/tracks", json={"album_id": album["id"]}
    )
    assert response.json()["track_count"] == 2


# ------------------------------------------------------------------ その他


def test_artwork_404_when_missing(client: TestClient) -> None:
    albums = client.get("/api/library/albums").json()["items"]
    assert client.get(f"/api/artwork/{albums[0]['id']}").status_code == 404


def test_openapi_is_available(client: TestClient) -> None:
    assert client.get("/api/openapi.json").status_code == 200
