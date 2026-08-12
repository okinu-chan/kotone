<script>
  /**
   * 「曲・アルバムを選択してから既存／新規プレイリストへ追加」のシート（要件 3.4）。
   */
  import Modal from './Modal.svelte';
  import { api } from '../api.js';
  import { toast } from '../stores.svelte.js';

  let { open = $bindable(false), trackIds = [], albumId = null, artistId = null } = $props();

  let playlists = $state([]);
  let newName = $state('');
  let busy = $state(false);

  $effect(() => {
    if (!open) return;
    api
      .playlists()
      .then((payload) => {
        playlists = payload.items;
      })
      .catch((error) => toast.error(error.detail || String(error)));
  });

  const payload = $derived({
    track_ids: trackIds,
    album_id: albumId,
    artist_id: artistId,
  });

  async function addTo(playlist) {
    busy = true;
    try {
      const updated = await api.addToPlaylist(playlist.id, payload);
      toast.info(`「${playlist.name}」に追加しました（${updated.track_count} 曲）`);
      open = false;
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      busy = false;
    }
  }

  async function createAndAdd() {
    const name = newName.trim();
    if (!name) return;
    busy = true;
    try {
      const created = await api.createPlaylist(name);
      await api.addToPlaylist(created.id, payload);
      toast.info(`「${name}」を作成して追加しました`);
      newName = '';
      open = false;
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      busy = false;
    }
  }
</script>

<Modal bind:open title="プレイリストへ追加">
  <div class="create">
    <input
      type="text"
      bind:value={newName}
      placeholder="新しいプレイリスト名"
      onkeydown={(event) => event.key === 'Enter' && createAndAdd()}
    />
    <button class="primary" onclick={createAndAdd} disabled={busy || !newName.trim()}>
      作成して追加
    </button>
  </div>

  <ul class="list">
    {#each playlists as playlist (playlist.id)}
      <li>
        <button onclick={() => addTo(playlist)} disabled={busy}>
          <span class="truncate">{playlist.name}</span>
          <span class="dim tabular">{playlist.track_count} 曲</span>
        </button>
      </li>
    {/each}
  </ul>

  {#if playlists.length === 0}
    <p class="empty-state">既存のプレイリストはありません。</p>
  {/if}
</Modal>

<style>
  .create {
    display: flex;
    gap: 8px;
    margin-bottom: 16px;
  }

  .primary {
    flex: none;
    min-height: var(--tap);
    padding: 0 16px;
    border-radius: var(--radius);
    background: var(--accent);
    color: var(--on-accent);
    font-size: var(--fs-lg);
    white-space: nowrap;
  }

  .primary:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .list li {
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .list button {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    width: 100%;
    min-height: var(--tap);
    padding: 8px 4px;
    text-align: left;
    font-size: var(--fs-lg);
  }

  .list button:hover {
    color: var(--accent);
  }
</style>
