"""``kotone-cli`` — 保守用のコマンドラインツール。

    kotone-cli migrate            DB マイグレーションを適用
    kotone-cli scan [--full]      ライブラリスキャン
    kotone-cli analyze-artists    ARTIST タグの区切り文字を調査（要件 3.6）
    kotone-cli rebuild-readings   読み仮名とソートキーを作り直す
    kotone-cli backup             SQLite DB のバックアップを取る
    kotone-cli stats              ライブラリの統計を表示
    kotone-cli settings [k v]     設定の表示・変更
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from kotone import __version__
from kotone.config import get_config
from kotone.db import Database, apply_migrations, current_version
from kotone.library.artists import CANDIDATE_SEPARATORS, analyze_separators
from kotone.library.scanner import LibraryScanner
from kotone.main import configure_logging
from kotone.repository import LibraryRepository
from kotone.settings import SettingsStore, spec_dicts
from kotone.text import get_reading_generator, make_sort_fields


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kotone-cli", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--version", action="version", version=f"kotone {__version__}")
    parser.add_argument("--log-level", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="DB マイグレーションを適用する")

    scan_parser = sub.add_parser("scan", help="ライブラリスキャンを実行する")
    scan_parser.add_argument("--full", action="store_true",
                             help="変更が無いファイルも読み直す")

    analyze_parser = sub.add_parser(
        "analyze-artists", help="ARTIST タグの区切り文字候補を調べる"
    )
    analyze_parser.add_argument("--examples", type=int, default=5)
    analyze_parser.add_argument("--json", action="store_true")

    sub.add_parser("rebuild-readings", help="読み仮名とソートキーを作り直す")
    sub.add_parser("backup", help="SQLite DB のバックアップを取る")
    sub.add_parser("stats", help="ライブラリの統計を表示する")

    settings_parser = sub.add_parser("settings", help="設定の表示・変更")
    settings_parser.add_argument("key", nargs="?")
    settings_parser.add_argument("value", nargs="?")

    args = parser.parse_args(argv)
    config = get_config()
    configure_logging(args.log_level or config.log_level)
    config.ensure_dirs()

    db = Database(config.db_path)
    try:
        apply_migrations(db.conn)
        return _dispatch(args, db, config)
    finally:
        db.close()


def _dispatch(args: argparse.Namespace, db: Database, config) -> int:  # noqa: ANN001
    settings = SettingsStore(db)
    repo = LibraryRepository(db)

    if args.command == "migrate":
        print(f"スキーマバージョン: {current_version(db.conn)}")
        return 0

    if args.command == "scan":
        scanner = LibraryScanner(db, config, settings, progress=_print_progress)
        result = scanner.scan(full=args.full)
        print()
        print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "analyze-artists":
        return _analyze_artists(db, config, settings, args)

    if args.command == "rebuild-readings":
        return _rebuild_readings(db, settings)

    if args.command == "backup":
        return _backup(config)

    if args.command == "stats":
        print(json.dumps(
            {"library": repo.stats(), "scan": repo.scan_state()},
            ensure_ascii=False, indent=2, default=str,
        ))
        return 0

    if args.command == "settings":
        return _settings(settings, args)

    return 1


# ------------------------------------------------------------------ 各処理


def _print_progress(phase: str, processed: int, total: int) -> None:
    if total <= 0:
        return
    percent = processed * 100 // total
    bar = "#" * (percent // 4)
    sys.stdout.write(f"\r{phase}: {processed}/{total} [{bar:<25}] {percent}%")
    sys.stdout.flush()


def _analyze_artists(db: Database, config, settings: SettingsStore, args) -> int:  # noqa: ANN001
    """スキャン済みならば DB から、未スキャンならファイルから ARTIST 値を集める。"""
    names = [r["name"] for r in db.query("SELECT name FROM artists")]
    source = "タグ DB"
    if not names:
        source = "音源ファイル"
        names = list(_collect_artist_values(config.music_dir))

    reports = analyze_separators(names, CANDIDATE_SEPARATORS, max_examples=args.examples)
    payload = {
        "source": source,
        "current": settings.get("artist_separators"),
        "values_examined": len(names),
        "candidates": [
            {"separator": r.separator, "count": r.count, "examples": r.examples}
            for r in reports
        ],
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"調査対象: {source} / {len(names)} 件")
    print(f"現在の設定: {payload['current']}")
    if not reports:
        print("区切り文字の候補は見つかりませんでした。")
        return 0
    print()
    print(f"{'区切り':<14}{'件数':>8}  例")
    print("-" * 72)
    for report in reports:
        example = report.examples[0] if report.examples else ""
        print(f"{report.separator!r:<14}{report.count:>8}  {example}")
    print()
    print("※ アーティスト名自体に含まれうる区切り（',' や '&'）は既定では使いません。")
    print("   設定するには: kotone-cli settings artist_separators '[\";\", \" / \"]'")
    return 0


def _collect_artist_values(music_dir: Path):  # noqa: ANN201
    from kotone.library.tags import is_audio_file, read_tags  # noqa: PLC0415

    for path in sorted(music_dir.rglob("*")):
        if not path.is_file() or not is_audio_file(path):
            continue
        tags = read_tags(path)
        if tags is None:
            continue
        yield from tags.artists
        yield from tags.album_artists


def _rebuild_readings(db: Database, settings: SettingsStore) -> int:
    """読み仮名とソートキーを作り直す（形態素解析器を後から入れた場合など）。

    ユーザーが手動修正した行（``reading_locked = 1``）はそのまま残す。
    """
    reader = get_reading_generator()
    ignore_article = bool(settings.get("ignore_leading_article"))
    total = 0
    for table, column in (("artists", "name"), ("albums", "name"),
                          ("genres", "name"), ("tracks", "title")):
        rows = db.query(f"SELECT id, {column} AS label FROM {table} WHERE reading_locked = 0")
        for row in rows:
            reading = reader.reading(row["label"])
            sort_key, initial, group = make_sort_fields(
                row["label"], reading, ignore_leading_article=ignore_article
            )
            db.execute(
                f"UPDATE {table} SET reading = ?, sort_key = ?, initial = ?, "
                "initial_group = ? WHERE id = ?",
                (reading, sort_key, initial, group, row["id"]),
            )
        db.commit()
        print(f"{table}: {len(rows)} 件を更新しました")
        total += len(rows)
    print(f"合計 {total} 件（形態素解析器: {'あり' if reader.available else 'なし'}）")
    return 0


def _backup(config) -> int:  # noqa: ANN001
    if not config.db_path.exists():
        print("DB がまだありません。", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    target = config.backup_dir / f"library-{stamp}.sqlite3"
    target.parent.mkdir(parents=True, exist_ok=True)
    # WAL を含めて整合性のあるコピーを取るため sqlite のバックアップ API を使う
    import sqlite3  # noqa: PLC0415

    source = sqlite3.connect(str(config.db_path))
    destination = sqlite3.connect(str(target))
    with destination:
        source.backup(destination)
    source.close()
    destination.close()
    print(f"バックアップを作成しました: {target}")

    # 古いバックアップは 10 世代まで残す
    backups = sorted(config.backup_dir.glob("library-*.sqlite3"))
    for old in backups[:-10]:
        old.unlink(missing_ok=True)
    return 0


def _settings(settings: SettingsStore, args) -> int:  # noqa: ANN001
    if args.key is None:
        values = settings.all()
        width = max(len(k) for k in values) if values else 10
        for key, value in sorted(values.items()):
            print(f"{key:<{width}}  {json.dumps(value, ensure_ascii=False)}")
        return 0

    if args.value is None:
        print(json.dumps(settings.get(args.key), ensure_ascii=False))
        return 0

    try:
        parsed = json.loads(args.value)
    except json.JSONDecodeError:
        parsed = args.value
    try:
        result = settings.set(args.key, parsed)
    except (KeyError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        known = ", ".join(s["key"] for s in spec_dicts())
        print(f"設定できるキー: {known}", file=sys.stderr)
        return 1
    print(f"{args.key} = {json.dumps(result, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
