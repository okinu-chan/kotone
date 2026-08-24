<script>
  /** 設定画面。バックエンドの設定定義（specs）からフォームを自動生成する。 */
  import AudioOutput from './AudioOutput.svelte';
  import Modal from './Modal.svelte';
  import PageHeader from './PageHeader.svelte';
  import { api } from '../api.js';
  import { formatBytes, formatDuration } from '../format.js';
  import { formatTime } from '../format.js';
  import { scan, settings, toast } from '../stores.svelte.js';

  const GROUPS = [
    {
      title: '再生',
      keys: [
        'volume_control',
        'startup_warmup_ms',
        'gapless_enabled',
        'random_avoid_last',
      ],
    },
    {
      title: 'ラウドネス補正',
      keys: [
        'loudness_enabled',
        'loudness_mode',
        'loudness_preamp_db',
        'loudness_clip_prevention',
      ],
      // この機能を知らない人が最初に読む場所。専門用語を使わずに何が起きるかを書く
      intro:
        'CD や配信は、作られた時期やジャンルによって録音そのものの音量がばらばらです。' +
        'そのままだと曲が変わるたびにボリュームを触ることになります。' +
        'ラウドネス補正は、あらかじめ計測しておいた曲ごとの音量をもとに再生音量を自動で調整し、' +
        'どの曲もおおよそ同じ大きさで聴こえるようにする機能です。' +
        '音のデータ自体は書き換えません。',
      note: 'ハードウェアミキサー（DAC 内蔵ボリューム）を使う場合、補正の精度は保証されません。',
    },
    {
      title: 'ラウドネスの計測',
      keys: [
        'rsgain_enabled',
        'use_replaygain_tags',
        'loudness_true_peak',
        'loudness_concurrency',
      ],
      note: '計測済みの曲は再スキャンでも測り直しません。中断しても続きから再開できます。',
      loudnessActions: true,
    },
    {
      title: 'ライブラリ',
      keys: ['artist_separators', 'ignore_leading_article', 'scan_on_start'],
    },
    {
      title: 'Bluetooth',
      keys: ['bluetooth_prefer_quality'],
    },
    { title: '表示', keys: ['default_library_sort', 'album_layout', 'theme', 'font_size'] },
  ];

  const ENUM_LABELS = {
    track: 'トラック単位',
    album: 'アルバム単位',
    artist: 'アーティスト',
    album_sort: 'アルバム',
    genre: 'ジャンル',
    year: 'リリース年',
    added: '追加日',
    dark: 'ダーク',
    light: 'ライト',
    grid: 'サムネイル',
    list: '1 列',
    small: '小',
    normal: '標準',
    large: '大',
    kotone: 'kotone（画面のスライダー）',
    external: '外部（アンプ側で調整）',
  };

  let system = $state(null);
  let separatorReport = $state(null);
  let analyzing = $state(false);
  let loudness = $state(null);

  // 実行前の確認ダイアログ（所要時間を提示してから開始する）
  let confirmOpen = $state(false);
  let confirmRemeasure = $state(false);
  let confirmEstimate = $state(null);
  let confirmLoading = $state(false);
  let confirmCalibrating = $state(false);

  async function refreshLoudness() {
    try {
      loudness = await api.loudnessProgress();
    } catch {
      /* 起動直後などは無視してよい */
    }
  }

  /** 所要時間を見積もってから確認ダイアログを開く。 */
  async function askLoudness(remeasure) {
    confirmRemeasure = remeasure;
    confirmEstimate = null;
    confirmLoading = true;
    confirmOpen = true;
    try {
      let estimate = await api.loudnessProgress(remeasure);
      // 実測値がまだ無ければ、数秒のサンプル計測で見積もりを実機に合わせる。
      // 機器と音源次第で 2 倍近くずれるため、長時間の処理を始める前に確かめる。
      if (!estimate.is_calibrated && estimate.available && estimate.remaining > 0) {
        confirmCalibrating = true;
        try {
          await api.calibrateLoudness(6);
          estimate = await api.loudnessProgress(remeasure);
        } catch {
          /* 較正できなくても概算のまま進められる */
        } finally {
          confirmCalibrating = false;
        }
      }
      confirmEstimate = estimate;
    } catch (error) {
      toast.error(error.detail || String(error));
      confirmOpen = false;
    } finally {
      confirmLoading = false;
    }
  }

  async function runLoudness() {
    confirmOpen = false;
    await scan.startLoudness(confirmRemeasure);
    refreshLoudness();
  }

  $effect(() => {
    if (!settings.loaded) settings.load();
    api
      .system()
      .then((payload) => {
        system = payload;
      })
      .catch(() => {});
    scan.refresh();
    refreshLoudness();
  });

  // 計測が終わったら残り曲数を更新する
  $effect(() => {
    void scan.running;
    refreshLoudness();
  });

  function specFor(key) {
    return settings.specs.find((spec) => spec.key === key);
  }

  function enumLabel(spec, value) {
    if (spec.key === 'default_library_sort' && value === 'album') return 'アルバム';
    return ENUM_LABELS[value] ?? value;
  }

  function update(key, value) {
    settings.set(key, value);
  }

  function updateSeparators(text) {
    const list = text
      .split('\n')
      .map((line) => line.replace(/\r$/, ''))
      .filter((line) => line.length > 0);
    settings.set('artist_separators', list);
  }

  async function analyze() {
    analyzing = true;
    try {
      separatorReport = await api.analyzeArtists();
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      analyzing = false;
    }
  }
