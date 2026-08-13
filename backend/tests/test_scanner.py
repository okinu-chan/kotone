"""ライブラリスキャンのテスト。

実際の音源を用意する代わりに、``read_tags`` を差し替えて合成タグを流し込む。
DB への取り込み・読み仮名生成・アーティスト分割・再スキャン時の挙動を確認する。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kotone.config import Config
from kotone.db import Database
from kotone.library import scanner as scanner_module
from kotone.library.scanner import LibraryScanner
from kotone.library.tags import TrackTags
from kotone.repository import LibraryRepository
from kotone.settings import SettingsStore
from kotone.text import KANA_INITIALS

# path -> タグ定義
LIBRARY: dict[str, dict] = {
    "宇多田ヒカル/First Love/01 Automatic.flac": {
        "title": "Automatic",
        "artists": ["宇多田ヒカル"],
        "album_artists": ["宇多田ヒカル"],
        "album": "First Love",
        "genres": ["J-Pop"],
        "date": "1999",
        "track_no": 1,
    },
    "宇多田ヒカル/First Love/02 First Love.flac": {
        "title": "First Love",
        "artists": ["宇多田ヒカル"],
        "album_artists": ["宇多田ヒカル"],
        "album": "First Love",
        "genres": ["J-Pop"],
        "date": "1999",
        "track_no": 2,
    },
    "Radiohead/OK Computer/01 Airbag.flac": {
        "title": "Airbag",
        "artists": ["Radiohead"],
        "album_artists": ["Radiohead"],
        "album": "OK Computer",
        "genres": ["Alternative"],
        "date": "1997",
        "track_no": 1,
    },
    "Various Artists/Compilation/01 Duet.flac": {
        "title": "Duet",
        # 単値タグに複数名が並記されているケース（要件 3.6）
        "artists": ["椎名林檎; 宇多田ヒカル"],
        "album_artists": ["Various Artists"],
        "album": "Compilation",
        "genres": ["J-Pop"],
        "date": "2005",
        "track_no": 1,
    },
}


@pytest.fixture
def library(config: Config, monkeypatch: pytest.MonkeyPatch) -> Config:
    for rel_path in LIBRARY:
        path = config.music_dir / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 128)

    def fake_read_tags(path: Path) -> TrackTags | None:
        rel = path.relative_to(config.music_dir).as_posix()
        spec = LIBRARY.get(rel)
        if spec is None:
            return None
        tags = TrackTags(path=path, **spec)
        tags.year = int(spec["date"])
        tags.duration = 240.0
        tags.codec = "flac"
        tags.samplerate = 44100
        tags.bit_depth = 16
        tags.channels = 2
        return tags

    monkeypatch.setattr(scanner_module, "read_tags", fake_read_tags)
    return config


@pytest.fixture
def scan(library: Config, db: Database, settings: SettingsStore) -> LibraryScanner:
    settings.set("rsgain_enabled", False)
    return LibraryScanner(db, library, settings)


def test_scan_imports_tracks_albums_artists(scan: LibraryScanner, db: Database) -> None:
    result = scan.scan()

    assert result.added == len(LIBRARY)
    assert result.updated == 0
    assert db.scalar("SELECT COUNT(*) FROM tracks") == len(LIBRARY)
    # First Love / OK Computer / Compilation
    assert db.scalar("SELECT COUNT(*) FROM albums") == 3
    assert db.scalar("SELECT COUNT(*) FROM genres") == 2


def test_multi_artist_field_is_split(scan: LibraryScanner, db: Database) -> None:
    scan.scan()
    names = {
        r["name"]
        for r in db.query(
            """
            SELECT a.name FROM artists a
            JOIN track_artists ta ON ta.artist_id = a.id
            JOIN tracks t ON t.id = ta.track_id
            WHERE t.title = 'Duet'
            """
        )
    }
    assert names == {"椎名林檎", "宇多田ヒカル"}


def test_readings_and_initials_are_generated(scan: LibraryScanner, db: Database) -> None:
    scan.scan()
    row = db.query_one("SELECT * FROM artists WHERE name = 'Radiohead'")
    assert row is not None
    assert row["sort_key"] == "radiohead"
    assert row["initial"] == "R"
    assert row["initial_group"] == "latin"


def test_rescan_is_incremental(scan: LibraryScanner) -> None:
    scan.scan()
    second = scan.scan()
    assert second.added == 0
    assert second.updated == 0
    assert second.unchanged == len(LIBRARY)


def test_full_rescan_rereads_everything(scan: LibraryScanner) -> None:
    scan.scan()
    second = scan.scan(full=True)
    assert second.updated == len(LIBRARY)
    assert second.unchanged == 0


def test_removed_files_are_deleted(scan: LibraryScanner, library: Config,
                                   db: Database) -> None:
    scan.scan()
    (library.music_dir / "Radiohead/OK Computer/01 Airbag.flac").unlink()

    result = scan.scan()
    assert result.removed == 1
    assert db.scalar("SELECT COUNT(*) FROM tracks") == len(LIBRARY) - 1
    # 曲が無くなったアルバム・アーティストは片付けられる
    assert db.query_one("SELECT id FROM albums WHERE name = 'OK Computer'") is None
    assert db.query_one("SELECT id FROM artists WHERE name = 'Radiohead'") is None


def test_manual_reading_is_preserved_on_rescan(scan: LibraryScanner, db: Database) -> None:
    scan.scan()
    track_id = db.scalar("SELECT id FROM tracks WHERE title = 'Automatic'")
    db.execute(
        "UPDATE tracks SET reading = 'オートマチック', reading_locked = 1 WHERE id = ?",
        (track_id,),
    )
    db.commit()

    scan.scan(full=True)
    row = db.query_one("SELECT reading, reading_locked FROM tracks WHERE id = ?", (track_id,))
    assert row["reading"] == "オートマチック"
    assert row["reading_locked"] == 1


def test_manual_artist_links_are_preserved_on_rescan(scan: LibraryScanner,
                                                     db: Database) -> None:
    scan.scan()
    track_id = db.scalar("SELECT id FROM tracks WHERE title = 'Duet'")
    artist_id = db.scalar("SELECT id FROM artists WHERE name = '椎名林檎'")
    db.execute("DELETE FROM track_artists WHERE track_id = ?", (track_id,))
    db.execute(
        "INSERT INTO track_artists (track_id, artist_id, role, position) "
        "VALUES (?, ?, 'artist', 0)",
        (track_id, artist_id),
    )
    db.execute("UPDATE tracks SET artists_locked = 1 WHERE id = ?", (track_id,))
    db.commit()

    scan.scan(full=True)
    names = [
        r["name"]
        for r in db.query(
            "SELECT a.name FROM track_artists ta JOIN artists a ON a.id = ta.artist_id "
            "WHERE ta.track_id = ?",
            (track_id,),
        )
    ]
    assert names == ["椎名林檎"]


def test_repository_sorting_and_scope(scan: LibraryScanner, db: Database) -> None:
    scan.scan()
    repo = LibraryRepository(db)

    albums = repo.list_albums(sort="album")
    assert [a["name"] for a in albums] == sorted(
        [a["name"] for a in albums], key=lambda n: [a["sort_key"] for a in albums
                                                    if a["name"] == n][0]
    )

    tracks = repo.resolve_scope("all", None)
    assert len(tracks) == len(LIBRARY)

    album_id = db.scalar("SELECT id FROM albums WHERE name = 'First Love'")
    album_tracks = repo.resolve_scope("album", str(album_id))
    assert [t["title"] for t in album_tracks] == ["Automatic", "First Love"]


def test_album_artist_inferred_when_tag_missing(
    library: Config, db: Database, settings: SettingsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ALBUMARTIST タグが無くても、収録曲から補完される（実機の BOØWY で発覚）。"""
    path = library.music_dir / "NoAlbumArtist/Album/01 Track.flac"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x00" * 128)

    def fake_read_tags(p: Path) -> TrackTags | None:
        if "NoAlbumArtist" not in p.parts:
            return None  # 既存のデモ音源は対象外にする
        tags = TrackTags(path=p, title="Track", album="タグ無しアルバム")
        tags.artists = ["BOØWY"]        # ARTIST のみ。ALBUMARTIST は無い
        tags.album_artists = []
        tags.duration = 100.0
        return tags

    monkeypatch.setattr(scanner_module, "read_tags", fake_read_tags)
    settings.set("rsgain_enabled", False)
    LibraryScanner(db, library, settings).scan()

    row = db.query_one(
        "SELECT a.name AS artist FROM albums al "
        "JOIN artists a ON a.id = al.album_artist_id WHERE al.name = ?",
        ("タグ無しアルバム",),
    )
    assert row is not None
    assert row["artist"] == "BOØWY"


