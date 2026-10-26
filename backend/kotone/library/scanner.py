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
from kotone.library.artwork import ArtworkCache, folder_signature, load_source
from kotone.library.loudness import LoudnessScanner
from kotone.library.tags import TrackTags, is_audio_file, read_tags
from kotone.settings import SettingsStore
from kotone.stats import StatsStore
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
    artwork_updated: int = 0
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
            "artwork_updated": self.artwork_updated,
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
        self.stats = StatsStore(db)
        self._reader = get_reading_generator()
        self._ignore_article = bool(settings.get("ignore_leading_article"))
        self._separators = list(settings.get("artist_separators") or [])
        self._use_tag_gain = bool(settings.get("use_replaygain_tags"))
        # 通常スキャン中に計測するかどうか。measure_loudness() では強制的に有効にする
        self._loudness_enabled = bool(settings.get("rsgain_enabled"))
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
            files, blind_spots = self._walk()
            if not files:
                existing = self.db.scalar("SELECT COUNT(*) FROM tracks") or 0
                if existing:
                    # 本当に全曲消したのではなく、ドライブが外れているとみるほうが
                    # ずっと自然。ここで続けるとライブラリが空になる
                    raise RuntimeError(
                        f"音楽ディレクトリに曲が 1 つも見つかりません（ライブラリには "
                        f"{existing} 曲あります）。ドライブが外れている可能性があるため、"
                        "ライブラリを空にせずに中止しました。"
                    )
            for spot in blind_spots:
                result.errors.append(f"読み込めない場所があります: {spot or '（全体）'}")
            total = len(files)
            self._set_state(phase="タグを読み込み中", total=total)
            log.info("ライブラリスキャン開始: %d ファイル (full=%s)", total, full)

            dirty_albums: set[int] = set()
            seen_paths: set[str] = set()
            tag_started = time.monotonic()

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
                    phase = _phase_with_eta("タグを読み込み中", tag_started, index, total)
                    self._progress(phase, index, total)
                    self._set_state(phase=phase, processed=index)

            self.db.commit()

            result.removed = self._remove_missing(seen_paths, blind_spots)
            self._infer_album_artists()
            self._prune_orphans()
            self.db.commit()

            self._scan_artwork(dirty_albums, result, full=full)
            # 既定ではここで計測しない（設定 rsgain_enabled がオフのため）。
            # 計測は measure_loudness() で任意のタイミングに実行する。
            self._scan_loudness(result)
            self.db.commit()

            # 初回スキャンで取り込んだぶんは「最近追加した」に含めない。
            # ここを起点にして、以降に増えたアルバムだけを新着として扱う。
            self.stats.ensure_recent_baseline()

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

    # ==================================================== ラウドネス計測（単独）

    def measure_loudness(self, *, remeasure: bool = False) -> ScanResult:
        """ラウドネス計測だけを実行する（要件 3.1）。

        通常のライブラリスキャンとは切り離してあるので、曲数の多いライブラリでは
        取り込みを先に済ませて再生できる状態にし、計測は後から好きなときに走らせる
        運用ができる。未計測の曲だけを対象にするため、中断しても続きから再開できる。
        """
        started = time.monotonic()
        result = ScanResult()
        # このメソッドは明示的に呼ばれるので、設定のオン／オフによらず計測する
        self._loudness_enabled = True
        self._set_state(status="running", phase="ラウドネスを計測中", processed=0,
                        total=0, started_at=True, error=None)
        try:
            self._scan_loudness(result, remeasure=remeasure)
            self.db.commit()
        except Exception as exc:
            log.exception("ラウドネス計測が失敗しました")
            self._set_state(status="error", phase="", error=str(exc), finished_at=True)
            raise
        else:
            result.duration_seconds = time.monotonic() - started
            self._set_state(status="idle", phase="", finished_at=True)
        return result

    def calibrate_loudness(self, sample_size: int = 6) -> dict[str, Any]:
        """短いサンプルを実測して、計測速度（実時間比）を較正する。

        所要時間の見積もりは機器と音源の性質で大きく変わる（Pi 4B の実測で
        44.1kHz/16bit なら 164x、48kHz/24bit を含むと 85x 程度）。
        十数時間かかりうる処理を始める前に、数秒の実測で見積もりを実機に合わせる。

        計測結果は保存しない（アルバム単位で揃っていないとアルバムゲインが
        正しく出せないため）。あくまで速度の測定に徹する。
        """
        scanner = LoudnessScanner(
            enabled=True,
            concurrency=int(self.settings.get("loudness_concurrency") or 0),
            true_peak=bool(self.settings.get("loudness_true_peak")),
        )
        if not scanner.available:
            return {"calibrated": False, "reason": "計測に使えるツールがありません"}

        rows = self.db.query(
            "SELECT path, duration FROM tracks "
            "WHERE album_id IS NOT NULL AND duration > 30 AND gain_scanned_at IS NULL "
            "ORDER BY RANDOM() LIMIT ?",
            (max(1, sample_size),),
        )
        if not rows:
            rows = self.db.query(
                "SELECT path, duration FROM tracks WHERE duration > 30 "
                "ORDER BY RANDOM() LIMIT ?",
                (max(1, sample_size),),
            )
        if not rows:
            return {"calibrated": False, "reason": "計測できる曲がありません"}

        paths = [self.config.music_dir / row["path"] for row in rows]
        audio_seconds = sum(float(row["duration"] or 0) for row in rows)

        # 本番と同じ並列度で測らないと見積もりがずれる（rsgain は
        # アルバム単位で並列化するため、1 アルバムだけ測ると 1 コア分の
        # 速度しか出ない）。サンプルを並列数ぶんの束に分けて流す。
        groups = [paths[i :: scanner.concurrency] for i in range(scanner.concurrency)]
        groups = [g for g in groups if g]

        started = time.monotonic()
        results = list(scanner.scan_albums(groups))
        elapsed = time.monotonic() - started

        done = {path: r for result in results for path, r in result.tracks.items()}
        if not done or elapsed <= 0:
            return {"calibrated": False, "reason": "計測に失敗しました"}

        # 実際に結果が返った曲の長さだけを分母にする
        done_seconds = sum(
            float(row["duration"] or 0)
            for row, path in zip(rows, paths, strict=True)
            if str(path) in done
        )
        self.stats.record_loudness_throughput(done_seconds, elapsed)
        log.info(
            "ラウドネス計測の較正: %d 曲 / %.1f 秒 → %.1fx (backend=%s)",
            len(done), elapsed, done_seconds / elapsed, scanner.backend,
        )
        return {
            "calibrated": True,
            "backend": scanner.backend,
            "sample_tracks": len(done),
            "sample_audio_seconds": round(audio_seconds),
            "elapsed_seconds": round(elapsed, 1),
            "throughput": round(self.stats.loudness_throughput, 1),
        }

    # =============================================================== 走査

    def _walk(self) -> tuple[list[tuple[str, Path, os.stat_result]], list[str]]:
        """音楽ディレクトリを走査する。

        2 つ目の返り値は「読めなかった場所」（音楽ディレクトリからの相対パス。
        空文字は全体）。``os.walk`` は既定で読めないディレクトリを黙って飛ばす
        ため、そのままだとドライブの不調で読めなかった曲まで「消えた」ことに
        なり、ライブラリから削除されてしまう。読めなかった場所にある曲は、
        見つからなくても削除しない。
        """
        root = self.config.music_dir
        if not root.is_dir():
            raise FileNotFoundError(f"音楽ディレクトリが見つかりません: {root}")
        try:
            with os.scandir(root) as it:
                next(it, None)
        except OSError as exc:
            raise OSError(f"音楽ディレクトリを読み込めません: {root} ({exc.strerror})") from exc

        blind_spots: list[str] = []

        def on_error(error: OSError) -> None:
            where = Path(error.filename) if error.filename else root
            spot = _relative_or_root(where, root)
            blind_spots.append(spot)
            log.warning("読み込めないディレクトリがあります: %s (%s)", where, error.strerror)

        files: list[tuple[str, Path, os.stat_result]] = []
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=on_error):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            for filename in sorted(filenames):
                if filename.startswith("."):
                    continue
                abs_path = Path(dirpath) / filename
                if not is_audio_file(abs_path):
                    continue
                rel_path = abs_path.relative_to(root).as_posix()
                try:
                    stat = abs_path.stat()
                except OSError:
                    blind_spots.append(rel_path)   # 在るのに読めない。消したのではない
                    continue
                files.append((rel_path, abs_path, stat))
        return files, blind_spots

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

    def _fill_album_gaps(
        self, album_id: int, tags: TrackTags, primary_artist_id: int | None
    ) -> None:
        """既存アルバムの空欄を、後続トラックのタグで補う。

        同じアルバムでも曲によってタグの充実度が違うことがある（ALBUMARTIST が
        一部の曲にしか入っていない等）。先に取り込んだ曲に無かった情報を、
        後から来た曲が持っていれば埋める。
        """
        if tags.year:
            self.db.execute(
                "UPDATE albums SET year = COALESCE(year, ?), "
                "release_date = COALESCE(NULLIF(release_date, ''), ?) WHERE id = ?",
                (tags.year, tags.date, album_id),
            )
        if primary_artist_id is not None:
            self.db.execute(
                "UPDATE albums SET album_artist_id = COALESCE(album_artist_id, ?) "
                "WHERE id = ?",
                (primary_artist_id, album_id),
            )
            self.db.execute(
                "INSERT OR IGNORE INTO album_artists (album_id, artist_id, position) "
                "VALUES (?, ?, 0)",
                (album_id, primary_artist_id),
            )

    def _get_or_create_album(
        self, tags: TrackTags, rel_path: str, album_artist_ids: list[int]
    ) -> int | None:
        directory = str(Path(rel_path).parent.as_posix())
        album_name = tags.album.strip()
        primary_artist_id = album_artist_ids[0] if album_artist_ids else None

        key = _album_key(album_name, tags.musicbrainz_album_id, directory)
        cached = self._album_ids.get(key)
        if cached is not None:
            self._fill_album_gaps(cached, tags, primary_artist_id)
            return cached

        row = self.db.query_one("SELECT id FROM albums WHERE album_key = ?", (key,))
        if row is not None:
            album_id = int(row["id"])
            self._album_ids[key] = album_id
            self._fill_album_gaps(album_id, tags, primary_artist_id)
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

    def _remove_missing(
        self, seen_paths: set[str], blind_spots: list[str] | None = None
    ) -> int:
        """見つからなかった曲をライブラリから消す。読めなかった場所の曲は残す。"""
        self._set_state(phase="削除された曲を整理中")
        removed = 0
        spots = blind_spots or []
        rows = self.db.query("SELECT id, path FROM tracks")
        for row in rows:
            if row["path"] in seen_paths or _is_under(row["path"], spots):
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
            replaced = self._albums_with_replaced_folder_image()
            album_ids = sorted(dirty_albums | replaced | {int(r["id"]) for r in missing})

        total = len(album_ids)
        self._set_state(phase="アートワークを生成中", processed=0, total=total)
        art_started = time.monotonic()

        for index, album_id in enumerate(album_ids, start=1):
            try:
                if self._sync_album_artwork(album_id, force=full, result=result):
                    result.albums_with_artwork += 1
            except Exception as exc:
                log.exception("アートワーク生成に失敗: album_id=%s", album_id)
                result.errors.append(f"artwork album {album_id}: {exc}")
            if index % 10 == 0 or index == total:
                self.db.commit()
                phase = _phase_with_eta("アートワークを生成中", art_started, index, total)
                self._progress(phase, index, total)
                self._set_state(phase=phase, processed=index)

        # 差し替えやアルバムの削除で使われなくなったキャッシュを片付ける
        keep = {r["source_hash"] for r in self.db.query("SELECT source_hash FROM artwork")}
        pruned = self.artwork_cache.prune(keep)
        if pruned:
            log.info("使われなくなったアートワークのキャッシュを %d 件削除しました", pruned)

    def _albums_with_replaced_folder_image(self) -> set[int]:
        """フォルダ画像が前回のスキャンから変わったアルバムを探す。

        曲ファイルが変わっていないアルバムは通常のスキャンで作り直さないため、
        Folder.png だけを差し替えた場合はここで拾う。見るのはフォルダの一覧と
        画像ファイルの属性だけで、画像の中身は読まない。
        """
        rows = self.db.query(
            """
            SELECT t.album_id, t.path, a.folder_sig
            FROM artwork a
            JOIN tracks t ON t.album_id = a.album_id AND t.missing = 0
            ORDER BY t.album_id, t.disc_no, t.track_no, t.path
            """
        )
        # _sync_album_artwork と同じく、アルバムの先頭の曲のフォルダを見る
        first: dict[int, tuple[str, str | None]] = {}
        for row in rows:
            first.setdefault(int(row["album_id"]), (row["path"], row["folder_sig"]))

        total = len(first)
        self._set_state(phase="アートワークの差し替えを確認中", processed=0, total=total)
        replaced: set[int] = set()
        for index, (album_id, (path, stored)) in enumerate(first.items(), start=1):
            signature = folder_signature((self.config.music_dir / path).parent)
            # 読めないフォルダは変わったかどうか分からないので触らない
            if signature is not None and signature != stored:
                replaced.add(album_id)
            if index % 100 == 0 or index == total:
                self._progress("アートワークの差し替えを確認中", index, total)
                self._set_state(processed=index)
        return replaced

    def _sync_album_artwork(
        self, album_id: int, *, force: bool, result: ScanResult | None = None
    ) -> bool:
        row = self.db.query_one(
            "SELECT path FROM tracks WHERE album_id = ? AND missing = 0 "
            "ORDER BY disc_no, track_no, path LIMIT 1",
            (album_id,),
        )
        if row is None:
            return False

        track_path = self.config.music_dir / row["path"]
        # 画像を読む前に取る。読んでいる間に差し替えられても次のスキャンで拾える
        signature = folder_signature(track_path.parent)
        source = load_source(track_path)
        if source is None:
            self.db.execute("DELETE FROM artwork WHERE album_id = ?", (album_id,))
            return False

        existing = self.db.query_one(
            "SELECT id, source_hash, folder_sig FROM artwork WHERE album_id = ?", (album_id,)
        )
        digest = source.digest
        if existing is not None and existing["source_hash"] == digest and not force:
            if existing["folder_sig"] != signature:
                # 画像は同じ（埋め込み画像が優先されている、属性だけ変わった等）
                self.db.execute(
                    "UPDATE artwork SET folder_sig = ? WHERE id = ?",
                    (signature, existing["id"]),
                )
            return True

        tiers = self.artwork_cache.generate(source, force=force)
        if not tiers:
            return False

        if existing is None:
            cursor = self.db.execute(
                """
                INSERT INTO artwork
                    (album_id, source_kind, source_path, source_hash, src_width, src_height,
                     folder_sig)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (album_id, source.kind, source.source_path, digest,
                 source.width, source.height, signature),
            )
            artwork_id = int(cursor.lastrowid)
        else:
            artwork_id = int(existing["id"])
            self.db.execute(
                """
                UPDATE artwork SET source_kind = ?, source_path = ?, source_hash = ?,
                    src_width = ?, src_height = ?, folder_sig = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (source.kind, source.source_path, digest,
                 source.width, source.height, signature, artwork_id),
            )
            self.db.execute("DELETE FROM artwork_files WHERE artwork_id = ?", (artwork_id,))
            if existing["source_hash"] != digest and result is not None:
                result.artwork_updated += 1

        for tier in tiers:
            self.db.execute(
                "INSERT OR REPLACE INTO artwork_files "
                "(artwork_id, tier, path, width, height, bytes) VALUES (?, ?, ?, ?, ?, ?)",
                (artwork_id, tier.tier, tier.path, tier.width, tier.height, tier.bytes),
            )
        return True

    # ============================================================ ラウドネス

    def _scan_loudness(
        self, result: ScanResult, *, remeasure: bool = False
    ) -> None:
        """未計測の曲だけラウドネスを計測する。

        計測は 1 曲あたり数秒かかるため、``full`` 再スキャンでも既に値を持つ曲は
        測り直さない（18,000 曲規模で全曲再計測すると数時間かかるため）。
        本当に測り直したい場合だけ ``remeasure=True`` を指定する。
        """
        scanner = LoudnessScanner(
            enabled=self._loudness_enabled,
            concurrency=int(self.settings.get("loudness_concurrency") or 0),
            true_peak=bool(self.settings.get("loudness_true_peak")),
        )
        if not scanner.available:
            if self._loudness_enabled:
                log.warning(
                    "rsgain も FFmpeg も見つからないため、ラウドネス計測をスキップします"
                )
            return

        if remeasure:
            rows = self.db.query(
                "SELECT id, path, album_id, duration FROM tracks WHERE album_id IS NOT NULL"
            )
        else:
            rows = self.db.query(
                "SELECT id, path, album_id, duration FROM tracks "
                "WHERE album_id IS NOT NULL AND gain_scanned_at IS NULL"
            )
        if not rows:
            log.info("ラウドネス計測: 未計測の曲はありません")
            return

        # 実測スループット（音源の秒数 / 実時間）の算出に使う
        measured_audio_seconds = 0.0

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

        # アルバム単位でまとめて渡し、並列化はバックエンドの都合に任せる。
        # 結果は入力順に 1 アルバムずつ返るので、その都度 DB に書ける
        # （途中で電源が落ちても、そこまでの計測は残る）。
        grouped = list(by_album.values())
        paths_per_album = [
            [self.config.music_dir / t["path"] for t in tracks] for tracks in grouped
        ]

        for tracks, measured in zip(
            grouped, scanner.scan_albums(paths_per_album), strict=False
        ):
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
                measured_audio_seconds += float(track.get("duration") or 0)

            done += len(tracks)
            self.db.commit()
            phase = _phase_with_eta("ラウドネスを計測中", started, done, total)
            self._progress(phase, done, total)
            self._set_state(phase=phase, processed=done)

            # 長時間の処理なので、途中で中断されても次回の見積もりが実機に
            # 即したものになるよう、定期的にスループットを記録しておく
            elapsed_so_far = time.monotonic() - started
            if measured_audio_seconds > 0 and elapsed_so_far > 60 and done % 200 < len(tracks):
                self.stats.record_loudness_throughput(measured_audio_seconds, elapsed_so_far)

        elapsed = time.monotonic() - started
        if result.tracks_measured:
            log.info(
                "ラウドネス計測 完了: %d 曲 / %.0f 秒 (%.1f 曲/分)",
                result.tracks_measured, elapsed,
                result.tracks_measured / max(elapsed / 60, 0.001),
            )
            # 次回の所要時間の見積もりに使うため、実測スループットを残す
            self.stats.record_loudness_throughput(measured_audio_seconds, elapsed)

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


