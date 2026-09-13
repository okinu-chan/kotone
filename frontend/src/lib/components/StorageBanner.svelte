<script>
  /**
   * 音楽用ドライブが読めないときの帯。
   *
   * ドライブが外れると、どの曲も再生が始まらなくなる。画面からは理由が
   * 分からないので、どの画面にいても目に入る場所で知らせる。自動復旧の
   * 進み具合（kotone-storage-recover が書く記録）も一緒に出す。
   */
  import { storage } from '../stores.svelte.js';

  const detail = $derived.by(() => {
    const recovery = storage.recovery;
    if (recovery?.state === 'recovering') return '自動で復旧しています…';
    if (recovery?.state === 'failed' || recovery?.state === 'waiting') return recovery.message;
    return '接続し直すと、自動で復旧を試みます。';
  });
</script>

{#if !storage.available}
  <div class="banner" role="alert">
    <p class="title">{storage.message || '音楽用ドライブを読み込めません。'}</p>
    {#if detail}
      <p class="detail">{detail}</p>
    {/if}
  </div>
{/if}

<style>
  .banner {
    position: sticky;
    top: 0;
    z-index: 31;
    padding: 10px 16px;
    background: color-mix(in srgb, var(--danger) 16%, var(--bg));
    border-bottom: 1px solid color-mix(in srgb, var(--danger) 45%, transparent);
  }

  .title {
    margin: 0;
    font-size: var(--fs-md);
    font-weight: 600;
    color: var(--danger);
  }

  .detail {
    margin: 2px 0 0;
    font-size: var(--fs-sm);
    line-height: 1.5;
    color: var(--text-muted);
  }
</style>
