<script>
  /**
   * アルバムアート表示。
   *
   * 非正方形のアートを強制クロップせず、object-fit: contain で縦横比を保つ
   * （要件 3.3）。画面内に入ってから読み込む遅延読み込みつき（要件 4.6）。
   */
  import { artworkUrl } from '../api.js';

  let { album, tier = 'small', alt = '', rounded = true, eager = false } = $props();

  let failed = $state(false);
  const url = $derived(artworkUrl(album, tier));

  $effect(() => {
    // アルバムが変わったらエラー状態をリセットする
    void url;
    failed = false;
  });
</script>

<div class="art" class:rounded>
  {#if url && !failed}
    <img
      src={url}
      {alt}
      loading={eager ? 'eager' : 'lazy'}
      decoding="async"
      onerror={() => (failed = true)}
    />
  {:else}
    <div class="placeholder" aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.4">
        <circle cx="12" cy="12" r="9" />
        <circle cx="12" cy="12" r="2.5" />
      </svg>
    </div>
  {/if}
</div>

<style>
  .art {
    position: relative;
    width: 100%;
    aspect-ratio: 1 / 1;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--bg-sunken);
    overflow: hidden;
  }

  .rounded {
    border-radius: var(--radius);
  }

  img {
    max-width: 100%;
    max-height: 100%;
    width: auto;
    height: auto;
    /* 非正方形をクロップしない（要件 3.3） */
    object-fit: contain;
    display: block;
  }

  .placeholder {
    color: var(--text-dim);
    width: 40%;
    height: 40%;
  }

  .placeholder svg {
    width: 100%;
    height: 100%;
  }
</style>
