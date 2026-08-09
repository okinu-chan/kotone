<script>
  /** 画面上部の共通ヘッダー。「戻る」は遷移履歴を 1 段遡る（要件 3.6）。 */
  import { router } from '../router.svelte.js';

  let { title = '', subtitle = '', actions } = $props();
</script>

<header class="page-header">
  {#if router.depth > 0}
    <button class="back" onclick={() => router.back()} aria-label="戻る">‹</button>
  {/if}
  <div class="titles">
    <h1 class="truncate">{title}</h1>
    {#if subtitle}<p class="truncate dim">{subtitle}</p>{/if}
  </div>
  {#if actions}
    <div class="actions">{@render actions()}</div>
  {/if}
</header>

<style>
  .page-header {
    position: sticky;
    top: 0;
    z-index: 20;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: calc(8px + var(--safe-top)) 12px 8px;
    background: color-mix(in srgb, var(--bg) 90%, transparent);
    backdrop-filter: blur(12px);
    border-bottom: 1px solid var(--border);
  }

  .back {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    flex: none;
    border-radius: 50%;
    font-size: 28px;
    line-height: 1;
    color: var(--text-muted);
  }

  .back:hover {
    background: var(--surface);
    color: var(--text);
  }

  .titles {
    flex: 1;
    min-width: 0;
  }

  h1 {
    font-size: 17px;
  }

  p {
    margin: 0;
    font-size: 12px;
  }

  .actions {
    display: flex;
    gap: 6px;
    flex: none;
  }
</style>