</script>

<PageHeader title="設定" />

<div class="page">
  <!-- ライブラリスキャン -->
  <section class="card">
    <h2>ライブラリ</h2>
    {#if system?.library}
      <p class="stats dim tabular">
        {system.library.tracks} 曲 / {system.library.albums} アルバム /
        {system.library.artists} アーティスト
        <br />
        総再生時間 {formatDuration(system.library.total_duration)}
        · アートワーク {system.library.albums_with_artwork} 枚
        · ラウドネス計測済み {system.library.tracks_with_gain} 曲
      </p>
    {/if}

    {#if scan.running}
      <div class="progress">
        <div class="bar"><div class="fill" style={`width: ${scan.percent}%`}></div></div>
        <p class="dim tabular">{scan.phase} {scan.processed}/{scan.total}（{scan.percent}%）</p>
      </div>
    {:else}
      <div class="buttons">
        <button class="primary" onclick={() => scan.start(false)}>スキャン</button>
        <button class="ghost" onclick={() => scan.start(true)}>完全に再スキャン</button>
      </div>
      {#if scan.summary}
        <p class="dim tabular">
          前回: 追加 {scan.summary.added} / 更新 {scan.summary.updated} / 削除
          {scan.summary.removed}（{scan.summary.duration_seconds} 秒）
        </p>
      {/if}
      {#if scan.error}
        <p class="error">{scan.error}</p>
      {/if}
    {/if}
  </section>

  <!-- 設定項目 -->
  <!-- 出力先と Bluetooth。設定項目の自動生成とは作りが違うので別部品にした -->
  <AudioOutput />

  {#each GROUPS as group (group.title)}
    <section class="card">
      <h2>{group.title}</h2>
      {#if group.intro}<p class="intro">{group.intro}</p>{/if}
      {#if group.note}<p class="note dim">{group.note}</p>{/if}

      {#if group.loudnessActions}
        <div class="loudness-actions">
          {#if loudness}
            <div class="meter">
              <div class="bar">
                <div
                  class="fill"
                  style={`width: ${loudness.total ? (loudness.measured / loudness.total) * 100 : 0}%`}
                ></div>
              </div>
              <p class="stats dim tabular">
                計測済み {loudness.measured} / {loudness.total} 曲
                {#if loudness.remaining > 0}
                  · 未計測 {loudness.remaining} 曲（所要 約{loudness.estimated_text}）
                {:else}
                  · すべて計測済みです
                {/if}
              </p>
            </div>
          {/if}
          {#if scan.running}
            <p class="dim">スキャン／計測の実行中です。</p>
          {:else}
            <div class="buttons">
              <button
                class="primary"
                onclick={() => askLoudness(false)}
                disabled={loudness ? loudness.remaining === 0 : false}
              >
                未計測の曲を計測
              </button>
              <button class="ghost" onclick={() => askLoudness(true)}>すべて測り直す</button>
            </div>
          {/if}
        </div>
      {/if}

      {#each group.keys as key (key)}
        {@const spec = specFor(key)}
        {#if spec}
          <div class="field">
            <div class="label">
              <span>{spec.label}</span>
              {#if spec.description}<small class="dim">{spec.description}</small>{/if}
            </div>

            <div class="control">
              {#if spec.kind === 'bool'}
                <label class="switch">
                  <input
                    type="checkbox"
                    checked={settings.values[key] ?? false}
                    onchange={(event) => update(key, event.currentTarget.checked)}
                  />
                  <span class="slider"></span>
                </label>
              {:else if spec.kind === 'enum'}
                <select
                  value={settings.values[key]}
                  onchange={(event) => update(key, event.currentTarget.value)}
                >
                  {#each spec.choices ?? [] as choice (choice)}
                    <option value={choice}>{enumLabel(spec, choice)}</option>
                  {/each}
                </select>
              {:else if spec.kind === 'list[str]'}
                <textarea
                  rows="4"
                  value={(settings.values[key] ?? []).join('\n')}
                  onchange={(event) => updateSeparators(event.currentTarget.value)}
                ></textarea>
              {:else}
                <input
                  type="number"
                  min={spec.minimum ?? undefined}
                  max={spec.maximum ?? undefined}
                  step={spec.kind === 'float' ? 0.5 : 1}
                  value={settings.values[key]}
                  onchange={(event) => update(key, Number(event.currentTarget.value))}
                />
              {/if}
            </div>
          </div>

          {#if key === 'artist_separators'}
            <div class="analyze">
              <button class="ghost small" onclick={analyze} disabled={analyzing}>
                実データの区切り文字を調べる
              </button>
              {#if separatorReport}
                {#if separatorReport.candidates.length === 0}
                  <p class="dim">区切り文字の候補は見つかりませんでした。</p>
                {:else}
                  <table>
                    <thead>
                      <tr><th>区切り</th><th>件数</th><th>例</th></tr>
                    </thead>
                    <tbody>
                      {#each separatorReport.candidates as candidate (candidate.separator)}
                        <tr>
                          <td><code>{JSON.stringify(candidate.separator)}</code></td>
                          <td class="tabular">{candidate.count}</td>
                          <td class="truncate">{candidate.examples[0] ?? ''}</td>
                        </tr>
                      {/each}
                    </tbody>
                  </table>
                {/if}
              {/if}
            </div>
          {/if}
        {/if}
      {/each}
    </section>
  {/each}

  <!-- システム情報 -->
  {#if system}
    <section class="card">
      <h2>システム</h2>
      <dl>
        <dt>バージョン</dt>
        <dd>kotone {system.version}（スキーマ v{system.schema_version}）</dd>
        <dt>MPD</dt>
        <dd class:offline={!system.mpd.connected}>
          {system.mpd.connected ? `接続中（${system.mpd.host}）` : '未接続'}
          {#if system.mpd.audio}<br />出力: {system.mpd.audio}{/if}
        </dd>
        <dt>音楽ディレクトリ</dt>
        <dd><code>{system.paths.music_dir}</code></dd>
        <dt>データ領域</dt>
        <dd>
          <code>{system.paths.data_dir}</code>
          {#if system.storage}
            <br />空き {formatBytes(system.storage.free)} / 全体
            {formatBytes(system.storage.total)}
          {/if}
        </dd>
        <dt>外部ツール</dt>
        <dd>
          rsgain: {system.tools.rsgain ? 'あり' : 'なし'} ·
          FFmpeg: {system.tools.ffmpeg ? 'あり' : 'なし'} ·
          形態素解析: {system.tools.morphological_analyzer ? 'あり' : 'なし'}
        </dd>
      </dl>
      <p class="note dim">
        API ドキュメント: <a href="/api/docs" target="_blank" rel="noreferrer">/api/docs</a>
      </p>
    </section>
  {/if}
</div>

<!-- ラウドネス計測は長時間かかるため、所要時間を示してから実行を確認する -->
<Modal bind:open={confirmOpen} title="ラウドネスを計測しますか？">
  {#if confirmLoading}
    <div class="loading">
      <div class="spinner"></div>
      {#if confirmCalibrating}
        <p class="dim">この機器での計測速度を測っています…</p>
      {/if}
    </div>
  {:else if confirmEstimate}
    <dl class="estimate">
      <dt>対象</dt>
      <dd>
        {confirmEstimate.remaining} 曲
        {#if confirmRemeasure}<span class="dim">（計測済みも含めて測り直します）</span>{/if}
      </dd>
      <dt>音源の長さ</dt>
      <dd class="tabular">{formatTime(confirmEstimate.remaining_audio_seconds)}</dd>
      <dt>予想所要時間</dt>
      <dd class="highlight">約 {confirmEstimate.estimated_text}</dd>
      <dt>計測方式</dt>
      <dd>{confirmEstimate.backend}（実時間比 {confirmEstimate.throughput}x）</dd>
    </dl>

    {#if !confirmEstimate.is_calibrated}
      <p class="note dim">
        ※ この機器での実測ができなかったため、標準的な速度による概算です。
        実際の時間は前後することがあります。
      </p>
    {:else}
      <p class="note dim">※ この機器で実測した速度に基づく見積もりです。</p>
    {/if}

    <p class="note dim">
      計測中も再生はできます。<strong>途中で中断しても、計測済みの曲は記録されるので
      次回は続きから再開します。</strong>
      夜間などに分けて進めることもできます。
    </p>

    {#if !confirmEstimate.available}
      <p class="warn">rsgain も FFmpeg も見つからないため計測できません。</p>
    {/if}
  {/if}

  {#snippet footer()}
    <button class="ghost" onclick={() => (confirmOpen = false)}>キャンセル</button>
    <button
      class="primary"
      onclick={runLoudness}
      disabled={confirmLoading || !confirmEstimate?.available || confirmEstimate?.remaining === 0}
    >
      計測を開始
    </button>
  {/snippet}
</Modal>

<style>
  .page {
    display: flex;
    flex-direction: column;
    gap: 14px;
    padding: 16px;
    max-width: 720px;
    margin: 0 auto;
  }

  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    padding: 16px;
  }

  h2 {
    font-size: var(--fs-lg);
    letter-spacing: 0.04em;
    color: var(--accent);
    margin-bottom: 12px;
  }

  .stats,
  .note {
    margin: 0 0 12px;
    font-size: var(--fs-sm);
    line-height: 1.6;
  }

  /* この機能を知らない人向けの説明。注意書き（.note）より読ませたいので、
     本文と同じ濃さにして左のアクセント罫で読み物として区切る。 */
  .intro {
    margin: 0 0 14px;
    padding: 2px 0 2px 12px;
    border-left: 2px solid var(--accent-dim);
    font-size: var(--fs-md);
    line-height: 1.75;
    color: var(--text-muted);
  }

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
    font-size: var(--fs-xs);
    line-height: 1.45;
  }

  .control {
    flex: none;
    width: 150px;
  }

  .control textarea {
    width: 100%;
    font-family: ui-monospace, 'SF Mono', Consolas, monospace;
    font-size: var(--fs-sm);
    resize: vertical;
  }

  .control input[type='number'],
  .control select {
    width: 100%;
  }

  /* トグルスイッチ（タップ領域 44px 確保） */
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
    width: 52px;
    height: 32px;
    border-radius: 999px;
    background: var(--bg-sunken);
    border: 1px solid var(--border);
    transition: background 0.18s;
    position: relative;
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

  .buttons {
    display: flex;
    gap: 10px;
    flex-wrap: wrap;
    margin-bottom: 10px;
  }

  .primary,
  .ghost {
    min-height: var(--tap);
    padding: 0 20px;
    border-radius: 999px;
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

  .ghost.small {
    min-height: 38px;
    padding: 0 14px;
    font-size: var(--fs-md);
  }

  .ghost:disabled {
    opacity: 0.45;
    cursor: default;
  }

  .progress .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--bg-sunken);
    overflow: hidden;
  }

  .progress .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s;
  }

  .progress p {
    margin: 8px 0 0;
    font-size: var(--fs-sm);
  }

  .error {
    color: var(--danger);
    font-size: var(--fs-sm);
  }

  .loudness-actions {
    padding-bottom: 8px;
  }

  .meter {
    margin-bottom: 12px;
  }

  .meter .bar {
    height: 6px;
    border-radius: 3px;
    background: var(--bg-sunken);
    overflow: hidden;
  }

  .meter .fill {
    height: 100%;
    background: var(--accent);
    transition: width 0.3s;
  }

  .meter .stats {
    margin: 8px 0 0;
  }

  /* 実行前の確認ダイアログ */
  .estimate {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 10px 16px;
    margin: 0 0 16px;
    font-size: var(--fs-md);
  }

  .estimate dt {
    color: var(--text-dim);
    white-space: nowrap;
  }

  .estimate dd {
    margin: 0;
  }

  .estimate .highlight {
    color: var(--accent);
    font-size: 16px;
    font-weight: 600;
  }

  .loading {
    display: grid;
    place-items: center;
    padding: 32px;
  }

  .warn {
    color: var(--danger);
    font-size: var(--fs-md);
  }

  .analyze {
    padding: 4px 0 12px;
  }

  .analyze table {
    width: 100%;
    margin-top: 10px;
    border-collapse: collapse;
    font-size: var(--fs-sm);
  }

  .analyze th,
  .analyze td {
    text-align: left;
    padding: 5px 8px 5px 0;
    border-bottom: 1px solid color-mix(in srgb, var(--border) 45%, transparent);
  }

  .analyze th {
    color: var(--text-dim);
    font-weight: 500;
  }

  dl {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 8px 16px;
    margin: 0;
    font-size: var(--fs-md);
  }

  dt {
    color: var(--text-dim);
    white-space: nowrap;
  }

  dd {
    margin: 0;
    min-width: 0;
    overflow-wrap: anywhere;
  }

  dd.offline {
    color: var(--danger);
  }

  code {
    font-family: ui-monospace, 'SF Mono', Consolas, monospace;
    font-size: var(--fs-xs);
  }

  a {
    color: var(--accent);
  }

  @media (max-width: 520px) {
    .field {
      flex-direction: column;
      align-items: stretch;
      gap: 8px;
    }

    .control {
      width: 100%;
    }

    .switch {
      justify-content: flex-start;
    }
  }
</style>
