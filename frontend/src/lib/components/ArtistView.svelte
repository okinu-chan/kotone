<script>
  /**
   * アーティスト詳細（ダイレクトサーチの中心。要件 3.6）。
   *
   * そのアーティストを含む全曲一覧（All ○○）と、収録アルバム一覧を表示する。
   * 曲を選ぶとその曲が収録されるアルバムのスコープで再生を開始し、
   * さらにそこから別のアーティストへ辿れる。
   */
  import AddToPlaylist from './AddToPlaylist.svelte';
  import AlbumGrid from './AlbumGrid.svelte';
  import ArtistEditor from './ArtistEditor.svelte';
  import PageHeader from './PageHeader.svelte';
  import TrackList from './TrackList.svelte';
  import { api } from '../api.js';
  import { player, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  let { id } = $props();

  let artist = $state(null);
  let loading = $state(true);
  let tab = $state('albums');
  let addOpen = $state(false);
  let editorOpen = $state(false);

  $effect(() => {
    const artistId = id;
    loading = true;
    tab = 'albums';
    api
      .artist(artistId)
      .then((payload) => {
        artist = payload;
      })
      .catch((error) => toast.error(error.detail || String(error)))
      .finally(() => {
        loading = false;
      });
  });

  async function playTrack(track) {
    try {
      // 曲を選んだらその曲が収録されるアルバムへ遷移して再生を開始する
      if (track.album_id) {
        player.apply(await api.playScope('album', track.album_id, track.id));
        router.navigate(`/albums/${track.album_id}`);
      } else {
        player.apply(await api.playTracks([track.id]));
        router.navigate('/');
      }
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function playAll(order) {
    try {
      if (order) await api.setOrder(order);
      player.apply(await api.playScope('artist', id));
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }
</script>

<PageHeader
  title={artist?.name ?? 'アーティスト'}
  subtitle={artist?.reading && artist.reading !== artist.name ? artist.reading : ''}
>
  {#snippet actions()}
    {#if artist}
      <button
        class="icon-btn"
        onclick={() => (editorOpen = true)}
        aria-label="アーティスト情報を編集">✎</button
      >
    {/if}
  {/snippet}
</PageHeader>

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if artist}
  <div class="actions">
    <button class="primary" onclick={() => playAll(null)}>すべて再生</button>
    <button class="ghost" onclick={() => playAll('shuffle')}>シャッフル再生</button>
    <button class="ghost" onclick={() => (addOpen = true)}>プレイリストへ追加</button>
  </div>

  <div class="tabs" role="tablist">
    <button
      role="tab"
      aria-selected={tab === 'albums'}
      class:active={tab === 'albums'}
      onclick={() => (tab = 'albums')}
    >
      アルバム（{artist.albums.length}）
    </button>
    <button
      role="tab"
      aria-selected={tab === 'tracks'}
      class:active={tab === 'tracks'}
      onclick={() => (tab = 'tracks')}
    >
      All {artist.name}（{artist.tracks.length} 曲）
    </button>
  </div>

  {#if tab === 'albums'}
    <AlbumGrid albums={artist.albums} />
  {:else}
    <TrackList tracks={artist.tracks} showArtwork onplay={playTrack} />
  {/if}

  <AddToPlaylist bind:open={addOpen} artistId={artist.id} />
  <ArtistEditor
    bind:open={editorOpen}
    {artist}
    onsaved={(updated) => (artist = { ...artist, ...updated })}
  />
{:else}
  <p class="empty-state">アーティストが見つかりません。</p>
{/if}

<style>
  .icon-btn {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 50%;
    background: var(--surface);
    border: 1px solid var(--border);
    font-size: var(--fs-lg);
  }

  .icon-btn:hover {
    background: var(--surface-hover);
    border-color: var(--accent-dim);
  }

  .loading {
    display: grid;
    place-items: center;
    padding: 60px;
  }

  .actions {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    padding: 16px 16px 8px;
  }

  .primary,
  .ghost {
    min-height: var(--tap);
    padding: 0 20px;
    border-radius: 999px;
    font-size: var(--fs-lg);
  }

  .primary {
    background: var(--accent);
    color: var(--on-accent);
  }

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }

  .ghost:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .tabs {
    display: flex;
    gap: 6px;
    padding: 8px 16px;
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
    scrollbar-width: none;
  }

  .tabs::-webkit-scrollbar {
    display: none;
  }

  .tabs button {
    min-height: 38px;
    padding: 0 14px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: var(--fs-md);
    white-space: nowrap;
  }

  .tabs button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
  }
</style>
