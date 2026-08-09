from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from kotone.context import AppContext
from kotone.mpdclient import MPDUnavailable


def get_ctx(request: Request) -> AppContext:
    ctx: AppContext | None = getattr(request.app.state, "ctx", None)
    if ctx is None:  # pragma: no cover - 起動前アクセス
        raise HTTPException(status_code=503, detail="アプリケーションが初期化されていません")
    return ctx


Ctx = Annotated[AppContext, Depends(get_ctx)]


def require_mpd(ctx: Ctx) -> AppContext:
    if not ctx.mpd.connected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MPD に接続できていません。`systemctl status mpd` を確認してください。",
        )
    return ctx


MpdCtx = Annotated[AppContext, Depends(require_mpd)]


def mpd_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MPDUnavailable):
        return HTTPException(status_code=503, detail=str(exc))
    return HTTPException(status_code=500, detail=str(exc))
