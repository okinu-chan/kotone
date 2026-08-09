import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

// 開発時は Vite の dev サーバー（5173）で動かし、API だけ Pi 上の
// バックエンドへプロキシする。KOTONE_DEV_API で接続先を差し替えられる。
const apiTarget = process.env.KOTONE_DEV_API || 'http://localhost:8088';

export default defineConfig({
  plugins: [svelte()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
    },
  },
  build: {
    // Pi 上ではビルドしないため、成果物はリポジトリに含める（要件 4.7 / 5 章）
    outDir: 'dist',
    emptyOutDir: true,
    target: 'es2020',
    sourcemap: false,
  },
});
