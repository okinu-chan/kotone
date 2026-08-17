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
  /** 新着（起点より後に追加された）アルバムの枚数。null は取得前 */
  let recentCount = $state(null);

  $effect(() => {
    if (!open || formats) return;
    api
      .formats()
      .then((payload) => {
        formats = payload;
      })
      .catch((error) => toast.error(error.detail || String(error)));
  });

  // 枚数は追加されるたびに変わるので、シートを開くたびに数え直す
  $effect(() => {
    if (!open) return;
    api
      .albums({ recent: true, limit: 1 })
      .then((payload) => {
        recentCount = payload.total;
      })
      .catch(() => {
        recentCount = null;
      });
  });

  function toggleRecent() {
    libraryView.recent = !libraryView.recent;
    // 新着は入った順に見たいので、並び順も追加日にそろえる
    if (libraryView.recent) libraryView.sort = 'added';
    onapply?.();
  }

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
    <h3>最近追加したアルバム</h3>
    <div class="chips">
      <button
        class:selected={libraryView.recent}
        onclick={toggleRecent}
        aria-pressed={libraryView.recent}
        disabled={recentCount === 0}
      >
        新しく増えたぶんだけ
        {#if recentCount !== null}<span class="count dim tabular">{recentCount}</span>{/if}
      </button>
    </div>
    <p class="hint dim">
      {#if recentCount === 0}
        最初に取り込んだぶんより後に追加されたアルバムはまだありません。
      {:else}
        最初に取り込んだライブラリは含みません。あとから足したアルバムだけが並びます。
      {/if}
    </p>
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
    font-size: var(--fs-sm);
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
    font-size: var(--fs-md);
  }

  .chips button:hover:not(:disabled) {
    background: var(--surface-hover);
    color: var(--text);
  }

  /* 該当が 0 件のときは選べない（押しても空の一覧になるだけ） */
  .chips button:disabled {
    opacity: 0.4;
    cursor: default;
  }

  .chips button.selected {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
  }

  .chips button.selected .count {
    color: color-mix(in srgb, var(--on-accent) 70%, transparent);
  }

  .count {
    font-size: var(--fs-xs);
  }

  .empty {
    margin: 0;
    font-size: var(--fs-md);
  }

  /* この絞り込みが何を出すのかは名前だけでは伝わらないので一行添える */
  .hint {
    margin: 8px 0 0;
    font-size: var(--fs-sm);
    line-height: 1.6;
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
    font-size: var(--fs-lg);
  }

  .primary {
    background: var(--accent);
    color: var(--on-accent);
  }

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }
</style>
