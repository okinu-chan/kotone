<script>
  /**
   * スキャン／ラウドネス計測の進捗バー。
   *
   * 曲数の多いライブラリでは数十分〜十数時間かかるため、どの画面にいても
   * 「いまどの工程か・どこまで進んだか・あとどれくらいか」が分かるようにする。
   * 残り時間の目安はバックエンドが phase 文字列に含めて送ってくる。
   */
  import { scan } from '../stores.svelte.js';

  const percent = $derived(scan.percent);
</script>

{#if scan.running}
  <div class="banner" role="status" aria-live="polite">
    <div class="row">
      <span class="phase truncate">{scan.phase || 'ライブラリを処理中'}</span>
      {#if scan.total > 0}
        <span class="counts tabular dim">{scan.processed} / {scan.total}（{percent}%）</span>
      {/if}
    </div>
    <div
      class="bar"
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin="0"
      aria-valuemax="100"
    >
      <div class="fill" class:indeterminate={scan.total === 0} style={`width: ${percent}%`}></div>
    </div>
  </div>
{/if}

<style>
  .banner {
    position: sticky;
    top: 0;
    z-index: 30;
    padding: 8px 16px 7px;
    background: color-mix(in srgb, var(--accent) 14%, var(--bg));
    border-bottom: 1px solid var(--accent-dim);
  }

  .row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 6px;
  }

  .phase {
    font-size: var(--fs-sm);
    min-width: 0;
  }

  .counts {
    flex: none;
    font-size: var(--fs-xs);
  }

  .bar {
    height: 4px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--accent) 25%, transparent);
    overflow: hidden;
  }

  .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.4s;
  }

  /* 総数が未確定のフェーズ（ファイル探索中など）は動きで示す */
  .fill.indeterminate {
    width: 35% !important;
    animation: slide 1.4s ease-in-out infinite;
  }

  @keyframes slide {
    0% {
      transform: translateX(-100%);
    }
    100% {
      transform: translateX(300%);
    }
  }
</style>
