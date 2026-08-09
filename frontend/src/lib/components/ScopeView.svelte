<script>
  /**
   * ジャンル / 年での絞り込み表示（要件 3.6）。
   * 値の完全一致によるシンプルなフィルタで、アルバムと曲の両方を出す。
   */
  import AlbumGrid from './AlbumGrid.svelte';
  import PageHeader from './PageHeader.svelte';
  import TrackList from './TrackList.svelte';
  import { api } from '../api.js';
  import { player, toast } from '../stores.svelte.js';
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
      api.albums({ ...params, sort: 'year' }),
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
  </div>

  {#if tab === 'albums'}
    <AlbumGrid {albums} />
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
    color: #0d0f13;
    font-size: 14px;
  }

  .tabs {
    display: flex;
    gap: 6px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
  }

  .tabs button {
    min-height: 38px;
    padding: 0 16px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: 13px;
  }

  .tabs button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #0d0f13;
  }
</style>
