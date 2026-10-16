<script>
  /**
   * 再生中画面。
   *
   * アーティスト / アルバム / 年 / ジャンルはタップでライブラリを横断的に
   * 絞り込める（ダイレクトサーチ。要件 3.6）。
   * アルバムアート上の横スワイプで曲送り／曲戻し（画面端ではなく操作対象の
   * 内部に限定。要件 3.5）。
   */
  import AddToPlaylist from './AddToPlaylist.svelte';
  import AlbumArt from './AlbumArt.svelte';
  import ArtworkViewer from './ArtworkViewer.svelte';
  import Modal from './Modal.svelte';
  import ScrollingTitle from './ScrollingTitle.svelte';
  import VolumeControl from './VolumeControl.svelte';
  import { fade } from 'svelte/transition';
  import { api, artworkUrl } from '../api.js';
  import { formatCodec, formatQuality, formatTime, ORDER_LABELS, REPEAT_LABELS } from '../format.js';
  import { player, settings, toast } from '../stores.svelte.js';
  import { router } from '../router.svelte.js';

  const track = $derived(player.track);

  /**
   * 表示するラウドネス補正値。
   *
   * 補正がオフのときの計測値は、いま鳴っている音と関係がないので出さない。
   * 「アルバム」設定ではアルバム単位の値が効いているので、そちらを出す。
   */
  const loudnessGain = $derived.by(() => {
    if (!settings.values.loudness_enabled || !track) return null;
    const gain =
      settings.values.loudness_mode === 'album' ? track.album_gain_db : track.track_gain_db;
    return gain == null ? null : gain.toFixed(1);
  });
  const album = $derived(track ? { id: track.album_id, artwork: track.artwork } : null);
  /**
   * 背景（アンビエント）に敷く画像。
   *
   * 強くぼかすので解像度は要らない。一覧用の small を拡大して使い、
   * 曲が変わるたびに大きな画像を読み直さないようにする。
   */
  const ambientUrl = $derived(artworkUrl(album, 'small'));
  const progress = $derived(player.duration > 0 ? (player.elapsed / player.duration) * 100 : 0);

  /**
   * 再生範囲の中で何曲目か（例: 5 / 20）。
   *
   * ランダム（重複あり）ではキューを [再生中, 次の1曲] しか持たないため
   * 「1 / 2」となって意味を成さない。その場合は出さない。
   */
  const trackPosition = $derived.by(() => {
    const { queue_position: index, queue_length: total, order } = player.state;
    if (order === 'random') return null;
    if (index === null || index === undefined || !total) return null;
    return `${index + 1} / ${total}`;
  });

  let zoomOpen = $state(false);
  let fullTitleOpen = $state(false);
  let addOpen = $state(false);

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
    // 終端では曲送りを受け付けない（指が滑っただけで再生が止まらないように）
    if (dx < 0 && !player.canNext) return;
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

<!-- アートワークをぼかして画面全体に敷く。曲が変わったら新旧を重ねて切り替える。
     ぼかしと色の合成は端末側の描画で、Pi は small の画像を配るだけ -->
