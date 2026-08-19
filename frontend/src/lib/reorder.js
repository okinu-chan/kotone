/**
 * ドラッグでの並び替え（マウス・タッチ共通）。
 *
 * Pointer Events を使う。HTML5 の draggable はタッチ操作で動かないため、
 * スマホとPCで別々の実装を持つことになる。Pointer Events なら 1 つで済む。
 *
 * つまみ（ハンドル）からしかドラッグを始められないようにしてある。行の
 * どこからでも掴めるようにすると、一覧を指でスクロールできなくなる。
 * ハンドル側の CSS に ``touch-action: none`` が要る（指の動きをブラウザの
 * スクロールに取られないようにするため）。
 *
 * 移動先はポインタの真下にある行から求める。行の高さがまちまちでも
 * 破綻せず、掴んだ行がその場で動くので、指を離す前に結果が見える。
 */

/**
 * @param {PointerEvent} event  ハンドルの pointerdown
 * @param {object}   options
 * @param {string}   options.itemSelector  行を表すセレクタ（例: '.tracks > li'）
 * @param {number}   options.from          掴んだ行の番号
 * @param {(from: number, to: number) => void} options.onMove  1 つ動くたびに呼ぶ
 * @param {() => void} [options.onEnd]  指を離したときに呼ぶ（保存はここで）。
 *   位置はすでに onMove で動かしてあるので、ここで動かし直してはいけない。
 */
export function beginDrag(event, { itemSelector, from, onMove, onEnd }) {
  const handle = event.currentTarget;
  const list = handle.closest('[data-reorder-list]');
  if (!list) return;

  event.preventDefault();
  // 指が行の外へ出てもイベントを受け取り続ける。掴んでいないポインタ id を
  // 渡すと例外になるので、捕まえられなくても続行する
  try {
    handle.setPointerCapture?.(event.pointerId);
  } catch {
    /* 捕捉できなくても window 側のリスナーで拾える */
  }
  list.classList.add('reordering');

  let current = from;

  const rows = () => [...list.querySelectorAll(itemSelector)];

  function onPointerMove(moveEvent) {
    const under = document.elementFromPoint(moveEvent.clientX, moveEvent.clientY);
    const row = under?.closest?.(itemSelector);
    if (!row || !list.contains(row)) return;
    const to = rows().indexOf(row);
    if (to < 0 || to === current) return;
    onMove(current, to);
    current = to;
  }

  function finish() {
    handle.releasePointerCapture?.(event.pointerId);
    list.classList.remove('reordering');
    window.removeEventListener('pointermove', onPointerMove);
    window.removeEventListener('pointerup', finish);
    window.removeEventListener('pointercancel', finish);
    if (current !== from) onEnd?.(from, current);
  }

  window.addEventListener('pointermove', onPointerMove);
  window.addEventListener('pointerup', finish);
  window.addEventListener('pointercancel', finish);
}

/** 配列の要素を from から to へ動かした新しい配列を返す。 */
export function moved(list, from, to) {
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}