def _album_key(album: str, mbid: str | None, directory: str) -> str:
    """アルバムを一意に識別するキー。

    **タグの有無で形を変えてはいけない。** 以前は ALBUMARTIST があるときだけ
    ``aa:名前|アーティスト`` を使っていたが、同じフォルダ内に ALBUMARTIST を
    持つ曲と持たない曲が混在すると 1 枚のアルバムが 2 つに割れてしまった
    （実ライブラリで 25 ディレクトリが該当）。

    そこで、曲ごとに変わりうる情報はキーに含めず、
    「フォルダ + アルバム名」だけで決める。アルバムアーティストは
    スキャン後に :meth:`LibraryScanner._infer_album_artists` で補完する。

    なお複数ディスクを別フォルダ（CD1/CD2 等）に分けている場合は別アルバムに
    なる。1 フォルダにまとめるか、DISCNUMBER タグを使う運用を推奨する。
    """
    if mbid:
        return f"mb:{mbid}"
    album_norm = unicodedata.normalize("NFKC", album).casefold().strip()
    if album_norm:
        return f"al:{album_norm}|{directory}"
    return f"dir:{directory}"


def _json_array(values: set[int]) -> str:
    import json  # noqa: PLC0415

    return json.dumps(sorted(values))


def _now_text() -> str:
    """SQLite の ``datetime('now')`` と同じ書式の現在時刻（UTC）。"""
    from datetime import UTC, datetime  # noqa: PLC0415

    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _phase_with_eta(label: str, started: float, done: int, total: int) -> str:
    """進捗ラベルに残り時間の目安を添える。

    これまでの平均速度から単純に外挿する。曲数の多いライブラリでは
    「あとどれくらいか」が分からないと待てないため、全フェーズで出す。
    """
    elapsed = time.monotonic() - started
    if done <= 0 or elapsed <= 0 or total <= done:
        return label
    remaining = (total - done) * (elapsed / done)
    return f"{label}（{_format_remaining(remaining)}）"


def _format_remaining(seconds: float) -> str:
    if seconds >= 3600:
        return f"残り約 {seconds / 3600:.1f} 時間"
    if seconds >= 60:
        return f"残り約 {seconds / 60:.0f} 分"
    return f"残り約 {seconds:.0f} 秒"


def _relative_or_root(path: Path, root: Path) -> str:
    """音楽ディレクトリからの相対パス（音楽ディレクトリそのものは空文字）。"""
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return ""
    return "" if rel == "." else rel


def _is_under(path: str, spots: list[str]) -> bool:
    """``path`` が、読めなかった場所のどれかの中にあるか（空文字は全体）。"""
    return any(spot == "" or path == spot or path.startswith(spot + "/") for spot in spots)
