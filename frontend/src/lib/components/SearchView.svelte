<script>
  /** 横断検索。曲名・アルバム名・アーティスト名を、表記と読みの両方で探す。 */
  import AlbumArt from './AlbumArt.svelte';
  import PageHeader from './PageHeader.svelte';
  import TrackList from './TrackList.svelte';
  import { api } from '../api.js';
  import { player, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  let query = $state('');
  let results = $state({ artists: [], albums: [], tracks: [] });
  let searching = $state(false);
  let timer = null;

  $effect(() => {
    const q = query.trim();
    clearTimeout(timer);
    if (!q) {
      results = { artists: [], albums: [], tracks: [] };
      return;
    }
    searching = true;
    timer = setTimeout(async () => {
      try {
        results = await api.search(q, 30);
      } catch (error) {
        toast.error(error.detail || String(error));
      } finally {
        searching = false;
      }
    }, 220);
    return () => clearTimeout(timer);
  });

  async function playTrack(track) {
    try {
      if (track.album_id) {
        player.apply(await api.playScope('album', track.album_id, track.id));
      } else {
        player.apply(await api.playTracks([track.id]));
      }
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  const empty = $derived(
    query.trim() &&
      !searching &&
      results.artists.length === 0 &&
      results.albums.length === 0 &&
      results.tracks.length === 0,
  );
</script>

<PageHeader title="検索" />

<div class="searchbar">
  <input
    type="search"
    bind:value={query}
    placeholder="曲名・アルバム・アーティスト（読みでも可）"
    autocomplete="off"
  />
</div>

{#if searching}
  <div class="loading"><div class="spinner"></div></div>
{:else if empty}
  <p class="empty-state">見つかりませんでした。</p>
{:else}
  {#if results.artists.length}
    <h2 class="section">アーティスト</h2>
    <ul class="rows">
      {#each results.artists as artist (artist.id)}
        <li>
          <button onclick={() => router.navigate(`/artists/${artist.id}`)}>
            <span class="truncate">{artist.name}</span>
            <span class="dim tabular">{artist.track_count} 曲</span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if results.albums.length}
    <h2 class="section">アルバム</h2>
    <ul class="rows">
      {#each results.albums as album (album.id)}
        <li>
          <button onclick={() => router.navigate(`/albums/${album.id}`)}>
            <span class="thumb"><AlbumArt {album} tier="small" alt="" /></span>
            <span class="text">
              <span class="truncate">{album.name}</span>
              <span class="truncate dim">{album.album_artist ?? ''}</span>
            </span>
          </button>
        </li>
      {/each}
    </ul>
  {/if}

  {#if results.tracks.length}
    <h2 class="section">曲</h2>
    <TrackList tracks={results.tracks} showArtwork onplay={playTrack} />
  {/if}
{/if}

<style>
  .searchbar {
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
  }

  .loading {
    display: grid;
    place-items: center;
    padding: 60px;
  }

  .section {
    padding: 18px 16px 6px;
    font-size: var(--fs-sm);
    letter-spacing: 0.08em;
    color: var(--text-dim);
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
    padding: 6px 16px;
    text-align: left;
    font-size: var(--fs-lg);
  }

  .rows button:hover {
    background: var(--surface-hover);
  }

  .thumb {
    width: 44px;
    height: 44px;
    flex: none;
  }

  .text {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.35;
  }

  .text .dim {
    font-size: var(--fs-sm);
  }
</style>
