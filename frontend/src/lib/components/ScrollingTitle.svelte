<script>
  /**
   * 1 行表示のタイトル。収まらない場合だけ横スクロールさせる。
   *
   * 折り返すとアートワークや操作部が押し下げられるため、再生中画面では
   * 常に 1 行に保つ。全文を待てない人のために、タップで全文表示もできる
   * （はみ出している場合のみ反応する）。
   */
  let { text = '', onfulltext = null } = $props();

  let viewport = $state(null);
  let inner = $state(null);
  let overflowing = $state(false);
  let distance = $state(0);

  function measure() {
    if (!viewport || !inner) return;
    overflowing = inner.scrollWidth - viewport.clientWidth > 4;
    // 文字列の終端が左端に届くまで送る距離＝文字列の幅そのもの。
    // 端で折り返さず、送りきったら左揃えへ戻す（逆走させない）。
    distance = inner.scrollWidth;
  }

  $effect(() => {
    // text が変わるたびに測り直す（フォント読み込み後のズレも拾う）
    void text;
    requestAnimationFrame(measure);
    const timer = setTimeout(measure, 300);
    return () => clearTimeout(timer);
  });

  // 1 秒あたり約 45px の速度で送る。先頭で少し止めるぶんを足す
  const duration = $derived(Math.max(6, distance / 45 + 2));
</script>

<svelte:window onresize={measure} />

<div class="title-line">
  {#if overflowing}
    <!-- はみ出しているときだけ、タップで全文を表示できるボタンにする -->
    <button
      class="viewport tappable"
      bind:this={viewport}
      onclick={() => onfulltext?.()}
      title="タップで全文を表示"
    >
      <span
        class="inner animate"
        bind:this={inner}
        style={`--scroll-distance: -${distance}px; --scroll-duration: ${duration}s`}
      >
        {text}
      </span>
    </button>
    <span class="hint dim" aria-hidden="true">…</span>
  {:else}
    <div class="viewport" bind:this={viewport}>
      <span class="inner" bind:this={inner}>{text}</span>
    </div>
  {/if}
</div>

<style>
  .title-line {
    display: flex;
    align-items: baseline;
    gap: 6px;
    min-width: 0;
  }

  .viewport {
    flex: 1;
    min-width: 0;
    overflow: hidden;
    /* 1 行に固定する（折り返して他の要素を押し下げない） */
    white-space: nowrap;
    text-align: left;
  }

  .viewport.tappable {
    cursor: pointer;
  }

  .inner {
    display: inline-block;
    white-space: nowrap;
    font-size: 22px;
    font-weight: 600;
    line-height: 1.35;
  }

  /* 左方向へ送りきったら左揃えへ戻す（逆向きには動かさない） */
  .inner.animate {
    animation: marquee var(--scroll-duration) linear infinite;
  }

  @keyframes marquee {
    /* 先頭でしばらく止めて読み始められるようにする */
    0%,
    12% {
      transform: translateX(0);
    }
    /* 文字列の終端が左端へ抜けたところで 1 周。次の周で左揃えに戻る */
    100% {
      transform: translateX(var(--scroll-distance));
    }
  }

  .hint {
    flex: none;
    font-size: 16px;
  }

  @media (min-width: 900px) {
    .inner {
      font-size: 26px;
    }
  }

  /* 動きを減らす設定では自動スクロールしない（タップで全文表示は使える） */
  @media (prefers-reduced-motion: reduce) {
    .inner.animate {
      animation: none;
    }
  }
</style>
