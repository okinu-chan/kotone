<script>
  /**
   * 出力先の選択と Bluetooth 機器の管理（設定画面の中）。
   *
   * 「どこで聴くか」を 1 つ選ぶ形にしている。MPD は有効な出力すべてへ同時に
   * 流せるが、イヤホンとスピーカーで同じ音が二重に鳴っても嬉しくない。
   */
  import Modal from './Modal.svelte';
  import { api } from '../api.js';
  import { settings, toast } from '../stores.svelte.js';

  let outputs = $state([]);
  let bt = $state({ adapter: { available: false, powered: false }, devices: [] });
  let loading = $state(true);
  let scanning = $state(false);
  /** 操作中の機器アドレス（ボタンを二重に押させない） */
  let busy = $state(null);
  let confirmForget = $state(null);

  const adapter = $derived(bt.adapter ?? {});
  const connected = $derived(bt.devices.find((d) => d.connected) ?? null);
  /** いま使われているコーデック（機器側の画面で分からない機種のために出す） */
  const codecName = $derived(bt.codec?.codec ?? null);
  const qualitySpec = $derived(
    settings.specs.find((spec) => spec.key === 'bluetooth_prefer_quality') ?? null,
  );
  const paired = $derived(bt.devices.filter((d) => d.paired));
  const nearby = $derived(bt.devices.filter((d) => !d.paired));

  async function load() {
    try {
      const [o, b] = await Promise.all([api.outputs(), api.bluetooth()]);
      outputs = o.items;
      bt = b;
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      loading = false;
    }
  }

  $effect(() => {
    load();
  });

  async function selectOutput(id) {
    try {
      outputs = (await api.selectOutput(id)).items;
      toast.info('出力先を切り替えました');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function togglePower() {
    try {
      bt = await api.btPower(!adapter.powered);
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function scan() {
    scanning = true;
    try {
      const result = await api.btScan(12);
      bt = { ...bt, devices: result.devices };
      const found = result.devices.filter((d) => !d.paired).length;
      toast.info(found ? `${found} 件見つかりました` : '新しい機器は見つかりませんでした');
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      scanning = false;
    }
  }

  /** 機器ごとの操作。終わったら一覧を取り直す */
  async function run(address, label, action) {
    busy = address;
    try {
      await action();
      await load();
      toast.info(label);
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      busy = null;
    }
  }

  const pair = (d) => run(d.address, `「${d.name}」に接続しました`, () => api.btPair(d.address));
  const connect = (d) =>
    run(d.address, `「${d.name}」に接続しました`, () => api.btConnect(d.address));
  const disconnect = (d) =>
    run(d.address, `「${d.name}」を切断しました`, () => api.btDisconnect(d.address));

  async function forget() {
    const device = confirmForget;
    confirmForget = null;
    if (!device) return;
    await run(device.address, `「${device.name}」のペアリングを解除しました`, () =>
      api.btForget(device.address),
    );
  }
</script>

<section class="card">
  <h2>出力先</h2>
  <p class="intro">
    音を出す場所を選びます。Bluetooth を選ぶ場合は、下で機器を接続してから切り替えてください。
  </p>

  {#if loading}
    <div class="loading"><div class="spinner"></div></div>
  {:else}
    <div class="chips">
      {#each outputs as output (output.id)}
        <button
          class:selected={output.enabled}
          aria-pressed={output.enabled}
          onclick={() => selectOutput(output.id)}
        >
          {output.name}
          {#if output.bluetooth && connected}
            <span class="sub dim">{connected.name}</span>
          {/if}
        </button>
      {/each}
    </div>

    {#if outputs.some((o) => o.bluetooth && o.enabled) && !connected}
      <p class="warn">Bluetooth 機器が接続されていません。下で接続してください。</p>
    {/if}
  {/if}
</section>

<section class="card">
  <h2>Bluetooth</h2>
  <p class="intro">イヤホン・ヘッドホン・スピーカーへ音を送れます。</p>

  {#if !adapter.available}
    <p class="warn">Bluetooth アダプタが見つかりません。</p>
  {:else}
    <div class="field">
      <div class="label">
        <span>Bluetooth を使う</span>
        <small class="dim">オフにすると電波を止めます。</small>
      </div>
      <div class="control">
        <label class="switch">
          <input type="checkbox" checked={adapter.powered} onchange={togglePower} />
          <span class="slider"></span>
        </label>
      </div>
    </div>

    <!-- 設定項目の自動生成に混ぜると、Bluetooth の話が画面の別々の場所に
         分かれてしまう。機器の操作と同じカードに置く -->
    {#if qualitySpec}
      <div class="field">
        <div class="label">
          <span>{qualitySpec.label}</span>
          <small class="dim">{qualitySpec.description}</small>
        </div>
        <div class="control">
          <label class="switch">
            <input
              type="checkbox"
              checked={settings.values.bluetooth_prefer_quality ?? true}
              onchange={(event) =>
                settings.set('bluetooth_prefer_quality', event.currentTarget.checked)}
            />
            <span class="slider"></span>
          </label>
        </div>
      </div>
    {/if}

    {#if adapter.powered}
      {#if paired.length > 0}
        <h3>登録済みの機器</h3>
        <ul class="devices">
          {#each paired as device (device.address)}
            <li class:connected={device.connected}>
              <span class="text">
                <span class="name truncate">{device.name}</span>
                <span class="state dim">
                  {#if device.connected}
                    {codecName ? `${codecName} で接続中` : '接続中'}
                  {:else}
                    未接続
                  {/if}
                </span>
              </span>
              <div class="row-actions">
                {#if device.connected}
                  <button class="ghost" disabled={busy === device.address}
                          onclick={() => disconnect(device)}>切断</button>
                {:else}
                  <button class="primary" disabled={busy === device.address}
                          onclick={() => connect(device)}>接続</button>
                {/if}
                <button class="danger" disabled={busy === device.address}
                        onclick={() => (confirmForget = device)}>解除</button>
              </div>
            </li>
          {/each}
        </ul>
      {/if}

      <h3>近くの機器</h3>
      <p class="hint dim">
        イヤホンをペアリングモードにしてから探してください
        （多くの機種はケースのボタン長押しで、ランプが点滅します）。
      </p>
      <button class="primary wide" onclick={scan} disabled={scanning}>
        {scanning ? '探しています…' : '機器を探す'}
      </button>

      {#if nearby.length > 0}
        <ul class="devices">
          {#each nearby as device (device.address)}
            <li>
              <span class="text">
                <span class="name truncate">{device.name}</span>
                <span class="state dim">{device.address}</span>
              </span>
              <div class="row-actions">
                <button class="primary" disabled={busy === device.address}
                        onclick={() => pair(device)}>
                  {busy === device.address ? '接続中…' : 'ペアリング'}
                </button>
              </div>
            </li>
          {/each}
        </ul>
      {/if}
    {/if}
  {/if}
</section>

<Modal open={confirmForget !== null} title="ペアリングを解除しますか？">
  <p class="confirm-lead">「{confirmForget?.name ?? ''}」の登録を消します。</p>
  <p class="confirm-note dim">
    もう一度使うには、機器をペアリングモードにして登録し直す必要があります。
  </p>
  {#snippet footer()}
    <button class="ghost" onclick={() => (confirmForget = null)}>取消</button>
    <button class="danger" onclick={forget}>解除</button>
  {/snippet}
</Modal>

<style>
  .intro {
    margin: 0 0 14px;
    padding: 2px 0 2px 12px;
    border-left: 2px solid var(--accent-dim);
    font-size: var(--fs-md);
    line-height: 1.75;
    color: var(--text-muted);
  }

  h3 {
    margin: 18px 0 8px;
    font-size: var(--fs-md);
    color: var(--text-muted);
  }

  .loading {
    display: grid;
    place-items: center;
    padding: 24px;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .chips button {
    display: inline-flex;
    flex-direction: column;
    align-items: flex-start;
    min-height: var(--tap);
    padding: 6px 18px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: var(--fs-md);
  }

  .chips button.selected {
    background: var(--accent);
    border-color: var(--accent);
    color: var(--on-accent);
  }

  .chips .sub {
    font-size: var(--fs-xs);
  }

  .chips button.selected .sub {
    color: color-mix(in srgb, var(--on-accent) 75%, transparent);
  }

  .warn {
    margin: 12px 0 0;
    font-size: var(--fs-sm);
    color: var(--danger);
  }

  .hint {
    margin: 0 0 10px;
    font-size: var(--fs-sm);
    line-height: 1.6;
  }

  .devices li {
    display: flex;
    align-items: center;
    gap: 12px;
    padding: 8px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .devices li.connected .name {
    color: var(--accent);
  }

  .text {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
    line-height: 1.35;
  }

  .name {
    font-size: var(--fs-lg);
  }

  .state {
    font-size: var(--fs-sm);
  }

  .row-actions {
    display: flex;
    flex: none;
    gap: 6px;
  }

  .primary,
  .ghost,
  .danger {
    min-height: 40px;
    padding: 0 14px;
    border-radius: 999px;
    font-size: var(--fs-md);
  }

  .primary {
    background: var(--accent);
    color: var(--on-accent);
  }

  .primary.wide {
    width: 100%;
    min-height: var(--tap);
  }

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }

  .danger {
    border: 1px solid color-mix(in srgb, var(--danger) 50%, transparent);
    color: var(--danger);
  }

  .primary:disabled,
  .ghost:disabled,
  .danger:disabled {
    opacity: 0.45;
    cursor: default;
  }

  /* 設定のトグル。SettingsView とスコープが別なので、同じ見た目を持たせる */
  .field {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 12px 0;
    border-top: 1px solid color-mix(in srgb, var(--border) 55%, transparent);
  }

  .label {
    display: flex;
    flex-direction: column;
    gap: 3px;
    min-width: 0;
    flex: 1;
    font-size: var(--fs-lg);
  }

  .label small {
    font-size: var(--fs-sm);
    line-height: 1.6;
  }

  .control {
    flex: none;
    width: 150px;
  }

  .switch {
    position: relative;
    display: inline-flex;
    align-items: center;
    justify-content: flex-end;
    width: 100%;
    min-height: var(--tap);
    cursor: pointer;
  }

  .switch input {
    position: absolute;
    opacity: 0;
    width: 52px;
    height: 32px;
    margin: 0;
    cursor: pointer;
  }

  .slider {
    position: relative;
    width: 52px;
    height: 32px;
    border-radius: 999px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    transition: background 0.18s;
  }

  .slider::after {
    content: '';
    position: absolute;
    top: 3px;
    left: 3px;
    width: 24px;
    height: 24px;
    border-radius: 50%;
    background: var(--text-dim);
    transition:
      transform 0.18s,
      background 0.18s;
  }

  .switch input:checked + .slider {
    background: var(--accent);
    border-color: var(--accent);
  }

  .switch input:checked + .slider::after {
    transform: translateX(20px);
    background: var(--on-accent);
  }

  .confirm-lead {
    margin: 0 0 10px;
    font-size: var(--fs-lg);
  }

  .confirm-note {
    margin: 0;
    font-size: var(--fs-sm);
    line-height: 1.6;
  }
</style>
