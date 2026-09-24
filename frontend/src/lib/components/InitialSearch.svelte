<script>
  /**
   * イニシャルサーチ（要件 3.7）。
   *
   * ・「カナ」「英数・他」のタブ + 頭文字グリッド
   * ・常時表示ではなく、専用ボタンから全画面オーバーレイとして開く
   * ・カナは五十音表（5 列）、英数・他はグリッド。小画面でも表の形は崩さず詰める
   * ・1 タップ領域は 44px 四方以上
   */
  import { api } from '../api.js';
  import { libraryView, settings } from '../stores.svelte.js';

  let { entity = 'albums', open = $bindable(false), onselect } = $props();

  let group = $state('kana');
  let items = $state([]);
  let loading = $state(false);

  // アルバム一覧はアーティスト名の頭文字でも引ける。アーティスト一覧では
  // アルバム名で引く意味がないので、切り替えはアルバムのときだけ出す。
  const canSwitchTarget = $derived(entity === 'albums');
  const initialBy = $derived(canSwitchTarget ? libraryView.initialBy : 'artist');

  function setInitialBy(next) {
    libraryView.initialBy = next;
    settings.set('initial_search_by', next);
  }

  $effect(() => {
    if (!open) return;
    const currentEntity = entity;
    const currentGroup = group;
    const currentBy = initialBy;
    loading = true;
    api
      .initials(currentEntity, currentGroup, currentBy)
      .then((payload) => {
        items = payload.items;
      })
      .finally(() => {
        loading = false;
      });
  });

  function choose(item) {
    if (item.count === 0) return;
    onselect?.({ initial: item.initial, group, by: initialBy });
    open = false;
  }

  /**
   * カナを五十音表の形（5 列）に並べ直す。
   *
   * サーバーは「あいうえお…」の一次元で返す。そのまま 5 列で流すと、
   * 3 文字しかない「や行」以降が 1 列ずつずれて表として読めなくなるため、
   * や行だけ「や・□・ゆ・□・よ」と空きを入れて母音の列を揃える。
   */
  const kanaRows = $derived.by(() => {
    const rows = [];
    let i = 0;
    while (i < items.length) {
      if (items[i].initial === 'や') {
        const [ya, yu, yo] = items.slice(i, i + 3);
        rows.push([ya, null, yu, null, yo]);
        i += 3;
      } else {
        rows.push(items.slice(i, i + 5));
        i += 5;
      }
    }
    return rows;
  });

  function onKeydown(event) {
    if (event.key === 'Escape') open = false;
  }
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
  <div
    class="overlay"
    role="dialog"
    aria-modal="true"
    aria-label="頭文字で絞り込む"
    tabindex="-1"
  >
    <header>
      <div class="tabs" role="tablist">
        <button
          role="tab"
          aria-selected={group === 'kana'}
          class:active={group === 'kana'}
          onclick={() => (group = 'kana')}>カナ</button
        >
        <button
          role="tab"
          aria-selected={group === 'latin'}
          class:active={group === 'latin'}
          onclick={() => (group = 'latin')}>英数・他</button
        >
      </div>
      <button class="close" onclick={() => (open = false)} aria-label="閉じる">✕</button>
    </header>

    {#if canSwitchTarget}
      <!-- どちらの名前の頭文字で引くか。既定はアーティスト名 -->
      <div class="target" role="group" aria-label="頭文字で引く対象">
        <button
          class:active={initialBy === 'artist'}
          aria-pressed={initialBy === 'artist'}
          onclick={() => setInitialBy('artist')}>アーティスト名</button
        >
        <button
          class:active={initialBy === 'album'}
          aria-pressed={initialBy === 'album'}
          onclick={() => setInitialBy('album')}>アルバム名</button
        >
      </div>
    {/if}

    {#if loading}
      <div class="loading"><div class="spinner"></div></div>
    {:else}
      {#if group === 'kana'}
        <!-- 五十音表。行と列が揃っていると目当ての文字を探しやすい -->
        <div class="chart">
          {#each kanaRows as row, index (index)}
            {#each row as item, column (column)}
              {#if item}
                <button
                  class="cell"
                  class:disabled={item.count === 0}
                  disabled={item.count === 0}
                  onclick={() => choose(item)}
                >
                  <span class="ch">{item.initial}</span>
                  <span class="count dim tabular">{item.count}</span>
                </button>
              {:else}
                <span class="gap" aria-hidden="true"></span>
              {/if}
            {/each}
          {/each}
        </div>
      {:else}
        <div class="grid">
          {#each items as item (item.initial)}
            <button
              class="cell"
              class:disabled={item.count === 0}
              disabled={item.count === 0}
              onclick={() => choose(item)}
            >
              <span class="ch">{item.initial}</span>
              <span class="count dim tabular">{item.count}</span>
            </button>
          {/each}
        </div>
      {/if}
    {/if}

    <footer class="dim">
      {#if canSwitchTarget && initialBy === 'artist'}
        アーティスト名の読み仮名をもとに頭出しします。
      {:else if canSwitchTarget}
        アルバム名の読み仮名をもとに頭出しします。
      {:else}
        読み仮名をもとに頭出しします。
      {/if}
    </footer>
  </div>
{/if}

<style>
  .overlay {
    position: fixed;
    inset: 0;
    z-index: 60;
    display: flex;
    flex-direction: column;
    background: color-mix(in srgb, var(--bg) 97%, transparent);
    backdrop-filter: blur(16px);
    padding: calc(12px + var(--safe-top)) 16px calc(16px + var(--safe-bottom));
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;
  }

  .tabs {
    display: flex;
    gap: 8px;
  }

  .tabs button {
    min-height: var(--tap);
    padding: 0 18px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: var(--fs-lg);
  }

  .tabs button.active {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
  }

  /* 頭文字を引く対象の切り替え。タブ（カナ／英数）と区別できるよう、
     塗りではなく下線で選択中を示す */
  .target {
    display: flex;
    gap: 4px;
    margin: -6px 0 12px;
    border-bottom: 1px solid var(--border);
  }

  .target button {
    min-height: var(--tap);
    padding: 0 14px;
    color: var(--text-dim);
    font-size: var(--fs-md);
    border-bottom: 2px solid transparent;
  }

  .target button.active {
    color: var(--accent);
    border-bottom-color: var(--accent);
  }

  .close {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 50%;
    background: var(--surface);
    font-size: 16px;
  }

  .loading {
    display: grid;
    place-items: center;
    flex: 1;
  }

  .grid {
    flex: 1;
    overflow-y: auto;
    display: grid;
    /* 1 タップ領域あたり 44px 四方以上（要件 3.7） */
    grid-template-columns: repeat(auto-fill, minmax(64px, 1fr));
    gap: 10px;
    align-content: start;
  }

  /* 五十音表。列数を 5 に固定して母音の列を揃える */
  .chart {
    flex: 1;
    overflow-y: auto;
    display: grid;
    grid-template-columns: repeat(5, 1fr);
    gap: 8px;
    align-content: start;
  }

  /* や行の空き。押せないぶんセルは置かず、場所だけ確保する */
  .gap {
    display: block;
  }

  .cell {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    min-width: 44px;
    min-height: 64px;
    border-radius: var(--radius);
    background: var(--surface);
    border: 1px solid var(--border);
    transition: background 0.12s;
  }

  .cell:hover:not(.disabled) {
    background: var(--surface-hover);
    border-color: var(--accent-dim);
  }

  .cell.disabled {
    opacity: 0.3;
    cursor: default;
  }

  .ch {
    font-size: calc(var(--fs-xl) * 1.18);
    line-height: 1;
  }

  .count {
    font-size: 10px;
  }

  footer {
    margin-top: 14px;
    font-size: var(--fs-sm);
    text-align: center;
  }

  /* 小画面。カナは 5 列のまま詰めて、表の形を崩さない（要件 3.7） */
  @media (max-width: 600px) {
    .grid {
      grid-template-columns: repeat(auto-fill, minmax(56px, 1fr));
    }

    .chart {
      gap: 6px;
    }

    .chart .cell {
      min-height: 56px;
    }
  }
</style>
