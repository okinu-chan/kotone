"""ライブラリスキャン。

音源ファイルを走査してタグ DB（SQLite）を更新し、読み仮名・ソートキー、
アートワークキャッシュ、ラウドネス計測値を生成する。

同期処理として書かれている（重い I/O とサブプロセスが主なため）。
API からはバックグラウンドスレッドで起動し、進捗は EventBus 経由で
WebSocket へ流す。

再スキャンでユーザーの手動修正を壊さないよう、``*_locked`` が立っている
行は自動生成値で上書きしない（要件 3.2 / 3.6）。
"""

from __future__ import annotations

import logging
import os
import time
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kotone.config import Config
from kotone.db import Database
from kotone.library.artists import split_artists
from kotone.library.artwork import ArtworkCache, load_source
from kotone.library.loudness import LoudnessScanner
from kotone.library.tags import TrackTags, is_audio_file, read_tags
from kotone.settings import SettingsStore
from kotone.text import get_reading_generator, make_sort_fields

log = logging.getLogger(__name__)

ProgressCallback = Callable[[str, int, int], None]


@dataclass
class ScanResult:
    added: int = 0
    updated: int = 0
    unchanged: int = 0
    removed: int = 0
    albums_with_artwork: int = 0
    tracks_measured: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "removed": self.removed,
            "albums_with_artwork": self.albums_with_artwork,
            "tracks_measured": self.tracks_measured,
            "errors": self.errors[:50],
            "duration_seconds": round(self.duration_seconds, 1),
        }


