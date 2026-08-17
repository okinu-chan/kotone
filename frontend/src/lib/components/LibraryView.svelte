<script>
  /**
   * ライブラリ画面。アルバム / アーティスト / ジャンル / 年 の 4 つの入口を持ち、
   * ソート軸の切り替え（要件 3.2）とイニシャルサーチ（要件 3.7）を提供する。
   */
  import AlbumGrid from './AlbumGrid.svelte';
  import AlbumLayoutToggle from './AlbumLayoutToggle.svelte';
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
  });



  $effect(() => {
    const currentView = libraryView.view;
    // 頭出しの指定はここで読んで、この effect の依存に入れる。並び順が
    // 変わる場合は一覧を読み直したあとに飛ばないと位置がずれる
    const jump = libraryView.pendingJump;
    // 頭文字は絞り込みに使わない（イニシャルサーチは頭出し）。一覧は全件のまま
    const params = {
      sort: libraryView.sort,
      codec: libraryView.codec,
      quality: libraryView.quality,
    };

    // 条件が前回と同じなら、取得済みの一覧をそのまま出す。
    // 1,000 件規模では再取得と再描画だけで待たされるため、
    // 他画面から戻ったときに読み込み待ちを挟まないようにする。
    const key = libraryView.cacheKey;
    if (libraryView.cache.key === key) {
      const cached = libraryView.cache;
      albums = cached.albums;
      artists = cached.artists;
      genres = cached.genres;
      years = cached.years;
      total = cached.total;
      loading = false;
      restoreScroll();
      applyJump(jump);
      return;
    }

    loading = true;

    const load = async () => {
      if (currentView === 'albums') {
        const payload = await api.albums(params);
        albums = payload.items;
        total = payload.total;
      } else if (currentView === 'artists') {
        const payload = await api.artists();
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
      .then(() => {
        libraryView.cache = { key, albums, artists, genres, years, total };
      })
      .catch((error) => toast.error(error.detail || String(error)))
      .finally(() => {
        loading = false;
        restoreScroll();
        applyJump(jump);
      });
  });

  /**
   * 一覧が描画されてからスクロール位置を戻す。
   * 戻ってきた直後は中身が空で高さが足りないため、描画後まで待つ必要がある。
   */
  function restoreScroll() {
    const saved = router.savedScroll('/library');
    if (restoredScroll || saved <= 0) return;
    restoredScroll = true;
    requestAnimationFrame(() => {
      requestAnimationFrame(() => window.scrollTo(0, saved));
    });
  }

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

  /**
   * 頭文字が選ばれたら、絞り込まずにその位置まで飛ぶ。
   *
   * 絞り込んでしまうと「か」の並びしか見えず、すぐ上の「お」を見るには
   * いったん解除して探し直すことになる。頭出しなら短いスクロールで届く。
   */
  function onInitialSelected(event) {
    if (event.by) libraryView.initialBy = event.by;
    if (libraryView.view === 'albums') {
      // 並び順が頭文字の対象と食い違っていると「位置」が意味を持たない
      // （アルバム名順の一覧でアーティストの「か」は散らばっている）
      libraryView.sort = event.by === 'album' ? 'album' : 'artist';
    }
    libraryView.pendingJump = { initial: event.initial, group: event.group, by: event.by };
    router.forgetScroll('/library');
  }

  /** 一覧の中で、頭出し先にあたる要素の番号。見つからなければ -1。 */
  function findJumpIndex(jump) {
    const matches = (initial, group) => initial === jump.initial && group === jump.group;
    if (libraryView.view === 'artists') {
      return artists.findIndex((a) => matches(a.initial, a.initial_group));
    }
    if (jump.by === 'album') {
      return albums.findIndex((a) => matches(a.initial, a.initial_group));
    }
    return albums.findIndex((a) =>
      matches(a.album_artist_initial, a.album_artist_initial_group),
    );
  }

  /** 固定表示されているヘッダー類の高さ（この分だけ余裕を持たせて止める）。 */
  function stickyOffset() {
    return ['.page-header', '.toolbar', '.filter-chips'].reduce((sum, selector) => {
      const el = document.querySelector(selector);
      return sum + (el ? el.getBoundingClientRect().height : 0);
    }, 0);
  }

  function applyJump(jump) {
    if (!jump) return;
    const index = findJumpIndex(jump);
    if (index < 0) {
      // 絞り込み（フォーマット・音質）と重なると 0 件になることがある
      libraryView.pendingJump = null;
      toast.info(`「${jump.initial}」で始まるものは見つかりませんでした`);
      return;
    }
    // 一覧が描画され、高さが確定してからでないと正しい位置へ飛べない。
    // requestAnimationFrame は画面が描画されていない状況（バックグラウンド
    // タブなど）では呼ばれないため、タイマーで待つ。
    const selector = JUMP_ITEM_SELECTOR;
    setTimeout(() => {
      const target = document.querySelectorAll(selector)[index];
      if (!target) return;
      libraryView.pendingJump = null;
      const top = target.getBoundingClientRect().top + window.scrollY - stickyOffset() - 8;
      window.scrollTo({ top: Math.max(0, top), behavior: 'auto' });
      flash(target);
    }, 0);
  }

  /** 飛んだ先を一瞬光らせる。どこに着いたのかが分かるように。 */
  function flash(element) {
    element.classList.add('jump-target');
    setTimeout(() => element.classList.remove('jump-target'), 1400);
  }

  const canUseInitialSearch = $derived(['albums', 'artists'].includes(libraryView.view));
  /** 頭出し先を数えるときの 1 件分の要素。表示形式で変わる。 */
  const JUMP_ITEM_SELECTOR = $derived(
    libraryView.view === 'artists'
      ? '.rows > li'
      : libraryView.layout === 'list'
        ? '.list > li'
        : '.grid > .card',
  );
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
      <AlbumLayoutToggle />
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
    font-size: var(--fs-md);
    white-space: nowrap;
  }

  .views button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
  }

  .right-tools {
    display: flex;
    align-items: center;
    gap: 8px;
    flex: none;
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
    font-size: var(--fs-md);
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
    color: var(--on-accent);
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
    font-size: var(--fs-md);
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
    font-size: var(--fs-sm);
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
    font-size: var(--fs-sm);
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
    font-size: var(--fs-lg);
  }

  .reading {
    font-size: var(--fs-xs);
  }

  .count {
    flex: none;
    font-size: var(--fs-sm);
  }
</style>
