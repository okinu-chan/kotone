<script>
  /**
   * プレイリスト一覧（作成・インポート、名前の変更・並び替え・削除）。要件 3.4。
   *
   * 並び替えはつまみのドラッグ（マウス・タッチ共通。lib/reorder.js）。
   */
  import AlbumArt from './AlbumArt.svelte';
  import Modal from './Modal.svelte';
  import PageHeader from './PageHeader.svelte';
  import { api } from '../api.js';
  import { formatDuration } from '../format.js';
  import { beginDrag, moved } from '../reorder.js';
  import { router } from '../router.svelte.js';
  import { toast } from '../stores.svelte.js';

  let playlists = $state([]);
  let loading = $state(true);
  let creating = $state(false);
  let newName = $state('');
  let fileInput = $state(null);

  /** 「⋯」で開くメニューの対象 */
  let menuOpen = $state(false);
  let menuTarget = $state(null);
  /** 名前の変更 */
  let renameOpen = $state(false);
  let renameDraft = $state('');

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

  function openMenu(playlist) {
    menuTarget = playlist;
    menuOpen = true;
  }

  function startRename() {
    renameDraft = menuTarget?.name ?? '';
    menuOpen = false;
    renameOpen = true;
  }

  async function applyRename() {
    const name = renameDraft.trim();
    if (!name || !menuTarget || name === menuTarget.name) {
      renameOpen = false;
      return;
    }
    try {
      await api.renamePlaylist(menuTarget.id, name);
      renameOpen = false;
      await load();
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function destroy() {
    const target = menuTarget;
    menuOpen = false;
    if (!target) return;
    if (!window.confirm(`「${target.name}」を削除します。よろしいですか？`)) return;
    try {
      await api.deletePlaylist(target.id);
      toast.info('削除しました');
      await load();
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  /**
   * ドラッグ中は手元の配列だけ動かし、指を離したときにまとめて保存する。
   * 1 マスごとに投げると、往復のたびに一覧が作り直されてドラッグが途切れる。
   */
  function onReorder(from, to) {
    playlists = moved(playlists, from, to);
  }

  async function saveOrder() {
    try {
      playlists = (await api.reorderPlaylists(playlists.map((p) => p.id))).items;
    } catch (error) {
      toast.error(error.detail || String(error));
      await load();
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
  <p class="hint dim">左のつまみをドラッグすると並び順を変えられます。</p>
  <ul class="list" data-reorder-list>
    {#each playlists as playlist, index (playlist.id)}
      <li>
        <button
          class="grip"
          onpointerdown={(event) =>
            beginDrag(event, {
              itemSelector: '.list > li',
              from: index,
              onMove: onReorder,
              onEnd: saveOrder,
            })}
          aria-label="ドラッグして並べ替え"
          title="ドラッグして並べ替え"
        >
          <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <circle cx="9" cy="6" r="1.6" /><circle cx="15" cy="6" r="1.6" />
            <circle cx="9" cy="12" r="1.6" /><circle cx="15" cy="12" r="1.6" />
            <circle cx="9" cy="18" r="1.6" /><circle cx="15" cy="18" r="1.6" />
          </svg>
        </button>
        <button class="open" onclick={() => router.navigate(`/playlists/${playlist.id}`)}>
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
        <button class="menu" onclick={() => openMenu(playlist)} aria-label="このプレイリストの操作">
          ⋯
        </button>
      </li>
    {/each}
  </ul>

  <Modal bind:open={menuOpen} title={menuTarget?.name ?? ''}>
    <ul class="menu-list">
      <li><button onclick={startRename}>名前の変更</button></li>
      <li><button class="danger-text" onclick={destroy}>削除</button></li>
    </ul>
    {#snippet footer()}
      <button class="ghost" onclick={() => (menuOpen = false)}>閉じる</button>
    {/snippet}
  </Modal>

  <Modal bind:open={renameOpen} title="名前の変更">
    <input
      type="text"
      bind:value={renameDraft}
      onkeydown={(event) => event.key === 'Enter' && applyRename()}
    />
    {#snippet footer()}
      <button class="ghost" onclick={() => (renameOpen = false)}>取消</button>
      <button class="primary" onclick={applyRename} disabled={!renameDraft.trim()}>保存</button>
    {/snippet}
  </Modal>
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
    display: flex;
    align-items: center;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .list .open {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    min-height: 64px;
    padding: 8px 12px;
    text-align: left;
  }

  .list .open:hover {
    background: var(--surface-hover);
  }

  /* 並び替えのつまみ。touch-action: none が無いと、指の動きが一覧の
     スクロールに取られてドラッグが始まらない */
  .grip {
    display: grid;
    place-items: center;
    width: 36px;
    min-height: var(--tap);
    flex: none;
    color: var(--text-dim);
    touch-action: none;
    cursor: grab;
  }

  .grip:hover {
    color: var(--text);
  }

  .grip svg {
    width: 20px;
    height: 20px;
  }

  .list.reordering {
    user-select: none;
  }

  .list.reordering .grip {
    cursor: grabbing;
  }

  .menu {
    width: var(--tap);
    min-height: var(--tap);
    flex: none;
    color: var(--text-dim);
    font-size: 18px;
  }

  .menu:hover {
    color: var(--text);
  }

  .hint {
    margin: 0;
    padding: 0 16px 10px;
    font-size: var(--fs-sm);
  }

  .menu-list li {
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .menu-list li:last-child {
    border-bottom: none;
  }

  .menu-list button {
    width: 100%;
    min-height: var(--tap);
    padding: 8px 4px;
    text-align: left;
    font-size: var(--fs-lg);
  }

  .menu-list button:hover {
    color: var(--accent);
  }

  .menu-list .danger-text {
    color: var(--danger);
  }

  .ghost {
    min-height: var(--tap);
    padding: 0 20px;
    border-radius: 999px;
    border: 1px solid var(--border);
    color: var(--text-muted);
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
