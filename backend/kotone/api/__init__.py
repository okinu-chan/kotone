"""REST / WebSocket API ルーター。"""

from __future__ import annotations

from fastapi import APIRouter

from kotone.api import (
    artwork,
    bluetooth,
    editing,
    library,
    player,
    playlists,
    system,
    ws,
)


def create_api_router() -> APIRouter:
    router = APIRouter(prefix="/api")
    router.include_router(library.router)
    router.include_router(player.router)
    router.include_router(playlists.router)
    router.include_router(artwork.router)
    router.include_router(editing.router)
    router.include_router(bluetooth.router)
    router.include_router(system.router)
    router.include_router(ws.router)
    return router


__all__ = ["create_api_router"]
