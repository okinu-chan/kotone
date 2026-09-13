"""ライブラリスキャンのテスト。

実際の音源を用意する代わりに、``read_tags`` を差し替えて合成タグを流し込む。
DB への取り込み・読み仮名生成・アーティスト分割・再スキャン時の挙動を確認する。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from kotone.config import Config
from kotone.db import Database
from kotone.library import scanner as scanner_module
from kotone.library.scanner import LibraryScanner
from kotone.library.tags import TrackTags
from kotone.repository import LibraryRepository
from kotone.settings import SettingsStore
from kotone.stats import StatsStore
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


def test_album_initials_can_use_the_artist_name(scan: LibraryScanner, db: Database) -> None:
    """アルバム一覧をアルバムアーティスト名の頭文字でも引けること（要件 3.7）。

    ウォークマンの頭出しに倣って既定はアーティスト名。同じ頭文字でも
    アルバム名で引いたときとは結果が変わる。
    """
    scan.scan()
    repo = LibraryRepository(db)

    by_artist = repo.list_albums(initial="う", initial_group="kana", initial_by="artist")
    # 「宇多田ヒカル」の「First Love」は、アルバム名では A〜Z 側に入る
    assert {a["name"] for a in by_artist} == {"First Love"}

    by_album = repo.list_albums(initial="う", initial_group="kana", initial_by="album")
    assert by_album == []


def test_album_initial_counts_match_the_filter(scan: LibraryScanner, db: Database) -> None:
    """表示する件数と、実際に絞り込んだときの枚数が一致すること。"""
    scan.scan()
    repo = LibraryRepository(db)

    for group in ("kana", "latin"):
        for item in repo.initials("albums", group, "artist"):
            got = repo.count_albums(
                initial=item["initial"], initial_group=group, initial_by="artist"
            )
            assert got == item["count"], (group, item["initial"])


def test_albums_without_an_album_artist_stay_reachable(
    scan: LibraryScanner, db: Database
) -> None:
    """アルバムアーティストが決まらないアルバムも頭文字から辿れること。

    実ライブラリでは 1,007 枚中 83 枚（すべてコンピレーション）が該当する。
    どの頭文字にも当たらないと、その分がイニシャルサーチから消えてしまう。
    """
    scan.scan()
    repo = LibraryRepository(db)
    db.execute("UPDATE albums SET album_artist_id = NULL WHERE name = 'Compilation'")
    db.commit()

    hits = repo.list_albums(initial="#", initial_group="latin", initial_by="artist")
    assert "Compilation" in {a["name"] for a in hits}

    # 頭文字の合計が総数と一致する = どこにも入らないアルバムが無い
    total = repo.count_albums()
    counted = sum(
        item["count"]
        for group in ("kana", "latin")
        for item in repo.initials("albums", group, "artist")
    )
    assert counted == total


# ------------------------------------------- 「最近追加したアルバム」の絞り込み


def test_first_scan_is_not_treated_as_recent(scan: LibraryScanner, db: Database) -> None:
    """初回スキャンで取り込んだぶんは新着に入らないこと。

    最初の取り込みではライブラリ全体が同じ時期に入るので、追加日で見ると
    全部が新着になってしまう。それでは絞り込む意味が無い。
    """
    scan.scan()
    repo = LibraryRepository(db)
    stats = StatsStore(db)

    assert stats.recent_baseline is not None
    assert repo.count_albums() > 0
    assert repo.count_albums(added_after=stats.recent_baseline) == 0


def test_albums_added_later_are_recent(
    scan: LibraryScanner, db: Database, config: Config
) -> None:
    """起点より後に増えたアルバムだけが新着として返ること。

    2 回目以降のスキャンで起点が動いてしまうと、足したばかりのアルバムが
    すぐ新着から外れる。そこも確かめる。
    """
    scan.scan()
    stats = StatsStore(db)
    repo = LibraryRepository(db)
    baseline = stats.recent_baseline
    assert repo.count_albums(added_after=baseline) == 0

    new_path = "中島みゆき/時代/01 時代.flac"
    LIBRARY[new_path] = {
        "title": "時代",
        "artists": ["中島みゆき"],
        "album_artists": ["中島みゆき"],
        "album": "時代",
        "genres": ["J-Pop"],
        "date": "1975",
        "track_no": 1,
    }
    try:
        path = config.music_dir / new_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(128))
        # 追加日は秒までしか持たないので、起点と同じ秒に入らないようずらす
        time.sleep(1.1)
        scan.scan()

        assert StatsStore(db).recent_baseline == baseline   # 起点は動かない
        recent = repo.list_albums(sort="added", added_after=baseline)
        assert [a["name"] for a in recent] == ["時代"]
    finally:
        LIBRARY.pop(new_path)


def test_recent_filter_combines_with_other_filters(
    scan: LibraryScanner, db: Database
) -> None:
    """新着の絞り込みは、ほかの絞り込みと重ねられること。"""
    scan.scan()
    stats = StatsStore(db)
    repo = LibraryRepository(db)
    db.execute(
        "INSERT INTO albums (album_key, name, sort_key, initial, initial_group, added_at) "
        "VALUES ('new', 'New One', 'new one', 'N', 'latin', datetime('now', '+1 hour'))"
    )
    db.commit()

    assert repo.count_albums(
        added_after=stats.recent_baseline, initial="N", initial_group="latin",
        initial_by="album",
    ) == 1
    assert repo.count_albums(
        added_after=stats.recent_baseline, initial="Z", initial_group="latin",
        initial_by="album",
    ) == 0


def _add_album(config: Config, folder: str, album: str, artist: str, count: int = 2) -> list[str]:
    """テスト用のライブラリにアルバムを 1 枚足す。"""
    paths = []
    for index in range(1, count + 1):
        rel = f"{folder}/{index:02d} {album} {index}.flac"
        LIBRARY[rel] = {
            "title": f"{album} {index}", "artists": [artist], "album_artists": [artist],
            "album": album, "genres": ["J-Pop"], "date": "2026", "track_no": index,
        }
        path = config.music_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(bytes(128))
        paths.append(rel)
    return paths


def _drop_album(config: Config, rel_paths: list[str]) -> None:
    """ライブラリからアルバムのファイルを消す（利用者が SSD 上で消した状況）。"""
    for rel in rel_paths:
        LIBRARY.pop(rel, None)
        (config.music_dir / rel).unlink(missing_ok=True)


def test_delete_and_add_in_one_scan_lists_only_the_new_album(
    scan: LibraryScanner, db: Database, config: Config
) -> None:
    """既存アルバムの削除と新規追加を同時に行っても、新しいほうだけが新着になる。

    利用者が SSD 上で入れ替えをしてから 1 回スキャンする、という使い方。
    消えたアルバムは行ごと無くなるので新着には出ず、増えたアルバムだけが残る。
    """
    scan.scan()
    stats = StatsStore(db)
    repo = LibraryRepository(db)
    baseline = stats.recent_baseline
    assert repo.count_albums(added_after=baseline) == 0

    added = _add_album(config, "新しい人/新しいアルバム", "新しいアルバム", "新しい人")
    removed = [p for p in LIBRARY if p.startswith("Radiohead/OK Computer")]
    # LIBRARY はモジュール共有なので、消した分は必ず戻す（他のテストに漏れる）
    saved = {rel: LIBRARY[rel] for rel in removed}
    _drop_album(config, removed)
    try:
        time.sleep(1.1)   # 追加日は秒単位。起点と同じ秒に入らないようずらす
        scan.scan()

        names = [a["name"] for a in repo.list_albums(sort="added", added_after=baseline)]
        assert names == ["新しいアルバム"]
        # 消したアルバムはライブラリ自体から消えている
        assert "OK Computer" not in [a["name"] for a in repo.list_albums()]
    finally:
        _drop_album(config, added)
        LIBRARY.update(saved)


def test_full_rescan_does_not_make_everything_recent(
    scan: LibraryScanner, db: Database
) -> None:
    """完全な再スキャンをしても、既存のアルバムが新着になり直さないこと。

    追加日を入れ直してしまうと、ライブラリ全体が新着になって使い物に
    ならなくなる。
    """
    scan.scan()
    stats = StatsStore(db)
    repo = LibraryRepository(db)
    baseline = stats.recent_baseline

    time.sleep(1.1)
    scan.scan(full=True)

    assert repo.count_albums(added_after=baseline) == 0
    assert StatsStore(db).recent_baseline == baseline


def test_adding_a_track_to_an_existing_album_is_not_recent(
    scan: LibraryScanner, db: Database, config: Config
) -> None:
    """既存アルバムに曲を足しただけでは新着に出ない（アルバム単位の機能のため）。"""
    scan.scan()
    stats = StatsStore(db)
    repo = LibraryRepository(db)
    baseline = stats.recent_baseline

    rel = "Radiohead/OK Computer/02 Paranoid Android.flac"
    LIBRARY[rel] = {
        "title": "Paranoid Android", "artists": ["Radiohead"],
        "album_artists": ["Radiohead"], "album": "OK Computer",
        "genres": ["Alternative"], "date": "1997", "track_no": 2,
    }
    path = config.music_dir / rel
    path.write_bytes(bytes(128))
    try:
        time.sleep(1.1)
        scan.scan()
        assert repo.count_albums(added_after=baseline) == 0
    finally:
        LIBRARY.pop(rel, None)
        path.unlink(missing_ok=True)


# ------------------------------------- ドライブが読めないときにライブラリを消さない
#
# 2026-09-13、USB の過電流で音楽用 SSD が外れ、どのファイルも読み込みエラーに
# なった。os.walk は既定で読めないディレクトリを黙って飛ばすため、この状態で
# スキャンすると全曲が「消えた」ことになり、ライブラリから削除されてしまう。


def _walk_failing_at(dirname: str):
    """``dirname`` のディレクトリだけ読み込みエラーにする os.walk。"""
    import os

    real_walk = os.walk

    def walk(top, **kwargs):  # noqa: ANN001, ANN202
        onerror = kwargs.get("onerror")
        for dirpath, dirnames, filenames in real_walk(top, **kwargs):
            if dirname in dirnames:
                dirnames.remove(dirname)
                if onerror is not None:
                    onerror(OSError(5, "Input/output error", str(Path(dirpath) / dirname)))
            yield dirpath, dirnames, filenames

    return walk


def test_tracks_in_unreadable_directories_are_kept(
    scan: LibraryScanner, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan.scan()
    monkeypatch.setattr(scanner_module.os, "walk", _walk_failing_at("Radiohead"))

    result = scan.scan()

    assert result.removed == 0
    assert db.query_one("SELECT id FROM tracks WHERE title = 'Airbag'") is not None
    assert any("Radiohead" in error for error in result.errors)


def test_really_deleted_files_are_still_removed_elsewhere(
    scan: LibraryScanner, library: Config, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """読めない場所があっても、ほかの場所で本当に消した曲は片付ける。"""
    scan.scan()
    (library.music_dir / "宇多田ヒカル/First Love/02 First Love.flac").unlink()
    monkeypatch.setattr(scanner_module.os, "walk", _walk_failing_at("Radiohead"))

    result = scan.scan()

    assert result.removed == 1
    assert db.query_one("SELECT id FROM tracks WHERE title = 'Airbag'") is not None


def test_scan_that_finds_nothing_does_not_empty_the_library(
    scan: LibraryScanner, library: Config, db: Database
) -> None:
    scan.scan()
    for rel_path in LIBRARY:
        (library.music_dir / rel_path).unlink()

    with pytest.raises(RuntimeError, match="ライブラリを空にせずに中止"):
        scan.scan()

    assert db.scalar("SELECT COUNT(*) FROM tracks") == len(LIBRARY)


def test_unreadable_music_directory_aborts_the_scan(
    scan: LibraryScanner, db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    scan.scan()

    def broken(path):  # noqa: ANN001, ANN202
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(scanner_module.os, "scandir", broken)

    with pytest.raises(OSError, match="音楽ディレクトリを読み込めません"):
        scan.scan()

    assert db.scalar("SELECT COUNT(*) FROM tracks") == len(LIBRARY)


def test_first_scan_of_an_empty_directory_is_fine(
    config: Config, db: Database, settings: SettingsStore
) -> None:
    """ライブラリが空なら、曲が無いのは異常ではない。"""
    settings.set("rsgain_enabled", False)
    result = LibraryScanner(db, config, settings).scan()
    assert result.added == 0


def test_blind_spot_matching() -> None:
    assert scanner_module._is_under("Radiohead/OK Computer/01.flac", ["Radiohead"])
    assert scanner_module._is_under("a.flac", ["a.flac"])
    assert scanner_module._is_under("anything.flac", [""])        # 全体が読めない
    assert not scanner_module._is_under("Radiohead2/x.flac", ["Radiohead"])
    root = Path("/music")
    assert scanner_module._relative_or_root(root, root) == ""
    assert scanner_module._relative_or_root(root / "a" / "b", root) == "a/b"
