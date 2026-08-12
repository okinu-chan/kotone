<script>
  /** プレイリスト一覧（作成・インポート）。要件 3.4。 */
  import AlbumArt from './AlbumArt.svelte';
  import PageHeader from './PageHeader.svelte';
  import { api } from '../api.js';
  import { formatDuration } from '../format.js';
  import { router } from '../router.svelte.js';
  import { toast } from '../stores.svelte.js';

  let playlists = $state([]);
  let loading = $state(true);
  let creating = $state(false);
  let newName = $state('');
  let fileInput = $state(null);

  async function load() {
    loading = true;
    try {
      playlists = (await api.playlists()).items;
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    load();
  });

  async function create() {
    const name = newName.trim();
    if (!name) return;
    try {
      const created = await api.createPlaylist(name);
      newName = '';
      creating = false;
      await load();
      router.navigate(`/playlists/${created.id}`);
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function onImport(event) {
    const file = event.currentTarget.files?.[0];
    if (!file) return;
    try {
      const result = await api.importPlaylist(file);
      const message =
        result.unresolved_count > 0
          ? `${result.imported} 曲を取り込みました（${result.unresolved_count} 曲は見つかりませんでした）`
          : `${result.imported} 曲を取り込みました`;
      toast.info(message);
      await load();
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      event.currentTarget.value = '';
    }
  }
</script>

<PageHeader title="プレイリスト" subtitle={`${playlists.length} 件`}>
  {#snippet actions()}
    <button class="icon-btn" onclick={() => (creating = !creating)} aria-label="新規作成">＋</button>
    <button class="icon-btn" onclick={() => fileInput?.click()} aria-label="m3u をインポート">
      ⇩
    </button>
  {/snippet}
</PageHeader>

<input
  bind:this={fileInput}
  class="hidden-file"
  type="file"
  accept=".m3u,.m3u8,audio/x-mpegurl"
  onchange={onImport}
/>

{#if creating}
  <div class="create">
    <input
      type="text"
      bind:value={newName}
      placeholder="プレイリスト名"
      onkeydown={(event) => event.key === 'Enter' && create()}
    />
    <button class="primary" onclick={create} disabled={!newName.trim()}>作成</button>
  </div>
{/if}

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if playlists.length === 0}
  <p class="empty-state">
    プレイリストがありません。<br />右上の ＋ から作成するか、m3u をインポートできます。
  </p>
{:else}
  <ul class="list">
    {#each playlists as playlist (playlist.id)}
      <li>
        <button onclick={() => router.navigate(`/playlists/${playlist.id}`)}>
          <div class="thumb">
            <AlbumArt
              album={playlist.artwork_hash
                ? { id: playlist.artwork_album_id, artwork: { hash: playlist.artwork_hash, url: `/api/artwork/${playlist.artwork_album_id}` } }
                : null}
              tier="small"
              alt=""
            />
          </div>
          <span class="text">
            <span class="name truncate">{playlist.name}</span>
            <span class="meta truncate dim tabular">
              {playlist.track_count} 曲{#if playlist.duration}<span class="dot">·</span>{formatDuration(playlist.duration)}{/if}
            </span>
          </span>
        </button>
      </li>
    {/each}
  </ul>
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
    font-size: 16px;
  }

  .icon-btn:hover {
    background: var(--surface-hover);
    border-color: var(--accent-dim);
  }

  .hidden-file {
    display: none;
  }

  .create {
    display: flex;
    gap: 8px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--border);
  }

  .primary {
    flex: none;
    min-height: var(--tap);
    padding: 0 20px;
    border-radius: var(--radius);
    background: var(--accent);
    color: var(--on-accent);
    font-size: var(--fs-lg);
  }

  .primary:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .loading {
    display: grid;
    place-items: center;
    padding: 60px;
  }

  .list li {
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .list button {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    min-height: 64px;
    padding: 8px 16px;
    text-align: left;
  }

  .list button:hover {
    background: var(--surface-hover);
  }

  .thumb {
    width: 48px;
    height: 48px;
    flex: none;
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

  .meta {
    font-size: var(--fs-sm);
  }

  .dot {
    margin: 0 5px;
    opacity: 0.6;
  }
</style>
