<script>
  /**
   * ライブラリ画面。アルバム / アーティスト / ジャンル / 年 の 4 つの入口を持ち、
   * ソート軸の切り替え（要件 3.2）とイニシャルサーチ（要件 3.7）を提供する。
   */
  import AlbumGrid from './AlbumGrid.svelte';
  import AlbumList from './AlbumList.svelte';
  import FilterSheet from './FilterSheet.svelte';
  import InitialSearch from './InitialSearch.svelte';
  import PageHeader from './PageHeader.svelte';
  import { api } from '../api.js';
  import { router } from '../router.svelte.js';
  import { libraryView, settings, toast } from '../stores.svelte.js';

  const VIEWS = [
    { key: 'albums', label: 'アルバム' },
    { key: 'artists', label: 'アーティスト' },
    { key: 'genres', label: 'ジャンル' },
    { key: 'years', label: '年' },
  ];

  const ALBUM_SORTS = ['artist', 'album', 'year', 'genre', 'added'];

  // 絞り込み・並び順・スクロール位置は共有ストア（libraryView）に置く。
  // 画面遷移でコンポーネントが作り直されても、アルバムから戻ったときに
  // 「は行で絞り込んだ状態の、見ていた位置」へ復帰させるため。
  let searchOpen = $state(false);
  let filterOpen = $state(false);
  let restoredScroll = false;

  const activeFilters = $derived(libraryView.activeFilters);

  function removeFilter(key) {
    libraryView.remove(key);
    router.forgetScroll('/library');
    window.scrollTo(0, 0);
  }

  /** シートで条件を変えたら、保存済みスクロール位置は捨てて先頭から見せる。 */
  function onFilterApplied() {
    router.forgetScroll('/library');
    window.scrollTo(0, 0);
  }

  function setLayout(next) {
    libraryView.layout = next;
    settings.set('album_layout', next);
  }

  let albums = $state([]);
  let artists = $state([]);
  let genres = $state([]);
  let years = $state([]);
  let loading = $state(false);
  let total = $state(0);

  $effect(() => {
    if (libraryView.initialised || !settings.loaded) return;
    libraryView.initialised = true;
    if (ALBUM_SORTS.includes(settings.values.default_library_sort)) {
      libraryView.sort = settings.values.default_library_sort;
    }
    if (['grid', 'list'].includes(settings.values.album_layout)) {
      libraryView.layout = settings.values.album_layout;
    }
  });



  $effect(() => {
    const currentView = libraryView.view;
    const initial = libraryView.initial;
    const initialGroup = libraryView.initialGroup;
    const params = {
      sort: libraryView.sort,
      initial,
      initial_group: initialGroup,
      codec: libraryView.codec,
      quality: libraryView.quality,
    };
    loading = true;

    const load = async () => {
      if (currentView === 'albums') {
        const payload = await api.albums(params);
        albums = payload.items;
        total = payload.total;
      } else if (currentView === 'artists') {
        const payload = await api.artists({ initial, initial_group: initialGroup });
        artists = payload.items;
        total = payload.total;
      } else if (currentView === 'genres') {
        const payload = await api.genres();
        genres = payload.items;
        total = payload.total;
      } else {
        const payload = await api.years();
        years = payload.items;
        total = payload.total;
      }
    };

    load()
      .catch((error) => toast.error(error.detail || String(error)))
      .finally(() => {
        loading = false;
        // 一覧が描画されてからスクロール位置を戻す。
        // 戻ってきた直後は中身が空で高さが足りないため、描画後まで待つ必要がある。
        const saved = router.savedScroll('/library');
        if (!restoredScroll && saved > 0) {
          restoredScroll = true;
          requestAnimationFrame(() => {
            requestAnimationFrame(() => window.scrollTo(0, saved));
          });
        }
      });
  });

  function switchView(key) {
    libraryView.view = key;
    libraryView.clearFilter();
    router.forgetScroll('/library');
    window.scrollTo(0, 0);
  }

  function clearInitial() {
    libraryView.clearFilter();
    router.forgetScroll('/library');
    window.scrollTo(0, 0);
  }

  function onInitialSelected(event) {
    libraryView.initial = event.initial;
    libraryView.initialGroup = event.group;
    router.forgetScroll('/library');
    window.scrollTo(0, 0);
  }

  const canUseInitialSearch = $derived(['albums', 'artists'].includes(libraryView.view));
  const entity = $derived(libraryView.view === 'artists' ? 'artists' : 'albums');
</script>

