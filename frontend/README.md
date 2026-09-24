# KOTONE フロントエンド

Svelte 5 + Vite の SPA。ビルド成果物（`dist/`）は**リポジトリに含めます**。
Raspberry Pi 上に Node.js を入れずに済ませるためです（要件定義書 4.7 / 5 章）。

## 開発

```bash
cd frontend
npm install
npm run dev            # http://localhost:5173
```

`/api` へのリクエストは `http://localhost:8088` へプロキシされます。
別のホストのバックエンドに繋ぐ場合:

```bash
KOTONE_DEV_API=http://raspberrypi.local:8088 npm run dev
```

## ビルド

```bash
npm run build          # dist/ に出力
```

**`dist/` の変更は必ずコミットしてください。** Pi 側ではこれをそのまま配信します。

## 構成

```
src/
├── App.svelte                  ルーティングとレイアウト
├── app.css                     配色・タイポグラフィ・共通クラス
├── lib/
│   ├── api.js                  バックエンド API のラッパー
│   ├── format.js               表示用の書式（時間・音質・ラベル）
│   ├── router.svelte.js        History API ベースのルーター
│   ├── stores.svelte.js        再生状態 / スキャン進捗 / 設定 / 通知
│   └── components/
│       ├── NowPlaying.svelte   再生中画面（ダイレクトサーチの起点）
│       ├── LibraryView.svelte  アルバム/アーティスト/ジャンル/年
│       ├── AlbumView.svelte    アルバム詳細
│       ├── ArtistView.svelte   アーティスト詳細（All ○○ + アルバム）
│       ├── ScopeView.svelte    ジャンル / 年での絞り込み
│       ├── InitialSearch.svelte イニシャルサーチのオーバーレイ
│       ├── PlaylistsView.svelte / PlaylistView.svelte
│       ├── SearchView.svelte / SettingsView.svelte / QueueView.svelte
│       ├── TagEditor.svelte    タグ・読みの手動修正
│       ├── TrackList.svelte / AlbumGrid.svelte / AlbumArt.svelte
│       └── TabBar.svelte / MiniPlayer.svelte / Modal.svelte / Toast.svelte
```

## UI の方針（要件 3.5 / 3.7）

- 画面遷移はすべてタップ / クリックで完結。常時表示のタブバーを持つ。
- ZX300 のような画面端スワイプは**再現しない**。ブラウザ標準のジェスチャー
  （戻る・プルリフレッシュ）と衝突するため。
- 補助的なジェスチャーはアルバムアート**の上**の横スワイプ（曲送り／曲戻し）だけ。
- タップ領域は 44px 四方以上。
- 「戻る」はブラウザの履歴をそのまま使うので、ダイレクトサーチで
  曲 → アーティスト → 別の曲 と辿った履歴も自然に遡れる。
- 画像は `loading="lazy"` で画面内に入ってから読み込む。
- 表示は全て日本語。
