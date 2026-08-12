<script>
  /**
   * ライブラリの絞り込み・並び替えをまとめたシート。
   *
   * ツールバーに軸を並べていくと横幅が足りなくなるため、「何を見るか」だけを
   * ツールバーに残し、「どう絞り込むか」はここへ集約する。
   * 軸が増えてもツールバーは膨らまない。
   *
   * 文字ラベルを維持するのは、これらの概念（ジャンル・年・フォーマット）に
   * 定着したアイコンが無く、記号化すると学習を強いるため（フル日本語 UI・要件 4.5）。
   */
  import Modal from './Modal.svelte';
  import { api } from '../api.js';
  import { SORT_LABELS } from '../format.js';
  import { libraryView, toast } from '../stores.svelte.js';

  let { open = $bindable(false), sorts = [], onapply } = $props();

  const QUALITY_LABELS = {
    hires: 'ハイレゾ（48kHz超 / 24bit）',
    cd: 'CD 品質（44.1kHz / 16bit）',
    dsd: 'DSD',
  };

  let formats = $state(null);

  $effect(() => {
    if (!open || formats) return;
    api
      .formats()
      .then((payload) => {
        formats = payload;
      })
      .catch((error) => toast.error(error.detail || String(error)));
  });

  function setQuality(key) {
    libraryView.quality = libraryView.quality === key ? null : key;
    onapply?.();
  }

  function setCodec(codec) {
    libraryView.codec = libraryView.codec === codec ? null : codec;
    onapply?.();
  }

  function setSort(key) {
    libraryView.sort = key;
    onapply?.();
  }

  /** コーデックが 1 種類しかないなら選択肢として出さない（意味が無いため） */
  const showCodecs = $derived((formats?.codecs ?? []).length > 1);
</script>

<Modal bind:open title="絞り込みと並び替え">
  <section>
    <h3>並び順</h3>
    <div class="chips">
      {#each sorts as key (key)}
        <button
          class:selected={libraryView.sort === key}
          onclick={() => setSort(key)}
          aria-pressed={libraryView.sort === key}
        >
          {SORT_LABELS[key] ?? key}
        </button>
      {/each}
    </div>
  </section>

  <section>
    <h3>音質</h3>
    {#if formats}
      {#if formats.qualities.length === 0}
        <p class="empty dim">音質の情報がありません。</p>
      {:else}
        <div class="chips">
          {#each formats.qualities as quality (quality.key)}
            <button
              class:selected={libraryView.quality === quality.key}
              onclick={() => setQuality(quality.key)}
              aria-pressed={libraryView.quality === quality.key}
            >
              {QUALITY_LABELS[quality.key] ?? quality.key}
              <span class="count dim tabular">{quality.track_count}</span>
            </button>
          {/each}
        </div>
      {/if}
    {:else}
      <div class="loading"><div class="spinner"></div></div>
    {/if}
  </section>

  {#if showCodecs}
    <section>
      <h3>フォーマット</h3>
      <div class="chips">
        {#each formats.codecs as codec (codec.codec)}
          <button
            class:selected={libraryView.codec === codec.codec}
            onclick={() => setCodec(codec.codec)}
            aria-pressed={libraryView.codec === codec.codec}
          >
            {codec.codec.toUpperCase()}
            <span class="count dim tabular">{codec.track_count}</span>
          </button>
        {/each}
      </div>
    </section>
  {/if}

  {#snippet footer()}
    <button class="ghost" onclick={() => { libraryView.clearFilter(); onapply?.(); }}>
      すべて解除
    </button>
    <button class="primary" onclick={() => (open = false)}>閉じる</button>
  {/snippet}
</Modal>

<style>
  section {
    margin-bottom: 20px;
  }

  section:last-of-type {
    margin-bottom: 0;
  }

  h3 {
    margin: 0 0 10px;
    font-size: 12px;
    letter-spacing: 0.06em;
    color: var(--text-dim);
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .chips button {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    /* タップ領域を 44px 以上に保つ（要件 3.7） */
    min-height: var(--tap);
    padding: 0 16px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: 13.5px;
  }

  .chips button:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  .chips button.selected {
    background: var(--accent);
    border-color: var(--accent);
    color: #0d0f13;
  }

  .chips button.selected .count {
    color: color-mix(in srgb, #0d0f13 70%, transparent);
  }

  .count {
    font-size: 11.5px;
  }

  .empty {
    margin: 0;
    font-size: 13px;
  }

  .loading {
    display: grid;
    place-items: center;
    padding: 20px;
  }

  .primary,
  .ghost {
    min-height: var(--tap);
    padding: 0 18px;
    border-radius: var(--radius);
    font-size: 14px;
  }

  .primary {
    background: var(--accent);
    color: #0d0f13;
  }

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }
</style>