<PageHeader title="ライブラリ" subtitle={total ? `${total} 件` : ''}>
  {#snippet actions()}
    {#if canUseInitialSearch}
      <button class="icon-btn" onclick={() => (searchOpen = true)} aria-label="頭文字で絞り込む">
        あ
      </button>
    {/if}
  {/snippet}
</PageHeader>

<div class="toolbar">
  <div class="views" role="tablist">
    {#each VIEWS as item (item.key)}
      <button
        role="tab"
        aria-selected={libraryView.view === item.key}
        class:active={libraryView.view === item.key}
        onclick={() => switchView(item.key)}
      >
        {item.label}
      </button>
    {/each}
  </div>

  {#if libraryView.view === 'albums'}
    <div class="right-tools">
      <button class="filter-button" onclick={() => (filterOpen = true)}>
        絞り込み
        {#if activeFilters.length > 0}
          <span class="badge tabular">{activeFilters.length}</span>
        {/if}
      </button>

      <!-- 表示形式は使用頻度が高いので、シートに入れず直接切り替えられるようにする -->
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
    </div>
  {/if}
</div>

{#if activeFilters.length > 0}
  <div class="filter-chips">
    {#each activeFilters as chip (chip.key)}
      <button class="chip" onclick={() => removeFilter(chip.key)}>
        {chip.label}<span class="remove" aria-hidden="true">✕</span>
      </button>
    {/each}
    <button class="clear" onclick={clearInitial}>すべて解除</button>
  </div>
{/if}

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if libraryView.view === 'albums'}
  {#if libraryView.layout === 'list'}
    <AlbumList {albums} showYear={libraryView.sort !== 'added'} />
  {:else}
    <AlbumGrid {albums} showYear={libraryView.sort !== 'added'} />
  {/if}
{:else if libraryView.view === 'artists'}
  <ul class="rows">
    {#each artists as artist (artist.id)}
      <li>
        <button onclick={() => router.navigate(`/artists/${artist.id}`)}>
          <span class="text">
            <span class="name truncate">{artist.name}</span>
            {#if artist.reading && artist.reading !== artist.name}
              <span class="reading truncate dim">{artist.reading}</span>
            {/if}
          </span>
          <span class="count dim tabular">{artist.album_count} 枚 / {artist.track_count} 曲</span>
        </button>
      </li>
    {/each}
  </ul>
  {#if artists.length === 0}<p class="empty-state">アーティストがいません。</p>{/if}
{:else if libraryView.view === 'genres'}
  <ul class="rows">
    {#each genres as genre (genre.id)}
      <li>
        <button onclick={() => router.navigate(`/genres/${genre.id}`)}>
          <span class="text"><span class="name truncate">{genre.name}</span></span>
          <span class="count dim tabular">{genre.track_count} 曲</span>
        </button>
      </li>
    {/each}
  </ul>
  {#if genres.length === 0}<p class="empty-state">ジャンルがありません。</p>{/if}
{:else}
  <ul class="rows">
    {#each years as year (year.year)}
      <li>
        <button onclick={() => router.navigate(`/years/${year.year}`)}>
          <span class="text"><span class="name tabular">{year.year} 年</span></span>
          <span class="count dim tabular">{year.album_count} 枚 / {year.track_count} 曲</span>
        </button>
      </li>
    {/each}
  </ul>
  {#if years.length === 0}<p class="empty-state">年の情報がありません。</p>{/if}
{/if}

<InitialSearch {entity} bind:open={searchOpen} onselect={onInitialSelected} />
<FilterSheet bind:open={filterOpen} sorts={ALBUM_SORTS} onapply={onFilterApplied} />

<style>
  .icon-btn {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 50%;
    background: var(--surface);
    border: 1px solid var(--border);
    font-size: 16px;
  }

  .icon-btn:hover {
    background: var(--surface-hover);
    border-color: var(--accent-dim);
  }

  /* 下までスクロールしても絞り込み・並び順を操作できるよう固定する。
     PageHeader（sticky, 高さ約 60px）の直下に貼り付ける。 */
  .toolbar {
    position: sticky;
    top: calc(var(--tap) + var(--safe-top) + 16px);
    z-index: 19;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    padding: 10px 12px;
    background: color-mix(in srgb, var(--bg) 94%, transparent);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
  }

  .views {
    display: flex;
    gap: 6px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .views::-webkit-scrollbar {
    display: none;
  }

  .views button {
    min-height: 36px;
    padding: 0 14px;
    border-radius: 999px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 13px;
    white-space: nowrap;
  }

  .views button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #0d0f13;
  }

  .right-tools {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
  }

  .layout-toggle {
    display: flex;
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
    font-size: 15px;
  }

  .layout-toggle button.active {
    background: var(--accent);
    color: #0d0f13;
  }

  /* 絞り込みは 1 つのボタンに集約する（軸が増えてもツールバーが膨らまない） */
  .filter-button {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex: none;
    min-height: 36px;
    padding: 0 14px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: 13px;
    white-space: nowrap;
  }

  .filter-button:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .badge {
    display: grid;
    place-items: center;
    min-width: 18px;
    height: 18px;
    padding: 0 5px;
    border-radius: 999px;
    background: var(--accent);
    color: #0d0f13;
    font-size: 11px;
  }

  /* 適用中の絞り込みもツールバーと一緒に固定表示する */
  .filter-chips {
    position: sticky;
    top: calc(var(--tap) + var(--safe-top) + 74px);
    z-index: 18;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    background: color-mix(in srgb, var(--accent) 14%, var(--bg));
    font-size: 13px;
    overflow-x: auto;
    scrollbar-width: none;
  }

  .filter-chips::-webkit-scrollbar {
    display: none;
  }

  .filter-chips .chip {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    flex: none;
    min-height: 32px;
    padding: 0 12px;
    border-radius: 999px;
    border: 1px solid var(--accent-dim);
    background: color-mix(in srgb, var(--bg) 60%, transparent);
    color: var(--accent);
    font-size: 12.5px;
    white-space: nowrap;
  }

  .filter-chips .remove {
    font-size: 10px;
    opacity: 0.8;
  }

  .filter-chips .clear {
    flex: none;
    min-height: 32px;
    padding: 0 10px;
    color: var(--text-muted);
    font-size: 12px;
    white-space: nowrap;
  }

  .loading {
    display: grid;
    place-items: center;
    padding: 60px;
  }

  .rows li {
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .rows button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    min-height: 56px;
    padding: 8px 16px;
    text-align: left;
  }

  .rows button:hover {
    background: var(--surface-hover);
  }

  .text {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.35;
  }

  .name {
    font-size: 15px;
  }

  .reading {
    font-size: 11.5px;
  }

  .count {
    flex: none;
    font-size: 12px;
  }
</style>
