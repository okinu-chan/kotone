<script>
  /**
   * 常時表示のタブバー。画面遷移はすべてタップで完結させる（要件 3.5）。
   * PC ではクリック、スマートフォンではタップで同じ導線を使う。
   */
  import { router } from '../router.svelte.js';
  import { libraryView, player } from '../stores.svelte.js';

  const TABS = [
    { path: '/', label: '再生中', icon: '▶' },
    { path: '/library', label: 'ライブラリ', icon: '≡' },
    { path: '/playlists', label: 'プレイリスト', icon: '☰' },
    { path: '/search', label: '検索', icon: '⌕' },
    { path: '/settings', label: '設定', icon: '⚙' },
  ];

  /**
   * ライブラリのタブは「絞り込みを解いて、いま聴いているアルバムの場所へ」。
   *
   * 絞り込みを解くのに、シートを開いて解除して閉じて……とタップが要るのは
   * 面倒なので、いつも見えているタブに割り当てる。解いたあと先頭へ飛ぶと
   * 現在地を見失うため、再生中の曲が入っているアルバムの位置で止める。
   */
  function onTab(path) {
    if (path === '/library') {
      libraryView.clearFilter();
      const albumId = player.track?.album_id;
      libraryView.pendingJump = albumId ? { albumId } : null;
    }
    router.switchTab(path);
  }

  const activeTab = $derived.by(() => {
    const name = router.current.name;
    if (name === 'now') return '/';
    if (['library', 'album', 'artist', 'genre', 'year'].includes(name)) return '/library';
    if (['playlists', 'playlist'].includes(name)) return '/playlists';
    if (name === 'search') return '/search';
    if (name === 'settings') return '/settings';
    return '/';
  });
</script>

<nav class="tabbar" aria-label="メインナビゲーション">
  {#each TABS as tab (tab.path)}
    <button
      class="tab"
      class:active={activeTab === tab.path}
      aria-current={activeTab === tab.path ? 'page' : undefined}
      onclick={() => onTab(tab.path)}
    >
      <span class="icon" aria-hidden="true">{tab.icon}</span>
      <span class="label">{tab.label}</span>
    </button>
  {/each}
</nav>

<style>
  .tabbar {
    position: fixed;
    left: 0;
    right: 0;
    bottom: 0;
    z-index: 40;
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    background: color-mix(in srgb, var(--bg-elevated) 92%, transparent);
    backdrop-filter: blur(12px);
    border-top: 1px solid var(--border);
    padding-bottom: var(--safe-bottom);
  }

  .tab {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    /* タップ領域を 44px 以上確保する（要件 3.7） */
    min-height: var(--tabbar-height);
    color: var(--text-dim);
    font-size: 10.5px;
    transition: color 0.15s;
  }

  .tab:hover {
    color: var(--text-muted);
  }

  .tab.active {
    color: var(--accent);
  }

  .icon {
    font-size: var(--fs-xl);
    line-height: 1;
  }

  .label {
    letter-spacing: 0.02em;
  }

  @media (min-width: 900px) {
    .tabbar {
      grid-template-columns: repeat(5, minmax(0, 160px));
      justify-content: center;
    }

    .tab {
      flex-direction: row;
      gap: 8px;
      font-size: var(--fs-md);
    }
  }
</style>
