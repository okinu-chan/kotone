"""ライブラリの読み出しクエリ。

ソート・絞り込み・イニシャルサーチ・ダイレクトサーチはすべてここに集約する。
日本語の並び順は ``ORDER BY sort_key, reading, name`` の 3 段で決める
（sort_key は濁点を落としているため、同点は reading で解決される）。
"""

from __future__ import annotations

from typing import Any

from kotone.db import Database
from kotone.text import KANA_INITIALS, LATIN_INITIALS

#: 一覧のソート軸（要件 3.2）
ALBUM_SORTS: dict[str, str] = {
    "album": "al.sort_key, al.reading, al.name",
    "artist": "aa.sort_key, aa.reading, aa.name, al.year, al.sort_key",
    "year": "al.year IS NULL, al.year, al.sort_key, al.reading",
    "added": "al.added_at DESC, al.sort_key",
    "genre": "genre_sort, al.sort_key, al.reading",
}

ARTIST_SORTS: dict[str, str] = {
    "artist": "ar.sort_key, ar.reading, ar.name",
    "added": "ar.created_at DESC, ar.sort_key",
}

TRACK_SORTS: dict[str, str] = {
    "album": "al.sort_key, al.reading, t.disc_no, t.track_no, t.path",
    "title": "t.sort_key, t.reading, t.title",
    "artist": "aa.sort_key, aa.reading, al.sort_key, t.disc_no, t.track_no",
    "year": "t.year IS NULL, t.year, al.sort_key, t.disc_no, t.track_no",
    "added": "t.added_at DESC, t.path",
}

_ALBUM_SELECT = """
SELECT
    al.id, al.name, al.reading, al.sort_key, al.initial, al.initial_group,
    al.year, al.release_date, al.added_at, al.is_compilation, al.directory,
    aa.id   AS album_artist_id,
    aa.name AS album_artist,
    -- イニシャルサーチの頭出し先を画面側で探すために返す。アルバム
    -- アーティストが決まらないアルバムは「#」に寄せる（_ARTIST_INITIAL と同じ扱い）
    COALESCE(aa.initial, '#')            AS album_artist_initial,
    COALESCE(aa.initial_group, 'latin')  AS album_artist_initial_group,
    art.source_hash AS artwork_hash,
    (SELECT COUNT(*) FROM tracks tr WHERE tr.album_id = al.id) AS track_count,
    (SELECT COALESCE(SUM(tr.duration), 0) FROM tracks tr WHERE tr.album_id = al.id)
        AS duration,
    (SELECT MIN(g.sort_key) FROM tracks tr
        JOIN track_genres tg ON tg.track_id = tr.id
        JOIN genres g ON g.id = tg.genre_id
        WHERE tr.album_id = al.id) AS genre_sort
FROM albums al
LEFT JOIN artists aa ON aa.id = al.album_artist_id
LEFT JOIN artwork art ON art.album_id = al.id
"""

_TRACK_SELECT = """
SELECT
    t.id, t.path, t.title, t.reading, t.sort_key, t.initial, t.initial_group,
    t.disc_no, t.track_no, t.duration, t.codec, t.samplerate, t.bit_depth,
    t.channels, t.bitrate, t.is_dsd, t.year, t.release_date, t.added_at,
    t.track_gain_db, t.track_peak, t.album_gain_db, t.album_peak,
    t.reading_locked, t.artists_locked,
    al.id   AS album_id,
    al.name AS album,
    al.year AS album_year,
    art.source_hash AS artwork_hash,
    aa.sort_key AS aa_sort_key,
    (SELECT GROUP_CONCAT(a.name, ' / ') FROM track_artists ta
        JOIN artists a ON a.id = ta.artist_id
        WHERE ta.track_id = t.id AND ta.role = 'artist') AS artist_names,
    (SELECT GROUP_CONCAT(a.id) FROM track_artists ta
        JOIN artists a ON a.id = ta.artist_id
        WHERE ta.track_id = t.id AND ta.role = 'artist') AS artist_ids,
    (SELECT GROUP_CONCAT(g.name, ' / ') FROM track_genres tg
        JOIN genres g ON g.id = tg.genre_id
        WHERE tg.track_id = t.id) AS genre_names,
    (SELECT GROUP_CONCAT(g.id) FROM track_genres tg
        JOIN genres g ON g.id = tg.genre_id
        WHERE tg.track_id = t.id) AS genre_ids
FROM tracks t
LEFT JOIN albums al ON al.id = t.album_id
LEFT JOIN artists aa ON aa.id = al.album_artist_id
LEFT JOIN artwork art ON art.album_id = al.id
"""


class LibraryRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    # ============================================================ アルバム

    def list_albums(
        self,
        *,
        sort: str = "artist",
        initial: str | None = None,
        initial_group: str | None = None,
        initial_by: str = "album",
        artist_id: int | None = None,
        genre_id: int | None = None,
        year: int | None = None,
        query: str | None = None,
        codec: str | None = None,
        quality: str | None = None,
        added_after: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = _album_filters(
            initial=initial, initial_group=initial_group, initial_by=initial_by,
            artist_id=artist_id,
            genre_id=genre_id, year=year, query=query, codec=codec, quality=quality,
            added_after=added_after,
        )
        order = ALBUM_SORTS.get(sort, ALBUM_SORTS["artist"])
        sql = f"{_ALBUM_SELECT} {where} ORDER BY {order}"
        sql, params = _paginate(sql, params, limit, offset)
        return [_album_row(r) for r in self.db.query(sql, params)]

    def count_albums(self, **kwargs: Any) -> int:
        where, params = _album_filters(**kwargs)
        sql = (
            "SELECT COUNT(*) FROM albums al "
            f"LEFT JOIN artists aa ON aa.id = al.album_artist_id {where}"
        )
        return int(self.db.scalar(sql, params) or 0)

    def get_album(self, album_id: int) -> dict[str, Any] | None:
        rows = self.db.query(f"{_ALBUM_SELECT} WHERE al.id = ?", (album_id,))
        if not rows:
            return None
        album = _album_row(rows[0])
        album["tracks"] = self.list_tracks(album_id=album_id, sort="album")
        album["artists"] = self.db.query(
            """
            SELECT DISTINCT a.id, a.name, a.reading
            FROM track_artists ta
            JOIN artists a ON a.id = ta.artist_id
            JOIN tracks t ON t.id = ta.track_id
            WHERE t.album_id = ?
            ORDER BY a.sort_key, a.reading, a.name
            """,
            (album_id,),
        )
        return album

    # ========================================================== アーティスト

    def list_artists(
        self,
        *,
        sort: str = "artist",
        initial: str | None = None,
        initial_group: str | None = None,
        query: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if initial:
            clauses.append("ar.initial = ?")
            params.append(initial)
        if initial_group:
            clauses.append("ar.initial_group = ?")
            params.append(initial_group)
        if query:
            clauses.append("(ar.name LIKE ? OR ar.reading LIKE ? OR ar.sort_key LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like, like])
        # 曲を 1 つも持たないアーティストは一覧に出さない。
        #
        # ALBUMARTIST タグだけに出てくる名前（「765PRO ALLSTARS」など）が
        # 「0 曲」として並び、目的の名前を探しにくくなる。タグを直したあとの
        # 行き場を失った名前も、再スキャンを待たずにここで消える。
        clauses.append(
            "EXISTS (SELECT 1 FROM track_artists ta WHERE ta.artist_id = ar.id)"
        )
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = ARTIST_SORTS.get(sort, ARTIST_SORTS["artist"])
        sql = f"""
            SELECT ar.id, ar.name, ar.reading, ar.sort_key, ar.initial, ar.initial_group,
                   ar.reading_locked,
                   (SELECT COUNT(DISTINCT ta.track_id) FROM track_artists ta
                        WHERE ta.artist_id = ar.id) AS track_count,
                   (SELECT COUNT(DISTINCT t.album_id) FROM track_artists ta
                        JOIN tracks t ON t.id = ta.track_id
                        WHERE ta.artist_id = ar.id AND t.album_id IS NOT NULL)
                        AS album_count
            FROM artists ar
            {where}
            ORDER BY {order}
        """
        sql, params = _paginate(sql, params, limit, offset)
        return self.db.query(sql, params)

    def get_artist(self, artist_id: int) -> dict[str, Any] | None:
        """アーティスト詳細（ダイレクトサーチ用の全曲 + アルバム一覧）。"""
        artist = self.db.query_one(
            "SELECT id, name, reading, sort_key, initial, initial_group, reading_locked "
            "FROM artists WHERE id = ?",
            (artist_id,),
        )
        if artist is None:
            return None
        # アルバム名順に並べる（リリース年順だと目当てのアルバムを探しにくい）
        artist["albums"] = self.list_albums(sort="album", artist_id=artist_id)
        artist["tracks"] = self.list_tracks(artist_id=artist_id, sort="album")
        return artist

    # ============================================================== 楽曲

    def list_tracks(
        self,
        *,
        sort: str = "album",
        album_id: int | None = None,
        artist_id: int | None = None,
        genre_id: int | None = None,
        year: int | None = None,
        initial: str | None = None,
        initial_group: str | None = None,
        query: str | None = None,
        codec: str | None = None,
        quality: str | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where, params = _track_filters(
            album_id=album_id, artist_id=artist_id, genre_id=genre_id, year=year,
            initial=initial, initial_group=initial_group, query=query,
            codec=codec, quality=quality,
        )
        order = TRACK_SORTS.get(sort, TRACK_SORTS["album"])
        sql = f"{_TRACK_SELECT} {where} ORDER BY {order}"
        sql, params = _paginate(sql, params, limit, offset)
        return [_track_row(r) for r in self.db.query(sql, params)]

    def get_track(self, track_id: int) -> dict[str, Any] | None:
        rows = self.db.query(f"{_TRACK_SELECT} WHERE t.id = ?", (track_id,))
        return _track_row(rows[0]) if rows else None

    def get_track_by_path(self, path: str) -> dict[str, Any] | None:
        rows = self.db.query(f"{_TRACK_SELECT} WHERE t.path = ?", (path,))
        return _track_row(rows[0]) if rows else None

    # ========================================================= ジャンル・年

    def list_genres(self) -> list[dict[str, Any]]:
        return self.db.query(
            """
            SELECT g.id, g.name, g.reading, g.sort_key, g.initial, g.initial_group,
                   (SELECT COUNT(*) FROM track_genres tg WHERE tg.genre_id = g.id)
                        AS track_count
            FROM genres g
            ORDER BY g.sort_key, g.reading, g.name
            """
        )

    def list_formats(self) -> dict[str, Any]:
        """ライブラリに実在するフォーマットと音質の一覧（絞り込みの選択肢用）。

        選択肢を固定で並べると、そのライブラリに無いものまで出てしまう。
        実際に入っているものだけを件数つきで返す。
        """
        codecs = self.db.query(
            """
            SELECT codec, COUNT(*) AS track_count,
                   COUNT(DISTINCT album_id) AS album_count
            FROM tracks WHERE codec != '' AND missing = 0
            GROUP BY codec ORDER BY track_count DESC
            """
        )
        qualities = []
        for key, condition in QUALITY_FILTERS.items():
            count = int(
                self.db.scalar(
                    f"SELECT COUNT(*) FROM tracks t WHERE t.missing = 0 AND {condition}"
                )
                or 0
            )
            if count:
                qualities.append({"key": key, "track_count": count})
        return {"codecs": codecs, "qualities": qualities}

    def list_years(self) -> list[dict[str, Any]]:
        return self.db.query(
            """
            SELECT year, COUNT(*) AS track_count,
                   COUNT(DISTINCT album_id) AS album_count
            FROM tracks
            WHERE year IS NOT NULL
            GROUP BY year
            ORDER BY year DESC
            """
        )

    # ======================================================= イニシャルサーチ

    def initials(
        self, entity: str, group: str = "kana", initial_by: str = "album"
    ) -> list[dict[str, Any]]:
        """頭文字ごとの件数（要件 3.7）。件数 0 の文字も返してグリッドを固定表示する。

        ``entity="albums"`` のときだけ ``initial_by`` が効く。``"artist"`` を
        指定すると、アルバム名ではなくアルバムアーティスト名の頭文字で数える
        （ウォークマンの頭出しに倣った既定）。
        """
        table = {"albums": "albums", "artists": "artists", "tracks": "tracks"}.get(entity)
        if table is None:
            raise ValueError(f"未知の対象です: {entity}")

        if table == "albums" and initial_by == "artist":
            rows = self.db.query(
                f"SELECT {_ARTIST_INITIAL} AS initial, COUNT(*) AS count "
                "FROM albums al LEFT JOIN artists aa ON aa.id = al.album_artist_id "
                f"WHERE {_ARTIST_INITIAL_GROUP} = ? GROUP BY 1",
                (group,),
            )
        elif table == "artists":
            # 一覧と同じ条件で数える（曲を持たない名前は出さない）。ここがずれると
            # 件数のあるセルを押しても何も無い、ということが起きる
            rows = self.db.query(
                "SELECT ar.initial AS initial, COUNT(*) AS count FROM artists ar "
                "WHERE ar.initial_group = ? AND EXISTS "
                "  (SELECT 1 FROM track_artists ta WHERE ta.artist_id = ar.id) "
                "GROUP BY ar.initial",
                (group,),
            )
        else:
            rows = self.db.query(
                f"SELECT initial, COUNT(*) AS count FROM {table} "
                "WHERE initial_group = ? GROUP BY initial",
                (group,),
            )
        counts = {r["initial"]: int(r["count"]) for r in rows}
        letters = KANA_INITIALS if group == "kana" else LATIN_INITIALS
        return [{"initial": ch, "count": counts.get(ch, 0)} for ch in letters]

    # ============================================================== 検索

    def search(self, query: str, limit: int = 20) -> dict[str, list[dict[str, Any]]]:
        """名前と読みの両方に対する部分一致検索。"""
        query = query.strip()
        if not query:
            return {"artists": [], "albums": [], "tracks": []}
        return {
            "artists": self.list_artists(query=query, limit=limit),
            "albums": self.list_albums(query=query, limit=limit, sort="album"),
            "tracks": self.list_tracks(query=query, limit=limit, sort="title"),
        }

    # ====================================================== 再生スコープの解決

    def resolve_scope(self, kind: str, scope_id: str | None) -> list[dict[str, Any]]:
        """再生スコープに属する曲を、再生順に並べて返す（要件 3.1）。"""
        if kind == "album":
            return self.list_tracks(album_id=int(scope_id), sort="album")
        if kind == "artist":
            return self.list_tracks(artist_id=int(scope_id), sort="album")
        if kind == "genre":
            return self.list_tracks(genre_id=int(scope_id), sort="album")
        if kind == "year":
            return self.list_tracks(year=int(scope_id), sort="album")
        if kind == "playlist":
            return self.list_playlist_tracks(int(scope_id))
        if kind == "all":
            return self.list_tracks(sort="artist")
        raise ValueError(f"未知の再生スコープです: {kind}")

    def scope_label(self, kind: str, scope_id: str | None) -> str:
        if kind == "all" or scope_id is None:
            return "すべての曲"
        table_column = {
            "album": ("albums", "name"),
            "artist": ("artists", "name"),
            "genre": ("genres", "name"),
            "playlist": ("playlists", "name"),
        }.get(kind)
        if kind == "year":
            return f"{scope_id} 年"
        if table_column is None:
            return "すべての曲"
        table, column = table_column
        value = self.db.scalar(f"SELECT {column} FROM {table} WHERE id = ?", (scope_id,))
        return str(value) if value else "すべての曲"

    # ========================================================== プレイリスト

    def list_playlists(self) -> list[dict[str, Any]]:
        return self.db.query(
            """
            SELECT p.id, p.name, p.reading, p.sort_key, p.initial, p.initial_group,
                   p.created_at, p.updated_at,
                   (SELECT COUNT(*) FROM playlist_tracks pt WHERE pt.playlist_id = p.id)
                        AS track_count,
                   (SELECT COALESCE(SUM(t.duration), 0) FROM playlist_tracks pt
                        JOIN tracks t ON t.id = pt.track_id
                        WHERE pt.playlist_id = p.id) AS duration,
                   (SELECT art.source_hash FROM playlist_tracks pt
                        JOIN tracks t ON t.id = pt.track_id
                        JOIN artwork art ON art.album_id = t.album_id
                        WHERE pt.playlist_id = p.id
                        ORDER BY pt.position LIMIT 1) AS artwork_hash,
                   (SELECT t.album_id FROM playlist_tracks pt
                        JOIN tracks t ON t.id = pt.track_id
                        JOIN artwork art ON art.album_id = t.album_id
                        WHERE pt.playlist_id = p.id
                        ORDER BY pt.position LIMIT 1) AS artwork_album_id
            FROM playlists p
            -- 並び順は利用者が決める（position）。同点は読み順で安定させる
            ORDER BY p.position, p.sort_key, p.reading, p.name
            """
        )

    def get_playlist(self, playlist_id: int) -> dict[str, Any] | None:
        playlists = [p for p in self.list_playlists() if p["id"] == playlist_id]
        if not playlists:
            return None
        playlist = playlists[0]
        playlist["tracks"] = self.list_playlist_tracks(playlist_id)
        return playlist

    def list_playlist_tracks(self, playlist_id: int) -> list[dict[str, Any]]:
        rows = self.db.query(
            f"""
            {_TRACK_SELECT}
            JOIN playlist_tracks pt ON pt.track_id = t.id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position
            """,
            (playlist_id,),
        )
        tracks = []
        for position, row in enumerate(rows):
            track = _track_row(row)
            track["position"] = position
            tracks.append(track)
        return tracks

    # ============================================================ 統計

    def stats(self) -> dict[str, Any]:
        return {
            "tracks": int(self.db.scalar("SELECT COUNT(*) FROM tracks") or 0),
            "albums": int(self.db.scalar("SELECT COUNT(*) FROM albums") or 0),
            "artists": int(self.db.scalar("SELECT COUNT(*) FROM artists") or 0),
            "genres": int(self.db.scalar("SELECT COUNT(*) FROM genres") or 0),
            "playlists": int(self.db.scalar("SELECT COUNT(*) FROM playlists") or 0),
            "total_duration": float(
                self.db.scalar("SELECT COALESCE(SUM(duration), 0) FROM tracks") or 0
            ),
            "albums_with_artwork": int(
                self.db.scalar("SELECT COUNT(*) FROM artwork") or 0
            ),
            "tracks_with_gain": int(
                self.db.scalar(
                    "SELECT COUNT(*) FROM tracks WHERE track_gain_db IS NOT NULL"
                )
                or 0
            ),
        }

    def scan_state(self) -> dict[str, Any]:
        return self.db.query_one("SELECT * FROM scan_state WHERE id = 1") or {}


# ============================================================ ヘルパ


def _paginate(
    sql: str, params: list[Any], limit: int | None, offset: int
) -> tuple[str, list[Any]]:
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        params = [*params, int(limit), int(offset)]
    elif offset:
        sql += " LIMIT -1 OFFSET ?"
        params = [*params, int(offset)]
    return sql, params


#: アルバムをアルバムアーティスト名の頭文字で引くときの式。
#:
#: アルバムアーティストが決まらないアルバム（実ライブラリでは 1,007 枚中 83 枚、
#: すべてコンピレーション）は NULL になる。そのままだとどの頭文字にも当たらず
#: イニシャルサーチから辿り着けなくなるため、「英数・他」タブの「#」に寄せる。
_ARTIST_INITIAL = "COALESCE(aa.initial, '#')"
_ARTIST_INITIAL_GROUP = "COALESCE(aa.initial_group, 'latin')"


def _album_filters(
    *,
    initial: str | None = None,
    initial_group: str | None = None,
    initial_by: str = "album",
    artist_id: int | None = None,
    genre_id: int | None = None,
    year: int | None = None,
    query: str | None = None,
    codec: str | None = None,
    quality: str | None = None,
    added_after: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    # 頭文字はアルバム名とアルバムアーティスト名のどちらでも引ける
    by_artist = initial_by == "artist"
    initial_expr = _ARTIST_INITIAL if by_artist else "al.initial"
    group_expr = _ARTIST_INITIAL_GROUP if by_artist else "al.initial_group"
    if initial:
        clauses.append(f"{initial_expr} = ?")
        params.append(initial)
    if initial_group:
        clauses.append(f"{group_expr} = ?")
        params.append(initial_group)
    if artist_id is not None:
        # アルバムアーティストだけでなく、収録曲のアーティストも対象にする
        # （客演のみの参加アルバムもアーティスト画面に出す。要件 3.6）
        clauses.append(
            "(al.album_artist_id = ? OR EXISTS ("
            "  SELECT 1 FROM tracks t JOIN track_artists ta ON ta.track_id = t.id"
            "  WHERE t.album_id = al.id AND ta.artist_id = ?))"
        )
        params.extend([artist_id, artist_id])
    if genre_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM tracks t JOIN track_genres tg ON tg.track_id = t.id"
            "        WHERE t.album_id = al.id AND tg.genre_id = ?)"
        )
        params.append(genre_id)
    if year is not None:
        clauses.append("al.year = ?")
        params.append(year)
    if query:
        clauses.append(
            "(al.name LIKE ? OR al.reading LIKE ? OR al.sort_key LIKE ? OR aa.name LIKE ?)"
        )
        like = f"%{query}%"
        params.extend([like, like, like, like])
    if codec:
        clauses.append(
            "EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND t.codec = ?)"
        )
        params.append(codec)
    if quality:
        condition = _quality_condition(quality)
        if condition:
            clauses.append(
                f"EXISTS (SELECT 1 FROM tracks t WHERE t.album_id = al.id AND {condition})"
            )
    if added_after:
        # 新着（この時刻より後に入ったアルバム）。起点の決め方は
        # kotone.stats.KEY_RECENT_BASELINE を参照
        clauses.append("al.added_at > ?")
        params.append(added_after)
    return (f"WHERE {' AND '.join(clauses)}" if clauses else ""), params


#: 音質による絞り込み（要件 4.1 の対応フォーマットに対応する分類）
QUALITY_FILTERS: dict[str, str] = {
    # ハイレゾ = 48kHz 超、または 16bit 超
    "hires": "(t.samplerate > 48000 OR t.bit_depth > 16)",
    "cd": "(t.samplerate <= 48000 AND (t.bit_depth IS NULL OR t.bit_depth <= 16) "
          "AND t.is_dsd = 0)",
    "dsd": "t.is_dsd = 1",
}


def _quality_condition(quality: str) -> str | None:
    return QUALITY_FILTERS.get(quality)


def _track_filters(
    *,
    album_id: int | None = None,
    artist_id: int | None = None,
    genre_id: int | None = None,
    year: int | None = None,
    initial: str | None = None,
    initial_group: str | None = None,
    query: str | None = None,
    codec: str | None = None,
    quality: str | None = None,
    added_after: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = ["t.missing = 0"]
    params: list[Any] = []
    if album_id is not None:
        clauses.append("t.album_id = ?")
        params.append(album_id)
    if artist_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM track_artists ta "
            "        WHERE ta.track_id = t.id AND ta.artist_id = ?)"
        )
        params.append(artist_id)
    if genre_id is not None:
        clauses.append(
            "EXISTS (SELECT 1 FROM track_genres tg "
            "        WHERE tg.track_id = t.id AND tg.genre_id = ?)"
        )
        params.append(genre_id)
    if year is not None:
        clauses.append("t.year = ?")
        params.append(year)
    if initial:
        clauses.append("t.initial = ?")
        params.append(initial)
    if initial_group:
        clauses.append("t.initial_group = ?")
        params.append(initial_group)
    if query:
        like = f"%{query}%"
        clauses.append(
            "(t.title LIKE ? OR t.reading LIKE ? OR t.sort_key LIKE ? OR al.name LIKE ?)"
        )
        params.extend([like, like, like, like])
    if codec:
        clauses.append("t.codec = ?")
        params.append(codec)
    if quality:
        condition = _quality_condition(quality)
        if condition:
            clauses.append(condition)
    return f"WHERE {' AND '.join(clauses)}", params


def _album_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row.pop("genre_sort", None)
    row["duration"] = float(row.get("duration") or 0)
    row["track_count"] = int(row.get("track_count") or 0)
    row["artwork"] = _artwork_ref(row.pop("artwork_hash", None), row["id"])
    return row


def _track_row(row: dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    row.pop("aa_sort_key", None)
    row["artists"] = _pair_list(row.pop("artist_ids", None), row.pop("artist_names", None))
    row["genres"] = _pair_list(row.pop("genre_ids", None), row.pop("genre_names", None))
    row["artist"] = " / ".join(a["name"] for a in row["artists"])
    row["genre"] = " / ".join(g["name"] for g in row["genres"])
    row["artwork"] = _artwork_ref(row.pop("artwork_hash", None), row.get("album_id"))
    row["duration"] = float(row.get("duration") or 0)
    return row


def _pair_list(ids: str | None, names: str | None) -> list[dict[str, Any]]:
    if not ids or not names:
        return []
    id_list = [int(i) for i in str(ids).split(",") if i]
    name_list = str(names).split(" / ")
    return [
        {"id": i, "name": n}
        for i, n in zip(id_list, name_list, strict=False)
    ]


def _artwork_ref(source_hash: str | None, album_id: int | None) -> dict[str, Any] | None:
    if not source_hash or album_id is None:
        return None
    return {
        "hash": source_hash,
        "url": f"/api/artwork/{album_id}",  # ?tier=small|medium|large&v=<hash>
    }
