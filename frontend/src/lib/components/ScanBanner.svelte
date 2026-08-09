<script>
  /** スキャン中に画面上部へ出る進捗バー。 */
  import { scan } from '../stores.svelte.js';
</script>

{#if scan.running}
  <div class="banner">
    <div class="text truncate">
      {scan.phase || 'ライブラリをスキャン中'}
      {#if scan.total > 0}
        <span class="tabular dim">{scan.processed}/{scan.total}</span>
      {/if}
    </div>
    <div class="bar"><div class="fill" style={`width: ${scan.percent}%`}></div></div>
  </div>
{/if}

<style>
  .banner {
    position: sticky;
    top: 0;
    z-index: 30;
    padding: 8px 16px 6px;
    background: color-mix(in srgb, var(--accent) 14%, var(--bg));
    border-bottom: 1px solid var(--accent-dim);
  }

  .text {
    font-size: 12px;
    margin-bottom: 6px;
  }

  .bar {
    height: 3px;
    border-radius: 2px;
    background: color-mix(in srgb, var(--accent) 25%, transparent);
    overflow: hidden;
  }

  .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.4s;
  }
</style>
