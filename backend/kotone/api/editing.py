"""タグ・読み仮名・アーティスト紐付けの編集 API（要件 3.2 / 3.6）。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kotone.api.deps import Ctx
from kotone.editing import EditError

router = APIRouter(prefix="/edit", tags=["edit"])


class ArtistPatch(BaseModel):
    name: str | None = None
    reading: str | None = None


class AlbumPatch(BaseModel):
    name: str | None = None
    reading: str | None = None
    year: int | None = None


class TrackPatch(BaseModel):
    title: str | None = None
    reading: str | None = None
    disc_no: int | None = None
    track_no: int | None = None
    year: int | None = None
    #: 既存アーティストの ID を並べて紐付けを差し替える
    artist_ids: list[int] | None = None
    #: 新規アーティスト名（存在しなければ作成される）
    artist_names: list[str] | None = None
    genre_names: list[str] | None = None


class MergeRequest(BaseModel):
    source_id: int
    target_id: int


class ReadingSuggestRequest(BaseModel):
    text: str


@router.post("/reading/suggest")
def suggest_reading(ctx: Ctx, body: ReadingSuggestRequest) -> dict[str, Any]:
    """形態素解析による読みの候補を返す（「自動生成に戻す」ボタン用）。"""
    return {
        "text": body.text,
        "reading": ctx.editing.suggest_reading(body.text),
        "analyzer_available": ctx.reading_available,
    }


@router.patch("/artists/{artist_id}")
def update_artist(ctx: Ctx, artist_id: int, body: ArtistPatch) -> dict[str, Any]:
    try:
        ctx.editing.update_artist(artist_id, name=body.name, reading=body.reading)
    except EditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ctx.db.query_one("SELECT * FROM artists WHERE id = ?", (artist_id,)) or {}


@router.post("/artists/merge")
def merge_artists(ctx: Ctx, body: MergeRequest) -> dict[str, Any]:
    """区切り文字の誤判定などでできた重複アーティストを統合する。"""
    try:
        ctx.editing.merge_artists(body.source_id, body.target_id)
    except EditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ctx.db.query_one("SELECT * FROM artists WHERE id = ?", (body.target_id,)) or {}


@router.patch("/albums/{album_id}")
def update_album(ctx: Ctx, album_id: int, body: AlbumPatch) -> dict[str, Any]:
    try:
        ctx.editing.update_album(
            album_id, name=body.name, reading=body.reading, year=body.year
        )
    except EditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    album = ctx.repo.get_album(album_id)
    if album is None:
        raise HTTPException(status_code=404, detail="アルバムが見つかりません")
    return album


@router.patch("/tracks/{track_id}")
def update_track(ctx: Ctx, track_id: int, body: TrackPatch) -> dict[str, Any]:
    try:
        ctx.editing.update_track(
            track_id,
            title=body.title,
            reading=body.reading,
            disc_no=body.disc_no,
            track_no=body.track_no,
            year=body.year,
            artist_ids=body.artist_ids,
            artist_names=body.artist_names,
            genre_names=body.genre_names,
        )
    except EditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    track = ctx.repo.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="曲が見つかりません")
    return track


@router.post("/tracks/{track_id}/unlock")
def unlock_track(ctx: Ctx, track_id: int) -> dict[str, Any]:
    """手動修正を解除し、次回スキャンで自動生成値に戻す。"""
    ctx.editing.unlock_track(track_id)
    return ctx.repo.get_track(track_id) or {}


@router.post("/{table}/{entity_id}/unlock-reading")
def unlock_reading(ctx: Ctx, table: str, entity_id: int) -> dict[str, str]:
    try:
        ctx.editing.unlock_reading(table, entity_id)
    except EditError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}
