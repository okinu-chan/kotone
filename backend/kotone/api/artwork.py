"""アートワーク配信 API。

要件 3.3 / 4.6 の通り、ここではリサイズ計算を一切行わない。
スキャン時に生成済みの PNG をそのまま返すだけ。
要求された階層が無い場合は、生成できた中で最大の階層へフォールバックする。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import FileResponse

from kotone.api.deps import Ctx
from kotone.library.artwork import TIER_ORDER

router = APIRouter(prefix="/artwork", tags=["artwork"])

#: URL にハッシュ (?v=) を含めるので、長期キャッシュで問題ない
CACHE_CONTROL = "public, max-age=31536000, immutable"


@router.get("/{album_id}")
def get_artwork(
    ctx: Ctx,
    album_id: int,
    tier: Literal["small", "medium", "large"] = "medium",
    v: str | None = None,  # noqa: ARG001 - キャッシュバスティング用。値は使わない
) -> Response:
    rows = ctx.db.query(
        """
        SELECT af.tier, af.path, af.width, af.height, a.source_hash
        FROM artwork a
        JOIN artwork_files af ON af.artwork_id = a.id
        WHERE a.album_id = ?
        """,
        (album_id,),
    )
    if not rows:
        raise HTTPException(status_code=404, detail="アートワークがありません")

    by_tier = {row["tier"]: row for row in rows}
    chosen = by_tier.get(tier)
    if chosen is None:
        # 要求より小さい階層しか無い場合は、存在する中で最大のものを返す
        for candidate in reversed(TIER_ORDER):
            if candidate in by_tier:
                chosen = by_tier[candidate]
                break
    if chosen is None:  # pragma: no cover - artwork_files が空
        raise HTTPException(status_code=404, detail="アートワークがありません")

    path = ctx.config.artwork_cache_dir / chosen["path"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="キャッシュ画像が見つかりません")

    return FileResponse(
        path,
        media_type="image/png",
        headers={
            "Cache-Control": CACHE_CONTROL,
            "ETag": f'"{chosen["source_hash"]}-{chosen["tier"]}"',
            "X-Artwork-Tier": chosen["tier"],
            "X-Artwork-Size": f"{chosen['width']}x{chosen['height']}",
        },
    )


@router.get("/{album_id}/info")
def artwork_info(ctx: Ctx, album_id: int) -> dict[str, object]:
    """生成済みの階層一覧（デバッグ・設定画面用）。"""
    artwork = ctx.db.query_one(
        "SELECT id, source_kind, source_path, source_hash, src_width, src_height, updated_at "
        "FROM artwork WHERE album_id = ?",
        (album_id,),
    )
    if artwork is None:
        raise HTTPException(status_code=404, detail="アートワークがありません")
    tiers = ctx.db.query(
        "SELECT tier, path, width, height, bytes FROM artwork_files WHERE artwork_id = ?",
        (artwork["id"],),
    )
    return {"album_id": album_id, **artwork, "tiers": tiers}
