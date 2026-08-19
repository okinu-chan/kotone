"""プレイリスト管理（要件 3.4）。

プレイリストは MPD の m3u ファイルではなくタグ DB と同じ SQLite で管理する。
再生時は収録曲を MPD のキューへ読み込む。
外部ソフトとのやり取りのため、拡張 m3u のインポート／エクスポートに対応する。

編集操作は「操作のたびに自動保存」なので、明示的な保存 API は用意していない。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kotone.db import Database
from kotone.settings import SettingsStore
from kotone.text import get_reading_generator, make_sort_fields

log = logging.getLogger(__name__)


class PlaylistError(ValueError):
    pass


class PlaylistService:
    def __init__(self, db: Database, settings: SettingsStore) -> None:
        self.db = db
        self.settings = settings

    # ============================================================ 作成・更新

    def create(self, name: str, track_ids: list[int] | None = None) -> int:
        name = name.strip()
        if not name:
            raise PlaylistError("プレイリスト名を入力してください")
        if self.db.query_one("SELECT id FROM playlists WHERE name = ?", (name,)):
            raise PlaylistError(f"同じ名前のプレイリストがすでにあります: {name}")

        reading = get_reading_generator().reading(name)
        sort_key, initial, group = make_sort_fields(
            name, reading,
            ignore_leading_article=bool(self.settings.get("ignore_leading_article")),
        )
        with self.db.transaction() as conn:
            # 新しいプレイリストは末尾に置く（並び順は利用者が決める）
            next_position = conn.execute(
                "SELECT COALESCE(MAX(position), -1) + 1 FROM playlists"
            ).fetchone()[0]
            cursor = conn.execute(
                "INSERT INTO playlists (name, reading, sort_key, initial, initial_group, "
                "position) VALUES (?, ?, ?, ?, ?, ?)",
                (name, reading, sort_key, initial, group, next_position),
            )
            playlist_id = int(cursor.lastrowid)
        if track_ids:
            self.add_tracks(playlist_id, track_ids)
        return playlist_id

    def rename(self, playlist_id: int, name: str) -> None:
        name = name.strip()
        if not name:
            raise PlaylistError("プレイリスト名を入力してください")
        existing = self.db.query_one(
            "SELECT id FROM playlists WHERE name = ? AND id != ?", (name, playlist_id)
        )
        if existing:
            raise PlaylistError(f"同じ名前のプレイリストがすでにあります: {name}")

        reading = get_reading_generator().reading(name)
        sort_key, initial, group = make_sort_fields(
            name, reading,
            ignore_leading_article=bool(self.settings.get("ignore_leading_article")),
        )
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE playlists SET name = ?, reading = ?, sort_key = ?, initial = ?, "
                "initial_group = ?, updated_at = datetime('now') WHERE id = ?",
                (name, reading, sort_key, initial, group, playlist_id),
            )

    def reorder_playlists(self, playlist_ids: list[int]) -> None:
        """プレイリストの並び順を、渡された順に置き換える。

        画面に出ていなかったプレイリスト（別の端末で増えた等）を落とさない
        よう、渡されなかったものは後ろへ回す。
        """
        with self.db.transaction() as conn:
            known = [
                int(r["id"])
                for r in conn.execute("SELECT id FROM playlists ORDER BY position, id")
            ]
            ordered = [pid for pid in playlist_ids if pid in known]
            ordered += [pid for pid in known if pid not in ordered]
            for position, playlist_id in enumerate(ordered):
                conn.execute(
                    "UPDATE playlists SET position = ? WHERE id = ?", (position, playlist_id)
                )

    def delete(self, playlist_id: int) -> None:
        with self.db.transaction() as conn:
            conn.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))

    # ============================================================ 曲の操作

    def add_tracks(
        self, playlist_id: int, track_ids: list[int], position: int | None = None
    ) -> int:
        """曲を追加して、追加後の総曲数を返す。"""
        self._require(playlist_id)
        valid = self._existing_track_ids(track_ids)
        if not valid:
            raise PlaylistError("追加できる曲がありません")

        with self.db.transaction() as conn:
            current = [
                r["track_id"]
                for r in conn.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? "
                    "ORDER BY position",
                    (playlist_id,),
                ).fetchall()
            ]
            index = len(current) if position is None else max(0, min(position, len(current)))
            merged = current[:index] + valid + current[index:]
            self._rewrite(conn, playlist_id, merged)
        return len(valid)

    def remove_at(self, playlist_id: int, positions: list[int]) -> None:
        self._require(playlist_id)
        drop = set(positions)
        with self.db.transaction() as conn:
            current = [
                r["track_id"]
                for r in conn.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? "
                    "ORDER BY position",
                    (playlist_id,),
                ).fetchall()
            ]
            merged = [t for i, t in enumerate(current) if i not in drop]
            self._rewrite(conn, playlist_id, merged)

    def move(self, playlist_id: int, from_position: int, to_position: int) -> None:
        self._require(playlist_id)
        with self.db.transaction() as conn:
            current = [
                r["track_id"]
                for r in conn.execute(
                    "SELECT track_id FROM playlist_tracks WHERE playlist_id = ? "
                    "ORDER BY position",
                    (playlist_id,),
                ).fetchall()
            ]
            if not 0 <= from_position < len(current):
                raise PlaylistError("移動元の位置が不正です")
            to_position = max(0, min(to_position, len(current) - 1))
            item = current.pop(from_position)
            current.insert(to_position, item)
            self._rewrite(conn, playlist_id, current)

    def reorder(self, playlist_id: int, track_ids: list[int]) -> None:
        """並び替え結果をまとめて反映する（ドラッグ＆ドロップ用）。"""
        self._require(playlist_id)
        with self.db.transaction() as conn:
            self._rewrite(conn, playlist_id, track_ids)

    # ============================================================ m3u 入出力

    def export_m3u(self, playlist_id: int, music_dir: Path) -> str:
        """拡張 m3u 形式で書き出す。パスは music_directory からの相対パス。"""
        playlist = self._require(playlist_id)
        rows = self.db.query(
            """
            SELECT t.path, t.title, t.duration,
                   (SELECT GROUP_CONCAT(a.name, ' / ') FROM track_artists ta
                        JOIN artists a ON a.id = ta.artist_id
                        WHERE ta.track_id = t.id) AS artist
            FROM playlist_tracks pt
            JOIN tracks t ON t.id = pt.track_id
            WHERE pt.playlist_id = ?
            ORDER BY pt.position
            """,
            (playlist_id,),
        )
        lines = ["#EXTM3U", f"#PLAYLIST:{playlist['name']}"]
        for row in rows:
            duration = int(round(float(row["duration"] or 0)))
            artist = row["artist"] or ""
            lines.append(f"#EXTINF:{duration},{artist} - {row['title']}")
            lines.append(row["path"])
        return "\n".join(lines) + "\n"

    def import_m3u(self, name: str, content: str, music_dir: Path) -> dict[str, Any]:
        """m3u を読み込んでプレイリストを作る。

        パスは music_directory からの相対パス、絶対パス、
        ファイル名のみ（同名が 1 件のときだけ）の順で解決を試みる。
        """
        entries = [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        track_ids: list[int] = []
        unresolved: list[str] = []
        for entry in entries:
            track_id = self._resolve_path(entry, music_dir)
            if track_id is None:
                unresolved.append(entry)
            else:
                track_ids.append(track_id)

        playlist_id = self.create(_unique_name(self.db, name))
        if track_ids:
            self.add_tracks(playlist_id, track_ids)
        return {
            "playlist_id": playlist_id,
            "imported": len(track_ids),
            "unresolved": unresolved[:50],
            "unresolved_count": len(unresolved),
        }

    def _resolve_path(self, entry: str, music_dir: Path) -> int | None:
        candidate = entry.replace("\\", "/")
        row = self.db.query_one("SELECT id FROM tracks WHERE path = ?", (candidate,))
        if row:
            return int(row["id"])

        # 絶対パスなら music_directory からの相対に直す
        try:
            relative = Path(entry).resolve().relative_to(music_dir.resolve()).as_posix()
        except (ValueError, OSError):
            relative = None
        if relative:
            row = self.db.query_one("SELECT id FROM tracks WHERE path = ?", (relative,))
            if row:
                return int(row["id"])

        # 最後の手段: ファイル名一致（一意なときだけ採用する）
        filename = Path(candidate).name
        rows = self.db.query(
            "SELECT id FROM tracks WHERE path LIKE ? LIMIT 2", (f"%/{filename}",)
        )
        if len(rows) == 1:
            return int(rows[0]["id"])
        return None

    # ============================================================ 内部

    def _require(self, playlist_id: int) -> dict[str, Any]:
        playlist = self.db.query_one(
            "SELECT id, name FROM playlists WHERE id = ?", (playlist_id,)
        )
        if playlist is None:
            raise PlaylistError("プレイリストが見つかりません")
        return playlist

    def _existing_track_ids(self, track_ids: list[int]) -> list[int]:
        if not track_ids:
            return []
        placeholders = ", ".join("?" for _ in track_ids)
        rows = self.db.query(
            f"SELECT id FROM tracks WHERE id IN ({placeholders})", track_ids
        )
        found = {int(r["id"]) for r in rows}
        return [tid for tid in track_ids if tid in found]

    @staticmethod
    def _rewrite(conn: Any, playlist_id: int, track_ids: list[int]) -> None:
        conn.execute("DELETE FROM playlist_tracks WHERE playlist_id = ?", (playlist_id,))
        conn.executemany(
            "INSERT INTO playlist_tracks (playlist_id, position, track_id) VALUES (?, ?, ?)",
            [(playlist_id, position, tid) for position, tid in enumerate(track_ids)],
        )
        conn.execute(
            "UPDATE playlists SET updated_at = datetime('now') WHERE id = ?", (playlist_id,)
        )


def _unique_name(db: Database, name: str) -> str:
    name = name.strip() or "インポートしたプレイリスト"
    if not db.query_one("SELECT id FROM playlists WHERE name = ?", (name,)):
        return name
    for suffix in range(2, 1000):
        candidate = f"{name} ({suffix})"
        if not db.query_one("SELECT id FROM playlists WHERE name = ?", (candidate,)):
            return candidate
    raise PlaylistError("プレイリスト名を決められませんでした")