<div class="ambient" aria-hidden="true">
  <!-- フェードは {#key} の直下に置いた local 遷移なので、曲が変わったときだけ動く。
       global にすると、ほかの画面へ移るときも再生中画面がフェードの間だけ残る -->
  {#if ambientUrl}
    {#key ambientUrl}
      <img src={ambientUrl} alt="" decoding="async" transition:fade={{ duration: 600 }} />
    {/key}
  {/if}
  <div class="veil"></div>
</div>

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
    <AlbumArt
      {album}
      tier="large"
      alt=""
      square={false}
      eager
      onzoom={() => (zoomOpen = true)}
    />
  </div>

  <ArtworkViewer bind:open={zoomOpen} {album} alt={track ? `${track.title} のアートワーク` : ''} />

  <div class="meta">
    <h1 class="title">
      <ScrollingTitle
        text={track?.title ?? '再生していません'}
        onfulltext={() => (fullTitleOpen = true)}
      />
    </h1>

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
        {#if loudnessGain}
          <span class="sep">·</span>ラウドネス補正 {loudnessGain} dB
        {/if}
        {#if player.state.bluetooth_codec}
          <!-- 何のコーデックで繋がっているか、機器側で分からない機種が多い -->
          <span class="sep">·</span>{player.state.bluetooth_codec}
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
      {#if trackPosition}
        <span class="position">{trackPosition}</span>
      {/if}
      <span>{formatTime(player.duration)}</span>
    </div>
  </div>

  <div class="transport">
    <!-- 再生順（通常／シャッフル／ランダム）。線画の透過ピクトグラム -->
    <button
      class="mode"
      class:active={player.state.order !== 'normal'}
      onclick={cycleOrder}
      title={ORDER_LABELS[player.state.order]}
      aria-label={ORDER_LABELS[player.state.order]}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
           stroke-linecap="round" stroke-linejoin="round">
        {#if player.state.order === 'shuffle'}
          <!-- 交差する2本の経路＝重複なしシャッフル -->
          <path d="M3 7h3.5l4 10H17" />
          <path d="M3 17h3.5l4-10H17" />
          <path d="M14.5 4.5 17.5 7l-3 2.5" />
          <path d="M14.5 14.5 17.5 17l-3 2.5" />
        {:else if player.state.order === 'random'}
          <!-- サイコロ＝毎回独立に抽選するランダム -->
          <rect x="4" y="4" width="16" height="16" rx="3.5" />
          <circle cx="9" cy="9" r="1.15" fill="currentColor" stroke="none" />
          <circle cx="15" cy="15" r="1.15" fill="currentColor" stroke="none" />
          <circle cx="15" cy="9" r="1.15" fill="currentColor" stroke="none" />
          <circle cx="9" cy="15" r="1.15" fill="currentColor" stroke="none" />
        {:else}
          <!-- 直線の矢印＝タグ順の通常再生 -->
          <path d="M3 12h15" />
          <path d="M14.5 8.5 18 12l-3.5 3.5" />
        {/if}
      </svg>
    </button>

    <!-- 音量は操作ボタンと同じ並びに置く。下のほうに置くと、縦長の画面では
         スクロールしないと届かない（iPhone 15 Pro / Safari で確認） -->
    <VolumeControl placement="up" />

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

    <button
      class="skip"
      onclick={() => player.command(api.next)}
      disabled={!player.canNext}
      aria-label="次の曲"
    >
      <svg viewBox="0 0 24 24" fill="currentColor"
        ><path d="M15 6h2v12h-2zM6 6l8.5 6L6 18z" /></svg
      >
    </button>

    <!-- リピート（オフ／範囲／1曲）。線画の透過ピクトグラム -->
    <button
      class="mode"
      class:active={player.state.repeat !== 'off'}
      onclick={cycleRepeat}
      title={REPEAT_LABELS[player.state.repeat]}
      aria-label={REPEAT_LABELS[player.state.repeat]}
    >
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"
           stroke-linecap="round" stroke-linejoin="round">
        <path d="M17 3.5 20 6l-3 2.5" />
        <path d="M20 6H7a3.5 3.5 0 0 0-3.5 3.5V11" />
        <path d="M7 20.5 4 18l3-2.5" />
        <path d="M4 18h13a3.5 3.5 0 0 0 3.5-3.5V13" />
        {#if player.state.repeat === 'one'}
          <!-- 中央の「1」で 1 曲リピートを示す -->
          <path d="M11.2 10.4 12.4 9.6V15" />
        {/if}
      </svg>
    </button>
  </div>

  <p class="mode-labels dim">
    {ORDER_LABELS[player.state.order]}<span class="sep">·</span>{REPEAT_LABELS[player.state.repeat]}
    <span class="sep">·</span>{player.state.scope?.label ?? ''}
  </p>



  <div class="extras">
    {#if track?.album_id}
      <button class="album-link" onclick={playAlbumFromHere}>
        このアルバムを最初から再生
      </button>
    {/if}
    {#if track}
      <button class="album-link" onclick={() => (addOpen = true)}>
        この曲をプレイリストへ追加
      </button>
    {/if}
    <!-- プレイリストから再生しているときは、その一覧へ戻れるようにする -->
    {#if player.state.scope?.kind === 'playlist' && player.state.scope?.id}
      <button
        class="album-link"
        onclick={() => router.navigate(`/playlists/${player.state.scope.id}`)}
      >
        再生中のプレイリストを開く
      </button>
    {/if}
  </div>
</section>

{#if track}
  <AddToPlaylist bind:open={addOpen} trackIds={[track.id]} />
{/if}

<!-- 長いタイトルの全文表示（スクロールを待たずに読めるように）。
     読むだけなので、どこをタップしても閉じる -->
<Modal bind:open={fullTitleOpen} title="曲名" dismissOnTap>
  <p class="full-title">{track?.title ?? ''}</p>
  {#if track}
    <dl class="full-meta">
      {#if track.artist}
        <dt>アーティスト</dt>
        <dd>{track.artist}</dd>
      {/if}
      {#if track.album}
        <dt>アルバム</dt>
        <dd>{track.album}</dd>
      {/if}
    </dl>
  {/if}
</Modal>

<style>
  /* 画面全体の背面に固定する。z-index: -1 は .app（isolation: isolate）の中での
     最背面なので、body の背景より上、ほかの内容より下に描かれる */
  .ambient {
    position: fixed;
    inset: 0;
    z-index: -1;
    overflow: hidden;
    pointer-events: none;
    background: var(--bg);
  }

  /* 縁がぼけて背景色が透けないよう、拡大してはみ出させる。
     静止画を 1 回ぼかすだけで、ぼかしの値は動かさない（端末の負荷を抑える） */
  .ambient img {
    position: absolute;
    inset: 0;
    width: 100%;
    height: 100%;
    object-fit: cover;
    filter: blur(48px) saturate(1.5);
    transform: scale(1.3);
  }

  /* 文字と操作部を読めるように、テーマの背景色を重ねて沈める。
     下ほど濃くして、操作ボタンの周りのコントラストを確保する */
  .veil {
    position: absolute;
    inset: 0;
    background: linear-gradient(
      to bottom,
      color-mix(in srgb, var(--bg) 30%, transparent),
      color-mix(in srgb, var(--bg) 45%, transparent) 55%,
      color-mix(in srgb, var(--bg) 70%, transparent)
    );
  }

  /* 白地のジャケットがライトテーマの背景に溶けないよう、少し濃く沈める */
  :global(:root[data-theme='light']) .veil {
    background: linear-gradient(
      to bottom,
      color-mix(in srgb, var(--bg) 55%, transparent),
      color-mix(in srgb, var(--bg) 70%, transparent) 55%,
      color-mix(in srgb, var(--bg) 85%, transparent)
    );
  }

  /* 「透明度を下げる」がオンの端末では、ぼかしを出さずテーマの背景色だけにする */
  @media (prefers-reduced-transparency: reduce) {
    .ambient img {
      display: none;
    }
  }

  .now {
    display: flex;
    flex-direction: column;
    gap: 18px;
    /* ホーム画面のウェブアプリではステータスバーの下まで描かれるので、その高さを足す */
    padding: calc(12px + var(--safe-top)) 20px 24px;
    max-width: 560px;
    margin: 0 auto;
  }

  .offline {
    background: color-mix(in srgb, var(--danger) 16%, transparent);
    border: 1px solid color-mix(in srgb, var(--danger) 45%, transparent);
    border-radius: var(--radius);
    padding: 12px 14px;
    font-size: var(--fs-lg);
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

  /* 曲名は 1 行に固定する（折り返してアートワークや操作部を押し下げない） */
  .title {
    min-width: 0;
  }

  .full-title {
    margin: 0 0 16px;
    font-size: 18px;
    font-weight: 600;
    line-height: 1.6;
    overflow-wrap: anywhere;
  }

  .full-meta {
    display: grid;
    grid-template-columns: auto 1fr;
    gap: 8px 16px;
    margin: 0;
    font-size: var(--fs-md);
  }

  .full-meta dt {
    color: var(--text-dim);
    white-space: nowrap;
  }

  .full-meta dd {
    margin: 0;
    overflow-wrap: anywhere;
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
    /* 背景のアートワークの色が透けるよう、半透明にする */
    background: color-mix(in srgb, var(--surface) 60%, transparent);
    border: 1px solid var(--border);
    color: var(--text);
    font-size: var(--fs-md);
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
    font-size: var(--fs-sm);
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

  /* 再生済みの部分に軌跡を残す（--progress は再生位置の割合） */
  .seek input[type='range']::-webkit-slider-runnable-track {
    height: 4px;
    border-radius: 2px;
    background: linear-gradient(
      to right,
      var(--accent) 0%,
      var(--accent) var(--progress, 0%),
      var(--border) var(--progress, 0%),
      var(--border) 100%
    );
  }

  .seek input[type='range']::-moz-range-track {
    height: 4px;
    border-radius: 2px;
    background: linear-gradient(
      to right,
      var(--accent) 0%,
      var(--accent) var(--progress, 0%),
      var(--border) var(--progress, 0%),
      var(--border) 100%
    );
  }

  /* 音量スライダーは軌跡を付けない（現在値だけ分かればよい） */
  .volume input[type='range']::-webkit-slider-runnable-track,
  .volume input[type='range']::-moz-range-track {
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
    /* 外側に光彩を入れて光っているように見せる */
    box-shadow:
      0 0 8px 1px var(--accent-glow),
      0 0 16px 2px var(--accent-glow);
  }

  input[type='range']::-moz-range-thumb {
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--accent);
    border: none;
    box-shadow:
      0 0 8px 1px var(--accent-glow),
      0 0 16px 2px var(--accent-glow);
  }

  .times {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: var(--fs-sm);
    margin-top: -4px;
  }

  /* 再生範囲の中での位置（5 / 20）。時間表示の間に置く */
  .position {
    color: var(--text-muted);
    letter-spacing: 0.02em;
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

  /* 曲送り・曲戻し・再生は白抜きのサークルで囲む */
  .skip,
  .play {
    border: 1.5px solid var(--text);
    background: transparent;
    transition:
      box-shadow 0.2s,
      border-color 0.2s,
      transform 0.1s;
  }

  .skip {
    width: 52px;
    height: 52px;
    /* 外側へ広がる光彩 */
    box-shadow:
      0 0 10px -2px var(--accent-glow),
      0 0 0 1px rgb(255 255 255 / 0.06);
  }

  .skip:hover:not(:disabled) {
    border-color: var(--accent);
    box-shadow: 0 0 16px -1px var(--accent-glow);
  }

  /* 終端では曲送りできない。光彩を落として「押しても効かない」ことを見せる */
  .skip:disabled {
    opacity: 0.32;
    box-shadow: none;
    cursor: default;
  }

  .transport svg {
    width: 26px;
    height: 26px;
  }

  .play {
    width: 72px;
    height: 72px;
    border-width: 2px;
    border-color: var(--accent);
    color: var(--accent);
    box-shadow:
      0 0 22px -2px var(--accent-glow),
      inset 0 0 14px -6px var(--accent-glow);
  }

  .play:hover {
    box-shadow:
      0 0 30px 0 var(--accent-glow),
      inset 0 0 18px -6px var(--accent-glow);
  }

  .play:active {
    transform: scale(0.96);
  }

  .play svg {
    width: 32px;
    height: 32px;
  }

  /* リピート・シャッフルは塗りのない線画（透過ピクトグラム） */
  .mode {
    color: var(--text-dim) !important;
    background: transparent;
    border: none;
  }

  .mode svg {
    width: 24px;
    height: 24px;
  }

  .mode.active {
    color: var(--accent) !important;
  }

  .mode.active svg {
    filter: drop-shadow(0 0 6px var(--accent-glow));
  }

  .mode-labels {
    margin: -8px 0 0;
    text-align: center;
    font-size: var(--fs-sm);
  }



  /* 副次的な操作。狭い画面では縦に積み、広ければ横に並べる */
  .extras {
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .album-link {
    min-height: var(--tap);
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: color-mix(in srgb, var(--surface) 60%, transparent);
    color: var(--text-muted);
    font-size: var(--fs-lg);
  }

  .album-link:hover {
    background: var(--surface-hover);
    color: var(--text);
  }

  @media (min-width: 900px) {
    .now {
      max-width: 620px;
      padding-top: calc(24px + var(--safe-top));
    }

    .title {
      font-size: 26px;
    }

    .extras {
      flex-direction: row;
    }

    .extras .album-link {
      flex: 1;
    }
  }
</style>
