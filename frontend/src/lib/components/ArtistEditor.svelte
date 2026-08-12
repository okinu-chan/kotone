<script>
  /**
   * アーティストの読み仮名の編集（要件 3.2）。
   *
   * 人名の読みは形態素解析では確定できない（「中島愛」は ナカジマアイ とも
   * ナカジマメグミ とも読める）。五十音順の並びとイニシャルサーチに直結するため、
   * ユーザーが直せるようにする。
   *
   * 編集はタグ DB に対してのみ行い、元の音源ファイルは書き換えない。
   * 保存すると読みがロックされ、再スキャンで自動生成値に戻らない。
   */
  import Modal from './Modal.svelte';
  import { api } from '../api.js';
  import { toast } from '../stores.svelte.js';

  let { open = $bindable(false), artist = null, onsaved } = $props();

  let name = $state('');
  let reading = $state('');
  let busy = $state(false);

  $effect(() => {
    if (!open || !artist) return;
    name = artist.name ?? '';
    reading = artist.reading ?? '';
  });

  async function regenerate() {
    try {
      const payload = await api.suggestReading(name);
      reading = payload.reading;
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }

  async function save() {
    if (!artist) return;
    busy = true;
    try {
      const updated = await api.updateArtist(artist.id, { name, reading });
      toast.info('保存しました');
      onsaved?.(updated);
      open = false;
    } catch (error) {
      toast.error(error.detail || String(error));
    } finally {
      busy = false;
    }
  }
</script>

<Modal bind:open title="アーティスト情報を編集">
  <div class="fields">
    <label>
      <span>アーティスト名</span>
      <input type="text" bind:value={name} />
    </label>

    <label>
      <span>
        読み仮名
        <button class="link" type="button" onclick={regenerate}>自動生成に戻す</button>
      </span>
      <input type="text" bind:value={reading} placeholder="カタカナで入力" />
      <small class="dim">
        五十音順の並び替えとイニシャルサーチに使われます。
        人名は自動生成が誤ることがあるため（例: 中島愛 → ナカジマアイ）、
        正しい読みに直してください。
      </small>
    </label>

    <p class="note dim">編集内容はタグ DB にのみ保存され、元の音源ファイルは変更しません。</p>
  </div>

  {#snippet footer()}
    <button class="ghost" onclick={() => (open = false)} disabled={busy}>キャンセル</button>
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

  small {
    font-size: 11.5px;
    line-height: 1.45;
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
