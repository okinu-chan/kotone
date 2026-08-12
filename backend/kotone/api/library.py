"""ライブラリの参照 API（一覧・ソート・イニシャルサーチ・ダイレクトサーチ）。"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from kotone.api.deps import Ctx
from kotone.repository import ALBUM_SORTS, ARTIST_SORTS, TRACK_SORTS

router = APIRouter(prefix="/library", tags=["library"])


@router.get("/stats")
def stats(ctx: Ctx) -> dict[str, Any]:
    """ライブラリの規模とスキャン状態。"""
    return {"library": ctx.repo.stats(), "scan": ctx.scan.state()}


@router.get("/sorts")
def sorts() -> dict[str, list[str]]:
    """UI に出すソート軸の一覧（要件 3.2）。"""
    return {
        "albums": list(ALBUM_SORTS),
        "artists": list(ARTIST_SORTS),
        "tracks": list(TRACK_SORTS),
    }


# ------------------------------------------------------------------ アルバム


@router.get("/albums")
def list_albums(
    ctx: Ctx,
    sort: str = "artist",
    initial: str | None = None,
    initial_group: Literal["kana", "latin"] | None = None,
    artist_id: int | None = None,
    genre_id: int | None = None,
    year: int | None = None,
    q: str | None = None,
    codec: str | None = None,
    quality: Literal["hires", "cd", "dsd"] | None = None,
    limit: int | None = Query(default=None, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items = ctx.repo.list_albums(
        sort=sort, initial=initial, initial_group=initial_group, artist_id=artist_id,
        genre_id=genre_id, year=year, query=q, codec=codec, quality=quality,
        limit=limit, offset=offset,
    )
    total = ctx.repo.count_albums(
        initial=initial, initial_group=initial_group, artist_id=artist_id,
        genre_id=genre_id, year=year, query=q, codec=codec, quality=quality,
    )
    return {"items": items, "total": total, "offset": offset}


@router.get("/formats")
def list_formats(ctx: Ctx) -> dict[str, Any]:
    """ライブラリに実在するフォーマット・音質（絞り込みの選択肢）。"""
    return ctx.repo.list_formats()


@router.get("/albums/{album_id}")
def get_album(ctx: Ctx, album_id: int) -> dict[str, Any]:
    album = ctx.repo.get_album(album_id)
    if album is None:
        raise HTTPException(status_code=404, detail="アルバムが見つかりません")
    return album


# -------------------------------------------------------------- アーティスト


@router.get("/artists")
def list_artists(
    ctx: Ctx,
    sort: str = "artist",
    initial: str | None = None,
    initial_group: Literal["kana", "latin"] | None = None,
    q: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items = ctx.repo.list_artists(
        sort=sort, initial=initial, initial_group=initial_group,
        query=q, limit=limit, offset=offset,
    )
    return {"items": items, "total": len(items), "offset": offset}


@router.get("/artists/{artist_id}")
def get_artist(ctx: Ctx, artist_id: int) -> dict[str, Any]:
    """アーティスト詳細。

    ダイレクトサーチの起点。そのアーティストの全曲（"All ○○"）と、
    収録アルバムの一覧を一度に返す（要件 3.6）。
    """
    artist = ctx.repo.get_artist(artist_id)
    if artist is None:
        raise HTTPException(status_code=404, detail="アーティストが見つかりません")
    return artist


# ------------------------------------------------------------------ 楽曲


@router.get("/tracks")
def list_tracks(
    ctx: Ctx,
    sort: str = "album",
    album_id: int | None = None,
    artist_id: int | None = None,
    genre_id: int | None = None,
    year: int | None = None,
    initial: str | None = None,
    initial_group: Literal["kana", "latin"] | None = None,
    q: str | None = None,
    codec: str | None = None,
    quality: Literal["hires", "cd", "dsd"] | None = None,
    limit: int | None = Query(default=None, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    items = ctx.repo.list_tracks(
        sort=sort, album_id=album_id, artist_id=artist_id, genre_id=genre_id,
        year=year, initial=initial, initial_group=initial_group,
        query=q, codec=codec, quality=quality, limit=limit, offset=offset,
    )
    return {"items": items, "total": len(items), "offset": offset}


@router.get("/tracks/{track_id}")
def get_track(ctx: Ctx, track_id: int) -> dict[str, Any]:
    track = ctx.repo.get_track(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail="曲が見つかりません")
    return track


# ------------------------------------------------------------ ジャンル・年


@router.get("/genres")
def list_genres(ctx: Ctx) -> dict[str, Any]:
    items = ctx.repo.list_genres()
    return {"items": items, "total": len(items)}


@router.get("/years")
def list_years(ctx: Ctx) -> dict[str, Any]:
    items = ctx.repo.list_years()
    return {"items": items, "total": len(items)}


# ------------------------------------------------------- イニシャルサーチ


@router.get("/initials/{entity}")
def initials(
    ctx: Ctx,
    entity: Literal["albums", "artists", "tracks"],
    group: Literal["kana", "latin"] = "kana",
) -> dict[str, Any]:
    """頭文字ごとの件数（要件 3.7）。

    件数 0 の文字も返すのでグリッドのレイアウトが動かない。
    """
    return {"entity": entity, "group": group, "items": ctx.repo.initials(entity, group)}


# ------------------------------------------------------------------ 検索


@router.get("/search")
def search(
    ctx: Ctx,
    q: str = Query(min_length=1),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """名前と読みの両方に対する横断検索。"""
    return ctx.repo.search(q, limit=limit)
