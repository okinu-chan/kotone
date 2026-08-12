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
  } = $props();

  const currentPath = $derived(player.state.raw_file ?? null);
</script>

<ul class="tracks">
  {#each tracks as track, index (track.id)}
    {@const active = track.path === currentPath}
    <li class:active>
      <button class="row" onclick={() => onplay?.(track, index)}>
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

      {#if onmenu}
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
    font-size: 13px;
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
    font-size: 15px;
  }

  li.active .title {
    color: var(--accent);
  }

  .sub {
    font-size: 12px;
  }

  .dot {
    margin: 0 5px;
    opacity: 0.5;
  }

  .time {
    flex: none;
    font-size: 12px;
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
