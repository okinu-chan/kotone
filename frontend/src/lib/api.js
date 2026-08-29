/** バックエンド API の薄いラッパー。 */

const BASE = '/api';

class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

/** 送り直すまでの間（接続を張り直させるため、ごく短く空ける） */
const RETRY_DELAY_MS = 150;

/**
 * 応答を受け取る前に落ちたか。
 *
 * ブラウザは一度開いた接続を次の操作でも使い回す。サーバーがその接続を
 * 閉じるのと同時に送ってしまうと、要求はサーバーへ届かないまま落ちる。
 * fetch はこれを TypeError として投げる（Safari の "Load failed"）。
 * 応答が返ってからの失敗は ApiError なので、ここには入らない。
 */
const isNetworkError = (error) => error instanceof TypeError;

/**
 * ``retry`` を付けた要求は、届かずに落ちたときだけ 1 度やり直す。
 *
 * 届いていないので送り直して構わない。ただし二度効くと困るもの
 * （曲送り・再生の切り替え・プレイリストへの追加や削除など）には付けない。
 * 「この状態にする」という要求だけを対象にしている。
 */
async function request(path, options = {}) {
  const { retry = false, ...init } = options;
  try {
    return await send(path, init);
  } catch (error) {
    if (!retry || !isNetworkError(error)) throw error;
    await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY_MS));
    return send(path, init);
  }
}

async function send(path, options) {
  const response = await fetch(`${BASE}${path}`, {
    headers: options.body ? { 'Content-Type': 'application/json' } : undefined,
    ...options,
  });

  if (response.status === 204) return null;

  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }

  if (!response.ok) {
    const detail =
      payload && typeof payload === 'object' && payload.detail ? payload.detail : String(payload);
    throw new ApiError(response.status, detail);
  }
  return payload;
}

