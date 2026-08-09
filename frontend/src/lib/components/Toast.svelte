<script>
  import { toast } from '../stores.svelte.js';
</script>

<div class="toasts" role="status" aria-live="polite">
  {#each toast.items as item (item.id)}
    <button class="toast" class:error={item.kind === 'error'} onclick={() => toast.dismiss(item.id)}>
      {item.message}
    </button>
  {/each}
</div>

<style>
  .toasts {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    bottom: calc(var(--tabbar-height) + var(--miniplayer-height) + var(--safe-bottom) + 12px);
    z-index: 80;
    display: flex;
    flex-direction: column;
    gap: 8px;
    width: min(92vw, 480px);
    pointer-events: none;
  }

  .toast {
    pointer-events: auto;
    text-align: left;
    padding: 12px 16px;
    border-radius: var(--radius);
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    box-shadow: 0 8px 28px rgb(0 0 0 / 0.4);
    font-size: 13.5px;
    line-height: 1.45;
  }

  .toast.error {
    border-color: color-mix(in srgb, var(--danger) 55%, transparent);
    color: var(--danger);
  }
</style>
