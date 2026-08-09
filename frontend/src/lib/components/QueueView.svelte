<script>
  /** 現在のキュー（MPD の再生待ち行列）。 */
  import PageHeader from './PageHeader.svelte';
  import TrackList from './TrackList.svelte';
  import { api } from '../api.js';
  import { player, toast } from '../stores.svelte.js';

  let items = $state([]);
  let loading = $state(true);

  $effect(() => {
    // 曲が変わったら読み直す
    void player.state.raw_file;
    void player.state.queue_length;
    loading = true;
    api
      .queue()
      .then((payload) => {
        items = payload.items;
      })
      .catch((error) => toast.error(error.detail || String(error)))
      .finally(() => {
        loading = false;
      });
  });

  const tracks = $derived(
    items.filter((item) => item.track).map((item) => ({ ...item.track, _position: item.position })),
  );

  async function playAt(track) {
    const entry = items.find((item) => item.track?.id === track.id);
    if (!entry) return;
    try {
      player.apply(await api.playTracks([track.id]));
    } catch (error) {
      toast.error(error.detail || String(error));
    }
  }
</script>

<PageHeader title="再生キュー" subtitle={`${items.length} 曲`} />

{#if loading}
  <div class="loading"><div class="spinner"></div></div>
{:else}
  <TrackList {tracks} showArtwork onplay={playAt} />
{/if}

<style>
  .loading {
    display: grid;
    place-items: center;
    padding: 60px;
  }
</style>
