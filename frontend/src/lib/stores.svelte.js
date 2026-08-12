/** アプリ全体で共有する状態（再生状態・スキャン進捗・設定・通知）。 */

import { api } from './api.js';

/* ------------------------------------------------------------------ 再生 */

class PlayerStore {
  state = $state({ connected: false, state: 'stop', volume: 60, repeat: 'off', order: 'normal' });
  /** WebSocket が切れている間は false。UI に接続状態を出す。 */
  live = $state(false);
  /** シーク中はサーバーからの elapsed を無視する */
  seeking = $state(false);
  localElapsed = $state(0);

  #socket = null;
  #reconnectTimer = null;
  #tickTimer = null;
  #backoff = 1000;

  track = $derived(this.state.track ?? null);
  playing = $derived(this.state.state === 'play');
  elapsed = $derived(this.seeking ? this.localElapsed : this.state.elapsed || 0);
  duration = $derived(this.state.duration || this.track?.duration || 0);

  connect() {
    this.#openSocket();
    // 再生位置は 1 秒ごとにローカルで進める（サーバーからの push は状態変化時のみ）
    this.#tickTimer = setInterval(() => {
      if (this.playing && !this.seeking && this.state.elapsed != null) {
        this.state = { ...this.state, elapsed: Math.min(this.state.elapsed + 1, this.duration) };
      }
    }, 1000);
  }

  #openSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${protocol}://${window.location.host}/api/ws`);
    this.#socket = socket;

    socket.onopen = () => {
      this.live = true;
      this.#backoff = 1000;
    };
    socket.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      if (message.type === 'player') {
        this.state = { ...this.state, ...message.payload };
      } else if (message.type === 'scan') {
        scan.update(message.payload);
      } else if (message.type === 'settings') {
        settings.values = { ...settings.values, ...message.payload.values };
      }
    };
    socket.onclose = () => {
      this.live = false;
      this.#scheduleReconnect();
    };
    socket.onerror = () => socket.close();
  }

  #scheduleReconnect() {
    clearTimeout(this.#reconnectTimer);
    this.#reconnectTimer = setTimeout(() => {
      this.#backoff = Math.min(this.#backoff * 2, 15000);
      this.#openSocket();
    }, this.#backoff);
  }

  /** サーバーから返った最新状態を反映する（REST 操作の後）。 */
  apply(state) {
    if (state) this.state = { ...this.state, ...state };
  }

  async command(fn) {
    try {
      this.apply(await fn());
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }
}

export const player = new PlayerStore();

/* ------------------------------------------------------------ スキャン進捗 */

class ScanStore {
  status = $state('idle');
  phase = $state('');
  processed = $state(0);
  total = $state(0);
  error = $state(null);
  summary = $state(null);

  running = $derived(this.status === 'running');
  percent = $derived(this.total > 0 ? Math.round((this.processed / this.total) * 100) : 0);

  update(payload) {
    if (!payload) return;
    this.status = payload.running ? 'running' : (payload.status ?? this.status);
    this.phase = payload.phase ?? '';
    this.processed = payload.processed ?? 0;
    this.total = payload.total ?? 0;
    this.error = payload.error ?? null;
    if (payload.added !== undefined && !payload.running) {
      this.summary = payload;
    }
  }

  async refresh() {
    try {
      this.update(await api.scanState());
    } catch {
      /* 起動直後などは無視してよい */
    }
  }

  async start(full = false) {
    try {
      this.update(await api.startScan(full));
      toast.info(full ? '完全な再スキャンを開始しました' : 'ライブラリのスキャンを開始しました');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async startLoudness(remeasure = false) {
    try {
      this.update(await api.startLoudnessScan(remeasure));
      toast.info('ラウドネスの計測を開始しました（中断しても続きから再開できます）');
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }
}

export const scan = new ScanStore();

/* ------------------------------------------------------------------ 設定 */

class SettingsStore {
  values = $state({});
  specs = $state([]);
  loaded = $state(false);

  async load() {
    try {
      const payload = await api.settings();
      this.values = payload.values;
      this.specs = payload.specs;
      this.loaded = true;
      applyTheme(this.values.theme);
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async set(key, value) {
    const previous = this.values[key];
    this.values = { ...this.values, [key]: value };
    try {
      const payload = await api.updateSettings({ [key]: value });
      this.values = payload.values;
      if (key === 'theme') applyTheme(payload.values.theme);
    } catch (error) {
      this.values = { ...this.values, [key]: previous };
      toast.error(error.detail || String(error));
    }
  }
}

export const settings = new SettingsStore();

export function applyTheme(theme) {
  document.documentElement.dataset.theme = theme === 'light' ? 'light' : 'dark';
}

/* ------------------------------------------------------------------ 通知 */

class ToastStore {
  items = $state([]);
  #seq = 0;

  #push(kind, message, timeout = 3200) {
    const id = ++this.#seq;
    this.items = [...this.items, { id, kind, message }];
    setTimeout(() => this.dismiss(id), timeout);
  }

  info(message) {
    this.#push('info', message);
  }

  error(message) {
    this.#push('error', message, 5000);
  }

  dismiss(id) {
    this.items = this.items.filter((item) => item.id !== id);
  }
}

export const toast = new ToastStore();

/* ------------------------------------------------- ライブラリ画面の表示状態 */

/**
 * ライブラリ画面の絞り込み・並び順・スクロール位置。
 *
 * コンポーネントは画面遷移のたびに作り直されるため、状態をここに置いて
 * 保持する。アルバムを開いて戻ったときに「は行で絞り込んだ状態の、
 * 見ていた位置」へ戻るようにするのが目的。
 */
class LibraryViewState {
  view = $state('albums');
  sort = $state('album');
  layout = $state('grid');
  initial = $state(null);
  initialGroup = $state(null);
  /** 一覧のスクロール位置（ピクセル） */
  scrollY = $state(0);
  /** 設定から初期値を読み込んだかどうか */
  initialised = $state(false);

  clearFilter() {
    this.initial = null;
    this.initialGroup = null;
    this.scrollY = 0;
  }
}

export const libraryView = new LibraryViewState();
