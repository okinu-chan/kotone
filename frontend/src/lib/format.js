/** 表示用の書式ヘルパー。UI は全て日本語（要件 4.5）。 */

export function formatTime(seconds) {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

export function formatDuration(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return '';
  const total = Math.floor(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.round((total % 3600) / 60);
  if (h > 0) return `${h} 時間 ${m} 分`;
  return `${m} 分`;
}

/** 44.1kHz / 16bit のような音質表記。DSD は別表記にする。 */
export function formatQuality(track) {
  if (!track) return '';
  if (track.is_dsd) {
    const rate = track.samplerate ? Math.round(track.samplerate / 44100) : null;
    return rate ? `DSD${rate * 64}` : 'DSD';
  }
  const parts = [];
  if (track.samplerate) parts.push(`${(track.samplerate / 1000).toFixed(1)}kHz`);
  if (track.bit_depth) parts.push(`${track.bit_depth}bit`);
  return parts.join(' / ');
}

export function formatCodec(track) {
  if (!track?.codec) return '';
  const names = {
    flac: 'FLAC',
    mp3: 'MP3',
    mp4: 'AAC',
    oggvorbis: 'Ogg Vorbis',
    oggopus: 'Opus',
    dsf: 'DSF',
    dsdiff: 'DFF',
    wave: 'WAV',
    aiff: 'AIFF',
    wavpack: 'WavPack',
    monkeysaudio: 'APE',
  };
  return names[track.codec] || track.codec.toUpperCase();
}

export function formatCount(count, unit = '曲') {
  return `${count} ${unit}`;
}

export function formatBytes(bytes) {
  if (!Number.isFinite(bytes)) return '';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let value = bytes;
  let index = 0;
  while (value >= 1024 && index < units.length - 1) {
    value /= 1024;
    index += 1;
  }
  return `${value.toFixed(index === 0 ? 0 : 1)} ${units[index]}`;
}

export const REPEAT_LABELS = {
  off: 'リピートなし',
  one: '1 曲リピート',
  range: '範囲内リピート',
};

export const ORDER_LABELS = {
  normal: '通常再生',
  shuffle: 'シャッフル（重複なし）',
  random: 'ランダム（重複あり）',
};

export const SORT_LABELS = {
  artist: 'アーティスト',
  album: 'アルバム',
  genre: 'ジャンル',
  year: 'リリース年',
  added: '追加日',
  title: '曲名',
};
