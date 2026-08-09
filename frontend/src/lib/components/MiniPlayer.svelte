<script>
  /** 再生中画面以外で下部に出る小さな再生バー。タップで再生中画面へ。 */
  import AlbumArt from './AlbumArt.svelte';
  import { api } from '../api.js';
  import { player } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  const track = $derived(player.track);
  const album = $derived(track ? { id: track.album_id, artwork: track.artwork } : null);
  const progress = $derived(player.duration > 0 ? (player.elapsed / player.duration) * 100 : 0);
</script>

{#if track}
  <div class="mini">
    <div class="progress" style={`width: ${progress}%`}></div>
    <button class="open" onclick={() => router.navigate('/')} aria-label="再生中画面を開く">
      <div class="thumb"><AlbumArt {album} tier="small" alt="" /></div>
      <div class="text">
        <span class="title truncate">{track.title}</span>
        <span class="artist truncate dim">{track.artist || track.album || ''}</span>
      </div>
    </button>
    <button
      class="control"
      onclick={() => player.command(api.toggle)}
      aria-label={player.playing ? '一時停止' : '再生'}
    >
      {#if player.playing}
        <svg viewBox="0 0 24 24" fill="currentColor"
          ><path d="M7 5h3.5v14H7zm6.5 0H17v14h-3.5z" /></svg
        >
      {:else}
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5l12 7-12 7z" /></svg>
      {/if}
    </button>
    <button class="control" onclick={() => player.command(api.next)} aria-label="次の曲">
      <svg viewBox="0 0 24 24" fill="currentColor"
        ><path d="M15 6h2v12h-2zM6 6l8.5 6L6 18z" /></svg
      >
    </button>
  </div>
{/if}

<style>
  .mini {
    position: fixed;
    left: 0;
    right: 0;
    bottom: calc(var(--tabbar-height) + var(--safe-bottom));
    z-index: 35;
    display: flex;
    align-items: center;
    gap: 4px;
    height: var(--miniplayer-height);
    padding: 0 8px 0 0;
    background: color-mix(in srgb, var(--surface) 94%, transparent);
    backdrop-filter: blur(12px);
    border-top: 1px solid var(--border);
  }

  .progress {
    position: absolute;
    top: 0;
    left: 0;
    height: 2px;
    background: var(--accent);
    transition: width 0.9s linear;
  }

  .open {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 10px;
    min-width: 0;
    height: 100%;
    padding: 0 8px;
    text-align: left;
  }

  .thumb {
    width: 44px;
    height: 44px;
    flex: none;
  }

  .text {
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.3;
  }

  .title {
    font-size: 14px;
  }

  .artist {
    font-size: 12px;
  }

  .control {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 50%;
    flex: none;
  }

  .control svg {
    width: 24px;
    height: 24px;
  }

  @media (min-width: 900px) {
    .mini {
      justify-content: center;
    }

    .open {
      max-width: 520px;
    }
  }
</style>
