<script>
  /**
   * アルバムの 1 列表示。
   *
   * グリッドより文字の一覧性が高く、アルバム名・アーティスト名を読みながら
   * 探せる（ZX300 のリスト表示に相当）。
   * 左に小さめのアートワーク、中央にアルバム名とアーティスト名（左揃え）、
   * 右端に曲数を置く。
   */
  import AlbumArt from './AlbumArt.svelte';
  import { router } from '../router.svelte.js';

  let { albums = [], showYear = true } = $props();
</script>

<ul class="list">
  {#each albums as album (album.id)}
    <li>
      <button onclick={() => router.navigate(`/albums/${album.id}`)}>
        <span class="thumb"><AlbumArt {album} tier="small" alt="" /></span>
        <span class="text">
          <span class="name truncate">{album.name}</span>
          <span class="sub truncate dim">
            {album.album_artist ?? (album.is_compilation ? '複数アーティスト' : '')}{#if showYear && album.year}<span
                class="dot">·</span
              >{album.year}{/if}
          </span>
        </span>
        <span class="count tabular dim">{album.track_count}</span>
      </button>
    </li>
  {/each}
</ul>

{#if albums.length === 0}
  <p class="empty-state">アルバムがありません。</p>
{/if}

<style>
  li {
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  button {
    display: flex;
    align-items: center;
    gap: 12px;
    width: 100%;
    /* タップ領域 44px 以上（要件 3.7） */
    min-height: 60px;
    padding: 7px 14px;
    text-align: left;
  }

  button:hover {
    background: var(--surface-hover);
  }

  button:hover .name {
    color: var(--accent);
  }

  .thumb {
    width: 46px;
    height: 46px;
    flex: none;
  }

  .text {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.35;
  }

  .name {
    font-size: var(--fs-lg);
  }

  .sub {
    font-size: var(--fs-sm);
  }

  .dot {
    margin: 0 5px;
    opacity: 0.6;
  }

  .count {
    flex: none;
    font-size: var(--fs-sm);
    min-width: 2.2em;
    text-align: right;
  }

  @media (min-width: 900px) {
    .list {
      max-width: 820px;
      margin: 0 auto;
    }
  }
</style>
