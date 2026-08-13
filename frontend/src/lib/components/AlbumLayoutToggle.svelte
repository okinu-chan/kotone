<script>
  /**
   * アルバム一覧の表示形式（サムネイル／1 列）の切り替え。
   *
   * ライブラリとダイレクトサーチ（アーティスト画面）で同じ設定を共有する。
   * 片方だけ別の形式になると、行き来したときに見た目が変わって戸惑うため。
   */
  import { libraryView, settings } from '../stores.svelte.js';

  function setLayout(next) {
    libraryView.layout = next;
    settings.set('album_layout', next);
  }
</script>

<div class="layout-toggle" role="group" aria-label="表示形式">
  <button
    class:active={libraryView.layout === 'grid'}
    aria-pressed={libraryView.layout === 'grid'}
    onclick={() => setLayout('grid')}
    title="サムネイル表示"
    aria-label="サムネイル表示">▦</button
  >
  <button
    class:active={libraryView.layout === 'list'}
    aria-pressed={libraryView.layout === 'list'}
    onclick={() => setLayout('list')}
    title="1 列表示"
    aria-label="1 列表示">☰</button
  >
</div>

<style>
  .layout-toggle {
    display: flex;
    flex: none;
    gap: 2px;
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
  }

  .layout-toggle button {
    display: grid;
    place-items: center;
    min-width: 38px;
    min-height: 36px;
    background: var(--surface);
    color: var(--text-dim);
    font-size: var(--fs-lg);
  }

  .layout-toggle button.active {
    background: var(--accent);
    color: var(--on-accent);
  }
</style>
