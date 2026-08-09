"""タグ・読み仮名・アーティスト紐付けの手動修正（要件 3.2 / 3.6）。

**編集はタグ DB に対してのみ行い、元の音源ファイルには書き戻さない。**
ラウドネス補正と同じ方針で、ユーザーの原盤ファイルを一切書き換えない。
代わりに編集した行へ ``*_locked`` を立て、再スキャン時に自動生成値で
上書きされないようにする。

（元ファイルへ書き戻したくなった場合は、この層に mutagen での書き込みを
足せばよい。API と UI はそのまま使える。）
"""

from __future__ import annotations

import logging
from typing import Any

from kotone.db import Database
from kotone.settings import SettingsStore
from kotone.text import get_reading_generator, make_sort_fields

log = logging.getLogger(__name__)


class EditError(ValueError):
    pass


class EditingService:
    def __init__(self, db: Database, settings: SettingsStore) -> None:
        self.db = db
        self.settings = settings

    @property
    def _ignore_article(self) -> bool:
        return bool(self.settings.get("ignore_leading_article"))

    # ============================================================ 共通処理

    def _apply_naming(
        self, table: str, entity_id: int, display: str, reading: str | None
    ) -> None:
        """表示名と読みからソートキー・頭文字を作り直す。"""
        generated = reading if reading is not None else get_reading_generator().reading(display)
        sort_key, initial, group = make_sort_fields(
            display, generated, ignore_leading_article=self._ignore_article
        )
        column = "title" if table == "tracks" else "name"
        self.db.execute(
            f"UPDATE {table} SET {column} = ?, reading = ?, sort_key = ?, initial = ?, "
            "initial_group = ?, reading_locked = 1 WHERE id = ?",
            (display, generated, sort_key, initial, group, entity_id),
        )

    def _require(self, table: str, entity_id: int) -> dict[str, Any]:
        row = self.db.query_one(f"SELECT * FROM {table} WHERE id = ?", (entity_id,))
        if row is None:
            raise EditError("対象が見つかりません")
        return row

    # ========================================================== アーティスト

    def update_artist(
        self, artist_id: int, *, name: str | None = None, reading: str | None = None
    ) -> None:
        row = self._require("artists", artist_id)
        new_name = (name or row["name"]).strip()
        if not new_name:
            raise EditError("アーティスト名を入力してください")
        conflict = self.db.query_one(
            "SELECT id FROM artists WHERE name = ? AND id != ?", (new_name, artist_id)
        )
        if conflict:
            raise EditError(
                f"「{new_name}」は既に存在します。統合したい場合は merge_artists を使ってください。"
            )
        with self.db.transaction():
            self._apply_naming("artists", artist_id, new_name,
                               reading if reading is not None else None)

    def merge_artists(self, source_id: int, target_id: int) -> None:
        """自動分割の誤りで重複したアーティストを 1 つにまとめる。"""
        if source_id == target_id:
            return
        self._require("artists", source_id)
        self._require("artists", target_id)
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE OR IGNORE track_artists SET artist_id = ? WHERE artist_id = ?",
                (target_id, source_id),
            )
            conn.execute(
                "UPDATE OR IGNORE album_artists SET artist_id = ? WHERE artist_id = ?",
                (target_id, source_id),
            )
            conn.execute(
                "UPDATE albums SET album_artist_id = ? WHERE album_artist_id = ?",
                (target_id, source_id),
            )
            conn.execute("DELETE FROM artists WHERE id = ?", (source_id,))
            # 統合したことが再スキャンで戻らないよう、関係する曲をロックする
            conn.execute(
                "UPDATE tracks SET artists_locked = 1 WHERE id IN "
                "(SELECT track_id FROM track_artists WHERE artist_id = ?)",
                (target_id,),
            )

    # ============================================================ アルバム

    def update_album(
        self,
        album_id: int,
        *,
        name: str | None = None,
        reading: str | None = None,
        year: int | None = None,
    ) -> None:
        row = self._require("albums", album_id)
        new_name = (name or row["name"]).strip()
        if not new_name:
            raise EditError("アルバム名を入力してください")
        with self.db.transaction():
            self._apply_naming("albums", album_id, new_name, reading)
            if year is not None:
                self.db.execute("UPDATE albums SET year = ? WHERE id = ?", (year, album_id))

    # ============================================================== 楽曲

    def update_track(
        self,
        track_id: int,
        *,
        title: str | None = None,
        reading: str | None = None,
        disc_no: int | None = None,
        track_no: int | None = None,
        year: int | None = None,
        artist_ids: list[int] | None = None,
        artist_names: list[str] | None = None,
        genre_names: list[str] | None = None,
    ) -> None:
        row = self._require("tracks", track_id)

        with self.db.transaction():
            if title is not None or reading is not None:
                self._apply_naming(
                    "tracks", track_id, (title or row["title"]).strip(), reading
                )

            fields: dict[str, Any] = {}
            if disc_no is not None:
                fields["disc_no"] = max(1, disc_no)
            if track_no is not None:
                fields["track_no"] = max(0, track_no)
            if year is not None:
                fields["year"] = year
            if fields:
                fields["tags_locked"] = 1
                assignments = ", ".join(f"{k} = ?" for k in fields)
                self.db.execute(
                    f"UPDATE tracks SET {assignments}, updated_at = datetime('now') "
                    "WHERE id = ?",
                    [*fields.values(), track_id],
                )

            if artist_ids is not None or artist_names is not None:
                resolved = list(artist_ids or [])
                for name in artist_names or []:
                    resolved.append(self._get_or_create_artist(name))
                self._set_track_artists(track_id, resolved)

            if genre_names is not None:
                genre_ids = [self._get_or_create_genre(n) for n in genre_names]
                self._set_track_genres(track_id, genre_ids)

    def unlock_track(self, track_id: int) -> None:
        """手動修正を解除し、次回スキャンで自動生成値に戻す。"""
        self.db.execute(
            "UPDATE tracks SET reading_locked = 0, artists_locked = 0, tags_locked = 0 "
            "WHERE id = ?",
            (track_id,),
        )
        self.db.commit()

    def unlock_reading(self, table: str, entity_id: int) -> None:
        if table not in ("artists", "albums", "genres", "tracks"):
            raise EditError(f"未知の対象です: {table}")
        self.db.execute(f"UPDATE {table} SET reading_locked = 0 WHERE id = ?", (entity_id,))
        self.db.commit()

    def suggest_reading(self, text: str) -> str:
        """自動生成した読みを返す（UI の「自動生成に戻す」ボタン用）。"""
        return get_reading_generator().reading(text)

    # ============================================================== 内部

    def _get_or_create_artist(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise EditError("アーティスト名が空です")
        row = self.db.query_one("SELECT id FROM artists WHERE name = ?", (name,))
        if row:
            return int(row["id"])
        reading = get_reading_generator().reading(name)
        sort_key, initial, group = make_sort_fields(
            name, reading, ignore_leading_article=self._ignore_article
        )
        cursor = self.db.execute(
            "INSERT INTO artists (name, reading, sort_key, initial, initial_group) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, reading, sort_key, initial, group),
        )
        return int(cursor.lastrowid)

    def _get_or_create_genre(self, name: str) -> int:
        name = name.strip()
        if not name:
            raise EditError("ジャンル名が空です")
        row = self.db.query_one("SELECT id FROM genres WHERE name = ?", (name,))
        if row:
            return int(row["id"])
        reading = get_reading_generator().reading(name)
        sort_key, initial, group = make_sort_fields(
            name, reading, ignore_leading_article=self._ignore_article
        )
        cursor = self.db.execute(
            "INSERT INTO genres (name, reading, sort_key, initial, initial_group) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, reading, sort_key, initial, group),
        )
        return int(cursor.lastrowid)

    def _set_track_artists(self, track_id: int, artist_ids: list[int]) -> None:
        self.db.execute(
            "DELETE FROM track_artists WHERE track_id = ? AND role = 'artist'", (track_id,)
        )
        seen: set[int] = set()
        position = 0
        for artist_id in artist_ids:
            if artist_id in seen:
                continue
            seen.add(artist_id)
            self.db.execute(
                "INSERT OR REPLACE INTO track_artists "
                "(track_id, artist_id, role, position) VALUES (?, ?, 'artist', ?)",
                (track_id, artist_id, position),
            )
            position += 1
        # 再スキャンで自動分割に戻らないようロックする（要件 3.6）
        self.db.execute("UPDATE tracks SET artists_locked = 1 WHERE id = ?", (track_id,))

    def _set_track_genres(self, track_id: int, genre_ids: list[int]) -> None:
        self.db.execute("DELETE FROM track_genres WHERE track_id = ?", (track_id,))
        for position, genre_id in enumerate(dict.fromkeys(genre_ids)):
            self.db.execute(
                "INSERT OR REPLACE INTO track_genres (track_id, genre_id, position) "
                "VALUES (?, ?, ?)",
                (track_id, genre_id, position),
            )
        self.db.execute("UPDATE tracks SET tags_locked = 1 WHERE id = ?", (track_id,))
