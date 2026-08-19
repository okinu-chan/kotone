<script>
  /**
   * 音量。スピーカーのアイコンをタップすると縦バーが出る。
   *
   * 横バーを常に置いておくと、再生中画面でもミニプレーヤーでも幅を取り、
   * 曲名を押しのけてしまう。ふだんはアイコンだけにして、触りたいときだけ
   * 縦バーを出す（もう一度タップで消える）。
   *
   * 音量をアンプ側で取る構成（設定「音量の操作方法」＝外部）ではアイコンごと
   * 出さない。KOTONE 側のスライダーは効かないため。
   */
  import { api } from '../api.js';
  import { player } from '../stores.svelte.js';

  /** 縦バーを上に出すか下に出すか（ミニプレーヤーは画面下端にあるので上） */
  let { placement = 'up' } = $props();

  let open = $state(false);
  let root = $state(null);

  const volume = $derived(player.state.volume ?? 60);
  const enabled = $derived(player.state.volume_control !== 'external');

  function onInput(event) {
    player.command(() => api.setVolume(Number(event.currentTarget.value)));
  }

  /** バーの外を触ったら閉じる（開きっぱなしで画面を隠さないように） */
  function onPointerDown(event) {
    if (open && root && !root.contains(event.target)) open = false;
  }

  function onKeydown(event) {
    if (event.key === 'Escape') open = false;
  }
</script>

<svelte:window onpointerdown={onPointerDown} onkeydown={onKeydown} />

{#if enabled}
  <div class="volume" class:up={placement === 'up'} bind:this={root}>
    <button
      class="speaker"
      class:active={open}
      onclick={() => (open = !open)}
      aria-expanded={open}
      aria-label={`音量 ${volume}`}
      title={`音量 ${volume}`}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
           stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <path d="M4 9.5h3.2L12 5.5v13L7.2 14.5H4z" />
        {#if volume > 0}
          <path d="M15.5 9.2a4 4 0 0 1 0 5.6" />
        {/if}
        {#if volume > 50}
          <path d="M18 6.8a7.5 7.5 0 0 1 0 10.4" />
        {/if}
        {#if volume === 0}
          <path d="M16 9.5l4.5 5M20.5 9.5l-4.5 5" />
        {/if}
      </svg>
    </button>

    {#if open}
      <div class="panel">
        <span class="value tabular">{volume}</span>
        <input
          class="bar"
          type="range"
          min="0"
          max="100"
          value={volume}
          oninput={onInput}
          aria-label="音量"
        />
      </div>
    {/if}
  </div>
{/if}

<style>
  .volume {
    position: relative;
    flex: none;
  }

  .speaker {
    display: grid;
    place-items: center;
    width: var(--tap);
    height: var(--tap);
    border-radius: 50%;
    color: var(--text-muted);
  }

  .speaker:hover {
    color: var(--text);
  }

  .speaker.active {
    color: var(--accent);
  }

  .speaker svg {
    width: 24px;
    height: 24px;
  }

  /* 縦バー。アイコンの真上（ミニプレーヤー）か真下（再生中画面）に出す */
  .panel {
    position: absolute;
    left: 50%;
    transform: translateX(-50%);
    z-index: 40;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 10px;
    padding: 14px 10px 12px;
    border-radius: var(--radius-lg);
    border: 1px solid var(--border);
    background: var(--bg-elevated);
    box-shadow: 0 8px 24px -8px rgb(0 0 0 / 0.5);
  }

  .volume.up .panel {
    bottom: calc(100% + 8px);
  }

  .volume:not(.up) .panel {
    top: calc(100% + 8px);
  }

  .value {
    font-size: var(--fs-md);
    color: var(--text-muted);
    min-width: 2.5em;
    text-align: center;
  }

  /* 縦向きのスライダー。writing-mode で縦にするのが現行の標準的な方法 */
  .bar {
    writing-mode: vertical-lr;
    direction: rtl;
    width: var(--tap);
    height: 150px;
    padding: 0;
    min-height: 0;
    border: none;
    background: none;
    accent-color: var(--accent);
    touch-action: none;
  }
</style>
