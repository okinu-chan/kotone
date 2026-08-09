<script>
  /** 汎用モーダル。背景タップまたは Esc で閉じる。 */
  let { open = $bindable(false), title = '', children, footer } = $props();

  function onKeydown(event) {
    if (event.key === 'Escape') open = false;
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <div class="backdrop">
    <button class="scrim" onclick={() => (open = false)} aria-label="閉じる" tabindex="-1"></button>
    <div class="sheet" role="dialog" aria-modal="true" aria-label={title}>
      <header>
        <h2 class="truncate">{title}</h2>
        <button class="close" onclick={() => (open = false)} aria-label="閉じる">✕</button>
      </header>
      <div class="body">{@render children?.()}</div>
      {#if footer}
        <footer>{@render footer()}</footer>
      {/if}
    </div>
  </div>
{/if}

<style>
  .backdrop {
    position: fixed;
    inset: 0;
    z-index: 70;
    display: flex;
    align-items: flex-end;
    justify-content: center;
  }

  .scrim {
    position: absolute;
    inset: 0;
    background: rgb(0 0 0 / 0.55);
    cursor: default;
  }

  .sheet {
    position: relative;
    width: 100%;
    max-width: 520px;
    max-height: 85vh;
    display: flex;
    flex-direction: column;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg) var(--radius-lg) 0 0;
    padding-bottom: var(--safe-bottom);
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    padding: 14px 16px;
    border-bottom: 1px solid var(--border);
  }

  h2 {
    font-size: 16px;
  }

  .close {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    flex: none;
    border-radius: 50%;
    color: var(--text-muted);
  }

  .body {
    overflow-y: auto;
    padding: 16px;
  }

  footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--border);
  }

  @media (min-width: 700px) {
    .backdrop {
      align-items: center;
    }

    .sheet {
      border-radius: var(--radius-lg);
    }
  }
</style>
