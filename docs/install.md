# インストールと運用

対象読者は「Raspberry Pi をオーディオ用途で導入できる程度の経験がある方」です
（要件定義書 1.3）。

## 1. 前提

| 項目 | 要件 |
|---|---|
| ハードウェア | Raspberry Pi 4B 以上（ファンレス運用しやすい Pi4 を推奨） |
| OS | 公式 Raspberry Pi OS **Lite**（Bookworm 以降 / 64bit 推奨） |
| Python | 3.11 以上（Bookworm の標準は 3.11） |
| ストレージ | 音楽ライブラリ用の SSD / USB メディア |
| DAC | USB Audio Class 2.0 (UAC2) 準拠の USB DAC |

ネットワークは家庭内 LAN のみを想定しています。外部公開はしないでください
（認証機構を持っていません）。

## 2. インストール

```bash
git clone https://github.com/USER/kotone.git
cd kotone
sudo ./install.sh --music-dir /mnt/music
```

インストーラが行うこと:

1. `mpd` / `ffmpeg` / `python3-venv` などの依存パッケージを apt で導入
2. サービス用ユーザー `kotone` の作成
3. `/opt/kotone` へアプリを配置し、`/opt/kotone/backend/.venv` に Python 環境を構築
4. `/etc/kotone/kotone.env` に設定ファイルを作成
5. `/etc/mpd.conf` を kotone 用に設定（既存設定は `.kotone-backup` に退避）
6. systemd サービス `kotone.service` を登録して起動

### 主なオプション

| オプション | 説明 |
|---|---|
| `--music-dir PATH` | 音楽ライブラリの場所（既定 `/var/lib/kotone/music`） |
| `--port PORT` | Web UI のポート（既定 `8088`） |
| `--with-rsgain` | rsgain を GitHub リリースから導入する |
| `--no-mpd-config` | `mpd.conf` を書き換えない（既存設定を使う場合） |

### rsgain について

ラウドネス補正の計測には `rsgain`（EBU R128 準拠）を使いますが、Debian の
公式リポジトリには入っていません。導入しない場合は **FFmpeg の `ebur128`
フィルタに自動でフォールバック**するため、そのままでも補正は機能します。
計測速度は rsgain のほうが速いので、大きなライブラリでは `--with-rsgain` を
検討してください。

## 3. 初期設定

### 3.1 USB DAC の指定

```bash
aplay -l
```

で出力先を確認し、`/etc/mpd.conf` の `audio_output` の `device` を
`"hw:1,0"` のように設定してから `sudo systemctl restart mpd` します。
既定では ALSA の `default` を使うため、DAC が 1 台だけならそのままでも動きます。

DSD をネイティブ再生できない DAC では `dop "yes"` を設定してください。

### 3.2 アーティスト名の区切り文字（重要）

1 つの `ARTIST` タグに複数名が並記されている場合、どの文字で区切るかは
ライブラリによって違います。**初回スキャンの前に**実データを確認してください。

```bash
sudo -u kotone kotone-cli analyze-artists
```

出力例:

```
区切り              件数  例
------------------------------------------------------------------------
';'                  128  宇多田ヒカル; 椎名林檎
' / '                 34  A / B
' & '                  6  Hall & Oates      ← これは分割すべきでない
```

必要なら設定を変更します（Web UI の「設定」画面からも変更できます）。

```bash
sudo -u kotone kotone-cli settings artist_separators '[";", " / "]'
```

> 既定値にカンマや `&` を含めていないのは、アーティスト名自体に含まれる
> 頻度が高いためです。分割を誤った曲は、後から UI のタグ編集で個別に
> 修正できます（修正は再スキャンでも保持されます）。

### 3.3 ライブラリスキャン

```bash
sudo -u kotone kotone-cli scan
```

Web UI の「設定」→「スキャン」からも実行できます。スキャンでは次の処理を行います。

1. タグの読み込みと SQLite への取り込み
2. 読み仮名の自動生成（fugashi + unidic-lite）とソートキーの算出
3. アートワークの抽出と 3 階層 PNG キャッシュの生成
4. ラウドネスの計測（rsgain または FFmpeg）

初回は曲数に応じて時間がかかります（1000 曲でおおむね 10〜30 分。
ラウドネス計測が支配的です）。2 回目以降は更新分だけを処理します。

## 4. 運用

| 目的 | コマンド |
|---|---|
| 状態確認 | `systemctl status kotone` |
| 再起動 | `sudo systemctl restart kotone` |
| ログ追跡 | `journalctl -u kotone -f` |
| MPD の状態 | `systemctl status mpd` / `mpc status` |
| アップデート | `sudo kotone-update` |
| DB バックアップ | `sudo -u kotone kotone-cli backup` |
| 設定一覧 | `sudo -u kotone kotone-cli settings` |

### ファイル配置

| パス | 内容 | アップデート時 |
|---|---|---|
| `/opt/kotone` | アプリ本体（git 管理下） | `git pull` で更新される |
| `/var/lib/kotone/library.sqlite3` | タグ DB・プレイリスト・設定 | **保持される** |
| `/var/lib/kotone/artwork/` | アートワークキャッシュ | **保持される** |
| `/var/lib/kotone/backups/` | DB の自動バックアップ（10 世代） | **保持される** |
| `/etc/kotone/kotone.env` | 環境設定 | **保持される** |

## 5. トラブルシューティング

### Web UI が開けない

```bash
systemctl status kotone
journalctl -u kotone -n 50
```

`http://<PiのIP>:8088/api/health` が応答するか確認してください。

### 「MPD に接続できていません」と表示される

```bash
systemctl status mpd
sudo journalctl -u mpd -n 50
```

Raspberry Pi OS では `mpd.socket` によるソケット起動が有効になっていることが
あります。インストーラはこれを無効化しますが、手動で設定した場合は

```bash
sudo systemctl disable --now mpd.socket
sudo systemctl enable --now mpd
```

### 音が出ない・DAC が認識されない

```bash
aplay -l                    # DAC が見えているか
mpc outputs                 # MPD が出力を認識しているか
speaker-test -D hw:1,0 -c 2 # 直接鳴らしてみる
```

UAC2 準拠の DAC は基本的にドライバ不要で認識されます。認識されない機種は
個別対応となります（要件定義書 2 章）。

### 読み仮名が生成されない

```bash
sudo -u kotone kotone-cli rebuild-readings
```

形態素解析器が使えているかは Web UI の「設定」→「システム」→「外部ツール」で
確認できます。「形態素解析: なし」の場合は再インストールしてください。

### 曲順が期待どおりでない

読み仮名が誤っている可能性があります。UI のタグ編集で読みを修正するか、
自動生成し直してください。修正した読みは再スキャンでも保持されます。

### 音量のばらつきが直らない

- 「設定」→「ラウドネス補正」がオンになっているか
- 「システム」→「ラウドネス計測済み」の曲数がライブラリ全体と一致しているか
- `mpd.conf` の `mixer_type` が `software` になっているか

ハードウェアミキサー（DAC 内蔵ボリューム）では補正が正しく効きません。

## 6. アンインストール

```bash
sudo ./uninstall.sh          # アプリのみ削除（データは残す）
sudo ./uninstall.sh --purge  # ユーザーデータも削除（音楽ファイルは残る）
```