class LibraryScanner:
    def __init__(
        self,
        db: Database,
        config: Config,
        settings: SettingsStore,
        progress: ProgressCallback | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.settings = settings
        self._progress = progress or (lambda phase, done, total: None)
        self.artwork_cache = ArtworkCache(config.artwork_cache_dir)
        self._reader = get_reading_generator()
        self._ignore_article = bool(settings.get("ignore_leading_article"))
        self._separators = list(settings.get("artist_separators") or [])
        self._use_tag_gain = bool(settings.get("use_replaygain_tags"))
        # 名前 -> id のキャッシュ（1 回のスキャン中だけ有効）
        self._artist_ids: dict[str, int] = {}
        self._genre_ids: dict[str, int] = {}
        self._album_ids: dict[str, int] = {}

    # =============================================================== 実行

    def scan(self, *, full: bool = False) -> ScanResult:
        started = time.monotonic()
        result = ScanResult()
        self._set_state(status="running", phase="ファイルを探索中", processed=0, total=0,
                        started_at=True, error=None)
        try:
            files = self._walk()
            total = len(files)
            self._set_state(phase="タグを読み込み中", total=total)
            log.info("ライブラリスキャン開始: %d ファイル (full=%s)", total, full)

            dirty_albums: set[int] = set()
            seen_paths: set[str] = set()

            for index, (rel_path, abs_path, stat) in enumerate(files, start=1):
                seen_paths.add(rel_path)
                try:
                    changed, album_id = self._sync_track(
                        rel_path, abs_path, stat, full=full, result=result
                    )
                    if changed and album_id is not None:
                        dirty_albums.add(album_id)
                except Exception as exc:  # 1 ファイルの失敗で全体を止めない
                    log.exception("ファイルの取り込みに失敗: %s", rel_path)
                    result.errors.append(f"{rel_path}: {exc}")

                if index % 50 == 0 or index == total:
                    self.db.commit()
                    self._progress("タグを読み込み中", index, total)
                    self._set_state(processed=index)

            self.db.commit()

            result.removed = self._remove_missing(seen_paths)
            self._infer_album_artists()
            self._prune_orphans()
            self.db.commit()

            self._scan_artwork(dirty_albums, result, full=full)
            self._scan_loudness(dirty_albums, result, full=full)
            self.db.commit()

        except Exception as exc:
            log.exception("ライブラリスキャンが失敗しました")
            self._set_state(status="error", phase="", error=str(exc), finished_at=True)
            raise
        else:
            result.duration_seconds = time.monotonic() - started
            self._set_state(
                status="idle", phase="", finished_at=True,
                added=result.added, updated=result.updated, removed=result.removed,
            )
            log.info(
                "スキャン完了: 追加 %d / 更新 %d / 変化なし %d / 削除 %d (%.1f 秒)",
                result.added, result.updated, result.unchanged, result.removed,
                result.duration_seconds,
            )
        return result

    # =============================================================== 走査

    def _walk(self) -> list[tuple[str, Path, os.stat_result]]:
        root = self.config.music_dir
        if not root.is_dir():
            raise FileNotFoundError(f"音楽ディレクトリが見つかりません: {root}")

        files: list[tuple[str, Path, os.stat_result]] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                abs_path = Path(dirpath) / filename
                if not is_audio_file(abs_path):
                    continue
                try:
                    stat = abs_path.stat()
                except OSError:
                    continue
                rel_path = abs_path.relative_to(root).as_posix()
                files.append((rel_path, abs_path, stat))
        return files

    # =========================================================== 1 曲の同期

    def _sync_track(
        self,
        rel_path: str,
        abs_path: Path,
        stat: os.stat_result,
        *,
        full: bool,
        result: ScanResult,
    ) -> tuple[bool, int | None]:
        existing = self.db.query_one(
            "SELECT id, mtime, size, album_id, missing, "
            "reading_locked, artists_locked, tags_locked "
            "FROM tracks WHERE path = ?",
            (rel_path,),
        )

        unchanged = (
            existing is not None
            and not full
            and abs(float(existing["mtime"]) - stat.st_mtime) < 1.0
            and int(existing["size"]) == stat.st_size
            and not existing.get("missing")
        )
        if unchanged:
            result.unchanged += 1
            if existing["id"]:
                self.db.execute("UPDATE tracks SET missing = 0 WHERE id = ?", (existing["id"],))
            return False, existing["album_id"]

        tags = read_tags(abs_path)
        if tags is None:
            result.errors.append(f"{rel_path}: タグを読み取れませんでした")
            return False, existing["album_id"] if existing else None

        artist_ids = self._resolve_artists(tags.artists, tags.artist_sort)
        album_artist_ids = self._resolve_artists(tags.album_artists, tags.artist_sort)
        genre_ids = [self._get_or_create_genre(name) for name in tags.genres]
        album_id = self._get_or_create_album(tags, rel_path, album_artist_ids)

        sort_key, initial, group = make_sort_fields(
            tags.display_title,
            tags.title_sort or self._reading(tags.display_title),
            ignore_leading_article=self._ignore_article,
        )
        reading = tags.title_sort or self._reading(tags.display_title)

        values = {
            "path": rel_path,
            "mtime": stat.st_mtime,
            "size": stat.st_size,
            "title": tags.display_title,
            "album_id": album_id,
            "disc_no": tags.disc_no or 1,
            "track_no": tags.track_no or 0,
            "duration": tags.duration,
            "codec": tags.codec,
            "samplerate": tags.samplerate,
            "bit_depth": tags.bit_depth,
            "channels": tags.channels,
            "bitrate": tags.bitrate,
            "is_dsd": 1 if tags.is_dsd else 0,
            "year": tags.year,
            "release_date": tags.date,
        }

        # ファイルに ReplayGain / R128 タグがあれば計測せずに採用する（要件 3.1）。
        # 1 曲あたり数秒かかる計測を丸ごと省けるため、大規模ライブラリで効く。
        if tags.has_gain and self._use_tag_gain:
            values.update(
                track_gain_db=tags.track_gain_db,
                track_peak=tags.track_peak,
                album_gain_db=(
                    tags.album_gain_db if tags.album_gain_db is not None else tags.track_gain_db
                ),
                album_peak=(
                    tags.album_peak if tags.album_peak is not None else tags.track_peak
                ),
                gain_scanned_at=_now_text(),
            )

        if existing is None:
            values.update(reading=reading, sort_key=sort_key, initial=initial,
                          initial_group=group)
            columns = ", ".join(values)
            placeholders = ", ".join("?" for _ in values)
            cursor = self.db.execute(
                f"INSERT INTO tracks ({columns}) VALUES ({placeholders})",
                list(values.values()),
            )
            track_id = int(cursor.lastrowid)
            result.added += 1
        else:
            track_id = int(existing["id"])
            if not existing["reading_locked"]:
                values.update(reading=reading, sort_key=sort_key, initial=initial,
                              initial_group=group)
            assignments = ", ".join(f"{k} = ?" for k in values)
            self.db.execute(
                f"UPDATE tracks SET {assignments}, missing = 0, "
                "updated_at = datetime('now') WHERE id = ?",
                [*values.values(), track_id],
            )
            result.updated += 1

        if not (existing and existing["artists_locked"]):
            self._set_track_artists(track_id, artist_ids)
        self._set_track_genres(track_id, genre_ids)
        return True, album_id

    # ======================================================= エンティティ解決

    def _reading(self, text: str) -> str:
        return self._reader.reading(text)

    def _resolve_artists(self, values: list[str], sort_hints: list[str]) -> list[int]:
        names = split_artists(values, self._separators)
        hints = split_artists(sort_hints, self._separators) if sort_hints else []
        ids: list[int] = []
        for index, name in enumerate(names):
            hint = hints[index] if index < len(hints) else None
            ids.append(self._get_or_create_artist(name, hint))
        return ids

    def _get_or_create_artist(self, name: str, sort_hint: str | None = None) -> int:
        name = name.strip()
        cached = self._artist_ids.get(name)
        if cached is not None:
            return cached

        row = self.db.query_one("SELECT id FROM artists WHERE name = ?", (name,))
        if row is not None:
            self._artist_ids[name] = int(row["id"])
            return int(row["id"])

        reading = sort_hint or self._reading(name)
        sort_key, initial, group = make_sort_fields(
            name, reading, ignore_leading_article=self._ignore_article
        )
        cursor = self.db.execute(
            "INSERT INTO artists (name, reading, sort_key, initial, initial_group) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, reading, sort_key, initial, group),
        )
        artist_id = int(cursor.lastrowid)
        self._artist_ids[name] = artist_id
        return artist_id

    def _get_or_create_genre(self, name: str) -> int:
        name = name.strip()
        cached = self._genre_ids.get(name)
        if cached is not None:
            return cached

        row = self.db.query_one("SELECT id FROM genres WHERE name = ?", (name,))
        if row is not None:
            self._genre_ids[name] = int(row["id"])
            return int(row["id"])

        reading = self._reading(name)
        sort_key, initial, group = make_sort_fields(
            name, reading, ignore_leading_article=self._ignore_article
        )
        cursor = self.db.execute(
            "INSERT INTO genres (name, reading, sort_key, initial, initial_group) "
            "VALUES (?, ?, ?, ?, ?)",
            (name, reading, sort_key, initial, group),
        )
        genre_id = int(cursor.lastrowid)
        self._genre_ids[name] = genre_id
        return genre_id

    def _get_or_create_album(
        self, tags: TrackTags, rel_path: str, album_artist_ids: list[int]
    ) -> int | None:
        directory = str(Path(rel_path).parent.as_posix())
        album_name = tags.album.strip()
        primary_artist_id = album_artist_ids[0] if album_artist_ids else None

        album_artist_name = ""
        if primary_artist_id is not None:
            row = self.db.query_one(
                "SELECT name FROM artists WHERE id = ?", (primary_artist_id,)
            )
            album_artist_name = row["name"] if row else ""

        key = _album_key(album_name, album_artist_name, tags.musicbrainz_album_id, directory)
        cached = self._album_ids.get(key)
        if cached is not None:
            return cached

        row = self.db.query_one("SELECT id FROM albums WHERE album_key = ?", (key,))
        if row is not None:
            album_id = int(row["id"])
            self._album_ids[key] = album_id
            # 年が後から埋まるケースがあるので補完する
            if tags.year:
                self.db.execute(
                    "UPDATE albums SET year = COALESCE(year, ?), "
                    "release_date = COALESCE(NULLIF(release_date, ''), ?) WHERE id = ?",
                    (tags.year, tags.date, album_id),
                )
            return album_id

        display_name = album_name or Path(rel_path).parent.name or "不明なアルバム"
        reading = tags.album_sort or self._reading(display_name)
        sort_key, initial, group = make_sort_fields(
            display_name, reading, ignore_leading_article=self._ignore_article
        )
        cursor = self.db.execute(
            """
            INSERT INTO albums
                (album_key, name, reading, sort_key, initial, initial_group,
                 album_artist_id, year, release_date, directory, musicbrainz_id,
                 is_compilation)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                key, display_name, reading, sort_key, initial, group,
                primary_artist_id, tags.year, tags.date, directory,
                tags.musicbrainz_album_id, 1 if tags.is_compilation else 0,
            ),
        )
        album_id = int(cursor.lastrowid)
        self._album_ids[key] = album_id

        for position, artist_id in enumerate(album_artist_ids):
            self.db.execute(
                "INSERT OR IGNORE INTO album_artists (album_id, artist_id, position) "
                "VALUES (?, ?, ?)",
                (album_id, artist_id, position),
            )
        return album_id

    def _set_track_artists(self, track_id: int, artist_ids: list[int]) -> None:
        self.db.execute(
            "DELETE FROM track_artists WHERE track_id = ? AND role = 'artist'", (track_id,)
        )
        for position, artist_id in enumerate(artist_ids):
            self.db.execute(
                "INSERT OR REPLACE INTO track_artists (track_id, artist_id, role, position) "
                "VALUES (?, ?, 'artist', ?)",
                (track_id, artist_id, position),
            )

    def _set_track_genres(self, track_id: int, genre_ids: list[int]) -> None:
        self.db.execute("DELETE FROM track_genres WHERE track_id = ?", (track_id,))
        for position, genre_id in enumerate(genre_ids):
            self.db.execute(
                "INSERT OR REPLACE INTO track_genres (track_id, genre_id, position) "
                "VALUES (?, ?, ?)",
                (track_id, genre_id, position),
            )

    # ============================================================ 後始末

    def _remove_missing(self, seen_paths: set[str]) -> int:
        self._set_state(phase="削除された曲を整理中")
        removed = 0
        rows = self.db.query("SELECT id, path FROM tracks")
        for row in rows:
            if row["path"] in seen_paths:
                continue
            self.db.execute("DELETE FROM tracks WHERE id = ?", (row["id"],))
            removed += 1
        return removed

    def _infer_album_artists(self) -> None:
        """ALBUMARTIST タグが無いアルバムのアーティストを収録曲から補完する。

        リッピング時に ARTIST しか書かれていない音源は珍しくない。そのままだと
        アルバム一覧のアーティスト欄が空欄になり、アーティスト軸のソートも
        効かないため、収録曲のアーティストから推定する。

        * 収録曲のアーティストが 1 人だけ → そのアーティストをアルバムアーティストにする
        * 複数いる → コンピレーション扱いにする（誰か 1 人を代表にはしない）
        """
        rows = self.db.query(
            """
            SELECT al.id                        AS album_id,
                   COUNT(DISTINCT ta.artist_id) AS artist_count,
                   MIN(ta.artist_id)            AS artist_id
            FROM albums al
            JOIN tracks t        ON t.album_id = al.id
            JOIN track_artists ta ON ta.track_id = t.id AND ta.role = 'artist'
            WHERE al.album_artist_id IS NULL
            GROUP BY al.id
            """
        )
        for row in rows:
            if int(row["artist_count"]) == 1:
                self.db.execute(
                    "UPDATE albums SET album_artist_id = ? WHERE id = ?",
                    (row["artist_id"], row["album_id"]),
                )
                self.db.execute(
                    "INSERT OR IGNORE INTO album_artists (album_id, artist_id, position) "
                    "VALUES (?, ?, 0)",
                    (row["album_id"], row["artist_id"]),
                )
            else:
                self.db.execute(
                    "UPDATE albums SET is_compilation = 1 WHERE id = ?", (row["album_id"],)
                )

    def _prune_orphans(self) -> None:
        """曲が 1 つも紐づかなくなったアルバム / アーティスト / ジャンルを削除する。"""
        self.db.execute(
            "DELETE FROM albums WHERE id NOT IN "
            "(SELECT DISTINCT album_id FROM tracks WHERE album_id IS NOT NULL)"
        )
        self.db.execute(
            """
            DELETE FROM artists WHERE id NOT IN (SELECT artist_id FROM track_artists)
              AND id NOT IN (SELECT artist_id FROM album_artists)
              AND id NOT IN (SELECT album_artist_id FROM albums
                             WHERE album_artist_id IS NOT NULL)
            """
        )
        self.db.execute(
            "DELETE FROM genres WHERE id NOT IN (SELECT genre_id FROM track_genres)"
        )

    # ========================================================== アートワーク

    def _scan_artwork(self, dirty_albums: set[int], result: ScanResult, *, full: bool) -> None:
        if full:
            album_ids = [r["id"] for r in self.db.query("SELECT id FROM albums")]
        else:
            missing = self.db.query(
                "SELECT id FROM albums WHERE id NOT IN (SELECT album_id FROM artwork)"
            )
            album_ids = sorted(dirty_albums | {int(r["id"]) for r in missing})

        total = len(album_ids)
        self._set_state(phase="アートワークを生成中", processed=0, total=total)

        for index, album_id in enumerate(album_ids, start=1):
            try:
                if self._sync_album_artwork(album_id, force=full):
                    result.albums_with_artwork += 1
            except Exception as exc:
                log.exception("アートワーク生成に失敗: album_id=%s", album_id)
                result.errors.append(f"artwork album {album_id}: {exc}")
            if index % 10 == 0 or index == total:
                self.db.commit()
                self._progress("アートワークを生成中", index, total)
                self._set_state(processed=index)

    def _sync_album_artwork(self, album_id: int, *, force: bool) -> bool:
        row = self.db.query_one(
            "SELECT path FROM tracks WHERE album_id = ? AND missing = 0 "
            "ORDER BY disc_no, track_no, path LIMIT 1",
            (album_id,),
        )
        if row is None:
            return False

        track_path = self.config.music_dir / row["path"]
        source = load_source(track_path)
        if source is None:
            self.db.execute("DELETE FROM artwork WHERE album_id = ?", (album_id,))
            return False

        existing = self.db.query_one(
            "SELECT id, source_hash FROM artwork WHERE album_id = ?", (album_id,)
        )
        digest = source.digest
        if existing is not None and existing["source_hash"] == digest and not force:
            return True

        tiers = self.artwork_cache.generate(source, force=force)
        if not tiers:
            return False

        if existing is None:
            cursor = self.db.execute(
                """
                INSERT INTO artwork
                    (album_id, source_kind, source_path, source_hash, src_width, src_height)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (album_id, source.kind, source.source_path, digest,
                 source.width, source.height),
            )
            artwork_id = int(cursor.lastrowid)
        else:
            artwork_id = int(existing["id"])
            self.db.execute(
                """
                UPDATE artwork SET source_kind = ?, source_path = ?, source_hash = ?,
                    src_width = ?, src_height = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (source.kind, source.source_path, digest,
                 source.width, source.height, artwork_id),
            )
            self.db.execute("DELETE FROM artwork_files WHERE artwork_id = ?", (artwork_id,))

        for tier in tiers:
            self.db.execute(
                "INSERT OR REPLACE INTO artwork_files "
                "(artwork_id, tier, path, width, height, bytes) VALUES (?, ?, ?, ?, ?, ?)",
                (artwork_id, tier.tier, tier.path, tier.width, tier.height, tier.bytes),
            )
        return True

    # ============================================================ ラウドネス

    def _scan_loudness(
        self, dirty_albums: set[int], result: ScanResult, *, full: bool, remeasure: bool = False
    ) -> None:
        """未計測の曲だけラウドネスを計測する。

        計測は 1 曲あたり数秒かかるため、``full`` 再スキャンでも既に値を持つ曲は
        測り直さない（18,000 曲規模で全曲再計測すると数時間かかるため）。
        本当に測り直したい場合だけ ``remeasure=True`` を指定する。
        """
        del dirty_albums  # 計測済みかどうかだけで判断する
        scanner = LoudnessScanner(
            enabled=bool(self.settings.get("rsgain_enabled")),
            concurrency=int(self.settings.get("loudness_concurrency") or 0),
            true_peak=bool(self.settings.get("loudness_true_peak")),
        )
        if not scanner.available:
            if self.settings.get("rsgain_enabled"):
                log.warning(
                    "rsgain も FFmpeg も見つからないため、ラウドネス計測をスキップします"
                )
            return

        if remeasure:
            rows = self.db.query(
                "SELECT id, path, album_id FROM tracks WHERE album_id IS NOT NULL"
            )
        else:
            rows = self.db.query(
                "SELECT id, path, album_id FROM tracks "
                "WHERE album_id IS NOT NULL AND gain_scanned_at IS NULL"
            )
        if not rows:
            log.info("ラウドネス計測: 未計測の曲はありません")
            return

        by_album: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_album[int(row["album_id"])].append(row)

        # 進捗はアルバム数ではなく曲数で出す（大規模ライブラリで残量が読めるように）
        total = len(rows)
        done = 0
        started = time.monotonic()
        self._set_state(phase=f"ラウドネスを計測中 ({scanner.backend})",
                        processed=0, total=total)
        log.info(
            "ラウドネス計測: %d 曲 / %d アルバム (backend=%s)",
            total, len(by_album), scanner.backend,
        )

        for tracks in by_album.values():
            paths = [self.config.music_dir / t["path"] for t in tracks]
            measured = scanner.scan_album(paths)
            album_gain = measured.album

            for track in tracks:
                abs_path = str(self.config.music_dir / track["path"])
                track_result = measured.tracks.get(abs_path)
                if track_result is None:
                    continue
                self.db.execute(
                    """
                    UPDATE tracks SET track_gain_db = ?, track_peak = ?,
                        album_gain_db = ?, album_peak = ?,
                        gain_scanned_at = datetime('now')
                    WHERE id = ?
                    """,
                    (
                        track_result.gain_db, track_result.peak,
                        album_gain.gain_db if album_gain else track_result.gain_db,
                        album_gain.peak if album_gain else track_result.peak,
                        track["id"],
                    ),
                )
                result.tracks_measured += 1

            done += len(tracks)
            self.db.commit()
            self._progress(_loudness_phase_label(started, done, total), done, total)
            self._set_state(processed=done)

        elapsed = time.monotonic() - started
        if result.tracks_measured:
            log.info(
                "ラウドネス計測 完了: %d 曲 / %.0f 秒 (%.1f 曲/分)",
                result.tracks_measured, elapsed,
                result.tracks_measured / max(elapsed / 60, 0.001),
            )

    # ============================================================ 進捗の保存

    def _set_state(self, **fields: Any) -> None:
        if fields.pop("started_at", False):
            fields["started_at"] = "__now__"
        if fields.pop("finished_at", False):
            fields["finished_at"] = "__now__"

        assignments: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if value == "__now__":
                assignments.append(f"{key} = datetime('now')")
            else:
                assignments.append(f"{key} = ?")
                params.append(value)
        if not assignments:
            return
        self.db.execute(f"UPDATE scan_state SET {', '.join(assignments)} WHERE id = 1", params)
        self.db.commit()


def _album_key(
    album: str, album_artist: str, mbid: str | None, directory: str
) -> str:
    """同名異アルバムを区別しつつ、複数ディスクを 1 アルバムにまとめるキー。"""
    if mbid:
        return f"mb:{mbid}"
    album_norm = unicodedata.normalize("NFKC", album).casefold().strip()
    if album_norm and album_artist:
        artist_norm = unicodedata.normalize("NFKC", album_artist).casefold().strip()
        return f"aa:{album_norm}|{artist_norm}"
    if album_norm:
        # アルバムアーティストが無い場合はディレクトリで区別する
        return f"al:{album_norm}|{directory}"
    return f"dir:{directory}"


def _json_array(values: set[int]) -> str:
    import json  # noqa: PLC0415

    return json.dumps(sorted(values))


def _now_text() -> str:
    """SQLite の ``datetime('now')`` と同じ書式の現在時刻（UTC）。"""
    from datetime import UTC, datetime  # noqa: PLC0415

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _loudness_phase_label(started: float, done: int, total: int) -> str:
    """残り時間の目安を含む進捗ラベル。"""
    elapsed = time.monotonic() - started
    if done <= 0 or elapsed <= 0:
        return "ラウドネスを計測中"
    remaining = (total - done) * (elapsed / done)
    if remaining >= 3600:
        eta = f"残り約 {remaining / 3600:.1f} 時間"
    elif remaining >= 60:
        eta = f"残り約 {remaining / 60:.0f} 分"
    else:
        eta = f"残り約 {remaining:.0f} 秒"
    return f"ラウドネスを計測中（{eta}）"
