<script>
  /**
   * ライブラリ画面。アルバム / アーティスト / ジャンル / 年 の 4 つの入口を持ち、
   * ソート軸の切り替え（要件 3.2）とイニシャルサーチ（要件 3.7）を提供する。
   */
  import AlbumGrid from './AlbumGrid.svelte';
  import InitialSearch from './InitialSearch.svelte';
  import PageHeader from './PageHeader.svelte';
  import { api } from '../api.js';
  import { SORT_LABELS } from '../format.js';
  import { router } from '../router.svelte.js';
  import { settings, toast } from '../stores.svelte.js';

  const VIEWS = [
    { key: 'albums', label: 'アルバム' },
    { key: 'artists', label: 'アーティスト' },
    { key: 'genres', label: 'ジャンル' },
    { key: 'years', label: '年' },
  ];

  const ALBUM_SORTS = ['artist', 'album', 'year', 'genre', 'added'];

  let view = $state('albums');
  let sort = $state('artist');
  let initial = $state(null);
  let initialGroup = $state(null);
  let searchOpen = $state(false);

  let albums = $state([]);
  let artists = $state([]);
  let genres = $state([]);
  let years = $state([]);
  let loading = $state(false);
  let total = $state(0);

  let sortInitialised = false;
  $effect(() => {
    if (sortInitialised || !settings.loaded) return;
    sortInitialised = true;
    if (ALBUM_SORTS.includes(settings.values.default_library_sort)) {
      sort = settings.values.default_library_sort;
    }
  });

  $effect(() => {
    const currentView = view;
    const params = { sort, initial, initial_group: initialGroup };
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
      });
  });

  function switchView(key) {
    view = key;
    clearInitial();
  }

  function clearInitial() {
    initial = null;
    initialGroup = null;
  }

  function onInitialSelected(event) {
    initial = event.initial;
    initialGroup = event.group;
  }

  const canUseInitialSearch = $derived(['albums', 'artists'].includes(view));
  const entity = $derived(view === 'artists' ? 'artists' : 'albums');
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
        aria-selected={view === item.key}
        class:active={view === item.key}
        onclick={() => switchView(item.key)}
      >
        {item.label}
      </button>
    {/each}
  </div>

  {#if view === 'albums'}
    <label class="sort">
      <span class="dim">並び順</span>
      <select bind:value={sort}>
        {#each ALBUM_SORTS as key (key)}
          <option value={key}>{SORT_LABELS[key]}</option>
        {/each}
      </select>
    </label>
  {/if}
</div>

{#if initial}
  <div class="filter-chip">
    <span>頭文字「{initial}」で絞り込み中</span>
    <button onclick={clearInitial}>解除</button>
  </div>
{/if}

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if view === 'albums'}
  <AlbumGrid {albums} showYear={sort !== 'added'} />
{:else if view === 'artists'}
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
{:else if view === 'genres'}
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

  .toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    flex-wrap: wrap;
    padding: 10px 12px;
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

  .sort {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
  }

  .sort select {
    min-height: 36px;
    width: auto;
    padding: 4px 10px;
    font-size: 13px;
  }

  .filter-chip {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 8px 14px;
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    font-size: 13px;
  }

  .filter-chip button {
    min-height: 32px;
    padding: 0 12px;
    border-radius: 999px;
    border: 1px solid var(--accent-dim);
    color: var(--accent);
    font-size: 12px;
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
