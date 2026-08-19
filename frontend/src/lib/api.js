/** バックエンド API の薄いラッパー。 */

const BASE = '/api';

class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

async function request(path, options = {}) {
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

const get = (path, params) => {
  const query = params ? buildQuery(params) : '';
  return request(`${path}${query}`);
};
const post = (path, body) =>
  request(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) });
const patch = (path, body) => request(path, { method: 'PATCH', body: JSON.stringify(body) });
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

  // --- 再生 -----------------------------------------------------------
  player: () => get('/player'),
  play: () => post('/player/play'),
  pause: () => post('/player/pause'),
  toggle: () => post('/player/toggle'),
  next: () => post('/player/next'),
  previous: () => post('/player/previous'),
  seek: (seconds) => post('/player/seek', { seconds }),
  setVolume: (volume) => post('/player/volume', { volume }),
  playScope: (kind, id, startTrackId) =>
    post('/player/scope', { kind, id: id === undefined ? null : String(id), start_track_id: startTrackId ?? null }),
  playTracks: (trackIds, startIndex = 0) =>
    post('/player/tracks', { track_ids: trackIds, start_index: startIndex }),
  setRepeat: (mode) => post(`/player/repeat/${mode}`),
  setOrder: (mode) => post(`/player/order/${mode}`),
  queue: () => get('/player/queue'),

  // --- プレイリスト ---------------------------------------------------
  playlists: () => get('/playlists'),
  playlist: (id) => get(`/playlists/${id}`),
  reorderPlaylists: (ids) => post('/playlists/reorder', { playlist_ids: ids }),
  createPlaylist: (name, trackIds = []) => post('/playlists', { name, track_ids: trackIds }),
  renamePlaylist: (id, name) => patch(`/playlists/${id}`, { name }),
  deletePlaylist: (id) => del(`/playlists/${id}`),
  addToPlaylist: (id, body) => post(`/playlists/${id}/tracks`, body),
  removeFromPlaylist: (id, positions) => post(`/playlists/${id}/remove`, { positions }),
  movePlaylistTrack: (id, from, to) =>
    post(`/playlists/${id}/move`, { from_position: from, to_position: to }),
  reorderPlaylist: (id, trackIds) => post(`/playlists/${id}/reorder`, { track_ids: trackIds }),
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
