<script>
  /** アルバム詳細。曲を選ぶとその曲から再生する（要件 3.4）。 */
  import AddToPlaylist from './AddToPlaylist.svelte';
  import AlbumArt from './AlbumArt.svelte';
  import AlbumEditor from './AlbumEditor.svelte';
  import ArtworkViewer from './ArtworkViewer.svelte';
  import PageHeader from './PageHeader.svelte';
  import TagEditor from './TagEditor.svelte';
  import TrackList from './TrackList.svelte';
  import { untrack } from 'svelte';
  import { api } from '../api.js';
  import { formatDuration } from '../format.js';
  import { player, scan, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  let { id } = $props();

  let album = $state(null);
  let loading = $state(true);
  let addOpen = $state(false);
  let editorOpen = $state(false);
  let editingTrack = $state(null);
  let albumEditorOpen = $state(false);
  let zoomOpen = $state(false);

  /**
   * プレイリストへ入れる曲の選択。
   *
   * 「プレイリストへ追加」を押すとまず選択モードに入る。入った時点では
   * 何も選ばれていない状態にして、要る曲だけを拾ってもらう。1 枚まるごとは
   * 「すべて選択」1 タップで済む。
   */
  let selecting = $state(false);
  let selectedIds = $state(new Set());

  const allTrackIds = $derived((album?.tracks ?? []).map((t) => t.id));
  const allSelected = $derived(
    allTrackIds.length > 0 && selectedIds.size === allTrackIds.length,
  );
  /**
   * 追加する曲 id を、アルバムの曲順で並べたもの。
   *
   * Set をそのまま展開すると「選んだ順」になり、いったん外して選び直した曲が
   * 末尾へ回ってしまう。プレイリストには曲順どおりに入れたい。
   */
  const selectedTrackIds = $derived(allTrackIds.filter((id) => selectedIds.has(id)));

  function startSelecting() {
    selectedIds = new Set();
    selecting = true;
  }

  function cancelSelecting() {
    selecting = false;
    selectedIds = new Set();
  }

  function toggleTrack(track) {
    // Set を作り直して差し替える（同じ Set を書き換えても再描画されない）
    const next = new Set(selectedIds);
    if (next.has(track.id)) next.delete(track.id);
    else next.add(track.id);
    selectedIds = next;
  }

  function toggleAll() {
    selectedIds = allSelected ? new Set() : new Set(allTrackIds);
  }

  function openAddSheet() {
    if (selectedIds.size === 0) return;
    addOpen = true;
  }

  /** 追加し終えたら選択モードから抜ける */
  function onAdded() {
    cancelSelecting();
  }

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

  // 表示中にスキャンが終わったら、その場で読み直す（アートワークの差し替えを反映する）
  let seenGeneration = untrack(() => scan.generation);
  $effect(() => {
    const generation = scan.generation;
    if (generation === seenGeneration) return;
    seenGeneration = generation;
    const albumId = untrack(() => id);
    api
      .album(albumId)
      .then((payload) => {
        if (albumId === id) album = payload;
      })
      .catch(() => {
        /* アルバムが消えていても、ここで知らせることはない */
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

<PageHeader title={album?.name ?? 'アルバム'} subtitle={album?.album_artist ?? ''}>
  {#snippet actions()}
    {#if album}
      <button
        class="icon-btn"
        onclick={() => (albumEditorOpen = true)}
        aria-label="アルバム情報を編集">✎</button
      >
    {/if}
  {/snippet}
</PageHeader>

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else if album}
  <div class="hero">
    <div class="cover">
      <AlbumArt {album} tier="medium" alt="" eager onzoom={() => (zoomOpen = true)} />
    </div>
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
        <button class="ghost" onclick={startSelecting} disabled={selecting}>
          プレイリストへ追加
        </button>
      </div>
    </div>
  </div>

  {#if selecting}
    <div class="select-bar">
      <span class="count tabular">{selectedIds.size} 曲を選択中</span>
      <div class="select-actions">
        <button class="ghost" onclick={toggleAll}>
          {allSelected ? 'すべて解除' : 'すべて選択'}
        </button>
        <button class="ghost" onclick={cancelSelecting}>やめる</button>
        <button class="primary" onclick={openAddSheet} disabled={selectedIds.size === 0}>
          追加
        </button>
      </div>
    </div>
  {/if}

  {#each discs as disc (disc.number ?? 'single')}
    {#if disc.number !== null}
      <h3 class="disc-heading">
        <span class="disc-label">ディスク {disc.number}</span>
        <span class="disc-count dim tabular">{disc.tracks.length} 曲</span>
      </h3>
    {/if}
    <!-- 曲名の下にタグ由来のアーティスト名を出す（アルバム名は冗長なので出さない）。
         コンピレーションや客演のある盤で、曲ごとの演者が分かるようにするため。 -->
    <TrackList
      tracks={disc.tracks}
      useTrackNumber
      showArtist
      showAlbum={false}
      onplay={playFrom}
      onmenu={openEditor}
      selectable={selecting}
      {selectedIds}
      ontoggleselect={toggleTrack}
    />
  {/each}

  <AddToPlaylist bind:open={addOpen} trackIds={selectedTrackIds} onadded={onAdded} />
  <TagEditor bind:open={editorOpen} track={editingTrack} onsaved={onTrackSaved} />
  <AlbumEditor
    bind:open={albumEditorOpen}
    {album}
    onsaved={(updated) => (album = { ...album, ...updated })}
  />
  <ArtworkViewer bind:open={zoomOpen} {album} alt={`${album.name} のアートワーク`} />
{:else}
  <p class="empty-state">アルバムが見つかりません。</p>
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
    font-size: calc(var(--fs-xl) * 1.18);
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
    font-size: var(--fs-md);
  }

  .chip:hover {
    background: var(--surface-hover);
    border-color: var(--accent-dim);
  }

  .info p {
    margin: 0;
    font-size: var(--fs-sm);
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

  .primary:disabled,
  .ghost:disabled {
    opacity: 0.45;
    cursor: default;
  }

  /* 選択中の操作バー。曲数の多いアルバムでもスクロール中に届くよう固定する */
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

  /* バーの中では小さめに（3 つ並ぶため） */
  .select-actions .primary,
  .select-actions .ghost {
    min-height: 40px;
    padding: 0 16px;
    font-size: var(--fs-md);
  }

  .disc-heading {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin: 0;
    padding: 16px 16px 6px;
    border-top: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
    font-size: var(--fs-sm);
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
    font-size: var(--fs-xs);
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
