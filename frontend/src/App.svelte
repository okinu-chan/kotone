<script>
  import AlbumView from './lib/components/AlbumView.svelte';
  import ArtistView from './lib/components/ArtistView.svelte';
  import LibraryView from './lib/components/LibraryView.svelte';
  import MiniPlayer from './lib/components/MiniPlayer.svelte';
  import NowPlaying from './lib/components/NowPlaying.svelte';
  import PlaylistView from './lib/components/PlaylistView.svelte';
  import PlaylistsView from './lib/components/PlaylistsView.svelte';
  import QueueView from './lib/components/QueueView.svelte';
  import ScanBanner from './lib/components/ScanBanner.svelte';
  import ScopeView from './lib/components/ScopeView.svelte';
  import SearchView from './lib/components/SearchView.svelte';
  import SettingsView from './lib/components/SettingsView.svelte';
  import StorageBanner from './lib/components/StorageBanner.svelte';
  import TabBar from './lib/components/TabBar.svelte';
  import Toast from './lib/components/Toast.svelte';
  import { router } from './lib/router.svelte.js';
  import { player, scan, settings } from './lib/stores.svelte.js';

  player.connect();
  settings.load();
  scan.refresh();

  const route = $derived(router.current);
  const onNowPlaying = $derived(route.name === 'now');
</script>

<div class="app" class:with-mini={!onNowPlaying}>
  <StorageBanner />
  <ScanBanner />

  <main>
    {#if route.name === 'now'}
      <NowPlaying />
    {:else if route.name === 'library'}
      <LibraryView />
    {:else if route.name === 'album'}
      {#key route.params.id}
        <AlbumView id={route.params.id} />
      {/key}
    {:else if route.name === 'artist'}
      {#key route.params.id}
        <ArtistView id={route.params.id} />
      {/key}
    {:else if route.name === 'genre'}
      {#key route.params.id}
        <ScopeView kind="genre" value={route.params.id} />
      {/key}
    {:else if route.name === 'year'}
      {#key route.params.year}
        <ScopeView kind="year" value={route.params.year} />
      {/key}
    {:else if route.name === 'playlists'}
      <PlaylistsView />
    {:else if route.name === 'playlist'}
      {#key route.params.id}
        <PlaylistView id={route.params.id} />
      {/key}
    {:else if route.name === 'queue'}
      <QueueView />
    {:else if route.name === 'search'}
      <SearchView />
    {:else if route.name === 'settings'}
      <SettingsView />
    {/if}
  </main>

  {#if !onNowPlaying}
    <MiniPlayer />
  {/if}
  <TabBar />
  <Toast />

  {#if !player.live}
    <div class="disconnected" role="status">サーバーと再接続中…</div>
  {/if}
</div>

<style>
  .app {
    min-height: 100%;
    /* 再生中画面の背景（z-index: -1）をこの中の最背面に留め、body の背景の下に潜らせない */
    isolation: isolate;
    padding-bottom: calc(var(--tabbar-height) + var(--safe-bottom));
  }

  .app.with-mini {
    padding-bottom: calc(
      var(--tabbar-height) + var(--miniplayer-height) + var(--safe-bottom)
    );
  }

  main {
    max-width: 1100px;
    margin: 0 auto;
  }

  .disconnected {
    position: fixed;
    top: calc(8px + var(--safe-top));
    left: 50%;
    transform: translateX(-50%);
    z-index: 90;
    padding: 6px 14px;
    border-radius: 999px;
    background: var(--bg-elevated);
    border: 1px solid var(--border);
    color: var(--text-muted);
    font-size: 12px;
  }
</style>
