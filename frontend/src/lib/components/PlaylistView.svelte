<script>
  /**
   * プレイリスト詳細。曲の追加・削除・並び替えができ、
   * 操作のたびに自動保存される（明示的な保存操作は無い。要件 3.4）。
   */
  import PageHeader from './PageHeader.svelte';
  import TrackList from './TrackList.svelte';
  import { api } from '../api.js';
  import { formatDuration } from '../format.js';
  import { player, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  let { id } = $props();

  let playlist = $state(null);
  let loading = $state(true);
  let editMode = $state(false);
  let renaming = $state(false);
  let nameDraft = $state('');

  async function load() {
    loading = true;
    try {
      playlist = await api.playlist(id);
      nameDraft = playlist?.name ?? '';
    } catch (error) {
      toast.error(error.detail || String(error));
      playlist = null;
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    void id;
    load();
  });

  async function playFrom(track) {
    try {
      player.apply(await api.playScope('playlist', id, track.id));
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function playAll() {
    try {
      player.apply(await api.playScope('playlist', id));
      router.navigate('/');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function remove(position) {
    try {
      playlist = await api.removeFromPlaylist(id, [position]);
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function move(position, delta) {
    const to = position + delta;
    if (to < 0 || to >= (playlist?.tracks.length ?? 0)) return;
    try {
      playlist = await api.movePlaylistTrack(id, position, to);
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function rename() {
    const name = nameDraft.trim();
    if (!name || name === playlist?.name) {
      renaming = false;
      return;
    }
    try {
      playlist = await api.renamePlaylist(id, name);
      renaming = false;
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function destroy() {
    if (!window.confirm(`「${playlist?.name}」を削除します。よろしいですか？`)) return;
    try {
      await api.deletePlaylist(id);
      toast.info('削除しました');
      router.navigate('/playlists', { replace: true });
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }
</script>

<PageHeader
  title={playlist?.name ?? 'プレイリスト'}
  subtitle={playlist
    ? `${playlist.track_count} 曲 · ${formatDuration(playlist.duration)}`
    : ''}
>
  {#snippet actions()}
    <button
      class="icon-btn"
      class:active={editMode}
      onclick={() => (editMode = !editMode)}
      aria-label="編集モード">✎</button
    >
  {/snippet}
</PageHeader>

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if !playlist}
  <p class="empty-state">プレイリストが見つかりません。</p>
{:else}
  <div class="actions">
    <button class="primary" onclick={playAll} disabled={playlist.track_count === 0}>再生</button>
    <a class="ghost" href={api.exportPlaylistUrl(id)} download>m3u で書き出し</a>
    {#if editMode}
      <button class="ghost" onclick={() => (renaming = true)}>名前を変更</button>
      <button class="danger" onclick={destroy}>削除</button>
    {/if}
  </div>

  {#if renaming}
    <div class="rename">
      <input
        type="text"
        bind:value={nameDraft}
        onkeydown={(event) => event.key === 'Enter' && rename()}
      />
      <button class="primary" onclick={rename}>保存</button>
      <button class="ghost" onclick={() => (renaming = false)}>取消</button>
    </div>
  {/if}

  {#if editMode}
    <ul class="edit-list">
      {#each playlist.tracks as track (track.id + '-' + track.position)}
        <li>
          <span class="text">
            <span class="title truncate">{track.title}</span>
            <span class="sub truncate dim">{track.artist}</span>
          </span>
          <div class="row-actions">
            <button onclick={() => move(track.position, -1)} aria-label="上へ移動">▲</button>
            <button onclick={() => move(track.position, 1)} aria-label="下へ移動">▼</button>
            <button class="remove" onclick={() => remove(track.position)} aria-label="削除">✕</button>
          </div>
        </li>
      {/each}
    </ul>
    <p class="hint dim">変更は操作のたびに自動保存されます。</p>
  {:else}
    <TrackList tracks={playlist.tracks} showArtwork onplay={playFrom} />
  {/if}
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

  .icon-btn.active {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
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
    padding: 16px;
  }

  .primary,
  .ghost,
  .danger {
    display: inline-flex;
    align-items: center;
    min-height: var(--tap);
    padding: 0 20px;
    border-radius: 999px;
    font-size: var(--fs-lg);
  }

  .primary {
    background: var(--accent);
    color: var(--on-accent);
  }

  .primary:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }

  .ghost:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .danger {
    border: 1px solid color-mix(in srgb, var(--danger) 50%, transparent);
    color: var(--danger);
  }

  .rename {
    display: flex;
    gap: 8px;
    padding: 0 16px 16px;
  }

  .edit-list li {
    display: flex;
    align-items: center;
    gap: 12px;
    min-height: 56px;
    padding: 6px 12px 6px 16px;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .text {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.35;
  }

  .title {
    font-size: var(--fs-lg);
  }

  .sub {
    font-size: var(--fs-sm);
  }

  .row-actions {
    display: flex;
    gap: 2px;
    flex: none;
  }

  .row-actions button {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 50%;
    color: var(--text-dim);
    font-size: var(--fs-sm);
  }

  .row-actions button:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .row-actions .remove:hover {
    color: var(--danger);
  }

  .hint {
    padding: 14px 16px;
    font-size: var(--fs-sm);
    text-align: center;
  }
</style>
