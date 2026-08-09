"""再生制御 API。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from kotone.api.deps import Ctx, MpdCtx, mpd_error
from kotone.mpdclient import MPDUnavailable

router = APIRouter(prefix="/player", tags=["player"])


class PlayScopeRequest(BaseModel):
    kind: Literal["all", "album", "artist", "genre", "year", "playlist"] = "all"
    id: str | None = None
    #: この曲から再生を始める（未指定ならスコープの先頭から）
    start_track_id: int | None = None


class PlayTracksRequest(BaseModel):
    track_ids: list[int] = Field(min_length=1)
    start_index: int = 0


class SeekRequest(BaseModel):
    seconds: float = Field(ge=0)


class VolumeRequest(BaseModel):
    volume: int = Field(ge=0, le=100)


@router.get("")
async def state(ctx: Ctx) -> dict[str, Any]:
    """現在の再生状態。WebSocket が使えない場合のポーリング用でもある。"""
    try:
        return await ctx.player.build_state()
    except MPDUnavailable:
        return {"connected": False, "state": "stop"}


@router.post("/play")
async def play(ctx: MpdCtx) -> dict[str, Any]:
    return await _run(ctx, ctx.player.play())


@router.post("/pause")
async def pause(ctx: MpdCtx) -> dict[str, Any]:
    return await _run(ctx, ctx.player.pause())


@router.post("/toggle")
async def toggle(ctx: MpdCtx) -> dict[str, Any]:
    return await _run(ctx, ctx.player.toggle())


@router.post("/stop")
async def stop(ctx: MpdCtx) -> dict[str, Any]:
    return await _run(ctx, ctx.player.stop())


@router.post("/next")
async def next_track(ctx: MpdCtx) -> dict[str, Any]:
    return await _run(ctx, ctx.player.next())


@router.post("/previous")
async def previous_track(ctx: MpdCtx) -> dict[str, Any]:
    return await _run(ctx, ctx.player.previous())


@router.post("/seek")
async def seek(ctx: MpdCtx, body: SeekRequest) -> dict[str, Any]:
    return await _run(ctx, ctx.player.seek(body.seconds))


@router.post("/volume")
async def set_volume(ctx: MpdCtx, body: VolumeRequest) -> dict[str, Any]:
    return await _run(ctx, ctx.player.set_volume(body.volume))


@router.post("/scope")
async def play_scope(ctx: MpdCtx, body: PlayScopeRequest) -> dict[str, Any]:
    """再生スコープを読み込んで再生を開始する（要件 3.1）。"""
    try:
        await ctx.player.play_scope(body.kind, body.id, start_track_id=body.start_track_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MPDUnavailable as exc:
        raise mpd_error(exc) from exc
    return await ctx.player.build_state()


@router.post("/tracks")
async def play_tracks(ctx: MpdCtx, body: PlayTracksRequest) -> dict[str, Any]:
    """任意の曲リストを再生する（検索結果からの再生など）。"""
    try:
        await ctx.player.play_tracks(body.track_ids, start_index=body.start_index)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MPDUnavailable as exc:
        raise mpd_error(exc) from exc
    return await ctx.player.build_state()


@router.post("/repeat/{mode}")
async def set_repeat(ctx: MpdCtx, mode: Literal["off", "one", "range"]) -> dict[str, Any]:
    """リピート: オフ / 1 曲 / スコープ内（要件 3.1）。"""
    return await _run(ctx, ctx.player.set_repeat(mode))


@router.post("/order/{mode}")
async def set_order(ctx: MpdCtx, mode: Literal["normal", "shuffle", "random"]) -> dict[str, Any]:
    """再生順:

    * ``normal`` … タグ順
    * ``shuffle`` … 重複なしシャッフル（スコープを一巡するまで同じ曲が出ない）
    * ``random`` … 重複ありランダム（毎回独立に抽選。往年の Walkman 準拠）
    """
    return await _run(ctx, ctx.player.set_order(mode))


@router.get("/queue")
async def queue(ctx: MpdCtx) -> dict[str, Any]:
    """MPD の現在のキュー（タグ DB の情報を突き合わせて返す）。"""
    try:
        entries = await ctx.mpd.queue()
    except MPDUnavailable as exc:
        raise mpd_error(exc) from exc

    items = []
    for position, entry in enumerate(entries):
        track = ctx.repo.get_track_by_path(entry.get("file", ""))
        items.append(
            {
                "position": position,
                "song_id": _int_or_none(entry.get("id")),
                "file": entry.get("file"),
                "track": track,
            }
        )
    return {"items": items, "total": len(items)}


async def _run(ctx: Ctx, coro: Any) -> dict[str, Any]:
    try:
        await coro
        return await ctx.player.build_state()
    except MPDUnavailable as exc:
        raise mpd_error(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
