<script>
  /**
   * プレイリスト詳細。曲の追加・削除・並び替えができ、
   * 操作のたびに自動保存される（明示的な保存操作は無い。要件 3.4）。
   *
   * 並び替えはつまみをドラッグする（マウス・タッチ共通。lib/reorder.js）。
   * 曲ごとの操作は右端の「⋯」から。名前の変更や削除といったプレイリスト
   * 自体の操作は一覧画面（PlaylistsView）に置いてある。
   */
  import AddToPlaylist from './AddToPlaylist.svelte';
  import Modal from './Modal.svelte';
  import PageHeader from './PageHeader.svelte';
  import TagEditor from './TagEditor.svelte';
  import TrackList from './TrackList.svelte';
  import { api } from '../api.js';
  import { formatDuration } from '../format.js';
  import { moved } from '../reorder.js';
  import { player, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  let { id } = $props();

  let playlist = $state(null);
  let loading = $state(true);

  /** 「⋯」で開くメニューの対象 */
  let menuOpen = $state(false);
  let menuTrack = $state(null);
  let editorOpen = $state(false);
  let editingTrack = $state(null);
  let addOpen = $state(false);
  /** プレイリストへ追加するときの対象（1 曲でも複数選択でも同じ経路を使う） */
  let addTrackIds = $state([]);

  /**
   * 削除の確認。
   *
   * 「⋯」を開いてすぐ下に削除があるので、押し間違いで消えてしまう。
   * 何を消すのかを見せてから確定させる。
   */
  let confirmOpen = $state(false);
  let pendingRemoval = $state({ positions: [], titles: [] });

  /** 複数選択モード。鍵は曲 id ではなく並び順（同じ曲が複数回入りうる） */
  let selecting = $state(false);
  let selectedPositions = $state(new Set());

  const tracks = $derived(playlist?.tracks ?? []);
  const allSelected = $derived(
    tracks.length > 0 && selectedPositions.size === tracks.length,
  );

  async function load() {
    loading = true;
    try {
      playlist = await api.playlist(id);
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

  async function playFrom(track, index) {
    try {
      // 同じ曲が複数回入っていても、押した行から始めたいので位置で指定する
      player.apply(await api.playScope('playlist', id, tracks[index]?.id ?? track.id));
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

  /* ------------------------------------------------------------ 並び替え */

  /**
   * ドラッグ中は手元の配列だけ動かし、指を離したときにまとめて保存する。
   * 1 マスごとにサーバーへ投げると、往復のたびに一覧が作り直されて
   * ドラッグが途切れる。
   */
  function onReorder(from, to) {
    if (!playlist) return;
    playlist = { ...playlist, tracks: moved(playlist.tracks, from, to) };
  }

  async function saveOrder() {
    try {
      playlist = await api.reorderPlaylist(id, tracks.map((t) => t.id));
    } catch (error) {
      toast.error(error.detail || String(error));
      await load();   // 失敗したらサーバーの状態へ戻す
    }
  }

  /* -------------------------------------------------------- 曲ごとの操作 */

  function openMenu(track, index) {
    menuTrack = { ...track, index };
    menuOpen = true;
  }

  function renameTrack() {
    editingTrack = menuTrack;
    menuOpen = false;
    editorOpen = true;
  }

  function onTrackSaved(updated) {
    if (!playlist) return;
    playlist = {
      ...playlist,
      tracks: playlist.tracks.map((t) => (t.id === updated.id ? { ...t, ...updated } : t)),
    };
  }

  function removeOne() {
    const track = menuTrack;
    menuOpen = false;
    if (!track) return;
    askRemoval([track.position], [track.title]);
  }

  function askRemoval(positions, titles) {
    pendingRemoval = { positions, titles };
    confirmOpen = true;
  }

  async function confirmRemoval() {
    const { positions } = pendingRemoval;
    confirmOpen = false;
    cancelSelecting();
    await removeAt(positions);
  }

  function addOne() {
    addTrackIds = [menuTrack.id];
    menuOpen = false;
    addOpen = true;
  }

  async function removeAt(positions) {
    try {
      playlist = await api.removeFromPlaylist(id, positions);
      toast.info(`${positions.length} 曲を削除しました`);
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  /* -------------------------------------------------------- 複数選択 */

  function startSelecting() {
    selectedPositions = new Set();
    selecting = true;
  }

  function cancelSelecting() {
    selecting = false;
    selectedPositions = new Set();
  }

  function toggleTrack(track) {
    const next = new Set(selectedPositions);
    if (next.has(track.position)) next.delete(track.position);
    else next.add(track.position);
    selectedPositions = next;
  }

  function toggleAll() {
    selectedPositions = allSelected
      ? new Set()
      : new Set(tracks.map((t) => t.position));
  }

  function removeSelected() {
    const positions = [...selectedPositions];
    if (positions.length === 0) return;
    const titles = tracks
      .filter((t) => selectedPositions.has(t.position))
      .map((t) => t.title);
    askRemoval(positions, titles);
  }

  function addSelected() {
    // プレイリスト内の並び順どおりに渡す
    addTrackIds = tracks.filter((t) => selectedPositions.has(t.position)).map((t) => t.id);
    if (addTrackIds.length === 0) return;
    addOpen = true;
  }

  function onAdded() {
    cancelSelecting();
  }
</script>

<PageHeader
  title={playlist?.name ?? 'プレイリスト'}
  subtitle={playlist ? `${playlist.track_count} 曲 · ${formatDuration(playlist.duration)}` : ''}
/>

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if !playlist}
  <p class="empty-state">プレイリストが見つかりません。</p>
{:else}
  <div class="actions">
    <button class="primary" onclick={playAll} disabled={playlist.track_count === 0}>再生</button>
    <button class="ghost" onclick={startSelecting} disabled={selecting || tracks.length === 0}>
      選択
    </button>
    <a class="ghost" href={api.exportPlaylistUrl(id)} download>m3u で書き出し</a>
  </div>

  {#if selecting}
    <div class="select-bar">
      <span class="count tabular">{selectedPositions.size} 曲を選択中</span>
      <div class="select-actions">
        <button class="ghost" onclick={toggleAll}>
          {allSelected ? 'すべて解除' : 'すべて選択'}
        </button>
        <button class="ghost" onclick={cancelSelecting}>やめる</button>
        <button class="ghost" onclick={addSelected} disabled={selectedPositions.size === 0}>
          追加
        </button>
        <button class="danger" onclick={removeSelected} disabled={selectedPositions.size === 0}>
          削除
        </button>
      </div>
    </div>
  {:else}
    <p class="hint dim">左のつまみをドラッグすると曲順を変えられます。</p>
  {/if}

  <TrackList
    {tracks}
    showArtwork
    onplay={playFrom}
    onmenu={openMenu}
    reorderable={!selecting}
    onreorder={onReorder}
    oncommit={saveOrder}
    selectable={selecting}
    selectedIds={selectedPositions}
    selectKey={(track) => track.position}
    ontoggleselect={toggleTrack}
  />

  <!-- 曲ごとのメニュー -->
  <Modal bind:open={menuOpen} title={menuTrack?.title ?? ''}>
    <ul class="menu-list">
      <li><button onclick={renameTrack}>名前の変更</button></li>
      <li><button onclick={addOne}>プレイリストに追加</button></li>
      <li><button class="danger-text" onclick={removeOne}>楽曲の削除</button></li>
    </ul>
    {#snippet footer()}
      <button class="ghost" onclick={() => (menuOpen = false)}>閉じる</button>
    {/snippet}
  </Modal>

  <!-- 削除の確認。消える曲名をそのまま見せる -->
  <Modal bind:open={confirmOpen} title="プレイリストから削除しますか？">
    <p class="confirm-lead">この {pendingRemoval.positions.length} 曲をプレイリストから外します。</p>
    <ul class="confirm-list">
      {#each pendingRemoval.titles.slice(0, 12) as title, index (index)}
        <li class="truncate">{title}</li>
      {/each}
    </ul>
    {#if pendingRemoval.titles.length > 12}
      <p class="confirm-more dim">ほか {pendingRemoval.titles.length - 12} 曲</p>
    {/if}
    <p class="confirm-note dim">音源そのものは消えません。</p>
    {#snippet footer()}
      <button class="ghost" onclick={() => (confirmOpen = false)}>取消</button>
      <button class="danger" onclick={confirmRemoval}>削除</button>
    {/snippet}
  </Modal>

  <TagEditor bind:open={editorOpen} track={editingTrack} onsaved={onTrackSaved} />
  <AddToPlaylist bind:open={addOpen} trackIds={addTrackIds} onadded={onAdded} />
{/if}

<style>
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

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }

  .ghost:hover:not(:disabled) {
    background: var(--surface-hover);
    color: var(--text);
  }

  .danger {
    border: 1px solid color-mix(in srgb, var(--danger) 50%, transparent);
    color: var(--danger);
  }

  .primary:disabled,
  .ghost:disabled,
  .danger:disabled {
    opacity: 0.45;
    cursor: default;
  }

  /* 選択中の操作バー。曲数が多くてもスクロール中に届くよう固定する */
  .select-bar {
    position: sticky;
    top: calc(var(--tap) + var(--safe-top) + 16px);
    z-index: 19;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 10px;
    padding: 10px 16px;
    background: var(--bg-elevated);
    border-top: 1px solid var(--border);
    border-bottom: 1px solid var(--border);
  }

  .select-bar .count {
    font-size: var(--fs-md);
    color: var(--text-muted);
  }

  .select-actions {
    display: flex;
    gap: 8px;
  }

  .select-actions .ghost,
  .select-actions .danger {
    min-height: 40px;
    padding: 0 14px;
    font-size: var(--fs-md);
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

  .confirm-lead {
    margin: 0 0 10px;
    font-size: var(--fs-lg);
  }

  .confirm-list {
    margin: 0;
    max-height: 40vh;
    overflow-y: auto;
  }

  .confirm-list li {
    padding: 6px 0;
    font-size: var(--fs-md);
    color: var(--text-muted);
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .confirm-more,
  .confirm-note {
    margin: 10px 0 0;
    font-size: var(--fs-sm);
  }
</style>