def test_multi_artist_album_marked_as_compilation(
    library: Config, db: Database, settings: SettingsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """収録曲のアーティストが複数なら、誰か 1 人を代表にせずコンピ扱いにする。"""
    for index in (1, 2):
        path = library.music_dir / f"Various/Album/{index:02d}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 128)

    artists_by_name = {"01.flac": "歌手A", "02.flac": "歌手B"}

    def fake_read_tags(p: Path) -> TrackTags | None:
        if "Various" not in p.parts:
            return None  # 既存のデモ音源は対象外にする
        tags = TrackTags(path=p, title=p.stem, album="寄せ集め")
        tags.artists = [artists_by_name[p.name]]
        tags.album_artists = []
        tags.duration = 100.0
        return tags

    monkeypatch.setattr(scanner_module, "read_tags", fake_read_tags)
    settings.set("rsgain_enabled", False)
    LibraryScanner(db, library, settings).scan()

    row = db.query_one(
        "SELECT album_artist_id, is_compilation FROM albums WHERE name = ?", ("寄せ集め",)
    )
    assert row is not None
    assert row["album_artist_id"] is None
    assert row["is_compilation"] == 1


def test_album_not_split_by_inconsistent_albumartist_tag(
    library: Config, db: Database, settings: SettingsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同じフォルダ内で ALBUMARTIST の有無が混在してもアルバムは分裂しない。

    実ライブラリで 25 ディレクトリが 2 つに割れていた不具合の回帰テスト。
    """
    for index in range(1, 5):
        path = library.music_dir / f"Mixed/サントラ/{index:02d}.flac"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00" * 128)

    def fake_read_tags(p: Path) -> TrackTags | None:
        if "Mixed" not in p.parts:
            return None
        number = int(p.stem)
        tags = TrackTags(path=p, title=f"曲{number}", album="サウンドトラック")
        tags.artists = ["作曲家A"]
        # 前半の曲だけ ALBUMARTIST を持つ（実データで見られた状態）
        tags.album_artists = ["作曲家A"] if number <= 2 else []
        tags.duration = 100.0
        return tags

    monkeypatch.setattr(scanner_module, "read_tags", fake_read_tags)
    settings.set("rsgain_enabled", False)
    LibraryScanner(db, library, settings).scan()

    albums = db.query("SELECT id, name, album_key FROM albums WHERE name = ?",
                      ("サウンドトラック",))
    assert len(albums) == 1, f"アルバムが分裂しています: {albums}"
    assert db.scalar(
        "SELECT COUNT(*) FROM tracks WHERE album_id = ?", (albums[0]["id"],)
    ) == 4
    # ALBUMARTIST を持つ曲の情報でアルバムアーティストが埋まる
    row = db.query_one(
        "SELECT a.name FROM albums al JOIN artists a ON a.id = al.album_artist_id "
        "WHERE al.id = ?",
        (albums[0]["id"],),
    )
    assert row is not None and row["name"] == "作曲家A"


def test_multi_disc_album_stays_single(
    library: Config, db: Database, settings: SettingsStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DISCNUMBER が違ってもアルバムは 1 つにまとまり、ディスク番号は保持される。"""
    for disc in (1, 2):
        for track in (1, 2):
            path = library.music_dir / f"MultiDisc/2枚組/d{disc}t{track}.flac"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\x00" * 128)

    def fake_read_tags(p: Path) -> TrackTags | None:
        if "MultiDisc" not in p.parts:
            return None
        disc = int(p.stem[1])
        track = int(p.stem[3])
        tags = TrackTags(path=p, title=f"D{disc}-{track}", album="2枚組アルバム")
        tags.artists = ["歌手"]
        tags.album_artists = ["歌手"]
        tags.disc_no = disc
        tags.track_no = track
        tags.duration = 100.0
        return tags

    monkeypatch.setattr(scanner_module, "read_tags", fake_read_tags)
    settings.set("rsgain_enabled", False)
    LibraryScanner(db, library, settings).scan()

    albums = db.query("SELECT id FROM albums WHERE name = ?", ("2枚組アルバム",))
    assert len(albums) == 1
    discs = [
        r["disc_no"]
        for r in db.query(
            "SELECT DISTINCT disc_no FROM tracks WHERE album_id = ? ORDER BY disc_no",
            (albums[0]["id"],),
        )
    ]
    assert discs == [1, 2]


def test_initials_include_empty_buckets(scan: LibraryScanner, db: Database) -> None:
    scan.scan()
    repo = LibraryRepository(db)
    kana = repo.initials("artists", "kana")
    # カナは五十音を 1 文字ずつ並べる（英数タブが A〜Z を並べるのと同じ形）
    assert [item["initial"] for item in kana] == KANA_INITIALS
    # 「宇多田ヒカル（うただひかる）」は「う」に入る（行の代表「あ」ではない）
    counts = {item["initial"]: item["count"] for item in kana}
    assert counts["う"] == 1
    assert counts["あ"] == 0
    latin = repo.initials("artists", "latin")
    assert {item["initial"] for item in latin} >= {"R", "#"}