// 読み取りは何度送っても同じなので、常にやり直してよい
const get = (path, params) => {
  const query = params ? buildQuery(params) : '';
  return request(`${path}${query}`, { retry: true });
};
const post = (path, body, retry = false) =>
  request(path, {
    method: 'POST',
    retry,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
/** 何度送っても同じ結果になる POST（届かなかったときに送り直す） */
const setPost = (path, body) => post(path, body, true);
const patch = (path, body) => request(path, { method: 'PATCH', retry: true, body: JSON.stringify(body) });
const del = (path) => request(path, { method: 'DELETE' });

function buildQuery(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `?${query}` : '';
}

export const api = {
  ApiError,

  // --- ライブラリ ---------------------------------------------------
  stats: () => get('/library/stats'),
  albums: (params) => get('/library/albums', params),
  album: (id) => get(`/library/albums/${id}`),
  artists: (params) => get('/library/artists', params),
  artist: (id) => get(`/library/artists/${id}`),
  tracks: (params) => get('/library/tracks', params),
  genres: () => get('/library/genres'),
  formats: () => get('/library/formats'),
  years: () => get('/library/years'),
  initials: (entity, group, initialBy) =>
    get(`/library/initials/${entity}`, { group, initial_by: initialBy }),
  search: (q, limit = 20) => get('/library/search', { q, limit }),

  // --- 出力先・Bluetooth ----------------------------------------------
  outputs: () => get('/outputs'),
  selectOutput: (id) => setPost('/outputs/select', { id }),
  bluetooth: () => get('/bluetooth'),
  btPower: (powered) => setPost('/bluetooth/power', { powered }),
  btScan: (seconds = 12) => post('/bluetooth/scan', { seconds }),
  btPair: (address) => post('/bluetooth/pair', { address }),
  btConnect: (address) => setPost('/bluetooth/connect', { address }),
  btDisconnect: (address) => setPost('/bluetooth/disconnect', { address }),
  btForget: (address) => setPost('/bluetooth/forget', { address }),

  // --- 再生 -----------------------------------------------------------
  // 曲送り・曲戻し・再生の切り替えは、二度効くと 2 曲進んでしまうので
  // 送り直さない（それ以外は「この状態にする」なので送り直してよい）
  player: () => get('/player'),
  play: () => setPost('/player/play'),
  pause: () => setPost('/player/pause'),
  toggle: () => post('/player/toggle'),
  next: () => post('/player/next'),
  previous: () => post('/player/previous'),
  seek: (seconds) => setPost('/player/seek', { seconds }),
  setVolume: (volume) => setPost('/player/volume', { volume }),
  playScope: (kind, id, startTrackId) =>
    setPost('/player/scope', { kind, id: id === undefined ? null : String(id), start_track_id: startTrackId ?? null }),
  playTracks: (trackIds, startIndex = 0) =>
    setPost('/player/tracks', { track_ids: trackIds, start_index: startIndex }),
  setRepeat: (mode) => setPost(`/player/repeat/${mode}`),
  setOrder: (mode) => setPost(`/player/order/${mode}`),
  queue: () => get('/player/queue'),

  // --- プレイリスト ---------------------------------------------------
  playlists: () => get('/playlists'),
  playlist: (id) => get(`/playlists/${id}`),
  // 並び順は「この順にする」なので送り直してよい。追加・削除・移動は
  // 二度効くと中身が変わるので送り直さない
  reorderPlaylists: (ids) => setPost('/playlists/reorder', { playlist_ids: ids }),
  createPlaylist: (name, trackIds = []) => post('/playlists', { name, track_ids: trackIds }),
  renamePlaylist: (id, name) => patch(`/playlists/${id}`, { name }),
  deletePlaylist: (id) => del(`/playlists/${id}`),
  addToPlaylist: (id, body) => post(`/playlists/${id}/tracks`, body),
  removeFromPlaylist: (id, positions) => post(`/playlists/${id}/remove`, { positions }),
  movePlaylistTrack: (id, from, to) =>
    post(`/playlists/${id}/move`, { from_position: from, to_position: to }),
  reorderPlaylist: (id, trackIds) => setPost(`/playlists/${id}/reorder`, { track_ids: trackIds }),
  exportPlaylistUrl: (id) => `${BASE}/playlists/${id}/export`,
  importPlaylist: async (file, name) => {
    const form = new FormData();
    form.append('file', file);
    const query = name ? `?name=${encodeURIComponent(name)}` : '';
    return request(`/playlists/import${query}`, { method: 'POST', body: form });
  },

  // --- 編集 -----------------------------------------------------------
  updateTrack: (id, body) => patch(`/edit/tracks/${id}`, body),
  updateAlbum: (id, body) => patch(`/edit/albums/${id}`, body),
  updateArtist: (id, body) => patch(`/edit/artists/${id}`, body),
  mergeArtists: (sourceId, targetId) =>
    post('/edit/artists/merge', { source_id: sourceId, target_id: targetId }),
  suggestReading: (text) => post('/edit/reading/suggest', { text }),
  unlockTrack: (id) => post(`/edit/tracks/${id}/unlock`),

  // --- 設定・システム -------------------------------------------------
  settings: () => get('/settings'),
  updateSettings: (values) => patch('/settings', { values }),
  system: () => get('/system'),
  scanState: () => get('/scan'),
  startScan: (full = false) => post('/scan', { full }),
  startLoudnessScan: (remeasure = false) => post('/scan/loudness', { remeasure }),
  loudnessProgress: (remeasure = false) => get('/scan/loudness', { remeasure }),
  calibrateLoudness: (sampleSize = 6) => post(`/scan/loudness/calibrate?sample_size=${sampleSize}`),
  analyzeArtists: () => get('/scan/analyze-artists'),
};

/** アルバムアートの URL。tier は small / medium / large。 */
export function artworkUrl(album, tier = 'small') {
  const artwork = album?.artwork;
  if (!artwork) return null;
  return `${artwork.url}?tier=${tier}&v=${artwork.hash}`;
}
