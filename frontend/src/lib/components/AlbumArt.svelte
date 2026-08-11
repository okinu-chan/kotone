<script>
  /**
   * アルバムアート表示。
   *
   * 非正方形のアートを強制クロップせず、object-fit: contain で縦横比を保つ
   * （要件 3.3）。画面内に入ってから読み込む遅延読み込みつき（要件 4.6）。
   */
  import { artworkUrl } from '../api.js';

  let {
    album,
    tier = 'small',
    alt = '',
    eager = false,
    /** false にすると正方形の枠に収めず、画像本来の縦横比で表示する。 */
    square = true,
    /** square={false} のときの高さ上限（CSS 長さ）。 */
    maxHeight = '52vh',
  } = $props();

  let failed = $state(false);
  const url = $derived(artworkUrl(album, tier));

  $effect(() => {
    // アルバムが変わったらエラー状態をリセットする
    void url;
    failed = false;
  });
</script>

<div
  class="art"
  class:natural={!square}
  style={square ? undefined : `--max-art-height: ${maxHeight}`}
>
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
    /* 角丸にはしない（原盤のジャケットを加工せずそのまま出す）。
       余白（レターボックス）も塗らず、背景を透過させて見えなくする。 */
    background: transparent;
    overflow: hidden;
  }

  img {
    width: 100%;
    height: 100%;
    /* 非正方形をクロップせず、枠内に収める（要件 3.3）。
       width/height を 100% にして object-fit に任せることで、
       flex の min-height:auto（既定値）で画像が縮まずにはみ出し、
       結果として切り取られてしまう問題を避ける。 */
    object-fit: contain;
    display: block;
    min-width: 0;
    min-height: 0;
  }

  /* 画像本来の縦横比で表示する（再生中画面用） */
  .art.natural {
    width: auto;
    max-width: 100%;
    aspect-ratio: auto;
  }

  .art.natural img {
    width: auto;
    height: auto;
    max-width: 100%;
    max-height: var(--max-art-height, 52vh);
  }

  /* アートワークが無いときだけ背景を敷く（画像がある場合は余白を透過させる） */
  .placeholder {
    position: absolute;
    inset: 0;
    display: grid;
    place-items: center;
    background: var(--bg-sunken);
    color: var(--text-dim);
  }

  .placeholder svg {
    width: 40%;
    height: 40%;
  }

</style>
