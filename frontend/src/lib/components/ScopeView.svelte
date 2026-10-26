<script>
  /**
   * ジャンル / 年での絞り込み表示（要件 3.6）。
   * 値の完全一致によるシンプルなフィルタで、アルバムと曲の両方を出す。
   */
  import AlbumGrid from './AlbumGrid.svelte';
  import AlbumLayoutToggle from './AlbumLayoutToggle.svelte';
  import AlbumList from './AlbumList.svelte';
  import PageHeader from './PageHeader.svelte';
  import TrackList from './TrackList.svelte';
  import { untrack } from 'svelte';
  import { api } from '../api.js';
  import { libraryView, player, scan, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  /** kind は 'genre' | 'year' */
  let { kind, value } = $props();

  let albums = $state([]);
  let tracks = $state([]);
  let label = $state('');
  let loading = $state(true);
  let tab = $state('albums');

  $effect(() => {
    const currentKind = kind;
    const currentValue = value;
    loading = true;
    tab = 'albums';

    const params =
      currentKind === 'genre'
        ? { genre_id: Number(currentValue) }
        : { year: Number(currentValue) };

    Promise.all([
      // ジャンル／年で絞った一覧はアルバム名順に並べる（リリース年順だと探しにくい）
      api.albums({ ...params, sort: 'album' }),
      api.tracks({ ...params, sort: 'album' }),
      currentKind === 'genre' ? api.genres() : Promise.resolve(null),
    ])
      .then(([albumPayload, trackPayload, genrePayload]) => {
        albums = albumPayload.items;
        tracks = trackPayload.items;
        if (currentKind === 'genre') {
          const genre = genrePayload?.items.find((g) => g.id === Number(currentValue));
          label = genre?.name ?? 'ジャンル';
        } else {
          label = `${currentValue} 年`;
        }
      })
      .catch((error) => toast.error(error.detail || String(error)))
      .finally(() => {
        loading = false;
      });
  });

  // 表示中にスキャンが終わったら、アルバムの一覧だけその場で読み直す
  let seenGeneration = untrack(() => scan.generation);
  $effect(() => {
    const generation = scan.generation;
    if (generation === seenGeneration) return;
    seenGeneration = generation;
    const [currentKind, currentValue] = untrack(() => [kind, value]);
    const params =
      currentKind === 'genre'
        ? { genre_id: Number(currentValue) }
        : { year: Number(currentValue) };
    api
      .albums({ ...params, sort: 'album' })
      .then((payload) => {
        if (currentKind === kind && currentValue === value) albums = payload.items;
      })
      .catch(() => {
        /* 読み直せなくても、表示中の一覧はそのまま使える */
      });
  });

  async function playAll() {
    try {
      player.apply(await api.playScope(kind, value));
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function playTrack(track) {
    try {
      player.apply(await api.playScope(kind, value, track.id));
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }
</script>

<PageHeader title={label} subtitle={`${albums.length} 枚 / ${tracks.length} 曲`} />

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else}
  <div class="actions">
    <button class="primary" onclick={playAll}>すべて再生</button>
  </div>

  <div class="tabs" role="tablist">
    <button
      role="tab"
      aria-selected={tab === 'albums'}
      class:active={tab === 'albums'}
      onclick={() => (tab = 'albums')}>アルバム</button
    >
    <button
      role="tab"
      aria-selected={tab === 'tracks'}
      class:active={tab === 'tracks'}
      onclick={() => (tab = 'tracks')}>曲</button
    >

    <!-- 表示形式はライブラリ・アーティスト画面と共有する -->
    {#if tab === 'albums'}
      <AlbumLayoutToggle />
    {/if}
  </div>

  {#if tab === 'albums'}
    {#if libraryView.layout === 'list'}
      <AlbumList {albums} />
    {:else}
      <AlbumGrid {albums} />
    {/if}
  {:else}
    <TrackList {tracks} showArtwork onplay={playTrack} />
  {/if}
{/if}

<style>
  .loading {
    display: grid;
    place-items: center;
    padding: 60px;
  }

  .actions {
    padding: 16px 16px 8px;
  }

  .primary {
    min-height: var(--tap);
    padding: 0 24px;
    border-radius: 999px;
    background: var(--accent);
    color: var(--on-accent);
    font-size: var(--fs-lg);
  }

  .tabs {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
  }

  /* タブは「アルバム」「曲」の 2 つだけなので、切り替えは右端へ寄せる */
  .tabs :global(.layout-toggle) {
    margin-left: auto;
  }

  .tabs > button {
    min-height: 38px;
    padding: 0 16px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: var(--fs-md);
  }

  .tabs > button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
  }
</style>
