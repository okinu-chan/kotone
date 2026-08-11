<script>
  /** アルバムのグリッド表示。画像は遅延読み込み（要件 4.6）。 */
  import AlbumArt from './AlbumArt.svelte';
  import { router } from '../router.svelte.js';

  let { albums = [], showYear = true } = $props();
</script>

<div class="grid">
  {#each albums as album (album.id)}
    <button class="card" onclick={() => router.navigate(`/albums/${album.id}`)}>
      <AlbumArt {album} tier="small" alt="" />
      <span class="name clamp-2">{album.name}</span>
      <span class="artist truncate dim">
        {album.album_artist ?? (album.is_compilation ? '複数アーティスト' : '')}{#if showYear && album.year}<span
            class="dot">·</span
          >{album.year}{/if}
      </span>
    </button>
  {/each}
</div>

{#if albums.length === 0}
  <p class="empty-state">アルバムがありません。</p>
{/if}

<style>
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
    gap: 16px 12px;
    padding: 16px 12px;
  }

  .card {
    display: flex;
    flex-direction: column;
    gap: 6px;
    text-align: left;
    min-width: 0;
  }

  .card:hover .name {
    color: var(--accent);
  }

  .name {
    font-size: 13.5px;
    line-height: 1.35;
  }

  .artist {
    font-size: 12px;
  }

  .dot {
    margin: 0 5px;
    opacity: 0.6;
  }

  @media (max-width: 420px) {
    .grid {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  @media (min-width: 1100px) {
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(170px, 1fr));
      padding: 20px;
    }
  }
</style>
