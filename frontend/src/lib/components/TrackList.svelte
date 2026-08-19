<script>
  /** 曲一覧。タップでその曲から再生（要件 3.4 / 3.6）。 */
  import { formatTime } from '../format.js';
  import { player } from '../stores.svelte.js';
  import AlbumArt from './AlbumArt.svelte';

  let {
    tracks = [],
    /** 行番号にトラック番号を使う（アルバム内表示）。false なら通し番号。 */
    useTrackNumber = false,
    showArtwork = false,
    showArtist = true,
    /** アルバム名も併記するか。アルバム内の一覧では冗長なので出さない。 */
    showAlbum = true,
    onplay,
    onmenu,
    /**
     * 選択モード。曲の左にチェックボックスを出し、タップで再生ではなく
     * 選択の切り替えになる（プレイリストへ入れる曲を選ぶときに使う）。
     */
    selectable = false,
    /** 選択済みの曲 id（Set）。1 つの一覧に同じ曲は並ばない前提で id を使う */
    selectedIds = new Set(),
    ontoggleselect,
  } = $props();

  const currentPath = $derived(player.state.raw_file ?? null);
</script>

<ul class="tracks">
  <!-- 同じ曲が複数回並ぶことがある（プレイリストへ同じアルバムを 2 回入れた、
       ランダム再生のキューで同じ曲が再抽選された、など）。曲 id で鍵を付けると
       重複してレンダリングごと落ち、読み込み中の表示のまま止まってしまう。
       行の識別子は「何番目か」なので index を鍵にする。 -->
  {#each tracks as track, index (index)}
    {@const active = track.path === currentPath}
    {@const checked = selectable && selectedIds.has(track.id)}
    <li class:active class:selected={checked}>
      <button
        class="row"
        onclick={() => (selectable ? ontoggleselect?.(track) : onplay?.(track, index))}
        aria-pressed={selectable ? checked : undefined}
      >
        {#if selectable}
          <span class="check" class:on={checked} aria-hidden="true">
            {#if checked}
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"
                   stroke-linecap="round" stroke-linejoin="round">
                <path d="M5 12.5 10 17.5 19 7" />
              </svg>
            {/if}
          </span>
        {/if}
        {#if showArtwork}
          <div class="thumb">
            <AlbumArt album={{ id: track.album_id, artwork: track.artwork }} tier="small" alt="" />
          </div>
        {:else}
          <span class="num tabular dim">
            {#if active}
              <span class="playing" aria-label="再生中">♪</span>
            {:else}
              {useTrackNumber ? track.track_no || index + 1 : index + 1}
            {/if}
          </span>
        {/if}

        <span class="text">
          <span class="title truncate">{track.title}</span>
          {#if showArtist && (track.artist || (showAlbum && track.album))}
            <span class="sub truncate dim">
              {track.artist}{#if showAlbum && track.artist && track.album}<span class="dot"
                  >·</span
                >{/if}{#if showAlbum}{track.album}{/if}
            </span>
          {/if}
        </span>

        <span class="time tabular dim">{formatTime(track.duration)}</span>
      </button>

      {#if onmenu && !selectable}
        <button class="menu" onclick={() => onmenu(track, index)} aria-label="この曲の操作">⋯</button>
      {/if}
    </li>
  {/each}
</ul>

{#if tracks.length === 0}
  <p class="empty-state">曲がありません。</p>
{/if}

<style>
  .tracks {
    display: flex;
    flex-direction: column;
  }

  li {
    display: flex;
    align-items: center;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  li.active {
    background: color-mix(in srgb, var(--accent) 9%, transparent);
  }

  li.selected {
    background: color-mix(in srgb, var(--accent) 14%, transparent);
  }

  /* 選択モードのチェックボックス。タップ領域は行全体なので、
     見た目だけを担う（枠は未選択でも見えるようにして操作を示す） */
  .check {
    display: grid;
    place-items: center;
    flex: none;
    width: 24px;
    height: 24px;
    border: 2px solid var(--text-dim);
    border-radius: 6px;
    color: var(--on-accent);
  }

  .check.on {
    background: var(--accent);
    border-color: var(--accent);
  }

  .check svg {
    width: 16px;
    height: 16px;
  }

  .row {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
    /* タップ領域 44px 以上 */
    min-height: 56px;
    padding: 6px 12px;
    text-align: left;
  }

  .row:hover {
    background: var(--surface-hover);
  }

  .num {
    width: 26px;
    flex: none;
    text-align: right;
    font-size: var(--fs-md);
  }

  .playing {
    color: var(--accent);
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

  .title {
    font-size: var(--fs-lg);
  }

  li.active .title {
    color: var(--accent);
  }

  .sub {
    font-size: var(--fs-sm);
  }

  .dot {
    margin: 0 5px;
    opacity: 0.5;
  }

  .time {
    flex: none;
    font-size: var(--fs-sm);
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
</style>
