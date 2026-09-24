# kotone

Raspberry Pi 向けの、日本語ネイティブな音楽プレイヤープラットフォーム。

Sony NW-ZX300 のような軽快な操作感を、Raspberry Pi + USB DAC の環境でブラウザから
実現することを目指しています。moOde 等の既存ディストリビューションには依存せず、
公式 Raspberry Pi OS (Lite) の上に単体で構築されます。

> **プロジェクト名について**: `kotone` は暫定の識別子です（要件定義書 8 章参照）。
> 正式名称の決定後は [`scripts/rename-project.sh`](scripts/rename-project.sh) で
> 一括リネームできます。

## 特徴

- **MPD ベースの再生エンジン** — gapless 再生、ALSA/USB DAC (UAC2) 制御
- **日本語ネイティブ** — 形態素解析による読み仮名の自動生成、正しい五十音順ソート、
  イニシャルサーチ（カナ / 英数）
- **ダイレクトサーチ** — 再生中画面のアーティスト / アルバム / 年 / ジャンルから
  ライブラリを横断的に辿れる
- **ラウドネス補正** — EBU R128 (rsgain) で計測した値を DB に保持し、
  再生時に動的補正（元ファイルへのタグ書き込みは行わない）
- **高品質アートワーク** — 非正方形をクロップせず表示、事前生成の PNG 3 階層キャッシュ
- **Walkman 由来の再生モード** — シャッフル（重複なし）とランダム（重複あり）を別機能として提供

## アーキテクチャ

```
┌─────────────────────────────────────────────┐
│ ブラウザ (PC / スマートフォン / iPad)        │
│   Svelte SPA  ── REST + WebSocket ──┐       │
└─────────────────────────────────────┼───────┘
                                      │
┌─────────────────────────────────────▼───────┐
│ kotone-backend (Python / FastAPI)           │
│   ・MPD 制御 / 再生モード管理                │
│   ・SQLite タグ DB / 読み仮名 / ソートキー   │
│   ・アートワークキャッシュ生成               │
│   ・ラウドネス補正の適用                     │
└─────────────────────────────────────┬───────┘
                                      │ MPD protocol (localhost:6600)
┌─────────────────────────────────────▼───────┐
│ MPD ── ALSA ── USB DAC (UAC2)               │
└─────────────────────────────────────────────┘
```

## インストール

Raspberry Pi OS (Lite, 64bit / Bookworm 以降) を書き込み・起動済みの Pi 上で:

```bash
curl -fsSL https://raw.githubusercontent.com/okinu-chan/kotone/main/install.sh | sudo bash
```

または、リポジトリを clone してから:

```bash
sudo ./install.sh
```

インストール後、ブラウザから `http://<PiのホストIP>:8088/` にアクセスしてください。

詳細は [docs/install.md](docs/install.md) を参照してください。

## 更新

```bash
sudo kotone-update
```

`git pull` + 依存関係の更新 + DB マイグレーションを実行します。
ユーザーデータ（SQLite DB、アートワークキャッシュ、設定）は git 管理外の
`/var/lib/kotone/` に置かれるため、更新で上書きされません。

## 開発

- **仕様書（いまのコードが何をしているか）: [SPEC.md](SPEC.md)**
- 変更履歴: [CHANGELOG.md](CHANGELOG.md)
- バックエンド: [backend/README.md](backend/README.md)
- フロントエンド: [frontend/README.md](frontend/README.md)
- 要件定義書: [docs/requirements.md](docs/requirements.md)
- 設計メモ: [docs/design-notes.md](docs/design-notes.md)

## ライセンス

MIT License — [LICENSE](LICENSE)
