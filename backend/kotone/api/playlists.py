"""プレイリスト API（要件 3.4）。

編集操作は「操作のたびに自動保存」なので、明示的な保存エンドポイントは無い。
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from kotone.api.deps import Ctx
from kotone.mpdclient import MPDUnavailable
from kotone.playlists import PlaylistError

router = APIRouter(prefix="/playlists", tags=["playlists"])


class CreateRequest(BaseModel):
    name: str
    track_ids: list[int] = Field(default_factory=list)


class RenameRequest(BaseModel):
    name: str


class ReorderPlaylistsRequest(BaseModel):
    playlist_ids: list[int]


class AddTracksRequest(BaseModel):
    track_ids: list[int] = Field(default_factory=list, min_length=0)
    #: アルバム単位でまとめて追加する場合
    album_id: int | None = None
    artist_id: int | None = None
    position: int | None = None


class RemoveRequest(BaseModel):
    positions: list[int] = Field(min_length=1)


class MoveRequest(BaseModel):
    from_position: int = Field(ge=0)
    to_position: int = Field(ge=0)


class ReorderRequest(BaseModel):
    track_ids: list[int]


@router.get("")
def list_playlists(ctx: Ctx) -> dict[str, Any]:
    items = ctx.repo.list_playlists()
    return {"items": items, "total": len(items)}


@router.post("/reorder")
def reorder_playlists(ctx: Ctx, body: ReorderPlaylistsRequest) -> dict[str, Any]:
    """プレイリスト自体の並び順を入れ替える（要件 3.4）。"""
    ctx.playlists.reorder_playlists(body.playlist_ids)
    items = ctx.repo.list_playlists()
    return {"items": items, "total": len(items)}


@router.post("", status_code=201)
def create(ctx: Ctx, body: CreateRequest) -> dict[str, Any]:
    try:
        playlist_id = ctx.playlists.create(body.name, body.track_ids)
    except PlaylistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ctx.repo.get_playlist(playlist_id) or {}


@router.get("/{playlist_id}")
def get_playlist(ctx: Ctx, playlist_id: int) -> dict[str, Any]:
    playlist = ctx.repo.get_playlist(playlist_id)
    if playlist is None:
        raise HTTPException(status_code=404, detail="プレイリストが見つかりません")
    return playlist


@router.patch("/{playlist_id}")
def rename(ctx: Ctx, playlist_id: int, body: RenameRequest) -> dict[str, Any]:
    try:
        ctx.playlists.rename(playlist_id, body.name)
    except PlaylistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ctx.repo.get_playlist(playlist_id) or {}


@router.delete("/{playlist_id}", status_code=204)
def delete(ctx: Ctx, playlist_id: int) -> None:
    ctx.playlists.delete(playlist_id)


async def _sync_player(ctx: Ctx, playlist_id: int) -> None:
    """このプレイリストを再生中なら、MPD のキューを新しい中身に合わせる。

    合わせないと、消したはずの曲が次に鳴る。
    """
    if not ctx.mpd.connected:
        return
    try:
        if await ctx.player.resync_scope("playlist", playlist_id):
            await ctx.player.broadcast_state()
    except MPDUnavailable:
        pass   # 再生していなくてもプレイリストの編集は成立させる


@router.post("/{playlist_id}/tracks")
async def add_tracks(ctx: Ctx, playlist_id: int, body: AddTracksRequest) -> dict[str, Any]:
    """曲・アルバム・アーティスト単位でまとめて追加する。"""
    track_ids = list(body.track_ids)
    if body.album_id is not None:
        track_ids += [t["id"] for t in ctx.repo.list_tracks(album_id=body.album_id)]
    if body.artist_id is not None:
        track_ids += [t["id"] for t in ctx.repo.list_tracks(artist_id=body.artist_id)]
    try:
        ctx.playlists.add_tracks(playlist_id, track_ids, body.position)
    except PlaylistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _sync_player(ctx, playlist_id)
    return ctx.repo.get_playlist(playlist_id) or {}


@router.post("/{playlist_id}/remove")
async def remove_tracks(ctx: Ctx, playlist_id: int, body: RemoveRequest) -> dict[str, Any]:
    try:
        ctx.playlists.remove_at(playlist_id, body.positions)
    except PlaylistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _sync_player(ctx, playlist_id)
    return ctx.repo.get_playlist(playlist_id) or {}


@router.post("/{playlist_id}/move")
async def move_track(ctx: Ctx, playlist_id: int, body: MoveRequest) -> dict[str, Any]:
    try:
        ctx.playlists.move(playlist_id, body.from_position, body.to_position)
    except PlaylistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _sync_player(ctx, playlist_id)
    return ctx.repo.get_playlist(playlist_id) or {}


@router.post("/{playlist_id}/reorder")
async def reorder(ctx: Ctx, playlist_id: int, body: ReorderRequest) -> dict[str, Any]:
    try:
        ctx.playlists.reorder(playlist_id, body.track_ids)
    except PlaylistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _sync_player(ctx, playlist_id)
    return ctx.repo.get_playlist(playlist_id) or {}


@router.get("/{playlist_id}/export", response_class=PlainTextResponse)
def export_m3u(ctx: Ctx, playlist_id: int) -> PlainTextResponse:
    """拡張 m3u としてエクスポートする（他ソフトへの持ち出し用）。"""
    try:
        content = ctx.playlists.export_m3u(playlist_id, ctx.config.music_dir)
    except PlaylistError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    playlist = ctx.repo.get_playlist(playlist_id) or {}
    # HTTP ヘッダーは latin-1 しか運べないため RFC 5987 形式でエンコードする
    # （日本語のプレイリスト名でも壊れないように）
    filename = quote(f"{playlist.get('name', 'playlist')}.m3u8", safe="")
    return PlainTextResponse(
        content,
        media_type="audio/x-mpegurl; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename=\"playlist.m3u8\"; "
            f"filename*=UTF-8''{filename}"
        },
    )


@router.post("/import", status_code=201)
async def import_m3u(
    ctx: Ctx,
    file: UploadFile,
    name: str | None = Query(default=None),
) -> dict[str, Any]:
    """m3u / m3u8 を読み込んでプレイリストを作る。"""
    raw = await file.read()
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        content = raw.decode("cp932", errors="replace")

    playlist_name = name or (file.filename or "インポート").rsplit(".", 1)[0]
    try:
        result = ctx.playlists.import_m3u(playlist_name, content, ctx.config.music_dir)
    except PlaylistError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    result["playlist"] = ctx.repo.get_playlist(result["playlist_id"])
    return result
