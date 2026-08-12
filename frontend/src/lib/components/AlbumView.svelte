<script>
  /** アルバム詳細。曲を選ぶとその曲から再生する（要件 3.4）。 */
  import AddToPlaylist from './AddToPlaylist.svelte';
  import AlbumArt from './AlbumArt.svelte';
  import PageHeader from './PageHeader.svelte';
  import TagEditor from './TagEditor.svelte';
  import TrackList from './TrackList.svelte';
  import { api } from '../api.js';
  import { formatDuration } from '../format.js';
  import { player, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  let { id } = $props();

  let album = $state(null);
  let loading = $state(true);
  let addOpen = $state(false);
  let editorOpen = $state(false);
  let editingTrack = $state(null);

  $effect(() => {
    const albumId = id;
    loading = true;
    api
      .album(albumId)
      .then((payload) => {
        album = payload;
      })
      .catch((error) => toast.error(error.detail || String(error)))
      .finally(() => {
        loading = false;
      });
  });

  async function playFrom(track) {
    try {
      player.apply(await api.playScope('album', id, track.id));
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function playAll() {
    try {
      player.apply(await api.playScope('album', id));
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  function openEditor(track) {
    editingTrack = track;
    editorOpen = true;
  }

  function onTrackSaved(updated) {
    if (!album) return;
    album = {
      ...album,
      tracks: album.tracks.map((t) => (t.id === updated.id ? updated : t)),
    };
  }

  /**
   * ディスクごとに区切る。
   *
   * タグからディスク番号が取れる（複数ディスクがある）場合だけ見出しを出す。
   * 1 枚もののアルバムに「ディスク 1」と出しても情報量が無いため。
   */
  const discs = $derived.by(() => {
    const tracks = album?.tracks ?? [];
    const numbers = [...new Set(tracks.map((t) => t.disc_no || 1))].sort((a, b) => a - b);
    if (numbers.length <= 1) return [{ number: null, total: 1, tracks }];
    return numbers.map((number) => ({
      number,
      total: numbers.length,
      tracks: tracks.filter((t) => (t.disc_no || 1) === number),
    }));
  });

  const discCount = $derived(discs.length);
</script>

<PageHeader title={album?.name ?? 'アルバム'} subtitle={album?.album_artist ?? ''} />

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if album}
  <div class="hero">
    <div class="cover"><AlbumArt {album} tier="medium" alt="" eager /></div>
    <div class="info">
      <h2 class="clamp-2">{album.name}</h2>
      <div class="chips">
        {#each album.artists ?? [] as artist (artist.id)}
          <button class="chip" onclick={() => router.navigate(`/artists/${artist.id}`)}>
            {artist.name}
          </button>
        {/each}
        {#if album.year}
          <button class="chip" onclick={() => router.navigate(`/years/${album.year}`)}>
            {album.year} 年
          </button>
        {/if}
      </div>
      <p class="dim tabular">
        {#if discCount > 1}{discCount} 枚組<span class="sep">·</span>{/if}{album.track_count}
        曲{#if album.duration}<span class="sep">·</span>{formatDuration(album.duration)}{/if}
      </p>
      <div class="actions">
        <button class="primary" onclick={playAll}>再生</button>
        <button class="ghost" onclick={() => (addOpen = true)}>プレイリストへ追加</button>
      </div>
    </div>
  </div>

  {#each discs as disc (disc.number ?? 'single')}
    {#if disc.number !== null}
      <h3 class="disc-heading">
        <span class="disc-label">ディスク {disc.number}</span>
        <span class="disc-count dim tabular">{disc.tracks.length} 曲</span>
      </h3>
    {/if}
    <TrackList
      tracks={disc.tracks}
      useTrackNumber
      showArtist={false}
      onplay={playFrom}
      onmenu={openEditor}
    />
  {/each}

  <AddToPlaylist bind:open={addOpen} albumId={album.id} />
  <TagEditor bind:open={editorOpen} track={editingTrack} onsaved={onTrackSaved} />
{:else}
  <p class="empty-state">アルバムが見つかりません。</p>
{/if}

<style>
  .loading {
    display: grid;
    place-items: center;
    padding: 60px;
  }

  .hero {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 18px;
    padding: 20px 20px 24px;
    text-align: center;
  }

  .cover {
    width: min(260px, 62vw);
  }

  .info {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    width: 100%;
    min-width: 0;
  }

  h2 {
    font-size: 20px;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
  }

  .chip {
    min-height: 34px;
    padding: 6px 12px;
    border-radius: 999px;
    background: var(--surface);
    border: 1px solid var(--border);
    font-size: 13px;
  }

  .chip:hover {
    background: var(--surface-hover);
    border-color: var(--accent-dim);
  }

  .info p {
    margin: 0;
    font-size: 12px;
  }

  .sep {
    margin: 0 6px;
    opacity: 0.5;
  }

  .actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    justify-content: center;
    margin-top: 4px;
  }

  .primary,
  .ghost {
    min-height: var(--tap);
    padding: 0 24px;
    border-radius: 999px;
    font-size: 14px;
  }

  .primary {
    background: var(--accent);
    color: #0d0f13;
  }

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }

  .ghost:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .disc-heading {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin: 0;
    padding: 16px 16px 6px;
    border-top: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
    font-size: 12px;
    letter-spacing: 0.06em;
  }

  .disc-heading:first-of-type {
    border-top: none;
  }

  .disc-label {
    color: var(--accent);
    font-weight: 600;
  }

  .disc-count {
    font-size: 11.5px;
  }

  @media (min-width: 760px) {
    .hero {
      flex-direction: row;
      align-items: flex-start;
      text-align: left;
      padding: 24px;
    }

    .cover {
      width: 240px;
      flex: none;
    }

    .info {
      align-items: flex-start;
    }

    .chips,
    .actions {
      justify-content: flex-start;
    }
  }
</style>
