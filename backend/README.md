# KOTONE バックエンド

Python / FastAPI 製。MPD の制御、タグ DB（SQLite）、アートワークキャッシュ、
ラウドネス補正を担当します。

## 開発環境

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Windows では `.venv\Scripts\pip`。

## 起動

```bash
KOTONE_MUSIC_DIR=/path/to/music \
KOTONE_DATA_DIR=./data \
.venv/bin/python -m kotone
```

`http://localhost:8088/api/docs` で Swagger UI が開きます。
MPD が動いていなくても起動し、再生系 API だけが 503 を返します。

## テスト

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

MPD を必要とするテストはありません（テスト用 config は空きポートを指し、
未接続時の挙動を確認します）。

## モジュール構成

| モジュール | 役割 |
|---|---|
| `config.py` | デプロイ設定（パス・ポート・MPD 接続先）。環境変数 `KOTONE_*` |
| `settings.py` | ユーザー設定（SQLite の `settings` テーブル）。UI から変更する項目 |
| `db/` | SQLite 接続とバージョン管理されたマイグレーション |
| `text/` | 読み仮名生成（fugashi）とソートキー・頭文字の算出 |
| `library/tags.py` | mutagen によるタグ読み出しの正規化 |
| `library/artists.py` | ARTIST タグの分割と、区切り文字の調査 |
| `library/artwork.py` | アートワークの取得と 3 階層 PNG キャッシュの生成 |
| `library/loudness.py` | rsgain / FFmpeg によるラウドネス計測 |
| `library/scanner.py` | ライブラリスキャンの全体制御 |
| `repository.py` | 一覧・ソート・検索・スコープ解決のクエリ |
| `mpdclient.py` | MPD への非同期接続（再接続と idle イベント購読） |
| `playback.py` | 再生スコープ、リピート/シャッフル/ランダム、ラウドネス補正の適用 |
| `playlists.py` | プレイリスト管理と m3u 入出力 |
| `editing.py` | タグ・読み・アーティスト紐付けの手動修正 |
| `api/` | REST / WebSocket ルーター |
| `context.py` | 各サービスの組み立てとライフサイクル |
| `cli.py` | 保守用コマンド（`kotone-cli`） |

設計判断の背景は [`../docs/design-notes.md`](../docs/design-notes.md) を参照してください。

## マイグレーションの追加

`kotone/db/migrations/` に `NNNN_説明.sql` を追加します（番号は連番）。
起動時と `kotone-cli migrate` で未適用分だけが順に適用されます。
**前方向のみ**で、ロールバックは用意していません。

## 設定項目の追加

`settings.py` の `SETTING_SPECS` に 1 行足すだけで、API と設定画面の両方に
自動で現れます。
