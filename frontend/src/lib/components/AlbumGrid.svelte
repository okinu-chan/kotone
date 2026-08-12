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
  /* アートワークを枠いっぱいに見せるため、余白を詰めて 1 枚あたりを大きくする。
     非正方形はトリミングせず letterbox のままなので、比率は保たれる。 */
  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
    gap: 14px 8px;
    padding: 10px 8px;
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
    font-size: var(--fs-md);
    line-height: 1.35;
  }

  .artist {
    font-size: var(--fs-sm);
  }

  .dot {
    margin: 0 5px;
    opacity: 0.6;
  }

  @media (max-width: 460px) {
    .grid {
      grid-template-columns: repeat(2, 1fr);
      gap: 12px 6px;
      padding: 8px 6px;
    }
  }

  @media (min-width: 1100px) {
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(190px, 1fr));
      gap: 18px 10px;
      padding: 12px 10px;
    }
  }
</style>
