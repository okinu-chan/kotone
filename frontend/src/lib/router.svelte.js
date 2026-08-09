/**
 * History API ベースの簡易ルーター。
 *
 * ブラウザ標準の「戻る」がそのまま遷移履歴を遡る操作になる（要件 3.5 / 3.6）。
 * ダイレクトサーチで 曲 → アーティスト → 別の曲 … と辿った履歴も、
 * これで自然に戻れる。
 */

const ROUTES = [
  { name: 'now', pattern: /^\/$/ },
  { name: 'library', pattern: /^\/library$/ },
  { name: 'album', pattern: /^\/albums\/(\d+)$/, keys: ['id'] },
  { name: 'artist', pattern: /^\/artists\/(\d+)$/, keys: ['id'] },
  { name: 'genre', pattern: /^\/genres\/(\d+)$/, keys: ['id'] },
  { name: 'year', pattern: /^\/years\/(\d+)$/, keys: ['year'] },
  { name: 'playlists', pattern: /^\/playlists$/ },
  { name: 'playlist', pattern: /^\/playlists\/(\d+)$/, keys: ['id'] },
  { name: 'queue', pattern: /^\/queue$/ },
  { name: 'search', pattern: /^\/search$/ },
  { name: 'settings', pattern: /^\/settings$/ },
];

function parse(pathname) {
  for (const route of ROUTES) {
    const match = route.pattern.exec(pathname);
    if (!match) continue;
    const params = {};
    (route.keys || []).forEach((key, index) => {
      params[key] = match[index + 1];
    });
    return { name: route.name, params };
  }
  return { name: 'now', params: {} };
}

class Router {
  path = $state(window.location.pathname);
  /** 遷移の深さ。1 以上なら「戻る」ボタンを出せる。 */
  depth = $state(0);

  current = $derived(parse(this.path));

  constructor() {
    window.addEventListener('popstate', (event) => {
      this.path = window.location.pathname;
      this.depth = Math.max(0, event.state?.depth ?? 0);
    });
    // 初期エントリにも depth を持たせておく
    window.history.replaceState({ depth: 0 }, '', window.location.pathname);
  }

  navigate(to, { replace = false } = {}) {
    if (to === this.path && !replace) return;
    const depth = replace ? this.depth : this.depth + 1;
    if (replace) {
      window.history.replaceState({ depth }, '', to);
    } else {
      window.history.pushState({ depth }, '', to);
    }
    this.path = to;
    this.depth = depth;
    window.scrollTo?.(0, 0);
  }

  /** タブバーからの遷移。履歴を積み上げずにルートを切り替える。 */
  switchTab(to) {
    this.navigate(to, { replace: this.depth === 0 });
  }

  back() {
    if (this.depth > 0) {
      window.history.back();
    } else {
      this.navigate('/', { replace: true });
    }
  }
}

export const router = new Router();

export const go = (to) => router.navigate(to);
