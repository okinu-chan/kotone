<script>
  /**
   * 再生中画面。
   *
   * アーティスト / アルバム / 年 / ジャンルはタップでライブラリを横断的に
   * 絞り込める（ダイレクトサーチ。要件 3.6）。
   * アルバムアート上の横スワイプで曲送り／曲戻し（画面端ではなく操作対象の
   * 内部に限定。要件 3.5）。
   */
  import AlbumArt from './AlbumArt.svelte';
  import { api } from '../api.js';
  import { formatCodec, formatQuality, formatTime, ORDER_LABELS, REPEAT_LABELS } from '../format.js';
  import { player, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  const track = $derived(player.track);
  const album = $derived(track ? { id: track.album_id, artwork: track.artwork } : null);
  const progress = $derived(player.duration > 0 ? (player.elapsed / player.duration) * 100 : 0);

  const REPEAT_ORDER = ['off', 'range', 'one'];
  const PLAY_ORDER = ['normal', 'shuffle', 'random'];

  function cycleRepeat() {
    const next = REPEAT_ORDER[(REPEAT_ORDER.indexOf(player.state.repeat) + 1) % REPEAT_ORDER.length];
    player.command(() => api.setRepeat(next));
  }

  function cycleOrder() {
    const next = PLAY_ORDER[(PLAY_ORDER.indexOf(player.state.order) + 1) % PLAY_ORDER.length];
    player.command(() => api.setOrder(next));
  }

  function onSeekInput(event) {
    player.seeking = true;
    player.localElapsed = Number(event.currentTarget.value);
  }

  async function onSeekCommit(event) {
    const seconds = Number(event.currentTarget.value);
    player.seeking = false;
    await player.command(() => api.seek(seconds));
  }

  function onVolume(event) {
    player.command(() => api.setVolume(Number(event.currentTarget.value)));
  }

  // --- アートワーク上の横スワイプ（曲送り／曲戻し） ------------------
  let touchStart = null;

  function onTouchStart(event) {
    const point = event.changedTouches[0];
    touchStart = { x: point.clientX, y: point.clientY };
  }

  function onTouchEnd(event) {
    if (!touchStart) return;
    const point = event.changedTouches[0];
    const dx = point.clientX - touchStart.x;
    const dy = point.clientY - touchStart.y;
    touchStart = null;
    // 縦スクロールとの誤爆を避けるため、横方向が明確に優位なときだけ反応する
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.8) return;
    player.command(() => (dx < 0 ? api.next() : api.previous()));
  }

  function openScope(kind, id) {
    if (id === null || id === undefined) return;
    router.navigate(`/${kind}/${id}`);
  }

  async function playAlbumFromHere() {
    if (!track?.album_id) return;
    try {
      player.apply(await api.playScope('album', track.album_id, track.id));
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }
</script>

<section class="now">
  {#if !player.state.connected}
    <div class="offline">
      <p>MPD に接続できていません。</p>
      <p class="dim">Pi 上で <code>systemctl status mpd</code> を確認してください。</p>
    </div>
  {/if}

  <div
    class="art-wrap"
    ontouchstart={onTouchStart}
    ontouchend={onTouchEnd}
    role="img"
    aria-label={track ? `${track.title} のアルバムアート` : 'アルバムアート'}
  >
    <AlbumArt {album} tier="large" alt="" square={false} eager />
  </div>

  <div class="meta">
    <h1 class="title clamp-2">{track?.title ?? '再生していません'}</h1>

    <div class="chips">
      {#each track?.artists ?? [] as artist (artist.id)}
        <button class="chip" onclick={() => openScope('artists', artist.id)}>
          {artist.name}
        </button>
      {/each}
      {#if track?.album}
        <button class="chip" onclick={() => openScope('albums', track.album_id)}>
          {track.album}
        </button>
      {/if}
      {#if track?.year}
        <button class="chip" onclick={() => openScope('years', track.year)}>
          {track.year} 年
        </button>
      {/if}
      {#each track?.genres ?? [] as genre (genre.id)}
        <button class="chip" onclick={() => openScope('genres', genre.id)}>
          {genre.name}
        </button>
      {/each}
    </div>

    {#if track}
      <p class="quality dim tabular">
        {formatCodec(track)}
        {#if formatQuality(track)}<span class="sep">·</span>{formatQuality(track)}{/if}
        {#if track.track_gain_db != null}
          <span class="sep">·</span>ラウドネス補正 {track.track_gain_db.toFixed(1)} dB
        {/if}
      </p>
    {/if}
  </div>

  <div class="seek">
    <input
      type="range"
      min="0"
      max={Math.max(player.duration, 1)}
      step="1"
      value={player.elapsed}
      oninput={onSeekInput}
      onchange={onSeekCommit}
      aria-label="再生位置"
      style={`--progress: ${progress}%`}
    />
    <div class="times tabular dim">
      <span>{formatTime(player.elapsed)}</span>
      <span>{formatTime(player.duration)}</span>
    </div>
  </div>

  <div class="transport">
    <button
      class="mode"
      class:active={player.state.order !== 'normal'}
      onclick={cycleOrder}
      title={ORDER_LABELS[player.state.order]}
      aria-label={ORDER_LABELS[player.state.order]}
    >
      {#if player.state.order === 'random'}
        <span class="mode-icon">🎲</span>
      {:else if player.state.order === 'shuffle'}
        <span class="mode-icon">🔀</span>
      {:else}
        <span class="mode-icon">➡</span>
      {/if}
    </button>

    <button class="skip" onclick={() => player.command(api.previous)} aria-label="前の曲">
      <svg viewBox="0 0 24 24" fill="currentColor"
        ><path d="M7 6h2v12H7zm2.5 6L18 6v12z" /></svg
      >
    </button>

    <button class="play" onclick={() => player.command(api.toggle)} aria-label={player.playing ? '一時停止' : '再生'}>
      {#if player.playing}
        <svg viewBox="0 0 24 24" fill="currentColor"
          ><path d="M7 5h3.5v14H7zm6.5 0H17v14h-3.5z" /></svg
        >
      {:else}
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M8 5l12 7-12 7z" /></svg>
      {/if}
    </button>

    <button class="skip" onclick={() => player.command(api.next)} aria-label="次の曲">
      <svg viewBox="0 0 24 24" fill="currentColor"
        ><path d="M15 6h2v12h-2zM6 6l8.5 6L6 18z" /></svg
      >
    </button>

    <button
      class="mode"
      class:active={player.state.repeat !== 'off'}
      onclick={cycleRepeat}
      title={REPEAT_LABELS[player.state.repeat]}
      aria-label={REPEAT_LABELS[player.state.repeat]}
    >
      <span class="mode-icon">{player.state.repeat === 'one' ? '🔂' : '🔁'}</span>
    </button>
  </div>

  <p class="mode-labels dim">
    {ORDER_LABELS[player.state.order]}<span class="sep">·</span>{REPEAT_LABELS[player.state.repeat]}
    <span class="sep">·</span>{player.state.scope?.label ?? ''}
  </p>

  <!-- 音量をアンプ側で取る構成ではスライダーを出さない（設定「音量の操作方法」） -->
  {#if player.state.volume_control !== 'external'}
    <div class="volume">
      <span aria-hidden="true">🔈</span>
      <input
        type="range"
        min="0"
        max="100"
        value={player.state.volume ?? 60}
        onchange={onVolume}
        aria-label="音量"
      />
      <span class="tabular dim vol-value">{player.state.volume ?? 0}</span>
    </div>
  {/if}

  {#if track?.album_id}
    <button class="album-link" onclick={playAlbumFromHere}>
      このアルバムを最初から再生
    </button>
  {/if}
</section>

<style>
  .now {
    display: flex;
    flex-direction: column;
    gap: 18px;
    padding: 12px 20px 24px;
    max-width: 560px;
    margin: 0 auto;
  }

  .offline {
    background: color-mix(in srgb, var(--danger) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--danger) 45%, transparent);
    border-radius: var(--radius);
    padding: 12px 14px;
    font-size: 14px;
  }

  .offline p {
    margin: 0;
  }

  /* 縦横比は画像に合わせる（正方形に切り取らない。要件 3.3）。
     縦長ジャケットでも操作部が画面外へ押し出されないよう高さだけ制限する。 */
  .art-wrap {
    width: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    touch-action: pan-y;
  }

  /* 角丸にはせず、余白も塗らない（原盤のジャケットをそのまま出す） */
  .art-wrap :global(.art) {
    overflow: hidden;
  }

  .meta {
    display: flex;
    flex-direction: column;
    gap: 10px;
  }

  .title {
    font-size: 22px;
    line-height: 1.35;
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  /* ダイレクトサーチの起点。タップ領域を十分に取る（要件 3.5 / 3.7） */
  .chip {
    min-height: 34px;
    padding: 6px 12px;
    border-radius: 999px;
    background: var(--surface);
    border: 1px solid var(--border);
    color: var(--text);
    font-size: 13px;
    max-width: 100%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    transition: background 0.15s;
  }

  .chip:hover {
    background: var(--surface-hover);
    border-color: var(--accent-dim);
  }

  .quality {
    margin: 0;
    font-size: 12px;
  }

  .sep {
    margin: 0 6px;
    opacity: 0.5;
  }

  .seek input[type='range'],
  .volume input[type='range'] {
    -webkit-appearance: none;
    appearance: none;
    width: 100%;
    min-height: 0;
    height: 28px;
    padding: 0;
    background: transparent;
    border: none;
  }

  input[type='range']::-webkit-slider-runnable-track {
    height: 4px;
    border-radius: 2px;
    background: var(--border);
  }

  input[type='range']::-moz-range-track {
    height: 4px;
    border-radius: 2px;
    background: var(--border);
  }

  input[type='range']::-webkit-slider-thumb {
    -webkit-appearance: none;
    appearance: none;
    width: 16px;
    height: 16px;
    margin-top: -6px;
    border-radius: 50%;
    background: var(--accent);
    border: none;
  }

  input[type='range']::-moz-range-thumb {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--accent);
    border: none;
  }

  .times {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin-top: -4px;
  }

  .transport {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
  }

  .transport button {
    display: grid;
    place-items: center;
    min-width: var(--tap);
    min-height: var(--tap);
    border-radius: 50%;
    color: var(--text);
  }

  .transport svg {
    width: 30px;
    height: 30px;
  }

  .play {
    width: 68px;
    height: 68px;
    background: var(--accent);
    color: #0d0f13 !important;
  }

  .play svg {
    width: 34px;
    height: 34px;
  }

  .mode {
    color: var(--text-dim) !important;
    font-size: 20px;
  }

  .mode.active {
    color: var(--accent) !important;
  }

  .mode-icon {
    line-height: 1;
  }

  .mode-labels {
    margin: -8px 0 0;
    text-align: center;
    font-size: 12px;
  }

  .volume {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .vol-value {
    min-width: 2.5em;
    text-align: right;
    font-size: 13px;
  }

  .album-link {
    min-height: var(--tap);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--surface);
    color: var(--text-muted);
    font-size: 14px;
  }

  .album-link:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  @media (min-width: 900px) {
    .now {
      max-width: 620px;
      padding-top: 24px;
    }

    .title {
      font-size: 26px;
    }
  }
</style>
