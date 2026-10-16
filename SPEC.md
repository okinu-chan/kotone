# KOTONE 仕様書（SPEC）

この文書は、KOTONE をまったく知らない人（別の AI アシスタントを含む）が、
**いまのコードが何をしているか**を把握できるようにまとめたものです。

- **書いてあるのは、リポジトリ内のコード・設定ファイルで確認できたことだけです。**
  コード以外を根拠にした記述は、そのことを明記しています（7.4 節）。
- 要件（こうしたい）は [docs/requirements.md](docs/requirements.md) にあります。
  要件とコードが食い違う場合は、この文書がコードの実態を表します。
- [docs/design-notes.md](docs/design-notes.md) には古くなった記述が含まれます（7.5 節）。
- 確認した時点: 2026-09-25（この文書を追加したコミット）
- リポジトリ: <https://github.com/okinu-chan/kotone>（公開、MIT License）
- **更新の決まり:** コードを変えたら、同じコミットで SPEC.md と
  [CHANGELOG.md](CHANGELOG.md) も更新します。

---

## 目次

1. [目的](#1-目的)
2. [想定ハードウェア・OS](#2-想定ハードウェアos)
3. [全体構成](#3-全体構成)
4. [機能一覧と操作方法](#4-機能一覧と操作方法)
5. [設定項目](#5-設定項目)
6. [インストール・起動・更新](#6-インストール起動更新)
7. [既知の不具合と未実装の機能](#7-既知の不具合と未実装の機能)
8. [設計判断とその理由](#8-設計判断とその理由)

---

## 1. 目的

Raspberry Pi 上で動く、日本語ネイティブの音楽プレイヤーです。PC・スマートフォン・
タブレットのブラウザから操作します。目標は Sony NW-ZX300 のような軽快な操作感です
（`README.md`、`docs/requirements.md` 1.2）。

- 再生エンジンは MPD（Music Player Daemon）です。KOTONE は MPD を制御する
  バックエンドと、ブラウザで動く画面（SPA）で構成されます。
- 日本語の曲名・アーティスト名を、読み仮名にもとづく五十音順で並べます。
- 家庭内 LAN での利用が前提で、**認証の仕組みはありません**（`main.py` に認証の
  処理は無く、`docs/install.md` にも外部公開しないよう書かれています）。
- 名称は **KOTONE** です（2026-09-25 に確定）。パッケージ名・コマンド名・パス・
  サービス用ユーザー名・URL などの識別子には、小文字の `kotone` を使います
  （`kotone/__init__.py`）。

## 2. 想定ハードウェア・OS

### 2.1 インストーラが前提とするもの（`install.sh`）

| 項目 | 内容 |
|---|---|
| OS | Debian 系（`apt-get` が無ければ中止）。想定は公式 Raspberry Pi OS（Lite） |
| アーキテクチャ | `arm64` / `armhf` / `amd64`。それ以外は警告だけ出して続行 |
| Python | 3.11 以上（未満なら中止） |
| 音の出口 | USB DAC（ALSA）または Bluetooth（bluez-alsa 経由の A2DP） |
| 音楽の置き場所 | 任意のディレクトリ（`--music-dir`。既定は `/var/lib/kotone/music`） |

### 2.2 開発・動作確認に使っている実機

以下は Pi 上のコマンド出力で確認した構成です（コードの外の情報）。

| 項目 | 内容 |
|---|---|
| 本体 | Raspberry Pi 4B |
| OS | Debian GNU/Linux 13 (trixie)、aarch64 |
| ソフトウェア | Python 3.13.5、MPD 0.24.4、bluez-alsa 4.3.1 |
| 音楽用ドライブ | USB 接続の SSD（ext4、`/mnt/music` にマウント） |
| USB DAC | Topping E50 II |
| Bluetooth 機器 | JBL Tour Pro 3、Technics EAH-AZ100、Sony NW-ZX300（Bluetooth 受信）、Amazon Echo Dot |
| 操作端末 | Windows PC のブラウザ、iPhone 15 Pro の Safari |

---

## 3. 全体構成

### 3.1 プロセスと音の経路

```
ブラウザ（Svelte の SPA）
   │  REST（/api/...）+ WebSocket（/api/ws）
   ▼
kotone-backend（Python / FastAPI / uvicorn。ポート 8088、ワーカー 1）
   │  MPD プロトコル（localhost:6600）      ├─ SQLite（/var/lib/kotone/library.sqlite3）
   ▼                                         ├─ D-Bus（BlueZ・bluez-alsa の操作）
MPD                                          └─ rsgain / ffmpeg（ラウドネス計測・画像抽出）
   ├─ 出力「USB DAC」 → ALSA → USB DAC
   └─ 出力「Bluetooth」→ ALSA（bluealsa）→ BlueZ → BT 機器
```

常駐するもの（systemd）:

| ユニット | 役割 | 定義 |
|---|---|---|
| `kotone.service` | バックエンド本体。ユーザー `kotone` で動く | `systemd/kotone.service` |
| `mpd.service` | 再生エンジン | Debian のパッケージ |
| `bluealsa.service` | Bluetooth の音声を ALSA に見せる。KOTONE 用の起動オプションを上書きする | `install.sh` が drop-in を書く |
| `kotone-storage-recover.timer` | 音楽用ドライブの見回り（2 分おき）。root で動く | `systemd/kotone-storage-recover.timer` |
| `kotone-storage-recover@.service` | 音楽用ドライブの自動復旧（udev とタイマーから起動） | `systemd/kotone-storage-recover@.service` |

`kotone.service` の主な指定: `Restart=on-failure`、`Nice=5`、`LimitNOFILE=8192`、
`ProtectSystem=strict`（書き込みはデータ領域だけ許可）、`NoNewPrivileges=true`。

### 3.2 使用言語・ライブラリ

**バックエンド**（`backend/pyproject.toml`、Python 3.11 以上）

| ライブラリ | 用途 |
|---|---|
| FastAPI / uvicorn[standard] | REST・WebSocket の API |
| python-multipart | m3u インポートのファイル受け取り |
| python-mpd2 | MPD との通信（asyncio 版） |
| mutagen | タグの読み取り |
| Pillow | アートワークの縮小・PNG 化 |
| pydantic / pydantic-settings | 入力検証・環境設定の読み込み |
| fugashi / unidic-lite | 形態素解析による読み仮名の生成 |
| dbus-fast | BlueZ・bluez-alsa の D-Bus 操作 |

開発用: pytest、pytest-asyncio、httpx、ruff（1 行 100 文字）。

**フロントエンド**（`frontend/package.json`）: Svelte 5（runes）と Vite 6 です。
ビルド済みの `frontend/dist/` をリポジトリに含めるので、**Pi 上でのビルドは不要**です。

**外部コマンド**（`install.sh` が apt で入れるもの）: `mpd`、`mpc`、`ffmpeg`、
`alsa-utils`、`bluez`、`bluez-alsa-utils`、`rfkill`、`git` ほか。
`rsgain` は apt にあれば入れます（無い環境では `--with-rsgain` 指定時だけ GitHub から取得）。

**データベース**: SQLite（WAL モード。`kotone/db/database.py`）。

### 3.3 ディレクトリ構成

```
.
├── backend/                 バックエンド（Python パッケージ kotone）
│   ├── kotone/
│   │   ├── __main__.py      kotone-backend（uvicorn の起動）
│   │   ├── main.py          FastAPI アプリの組み立て・フロントエンドの配信
│   │   ├── context.py       共有オブジェクト（AppContext）とスキャン管理（ScanManager）
│   │   ├── config.py        デプロイ設定（環境変数 KOTONE_*）
│   │   ├── settings.py      利用者設定（SQLite に保存。画面から変更）
│   │   ├── playback.py      再生制御（スコープ・リピート・ランダム・音量・ラウドネス補正）
│   │   ├── mpdclient.py     MPD 接続（再接続・idle 購読）
│   │   ├── outputs.py       出力先（USB DAC / Bluetooth）の切り替え
│   │   ├── bluetooth.py     Bluetooth 機器の管理（BlueZ / bluez-alsa を D-Bus で操作）
│   │   ├── storage.py       音楽用ドライブの見張り
│   │   ├── repository.py    ライブラリの検索・並べ替えクエリ
│   │   ├── playlists.py     プレイリスト（m3u の入出力を含む）
│   │   ├── editing.py       タグ・読み仮名の手動修正（DB のみ）
│   │   ├── stats.py         実測値の記録（計測速度・「最近追加」の起点）
│   │   ├── events.py        WebSocket へ流すイベントバス
│   │   ├── cli.py           kotone-cli
│   │   ├── api/             REST / WebSocket のルーター
│   │   ├── db/              SQLite 接続とマイグレーション（migrations/NNNN_*.sql）
│   │   ├── library/         スキャン・タグ・アーティスト分割・アートワーク・ラウドネス計測
│   │   └── text/            読み仮名の生成・ソートキーと頭文字
│   ├── tests/               pytest
│   └── pyproject.toml
├── frontend/                Web UI（Svelte 5 + Vite）
│   ├── src/                 App.svelte、lib/（api・ストア・ルーター・components/）
│   └── dist/                ビルド済みファイル（リポジトリに含める）
├── config/
│   ├── mpd.conf.template    install.sh が /etc/mpd.conf に展開する
│   └── 99-kotone-storage.rules   udev ルール（ドライブの再接続で自動復旧を起動）
├── systemd/                 kotone.service、ドライブ自動復旧のサービスとタイマー
├── scripts/
│   ├── kotone-update        更新コマンド（/usr/local/bin に入る）
│   ├── kotone-cli-wrapper   kotone-cli のラッパー（/usr/local/bin/kotone-cli）
│   └── kotone-storage-recover   ドライブの自動復旧（/usr/local/sbin に入る）
├── docs/                    要件定義書・設計メモ・インストール手順・アートワーク推奨仕様
├── install.sh / uninstall.sh
├── SPEC.md / CHANGELOG.md / README.md / LICENSE（MIT）
```

**インストール後の配置**（`install.sh`）

| パス | 内容 | 更新時 |
|---|---|---|
| `/opt/kotone` | アプリ本体（git 管理） | `kotone-update` で更新 |
| `/opt/kotone/backend/.venv` | Python 仮想環境 | `kotone-update` で更新 |
| `/var/lib/kotone/library.sqlite3` | タグ DB・プレイリスト・設定 | 保持 |
| `/var/lib/kotone/artwork/` | アートワークのキャッシュ | 保持 |
| `/var/lib/kotone/backups/` | DB のバックアップ（10 世代まで。`cli.py`） | 保持 |
| `/var/lib/kotone/mpd/` | MPD のデータベース・状態 | 保持 |
| `/etc/kotone/kotone.env` | デプロイ設定（3.6 節） | 保持 |
| `/etc/mpd.conf` | MPD の設定（元の設定は `/etc/mpd.conf.kotone-backup`） | 保持 |
| `/run/kotone-storage/status.json` | 自動復旧の状態（root が書き、KOTONE が読む） | 再起動で消える |

**注意:** pip は `kotone` パッケージを仮想環境の `site-packages` に**コピー**します。
`/opt/kotone/backend/kotone/` を書き換えただけでは動作は変わりません
（`kotone-update` は `pip install` し直すので問題ありません）。

### 3.4 データベース

マイグレーションは `backend/kotone/db/migrations/NNNN_*.sql` を番号順に適用します。
適用済みの番号は `schema_version` テーブルに残り、未適用の分だけが走ります。
**前方向だけで、巻き戻しはありません**（`kotone/db/migrations.py`）。
既定では、バックエンドの起動時に自動で適用されます（`auto_migrate`）。

| 番号 | 内容 |
|---|---|
| 0001 | 初期スキーマ |
| 0002 | 実測値の記録（`runtime_stats`） |
| 0003 | 頭文字を五十音の 1 文字単位へ詰め直す |
| 0004 | クロスフェード設定の削除 |
| 0005 | 「最近追加」の起点 |
| 0006 | プレイリストの並び順 |

テーブル: `artists`、`genres`、`albums`、`tracks`、`track_artists`（曲とアーティストの
多対多）、`album_artists`、`track_genres`、`artwork`、`artwork_files`、`playlists`、
`playlist_tracks`、`settings`、`scan_state`、`player_state`、`runtime_stats`。

DB への接続はスレッドごとに 1 本です。スレッドが終わると、その接続を閉じます
（`kotone/db/database.py`）。

### 3.5 API

すべて `/api` の下にあります。API ドキュメント（Swagger UI）は `/api/docs` です。

| 分類 | エンドポイント |
|---|---|
| 再生 | `GET /player`、`POST /player/{play,pause,toggle,stop,next,previous,seek,volume}`、`POST /player/scope`（範囲を読み込んで再生）、`POST /player/tracks`（曲リストを再生）、`POST /player/repeat/{off,one,range}`、`POST /player/order/{normal,shuffle,random}`、`GET /player/queue` |
| ライブラリ | `GET /library/{stats,sorts,albums,formats,artists,tracks,genres,years,search}`、`GET /library/albums/{id}`、`/artists/{id}`、`/tracks/{id}`、`GET /library/initials/{albums,artists,tracks}` |
| アートワーク | `GET /artwork/{album_id}`（`?tier=small|medium|large`、既定 `medium`）、`GET /artwork/{album_id}/info` |
| プレイリスト | `GET/POST /playlists`、`POST /playlists/reorder`、`GET/PATCH/DELETE /playlists/{id}`、`POST /playlists/{id}/{tracks,remove,move,reorder}`、`GET /playlists/{id}/export`、`POST /playlists/import` |
| 編集 | `PATCH /edit/{artists,albums,tracks}/{id}`、`POST /edit/artists/merge`、`POST /edit/reading/suggest`、`POST /edit/tracks/{id}/unlock`、`POST /edit/{table}/{id}/unlock-reading` |
| 出力・Bluetooth | `GET /outputs`、`POST /outputs/select`、`GET /bluetooth`、`POST /bluetooth/{power,scan,pair,connect,disconnect,forget}`、`GET /bluetooth/codec` |
| 設定・システム | `GET/PATCH /settings`、`GET /storage`、`GET/POST /scan`、`GET/POST /scan/loudness`、`POST /scan/loudness/calibrate`、`GET /scan/analyze-artists`、`GET /system`、`GET /health` |
| WebSocket | `/api/ws` |

**WebSocket**（`kotone/api/ws.py`）: 接続直後に、再生状態（`player`）・スキャン状態
（`scan`）・音楽用ドライブの状態（`storage`）を 1 度ずつ送ります。以降は
`{"type": "player" | "scan" | "settings" | "storage", "payload": {...}}` の形で
変化を送ります。25 秒ごとに `ping` も送ります。

**画面側の送り直し**（`frontend/src/lib/api.js`）: サーバーに届く前に落ちた要求
（Safari の "Load failed"）は、150 ms 後に 1 度だけ送り直します。対象は、何度送っても
結果が変わらない要求に限ります（読み取り、再生・一時停止・シーク・音量・範囲再生、
リピート・再生順、設定、出力先、Bluetooth の電源・接続・切断・登録解除、
プレイリストの並び順、名前の変更、編集）。曲送り・曲戻し・再生の切り替え、
プレイリストへの追加・削除・移動・作成・削除は送り直しません。

### 3.6 デプロイ設定（`/etc/kotone/kotone.env`）

環境変数 `KOTONE_<項目名>` で指定します（`kotone/config.py`）。

| 項目 | 既定値 | 内容 |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `8088` | 待ち受け |
| `CORS_ORIGINS` | 空 | 開発用。カンマ区切り |
| `MPD_HOST` / `MPD_PORT` / `MPD_PASSWORD` | `localhost` / `6600` / なし | MPD の接続先 |
| `MUSIC_DIR` | `/var/lib/kotone/music` | 音楽ディレクトリ。DB のパスはここからの相対パス |
| `DATA_DIR` | `/var/lib/kotone` | DB・キャッシュ・バックアップの置き場所 |
| `STATIC_DIR` | 自動検出 | ビルド済みフロントエンド。install.sh は `/opt/kotone/frontend/dist` を書く |
| `LOG_LEVEL` | `INFO` | |
| `AUTO_MIGRATE` | 有効 | 起動時にマイグレーションを適用する |

uvicorn の起動オプション（`kotone/__main__.py`）: ワーカー 1、アクセスログなし、
WebSocket の ping 間隔 20 秒・待ち 30 秒、keep-alive 75 秒、終了待ち 5 秒。

### 3.7 MPD の設定（`config/mpd.conf.template`）

- `replaygain "off"`、`mixer_type "software"`（ラウドネス補正は KOTONE が音量で当てるため）
- `auto_update "no"`（ライブラリの更新は KOTONE のスキャンから指示する）
- `audio_buffer_size "4096"`、`gapless_mp3_playback "yes"`、`bind_to_address "localhost"`
- 出力「USB DAC」: ALSA。`auto_resample` / `auto_format` / `auto_channels` を `no`
  （ビットパーフェクト）、`dop "no"`、`device` は指定なし（ALSA の default）
- 出力「Bluetooth」: ALSA の `bluealsa` デバイス。`auto_*` は `yes`、既定は無効
- 入力の `curl` は無効、デコーダは `ffmpeg` を有効

---

## 4. 機能一覧と操作方法

### 4.1 画面構成

下部に常にタブバーがあります（`TabBar.svelte`）。

| タブ | 画面 |
|---|---|
| 再生中 | 再生中画面（`/`） |
| ライブラリ | アルバム・アーティスト・ジャンル・年の一覧（`/library`） |
| プレイリスト | プレイリスト一覧（`/playlists`） |
| 検索 | 横断検索（`/search`） |
| 設定 | 設定とシステム情報（`/settings`） |

- 画面遷移はブラウザの履歴（History API）に積まれます。ブラウザの「戻る」で
  遷移をさかのぼれます（`router.svelte.js`）。一覧のスクロール位置は、戻ったときに復元します。
- 再生中画面以外では、画面の下にミニプレーヤーが出ます（`MiniPlayer.svelte`）。
  内容は再生・一時停止、次の曲、音量で、本体をタップすると再生中画面へ移ります。
- サーバーとの接続が切れている間は、画面上部に「サーバーと再接続中…」と出ます。
- テーマ（ダーク／ライト）と文字の大きさ（小・標準・大）を設定で変えられます。
- ホーム画面に追加してウェブアプリとして開くと、ページはステータスバーの下まで描かれます
  （`index.html` の `viewport-fit=cover` と `black-translucent`）。画面の上端に来る要素
  （`PageHeader.svelte`、再生中画面の `.now` など）は `--safe-top`
  （`env(safe-area-inset-top)`）の分だけ下げています。

### 4.2 再生中画面（`NowPlaying.svelte`）

- **アートワーク:** タップで拡大表示します。元の解像度を超えて拡大はしません（`ArtworkViewer.svelte`）。
- **曲送り・曲戻し（スワイプ）:** アートワークの上を横にスワイプします。
  横に 60 px 以上動き、かつ縦の動きの 1.8 倍を超えたときだけ反応します。
  範囲の終端では左スワイプ（曲送り）を受け付けません（4.10 節）。
- **曲名:** 1 行で表示し、収まらないときだけ横にスクロールします。はみ出している
  ときはタップで全文を表示し、表示中はどこをタップしても閉じます。
- **ダイレクトサーチ:** アーティスト・アルバム・年・ジャンルの各チップをタップすると、
  その値で絞った画面へ移ります。
- **音声情報の行:** コーデック、サンプリングレート／ビット深度を表示します。
  さらに次の 2 つを条件付きで表示します。
  - ラウドネス補正がオンのときだけ、補正値。「アルバム」基準ならアルバム単位の値を出します。
  - Bluetooth 出力のときは、使っているコーデック名。
- **操作ボタン**（左から）:
  - 再生順: 通常再生 → シャッフル（重複なし）→ ランダム（重複あり）の順に切り替え
  - 音量
  - 前の曲、再生／一時停止、次の曲（終端では無効表示）
  - リピート: なし → 範囲内 → 1 曲の順に切り替え
- **音量:** スピーカーのアイコンをタップすると、アイコンの上に縦のバーと数値が出ます。
  もう一度タップで消えます。音量の操作方法が「外部」のときはアイコンごと出しません（`VolumeControl.svelte`）。
- **シークバー:** ドラッグで再生位置を移動します。
- **その他のボタン:**
  - 「このアルバムを最初から再生」
  - 「この曲をプレイリストへ追加」
  - 「再生中のプレイリストを開く」（プレイリストを再生しているときだけ）
- **再生できなかったとき:** 出力を開けなかったなどの理由をトーストで表示します（4.10 節）。

### 4.3 ライブラリ（`LibraryView.svelte`）

- 上部で「アルバム」「アーティスト」「ジャンル」「年」の入口を切り替えます。
- **並べ替え**（`repository.py`）:
  - アルバム: アルバム名・アーティスト・年・ジャンル・追加日
  - アーティスト: 名前・追加日
  - 日本語は読み仮名にもとづく五十音順です（4.11 節）。
- **絞り込み**（シート。`FilterSheet.svelte`）:
  - 音質: ハイレゾ（48 kHz 超、または 16 bit 超）、CD 品質、DSD
  - フォーマット（コーデック）: ライブラリにあるものを表示します
  - **最近追加したアルバム:** 起点より後に追加されたアルバムだけを出します。
    起点は初回スキャンの直後に置くので、最初に取り込んだぶんは含みません（`stats.py`）。
  - 適用中の条件はチップで表示し、個別に外せます。
- **表示形式:** 「サムネイル」（複数列）と「1 列」を切り替えます。ライブラリ・
  アーティスト・ジャンル・年の画面で同じ設定を共有します（`AlbumLayoutToggle.svelte`）。
- **イニシャルサーチ:** 専用ボタンで全画面の表を開きます（`InitialSearch.svelte`）。
  - 「カナ」タブは五十音表（5 列。ゐ・ゑ を含む）、「英数・他」タブは A–Z と `#` です。
  - 各文字に件数を表示します。0 件の文字は押せません。
  - アルバム一覧では、アーティスト名とアルバム名のどちらの頭文字で引くかを切り替えられます。
    既定はアーティスト名で、選んだ方は次回に引き継ぎます。
  - 文字を選ぶと、**一覧を絞り込まずに**その文字の位置までスクロールします
    （頭出し）。該当が無ければその旨をトーストで表示します。
- **タブの「ライブラリ」を押すと:** 絞り込みを解除し、再生中の曲のアルバムの位置へスクロールします。
- 一覧は、条件が同じなら前回の結果を再利用して即座に表示します。

### 4.4 アルバム・アーティスト・ジャンル・年（ダイレクトサーチ）

- **アルバム画面**（`AlbumView.svelte`）: 曲をタップすると、アルバムを範囲にして
  その曲から再生します。「再生」でアルバムの先頭から再生します。
  - 各曲の「⋯」からタグの編集（4.8 節）を開きます。
  - 「プレイリストへ追加」を押すと選択モードになり、各曲の左にチェックボックスが出ます。
    最初は何も選ばれていません。「すべて選択／すべて解除」「やめる」があります。
  - アルバム情報の編集ボタンがあります。
- **アーティスト画面**（`ArtistView.svelte`）: そのアーティストの全曲と、収録アルバムの一覧です。
  - 「すべて再生」「シャッフル再生」があります。
  - 曲をタップすると、その曲のアルバムを範囲にして再生します。そこからさらに別の
    アーティストへ辿れます。
- **ジャンル・年の画面**（`ScopeView.svelte`）: 値の完全一致で絞ったアルバムと曲を出します。「すべて再生」があります。

### 4.5 プレイリスト

- **一覧**（`PlaylistsView.svelte`）:
  - 新規作成と m3u のインポートができます。
  - 左のつまみをドラッグして並べ替えます。マウスでもタッチでも動きます（`lib/reorder.js`）。
  - 「⋯」から名前の変更と削除を行います。削除の前に確認ダイアログを出します。
- **詳細**（`PlaylistView.svelte`）:
  - 「再生」で先頭から、曲をタップするとその曲から再生します。
  - 左のつまみのドラッグで曲順を変えます。
  - 曲ごとの「⋯」メニューは「名前の変更」「プレイリストに追加」「楽曲の削除」の 3 つです。
    「名前の変更」はタグの編集を開きます。「楽曲の削除」は曲名を示して確認します。
  - 「選択」で複数選択モードになります。選んだ曲をまとめて「追加」（別のプレイリストへ）、
    「削除」できます。
  - 「m3u で書き出し」があります。
  - 同じ曲を複数回入れられます。
- **保存:** 操作のたびに自動で保存します。明示的な保存操作はありません（`playlists.py`）。
- **再生中の追従:** 再生中のプレイリストを編集すると、MPD のキューも合わせて組み替えます
  （`PlaybackController.resync_scope`）。
  - 鳴っている曲が残っていれば、その曲は止めずに前後だけ入れ替えます。
  - 鳴っている曲を消した場合は、同じ位置から続けます。
- **m3u:** 書き出しは拡張 m3u（`#EXTM3U`、`#PLAYLIST:名前`、`#EXTINF:秒,アーティスト - 曲名`）です。
  読み込みは、音楽ディレクトリ内のパスをライブラリの曲に照合します。
- **追加のシート**（`AddToPlaylist.svelte`）: 既存のプレイリストを選ぶか、名前を入れて新規作成します。

### 4.6 検索（`SearchView.svelte`）

- 入力の 220 ms 後に、アーティスト・アルバム・曲をそれぞれ最大 30 件ずつ検索します。
  どれも部分一致で、照合する項目は次のとおりです（`repository.py`）。
  - アーティスト: 名前・読み仮名・ソートキー
  - アルバム: アルバム名・読み仮名・ソートキー・アルバムアーティスト名
  - 曲: 曲名・読み仮名・ソートキー・アルバム名
- 曲をタップすると、その曲のアルバムを範囲にしてその曲から再生し、再生中画面へ移ります。
- アーティスト・アルバムをタップすると、それぞれの画面へ移ります。

### 4.7 再生キュー（`QueueView.svelte`、`/queue`）

MPD のキューの中身を表示し、タップした曲を単独で再生します。
**画面内にこの画面へのリンクはありません**（7.1 節）。

### 4.8 タグ・読み仮名の編集

- **曲**（`TagEditor.svelte`）:
  - 曲名・読み仮名・ディスク番号・トラック番号・年・アーティスト（1 行に 1 名）・
    ジャンル（1 行に 1 つ）を直せます。
  - 読みは「自動生成に戻す」で、形態素解析の結果を取り直せます。
  - 「手動修正を解除」で、再スキャン時の自動値に戻します。
- **アルバム**（`AlbumEditor.svelte`）・**アーティスト**（`ArtistEditor.svelte`）: 読み仮名を直せます。
- **編集はタグ DB にだけ保存し、音源ファイルは書き換えません**（`editing.py`）。
  直した項目にはロックを立て、再スキャンで自動値に戻らないようにします。
- アーティストの統合（`POST /edit/artists/merge`）は API だけで、画面はありません（7.2 節）。

### 4.9 設定画面（`SettingsView.svelte`）

上から次の順に並びます。

1. **ライブラリ:** 曲数などの統計、「スキャン」「完全に再スキャン」、前回の結果
2. **出力先**（`AudioOutput.svelte`）: 「USB DAC」「Bluetooth」のどちらか 1 つを選びます
3. **Bluetooth:**
   - 「Bluetooth を使う」（電源）
   - 「高音質コーデックを優先する」
   - 登録済みの機器: 接続中は「◯◯ で接続中」とコーデック名を表示。接続・切断・解除（確認あり）
   - 「機器を探す」（12 秒）で近くの機器を探してペアリング
4. **再生・ラウドネス補正・ラウドネスの計測・ライブラリ・表示**の各設定（5 章）
   - ラウドネス補正には、この機能を知らない人向けの説明文があります。
   - 計測の欄には進捗と「未計測の曲を計測」「すべて測り直す」があります。
     開始前に、予想所要時間と計測方式を確認ダイアログで示します。実測値がまだ無いときは、
     先に 6 曲ほどを試しに測って見積もりを合わせます。
   - アーティストの区切り文字の欄には「実データの区切り文字を調べる」があります
     （区切り文字の候補ごとの件数と例を表示）。
5. **システム:** バージョンとスキーマ番号、MPD の接続状態と出力形式、音楽ディレクトリ、
   データ領域の使用量、外部ツール（rsgain・ffmpeg・形態素解析器）の有無

### 4.10 再生の仕組み（`playback.py`）

**スコープ（再生範囲）:** 再生を始めるとき、範囲（全曲・アルバム・アーティスト・
ジャンル・年・プレイリスト。検索などからの任意の曲リストは「選択した曲」）の
全曲を MPD のキューに読み込みます。

| 設定 | MPD の repeat | single | random | KOTONE の介入 |
|---|---|---|---|---|
| リピートなし | 0 | 0 | ― | 下の「範囲の終端」 |
| 範囲内リピート | 1 | 0 | ― | なし |
| 1 曲リピート | 1 | 1 | ― | なし |
| シャッフル（重複なし） | ― | ― | 1 | なし |
| ランダム（重複あり） | ― | ― | 0 | キューを `[再生中, 次の 1 曲]` に保ち、曲が変わるたびに次の曲を抽選して足す |

ランダムでは、直前の N 曲（設定 `random_avoid_last`）を抽選から外します。

**範囲の終端**（リピートなし・通常再生のとき）:

- 最後の曲で曲送り（ボタン・スワイプ）を受け付けません。画面のボタンも無効に見せます。
- 最後の曲が自然に終わると、範囲の先頭の曲を 0 秒・一時停止の状態で待機させます
  （NW-ZX300 に倣う）。停止の操作（`POST /player/stop`）で止めた場合は、この動きを
  しません（画面に停止ボタンはありません）。

**ラウドネス補正:** 曲が変わるたびに MPD の音量（`setvol`）を計算し直します。

```
実効音量 = 基準音量 × 10^((ゲイン dB + プリアンプ dB) / 20)
基準音量 = 音量スライダーの値（「外部」のときは 100）
```

- ゲインは、設定の「補正の基準」に応じて曲単位かアルバム単位の値を使います。
- クリッピング防止がオンなら、倍率を `1 / ピーク` 以下に抑えます。
- 倍率全体は 0.01〜4.0 に収め、結果の音量は 0〜100 に丸めます。
- 画面に出す音量はスライダーの値で、補正後の値ではありません。
- 曲の変わり目は 0.35 秒かけて音量を移します。スライダー操作は 0.12 秒で移します。
- 再生開始・曲送り・曲戻しでは、**MPD に指示する前に**次の曲の音量を入れておきます。
  - 次の曲は MPD の `nextsong` で求めます。
  - シャッフル中の曲戻しは、戻り先が分からないので先回りしません。
- 音量の操作方法やラウドネス補正の設定を変えると、その場で音量を計算し直します。

**再生開始時のウォームアップ**（USB DAC のとき）: 停止状態から再生するときは、
無音で先に再生します。設定の時間（既定 600 ms）が過ぎたら曲頭へ戻します。
Bluetooth では行いません。出力を開けなかったときは曲頭へ戻しません。

**一時停止からの再開:** 止めた位置から 0.4 秒を超えてずれていたら、止めた位置へ戻します。
再開に失敗したときは戻しません。

**Bluetooth で鳴らし始めるとき:** 接続中のサンプリングレートを読みます。MPD の
Bluetooth 出力は、そのレートに固定します（`allowed_formats`。変換は MPD が行います）。

**再生できなかったとき:** 鳴らし始める前に、MPD の前回のエラーを消します。
新たに出たエラーは、再生状態の `error` として画面に送ります。表示する文面は次の 3 通りです。

- Bluetooth 出力を開けなかったとき: 「Bluetooth 機器へ音を送れませんでした。
  機器との接続を切って、つなぎ直してください。」
- ほかの出力を開けなかったとき: 「「出力名」を開けませんでした。機器の電源と接続を確認してください。」
- それ以外: 「再生できませんでした（MPD のメッセージ）」

**MPD の外で音量が変わったとき:** ほかのクライアントが音量を変えたら、その値を
新しいスライダーの値にします。音量の操作方法が「外部」のときは追随しません。

**MPD との接続:** 切れたら 1 秒から倍々に最長 30 秒の間隔で再接続します（`mpdclient.py`）。

### 4.11 ライブラリスキャン（`library/scanner.py`）

「スキャン」は変更のあったファイルだけを処理します。「完全に再スキャン」は全ファイルを
読み直します。スキャンは同時に 1 本しか走りません。

1. **探索:** 音楽ディレクトリを走査します。
   - 対象の拡張子: `.flac .mp3 .m4a .mp4 .aac .alac .ogg .oga .opus .wav .aiff .aif .aifc
     .wv .ape .wma .mpc .dsf .dff .m4b`
   - 名前が `.` で始まるファイル・フォルダは飛ばします。
2. **タグの取り込み:** mutagen で読みます。DSD（`.dsf` `.dff`）には DSD の印を付けます。
3. **アーティストの分割:**
   - タグが複数値（Vorbis Comment や ID3 の TPE1）なら、そのまま使います。
   - 単一値なら、設定の区切り文字で分割します。
4. **読み仮名とソートキー:**
   - 読みは fugashi + unidic-lite で生成します。
   - ソートキーは比較用の文字列です（`text/sortkey.py`）。NFKC 正規化 → カタカナを
    ひらがなへ → 小書き文字を通常の文字へ → 濁点・半濁点を除く → 長音「ー」を直前の
    母音へ展開 → ラテン文字を小文字化、の順に作ります。
   - 並べるときは `ソートキー → 読み → 名前` の順に比較します。
   - 頭文字は、カナは五十音の 1 文字（あ〜ん、ゐ・ゑ を含む 48 文字）、英字は A–Z、それ以外は `#` です。
   - 手で直した値はロックされ、上書きしません。
5. **アルバムアーティストの補完:** ALBUMARTIST タグが無いアルバムを、収録曲から補います。
6. **削除された曲の整理:** 見つからなくなった曲を DB から消し、曲の無くなった
   アルバム・アーティストも片付けます。
7. **アートワーク:** 変わったアルバムだけ生成します。
   - 取得は次の順で試します。
     - 音源の埋め込み画像
     - 同じフォルダの `cover` / `folder` / `front` / `album` / `albumart` などの画像
     - 名前が合わなければ、フォルダ内で最大の画像
     - それも無ければ、FFmpeg で埋め込み画像を抽出
   - 短辺 300 / 800 / 1400 px の 3 階層を PNG で作ります（Pillow の LANCZOS）。
     元画像を超える拡大はしません。
   - 保存先は `artwork/<ハッシュ先頭 2 文字>/<ハッシュ>/` です。
   - 配信時は保存済みの画像を返すだけで、要求された階層が無ければ存在する最大の階層を返します。
8. **ラウドネス計測:** 設定「スキャン時にラウドネスを計測する」がオンのときだけ行います（既定はオフ）。
9. **「最近追加」の起点:** まだ起点が無ければ、ここで置きます（初回スキャンの直後）。

**スキャンでライブラリを消さないための保護:**

- 音楽ディレクトリ自体が読めなければ、スキャンを中止します。
- 読めなかったディレクトリ・ファイルにある曲は、見つからなくても消しません。
- 1 曲も見つからないのに DB に曲があれば、ライブラリを空にせず中止します。
- 音楽用ドライブが読めない間は、スキャンもラウドネス計測も始めません（4.12 節）。

**ラウドネス計測**（`library/loudness.py`）:

- 設定画面・`kotone-cli measure-loudness`・スキャン時のいずれかで実行します。
- 計測済みの曲は測り直さないので、中断しても続きから再開できます。
- 計測には rsgain を使い、無ければ FFmpeg の `ebur128` を使います。どちらも無ければ補正が効かないだけで、再生はできます。
  - rsgain はタグを書かないモード（`-s s`）で動かし、アルバム単位で並列化します。
  - FFmpeg はアルバム内で並列化します。
- 基準は −18 LUFS（ReplayGain 2.0）です。
- 並列数の既定は、CPU コア数と 3 の小さい方です。
- ファイルに ReplayGain / R128 のタグがあれば、計測せずにその値を使います（設定で切り替え）。
- 計測速度は実測して記録し、所要時間の見積もりに使います。300 秒未満のサンプルは記録しません。

### 4.12 音楽用ドライブの見張りと自動復旧

**見張り**（`storage.py`、バックエンド内）: 10 秒ごと、および再生とスキャンの直前に、
次の 3 点を確かめます。

- マウント元のデバイスが消えていないか
- ext4 が緊急停止（`shutdown`）していないか
- 音楽ディレクトリを実際に読めるか（3 秒で打ち切り）

読めない間の動きは次のとおりです。

- 画面上部に赤い帯を出します（`StorageBanner.svelte`）。
- 再生（開始・再開・曲送り・曲戻し・範囲再生）を理由付きで断ります（HTTP 503）。
  一時停止は受け付けます。
- 帯には自動復旧の状態（復旧中・失敗・待機）も出します。
- 読めるようになると「音楽用ドライブを自動で復旧しました」などとトーストで知らせます。

**自動復旧**（`scripts/kotone-storage-recover`、root）:

- 起動するきっかけは 2 つです。
  - ドライブが現れたとき（udev）
  - タイマーの見回り（起動 3 分後から 2 分おき）
- 手順: 古いマウントを外す → `e2fsck -p`（ext 系のみ）→ fstab の指定で付け直す。
  付け直したら、実際に読めることを確かめます。
- 次の場合は手を出しません。
  - 健全なマウント（同じ ID のデバイスが他に見えていても触らない）
  - 起動から 120 秒以内
  - 見回りで見つけた、付いていないだけのドライブ（人が外した可能性があるため）
  - 同じデバイスのまま使用中で外せないとき
  - `e2fsck` が自動で直せない問題を見つけたとき
  - 同じ ID の候補が複数あるとき
  - 1 時間に 3 回を超えたとき
- 状態は `/run/kotone-storage/status.json` に書きます。ログは journal の `kotone-storage` です。

### 4.13 Bluetooth（`bluetooth.py`、`outputs.py`）

- Pi は**音を送る側**（A2DP Source）だけです。
  - `install.sh` は `bluealsa-aplay` を止め、bluealsa を `-p a2dp-source` で起動します。
  - bluealsa で有効にするコーデックは SBC・aptX・aptX HD・LDAC です。
    LDAC は品質 high で、電波状態に応じてビットレートを下げます（`--ldac-abr`）。
    SBC は `--sbc-quality=xq` です。
- 出力先は MPD の出力の有効／無効で切り替え、常に 1 つだけを有効にします。
  - 先に新しい方を有効にしてから、他を無効にします。
  - Bluetooth 出力は bluez-alsa の `bluealsa` デバイスで、「最後に接続した機器」を指します。
- 機器の探索・ペアリング・接続は BlueZ の D-Bus API を直接使います。
  一覧に出すのは、音を出せる機器と、登録済みの機器です。音を出せる機器とは、
  A2DP Sink の UUID を持つ機器か、機器の種別（Class of Device）の大分類が
  Audio/Video の機器です（`_to_device`、`_is_audio_video_class`）。
- **高音質コーデックの優先**（設定がオンのとき）: 画面からペアリング・接続した 1 秒後に、
  使えるうちで一番上位のコーデックへ切り替えます。優先順は LDAC → aptX HD → aptX → AAC → SBC です。
- 接続中のコーデックは 5 秒間キャッシュします。再生中画面と設定画面に表示します。

### 4.14 コマンドラインツール（`kotone-cli`、`cli.py`）

| コマンド | 内容 |
|---|---|
| `migrate` | DB マイグレーションを適用 |
| `scan [--full]` | ライブラリスキャン |
| `measure-loudness [--remeasure]` | ラウドネス計測（中断・再開可） |
| `analyze-artists [--examples N] [--json]` | ARTIST タグの区切り文字の候補を集計 |
| `rebuild-readings` | 読み仮名とソートキーを作り直す |
| `backup` | DB のバックアップ（10 世代まで保持） |
| `stats` | ライブラリの統計 |
| `settings [キー [値]]` | 設定の一覧・表示・変更 |

Pi 上では `sudo -u kotone kotone-cli <コマンド>` で実行します（`docs/install.md`）。

---

## 5. 設定項目

画面の設定はバックエンドの定義（`kotone/settings.py` の `SETTING_SPECS`）から自動で作ります。
値は SQLite の `settings` テーブルに JSON で保存します。廃止された設定の値は読み飛ばします。

| キー | 画面の名前 | 型・選択肢 | 既定値 | 画面の場所 |
|---|---|---|---|---|
| `volume_control` | 音量の操作方法 | `kotone`（画面のスライダー）/ `external`（アンプ側で調整。MPD を最大に保つ） | `kotone` | 再生 |
| `startup_warmup_ms` | 再生開始時のウォームアップ (ms) | 整数 0〜3000 | 600 | 再生 |
| `gapless_enabled` | ギャップレス再生 | 真偽 | オン | 再生（**効果なし。7.1 節**） |
| `random_avoid_last` | ランダム時に直前の曲を避ける数 | 整数 0〜20 | 1 | 再生 |
| `loudness_enabled` | ラウドネス補正 | 真偽 | オン | ラウドネス補正 |
| `loudness_mode` | 補正の基準 | `track`（トラック単位）/ `album`（アルバム単位） | `track` | ラウドネス補正 |
| `loudness_preamp_db` | プリアンプ (dB) | 小数 −15〜15 | 0.0 | ラウドネス補正 |
| `loudness_clip_prevention` | クリッピング防止 | 真偽 | オン | ラウドネス補正 |
| `rsgain_enabled` | スキャン時にラウドネスを計測する | 真偽 | オフ | ラウドネスの計測 |
| `use_replaygain_tags` | ファイルの ReplayGain タグを使う | 真偽 | オン | ラウドネスの計測 |
| `loudness_true_peak` | トゥルーピークで計測する | 真偽 | オフ | ラウドネスの計測 |
| `loudness_concurrency` | ラウドネス計測の並列数 | 整数 0〜16（0 は自動） | 0 | ラウドネスの計測 |
| `artist_separators` | アーティスト名の区切り文字 | 文字列のリスト | `;` ` / ` `／` `、` | ライブラリ |
| `ignore_leading_article` | 先頭の The / A を無視して並べる | 真偽 | オフ | ライブラリ |
| `scan_on_start` | 起動時にライブラリを再スキャンする | 真偽 | オフ | ライブラリ |
| `default_library_sort` | ライブラリの既定ソート | `album` / `artist` / `genre` / `year` / `added` | `album` | 表示 |
| `album_layout` | アルバム一覧の表示形式 | `grid`（サムネイル）/ `list`（1 列） | `grid` | 表示（ライブラリのボタンでも切り替え） |
| `theme` | テーマ | `dark` / `light` | `dark` | 表示 |
| `font_size` | 文字の大きさ | `small` / `normal` / `large` | `normal` | 表示 |
| `bluetooth_prefer_quality` | 高音質コーデックを優先する | 真偽 | オン | Bluetooth のカード |
| `initial_search_by` | 頭文字で絞り込む対象 | `artist` / `album` | `artist` | イニシャルサーチの画面 |

- 範囲外の数値は最小・最大に丸めます。選択肢に無い値はエラーになります。
- 音量の操作方法・ラウドネス補正の 4 項目を変えると、再生中の音量をその場で計算し直します（`api/system.py` の `VOLUME_SETTINGS`）。
- 設定の変更は WebSocket で全画面に配られ、再読み込みなしで反映されます。
- デプロイ設定（環境変数）は 3.6 節を見てください。

---

## 6. インストール・起動・更新

### 6.1 インストール

```bash
git clone https://github.com/okinu-chan/kotone.git
cd kotone
sudo ./install.sh --music-dir /mnt/music
```

**`install.sh` のオプション**

| オプション | 内容 |
|---|---|
| `--music-dir PATH` | 音楽ディレクトリ（既定 `/var/lib/kotone/music`） |
| `--port PORT` | Web UI のポート（既定 8088） |
| `--prefix PATH` | アプリの配置先（既定 `/opt/kotone`） |
| `--repo URL` | clone 元（リポジトリの外から実行したときに使う） |
| `--with-rsgain` | apt に rsgain が無いとき、GitHub のリリース（v3.6）から入れる |
| `--no-rsgain` | rsgain を入れない |
| `--no-mpd-config` | `/etc/mpd.conf` を書き換えない |

**`install.sh` が行うこと**（順に）

1. 事前確認（root か、apt があるか、アーキテクチャ、Python のバージョン）
2. apt で依存パッケージを入れる
3. Bluetooth の設定
   - `/etc/bluetooth/main.conf` の `AutoEnable` を有効にする
   - `bluealsa-aplay` を止める
   - bluealsa の起動オプションを上書きする（drop-in）
4. rsgain を入れる（4.11 節の条件）
5. サービス用ユーザー `kotone` を作り、`audio` グループに入れる
6. ディレクトリを用意する
7. アプリを `/opt/kotone` に置く（git の履歴ごと。リポジトリの外から実行した場合は clone する）
8. Python の仮想環境を作り、`pip install`
9. `/etc/kotone/kotone.env` を作る（既にあれば残す）
10. `/etc/mpd.conf` を作る（元の設定は退避する）。`mpd.socket` を止め、`mpd` を有効化・再起動する
11. systemd ユニットを入れる
    - `kotone.service`、`kotone-update`、`kotone-cli`
    - ドライブ自動復旧のスクリプト・サービス・タイマー・udev ルール
12. サービスを有効化・起動し、アクセス先の URL を表示する

インストール後、ブラウザで `http://<Pi のホスト名またはIP>:8088/` を開きます。
USB DAC を指定する場合は `/etc/mpd.conf` の `device` を書き、MPD を再起動します
（`docs/install.md` 3.1）。

### 6.2 起動・停止・状態確認

| 目的 | コマンド |
|---|---|
| 状態 | `systemctl status kotone` / `systemctl status mpd` |
| 再起動 | `sudo systemctl restart kotone` |
| ログ | `journalctl -u kotone -f`（ドライブ自動復旧は `journalctl -t kotone-storage`） |
| 応答確認 | `curl http://localhost:8088/api/health` |

### 6.3 更新（`sudo kotone-update`）

処理は次の順です。

1. DB をバックアップする
2. ローカルの変更を `git stash` する
3. `git pull --ff-only`
4. `pip install` し直し、`/opt/kotone` をサービス用ユーザーの持ち物に戻す
5. `kotone-update` 自身と `kotone-cli` を `/usr/local/bin` に入れ直す
6. ドライブ自動復旧のファイルを入れ直す
7. マイグレーションを適用する
8. タイマーを有効化し、`kotone` を再起動する

自動では更新しません。`/opt/kotone` は `kotone` ユーザーの持ち物で、このスクリプトは
root で動きます。git がこれを拒まないよう、このリポジトリに限って `safe.directory`
を指定して操作します。

### 6.4 アンインストール

`sudo ./uninstall.sh` はアプリとサービスを消し、データは残します。
`--purge` を付けるとデータ・設定・サービス用ユーザーも消します。**音楽ファイルは消しません。**
`/etc/mpd.conf` は退避しておいた元の設定に戻します。

### 6.5 開発

```bash
# バックエンド
cd backend
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest        # テスト
.venv/bin/python -m ruff check kotone tests

# フロントエンド（Node.js が必要。ビルド結果 dist/ をコミットする）
cd frontend
npm install
npm run build
```

---

## 7. 既知の不具合と未実装の機能

### 7.1 コードで確認した不具合・不整合

| 内容 | 根拠 |
|---|---|
| 設定「ギャップレス再生」（`gapless_enabled`）は画面に出るが、バックエンドのどこからも読まれず、切り替えても何も変わらない | `settings.py` に定義があるだけで、他のモジュールから参照されていない |
| 設定「起動時にライブラリを再スキャンする」の説明文が「ウォッチャー経由」に触れているが、ファイルを見張る仕組みは無い | `settings.py` の説明文。ウォッチャーの実装は無い |
| 再生キュー画面（`/queue`）へのリンクが画面のどこにも無い。URL を直接開けば表示される | `router.svelte.js` に経路があるが、遷移させるコードが無い |
| シャッフル中の曲戻しでは、次の曲の音量を先回りして入れない。Bluetooth などバッファの深い出力では、補正後の音量になるまで遅れる | `PlaybackController.previous`（MPD がどの曲へ戻すか分からないため） |
| `install.sh` の `--unattended` は受け付けるが、何にも使われていない | `install.sh` で変数を設定するだけ |
| 高音質コーデックの優先順に AAC があるが、bluealsa の起動オプションで AAC を有効にしていないため、AAC は選ばれない | `bluetooth.py` の `CODEC_PRIORITY` と `install.sh` の bluealsa の `ExecStart` |

### 7.2 未実装の機能（要件・設計メモにあるが、コードに無いもの）

| 内容 | 状態 |
|---|---|
| アーティストの統合 | API（`POST /edit/artists/merge`）はあるが、画面から使う手段が無い |
| DSD の DoP の切り替え | `mpd.conf` の `dop` を手で書き換える必要がある。画面の設定は無い（`docs/design-notes.md` 7 章で「要検討」） |
| タグ編集の音源ファイルへの書き戻し | 行わない方針（`editing.py`）。DB にだけ保存する |
| 認証 | 無い。家庭内 LAN 専用 |

### 7.3 実装しないと決めているもの（`docs/requirements.md` 6 章）

- DLNA / AirPlay / Chromecast などの外部連携
- 複数台の Pi・マルチルーム
- スマホのネイティブアプリ
- EQ / トーンコントロール
- 歌詞表示
- クロスフェード（取り下げ済み。8 章）

### 7.4 実機で観測したが、コードでは確認できない事象

以下は Pi 上のコマンド出力で確認した事実で、原因は調べていません。

- `bluetoothctl info` で、JBL Tour Pro 3 と NW-ZX300 がどちらも `Paired: yes` /
  `Bonded: no` と表示される。JBL Tour Pro 3 では、リンクキーが保存されず、
  登録が失われることが繰り返し起きた。
- bluez-alsa は、再生の開始と終了のたびにログへ `PCM drop` を出すことがある。
- Technics EAH-AZ100 は、ペアリングモードでの探索に対して名前と種別
  （`Class: 0x00240404`、Audio/Video のヘッドセット）だけを返し、サービス UUID を
  1 つも返さない（2026-10-15、未ペアリングの状態で確認）。

### 7.5 古くなった文書の記述

[docs/design-notes.md](docs/design-notes.md) には、いまのコードと合わない記述があります。

| 設計メモの記述 | いまのコード |
|---|---|
| rsgain を `custom -s i` で実行 | `-s s`（タグを書かない）（`library/loudness.py`） |
| ラウドネス計測は FFmpeg の `ebur128`、`--with-rsgain` は未計測 | rsgain が主で、FFmpeg は予備。rsgain は apt にあれば既定で入る（`install.sh`） |
| クラス名 `RandomModeDriver` / `LoudnessCompensator` | その名前のクラスは無い。処理は `PlaybackController` にある |
| 頭文字は あ/か/さ… の「行」 | 五十音の 1 文字単位（マイグレーション 0003） |
| テーブル `artwork_cache` | `artwork` と `artwork_files` |
| 実効音量の式にクリッピング防止と上限・下限が無い | 4.10 節の式 |

[docs/install.md](docs/install.md) の「初回スキャンではラウドネス計測が支配的」という記述も古くなっています。
いまはスキャン時の計測は既定でオフです（`rsgain_enabled`）。

---

## 8. 設計判断とその理由

利用者の要望や実機での不具合をきっかけに決めたことです。理由はコード内の
コメントに残っているものを要約しています。

### 再生

| 判断 | 理由 | 根拠 |
|---|---|---|
| クロスフェードを提供しない（機能ごと取り下げ） | 曲の変わり目に「ジー」というノイズが数秒乗った。ラウドネス補正のオン・オフに関係なく出た。MPD のクロスフェードは 2 曲を同じ形式に揃えてから混ぜるので、ビットパーフェクトのために出力形式を固定していない構成と相性が悪い。固定すれば全曲がリサンプリングされ、ハイレゾ・DSD を持つ環境で折り合わない | `settings.py`、マイグレーション 0004 |
| 範囲の終端では曲送りを受け付けない | 受け付けると MPD がキューを使い切って止まり、「再生していません」の表示になって、聴いていたアルバムへ戻る手がかりが消える。スワイプは指が滑っただけでも起きる | `PlaybackController.next` |
| 鳴らし終えたら範囲の先頭で一時停止して待つ | NW-ZX300 に倣う。再生ボタンひとつで頭から聴き直せる | `_return_to_scope_top` |
| シャッフル（重複なし）とランダム（重複あり）を別の機能にする | 往年のウォークマンの「ランダム」を再現する。MPD の random は一巡するまで重複しないので、ランダムは独自に実装する | `playback.py` |
| ランダムは次の 1 曲を先にキューへ入れる | MPD のギャップレス機構をそのまま使える | `playback.py` |
| 再生中のプレイリストの編集をキューへ反映する | 画面の一覧と実際に鳴る順番が食い違うため | `resync_scope` |

### 音量・ラウドネス補正

| 判断 | 理由 | 根拠 |
|---|---|---|
| 計測値は DB に持ち、音源ファイルには書かない。補正は MPD の音量で当てる | 利用者の原盤ファイルを書き換えない | `library/loudness.py` |
| ラウドネス計測は通常のスキャンから切り離し、既定でオフ。中断・再開できる | 18,000 曲規模では長時間かかる。先に取り込んで再生できる状態にする | `settings.py` |
| rsgain をアルバム単位で並列化し、並列数の既定は最大 3 | 実測で FFmpeg より約 1.8 倍速い。Pi 4B ではメモリ帯域が先に上限になる | `library/loudness.py` |
| 曲の変わり目の音量を 0.35 秒かけて移す | 一気に切り替えると段差が「プツッ」と聴こえる（隣り合う曲で 29 dB 差の例あり） | `playback.py` |
| 曲送り・再生開始の前に次の曲の音量を入れておく | MPD は出力バッファを一瞬で埋め直す。後から音量を変えると、埋まった分は前の曲の音量で鳴る。Bluetooth では約 1.1 秒ずれていた | `_preset_loudness` |
| 音量スライダーはアイコンをタップして出す縦バー。操作ボタンと同じ並びに置き、バーは上に出す | 横バーを常に出すと曲名を押しのける。下に置くと iPhone 15 Pro / Safari ではスクロールしないと届かない | `VolumeControl.svelte`、`NowPlaying.svelte` |
| 音量の操作方法が「外部」なら MPD を最大に保ち、設定を変えたらその場で反映する | アンプ側で音量を取る構成でデジタル減衰を避ける。以前は次の曲まで下げた音量のまま残っていた | `playback.py`、`api/system.py` |
| ラウドネス補正の値は、補正がオンのときだけ、基準に合った値を表示する | オフのときの値や、使っていない基準の値は、鳴っている音と関係がない | `NowPlaying.svelte` |

### 出力・Bluetooth

| 判断 | 理由 | 根拠 |
|---|---|---|
| 出力先は常に 1 つだけ有効にする | MPD は有効な出力すべてへ同時に流すため、同じ音が二重に鳴る | `outputs.py` |
| Pi は送る側（a2dp-source）だけにする | 受け側を有効にすると、受信音声を DAC へ流す bluealsa-aplay が MPD と出力を奪い合う | `install.sh` |
| 未登録の機器は、A2DP Sink の UUID が無くても種別が Audio/Video なら一覧に出す | 探索の応答に UUID を載せない機器がある。UUID だけで絞ると Technics EAH-AZ100 が一覧に出ず、ペアリングできなかった | `_is_audio_video_class` |
| Bluetooth は bluetoothctl を使わず D-Bus を直接使う | 対話前提の作りで出力の解析が不安定だった（エージェント登録の取りこぼし） | `bluetooth.py` |
| Bluetooth ではウォームアップをしない | A2DP では不要で、曲頭へのシークが原因で鳴り始めの数秒が途切れた | `_start_with_warmup` |
| Bluetooth へ送る形式を、接続中のレートに固定する | bluez-alsa のプラグインは、求められたレートが違うと接続を設定し直す。NW-ZX300 はこれを拒否し（AVDTP OPEN を Bad State で拒否）、接続の表示を残したまま音が出なくなった | `_match_bluetooth_stream` |
| 再生できなかった理由を画面に出し、始まらなかったときはシークしない | MPD は出力を開けないと黙って一時停止するだけ。しかもシークするとエラーが消える | `describe_mpd_error`、`_did_not_start` |
| 接続中のコーデック名を表示する | 何のコーデックで繋がっているか、機器側で分からない機種が多い | `NowPlaying.svelte` |

### ライブラリ・画面

| 判断 | 理由 | 根拠 |
|---|---|---|
| 読み仮名は fugashi + unidic-lite で作り、手で直せるようにする | 精度を優先する。人名や固有名詞は形態素解析では決まらない（例: 「中島愛」） | `text/reading.py`、`ArtistEditor.svelte` |
| アーティスト一覧には、曲を持つアーティストだけを出す | ALBUMARTIST タグだけに出てくる名前が「0 曲」として並び、探しにくくなる。タグを直したあとの行き場の無い名前も再スキャンを待たずに消える | `repository.py` |
| イニシャルサーチは絞り込みではなく頭出しにする | 「か」を選んでも、すぐ上の「お」へ短いスクロールで届く | `stores.svelte.js` |
| イニシャルサーチの既定はアーティスト名 | ウォークマンの頭出しに倣う | `settings.py` |
| タブの「ライブラリ」で絞り込みを解除し、再生中のアルバムの位置へ | 解除のたびにシートを開くのは手間。先頭へ飛ぶと現在地を見失う | `TabBar.svelte` |
| 「最近追加」の起点を初回スキャンの直後に置く | そうしないとライブラリ全体が「最近追加」になる | `stats.py` |
| 表示形式をライブラリとダイレクトサーチで共有する | 行き来したときに見た目が変わって戸惑う | `AlbumLayoutToggle.svelte` |
| 曲名の全文表示は、どこをタップしても閉じる | 読むだけの画面なので、閉じるボタンを探させない（片手操作で左上の ✕ まで指を動かすのが手間、という要望による） | `NowPlaying.svelte` |
| 削除の確認は `window.confirm` ではなくアプリ内のダイアログ | iOS Safari などでは `window.confirm` が出ないことがある | `PlaylistsView.svelte` |
| 並べ替えは Pointer Events、つまみからだけ掴む | HTML5 の draggable はタッチで動かない。行のどこでも掴めると指でスクロールできない | `lib/reorder.js` |
| 曲一覧の各行は、曲ではなく位置で区別する | プレイリストには同じ曲が入りうる。曲で区別すると描画が止まり「読み込み中」のままになった | `TrackList.svelte` |
| アルバムアートはクロップせず、拡大もしない。PNG で 3 階層を事前生成する | 原盤の比率を保ち、リサイズ以外の劣化を足さない。配信時に計算しない | `library/artwork.py` |
| `index.html` は `Cache-Control: no-cache` で配る | 読み込む JS / CSS のファイル名を持つため。付けないとホーム画面のウェブアプリが古い `index.html` をキャッシュから開き続け、更新が画面に出ない | `main.py` |

### 信頼性

| 判断 | 理由 | 根拠 |
|---|---|---|
| 終わったスレッドの SQLite 接続を閉じる | 閉じずに積み上がり、504 本に達して Too many open files で画面も開けなくなった | `db/database.py` |
| `LimitNOFILE=8192` | 既定の 1024 では、アートワークの配信が重なると届きうる。届くと画面の配信まで落ちる | `systemd/kotone.service` |
| keep-alive を 75 秒にし、届かなかった要求を 1 度だけ送り直す | 5 秒で接続を閉じており、ブラウザの送信と重なると "Load failed" が出た | `__main__.py`、`api.js` |
| 音楽用ドライブを見張り、自動で付け直し、読めない間はスキャンで曲を消さない | USB の過電流で SSD が外れ、別の名前で戻ったのにマウントが古いまま残り、どの曲も読めなくなった。その状態でスキャンすると全曲が消える作りだった | `storage.py`、`scripts/kotone-storage-recover`、`library/scanner.py` |
| 自動復旧は健全なマウントを決して外さない | 試験で、同じ ID の別デバイスを手がかりに健全なマウントを外してしまったため | `scripts/kotone-storage-recover` |
