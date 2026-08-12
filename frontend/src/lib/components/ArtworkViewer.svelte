<script>
  /**
   * アートワークの拡大表示。
   *
   * 画面いっぱいまで大きく見せるが、**元解像度は超えない**（拡大による
   * 劣化を出さない方針。要件 3.3）。表示できる最大の階層を取得し、
   * その実解像度とビューポートの小さい方に収める。
   */
  import { artworkUrl } from '../api.js';

  let { open = $bindable(false), album = null, alt = '' } = $props();

  let natural = $state({ width: 0, height: 0 });
  let loaded = $state(false);

  // 最大階層を要求する（無ければ API 側が存在する最大へフォールバックする）
  const url = $derived(artworkUrl(album, 'large'));

  $effect(() => {
    void url;
    loaded = false;
    natural = { width: 0, height: 0 };
  });

  function onLoad(event) {
    const img = event.currentTarget;
    natural = { width: img.naturalWidth, height: img.naturalHeight };
    loaded = true;
  }

  /** 元解像度とビューポートの小さい方に収める。 */
  const sizeStyle = $derived(
    natural.width && natural.height
      ? `max-width: min(96vw, ${natural.width}px); max-height: min(92vh, ${natural.height}px);`
      : 'max-width: 96vw; max-height: 92vh;',
  );

  function onKeydown(event) {
    if (event.key === 'Escape') open = false;
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open && url}
  <div class="viewer" role="dialog" aria-modal="true" aria-label="アートワークの拡大表示">
    <button class="scrim" onclick={() => (open = false)} aria-label="閉じる"></button>

    <!-- 拡大した画像をもう一度タップすると閉じる -->
    <button class="image-button" onclick={() => (open = false)} aria-label="閉じる">
      <img src={url} {alt} style={sizeStyle} onload={onLoad} decoding="async" />
    </button>

    {#if loaded}
      <p class="info tabular">
        {natural.width} × {natural.height}<span class="sep">·</span>タップで閉じる
      </p>
    {/if}
  </div>
{/if}

<style>
  .viewer {
    position: fixed;
    inset: 0;
    z-index: 90;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 10px;
    padding: 16px;
  }

  .scrim {
    position: absolute;
    inset: 0;
    background: rgb(0 0 0 / 0.88);
    cursor: zoom-out;
  }

  .image-button {
    position: relative;
    display: block;
    padding: 0;
    line-height: 0;
    cursor: zoom-out;
  }

  img {
    /* 元解像度を超えて引き伸ばさない */
    width: auto;
    height: auto;
    object-fit: contain;
    box-shadow: 0 24px 60px rgb(0 0 0 / 0.6);
  }

  .info {
    position: relative;
    margin: 0;
    font-size: 11.5px;
    color: var(--text-dim);
  }

  .sep {
    margin: 0 6px;
    opacity: 0.5;
  }
</style>
