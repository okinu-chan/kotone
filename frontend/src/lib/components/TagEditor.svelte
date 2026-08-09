<script>
  /**
   * 楽曲タグの編集（要件 3.2 / 3.6）。
   *
   * ・自動生成された読み仮名の手動修正
   * ・アーティスト紐付けの手動修正（自動分割の誤り対策）
   *
   * 編集内容はタグ DB にだけ保存し、元の音源ファイルは書き換えない。
   * 保存すると該当項目がロックされ、再スキャンで自動生成値に戻らなくなる。
   */
  import Modal from './Modal.svelte';
  import { api } from '../api.js';
  import { toast } from '../stores.svelte.js';

  let { open = $bindable(false), track = null, onsaved } = $props();

  let title = $state('');
  let reading = $state('');
  let artistsText = $state('');
  let genresText = $state('');
  let discNo = $state(1);
  let trackNo = $state(0);
  let year = $state('');
  let busy = $state(false);

  $effect(() => {
    if (!open || !track) return;
    title = track.title ?? '';
    reading = track.reading ?? '';
    artistsText = (track.artists ?? []).map((a) => a.name).join('\n');
    genresText = (track.genres ?? []).map((g) => g.name).join('\n');
    discNo = track.disc_no ?? 1;
    trackNo = track.track_no ?? 0;
    year = track.year ?? '';
  });

  async function regenerateReading() {
    try {
      const payload = await api.suggestReading(title);
      reading = payload.reading;
      if (!payload.analyzer_available) {
        toast.error('形態素解析器が使えないため、簡易的な読みしか生成できません');
      }
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function save() {
    if (!track) return;
    busy = true;
    try {
      const updated = await api.updateTrack(track.id, {
        title,
        reading,
        disc_no: Number(discNo),
        track_no: Number(trackNo),
        year: year === '' ? null : Number(year),
        artist_names: artistsText
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean),
        genre_names: genresText
          .split('\n')
          .map((s) => s.trim())
          .filter(Boolean),
      });
      toast.info('保存しました');
      onsaved?.(updated);
      open = false;
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      busy = false;
    }
  }

  async function unlock() {
    if (!track) return;
    busy = true;
    try {
      await api.unlockTrack(track.id);
      toast.info('手動修正を解除しました。次回のスキャンで自動生成値に戻ります。');
      open = false;
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      busy = false;
    }
  }
</script>

<Modal bind:open title="タグを編集">
  <div class="fields">
    <label>
      <span>曲名</span>
      <input type="text" bind:value={title} />
    </label>

    <label>
      <span>
        読み仮名
        <button class="link" onclick={regenerateReading} type="button">自動生成に戻す</button>
      </span>
      <input type="text" bind:value={reading} placeholder="カタカナで入力" />
      <small class="dim">五十音順のソートとイニシャルサーチに使われます。</small>
    </label>

    <label>
      <span>アーティスト（1 行に 1 名）</span>
      <textarea rows="3" bind:value={artistsText}></textarea>
      <small class="dim">
        自動分割が誤っている場合はここで修正します。保存すると再スキャンでも維持されます。
      </small>
    </label>

    <label>
      <span>ジャンル（1 行に 1 つ）</span>
      <textarea rows="2" bind:value={genresText}></textarea>
    </label>

    <div class="row">
      <label>
        <span>ディスク</span>
        <input type="number" min="1" bind:value={discNo} />
      </label>
      <label>
        <span>トラック</span>
        <input type="number" min="0" bind:value={trackNo} />
      </label>
      <label>
        <span>年</span>
        <input type="number" bind:value={year} />
      </label>
    </div>

    <p class="note dim">
      編集内容はタグ DB にのみ保存され、元の音源ファイルは書き換えません。
    </p>
  </div>

  {#snippet footer()}
    <button class="ghost" onclick={unlock} disabled={busy}>手動修正を解除</button>
    <button class="primary" onclick={save} disabled={busy}>保存</button>
  {/snippet}
</Modal>

<style>
  .fields {
    display: flex;
    flex-direction: column;
    gap: 16px;
  }

  label {
    display: flex;
    flex-direction: column;
    gap: 6px;
    font-size: 13px;
  }

  label > span {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    color: var(--text-muted);
  }

  textarea {
    resize: vertical;
    min-height: 68px;
    line-height: 1.5;
  }

  small {
    font-size: 11.5px;
    line-height: 1.4;
  }

  .row {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: 10px;
  }

  .note {
    margin: 0;
    font-size: 11.5px;
  }

  .link {
    font-size: 12px;
    color: var(--accent);
  }

  .primary,
  .ghost {
    min-height: var(--tap);
    padding: 0 18px;
    border-radius: var(--radius);
    font-size: 14px;
  }

  .primary {
    background: var(--accent);
    color: #0d0f13;
  }

  .ghost {
    border: 1px solid var(--border);
    color: var(--text-muted);
  }

  .primary:disabled,
  .ghost:disabled {
    opacity: 0.45;
    cursor: default;
  }
</style>
