"""FastAPI アプリケーションの組み立て。"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from kotone import __version__
from kotone.api import create_api_router
from kotone.config import Config, get_config
from kotone.context import AppContext

log = logging.getLogger(__name__)


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    # systemd/journalctl 側でタイムスタンプが付くので冗長さを抑える
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def create_app(config: Config | None = None) -> FastAPI:
    config = config or get_config()
    configure_logging(config.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ctx = AppContext(config)
        app.state.ctx = ctx
        await ctx.startup()
        log.info("KOTONE %s を起動しました (http://%s:%s)", __version__, config.host, config.port)
        try:
            yield
        finally:
            await ctx.shutdown()

    app = FastAPI(
        title="kotone",
        version=__version__,
        description=(
            "Raspberry Pi 向け日本語ネイティブ音楽プレイヤーのバックエンド API。\n\n"
            "家庭内 LAN での利用を前提としており、認証は設けていません。"
        ),
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(create_api_router())
    _mount_frontend(app, config)
    return app


def _mount_frontend(app: FastAPI, config: Config) -> None:
    """ビルド済みフロントエンドの配信。

    Pi 上では Node.js を入れないため、ここで配るのはリポジトリに同梱された
    静的ファイルそのもの（要件 4.7 / 5 章）。
    """
    static_dir = config.resolved_static_dir
    if not static_dir.is_dir():
        log.warning(
            "フロントエンドのビルド成果物が見つかりません: %s "
            "（API は利用できます。frontend/ で `npm run build` を実行してください）",
            static_dir,
        )

        @app.get("/", include_in_schema=False)
        async def missing_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "フロントエンドが未ビルドです。"
                    "frontend/ で `npm install && npm run build` を実行してください。",
                    "api_docs": "/api/docs",
                },
            )

        return

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index_file = static_dir / "index.html"
    # index.html は読み込む JS / CSS のファイル名（ハッシュ付き）を持つので、毎回確かめさせる。
    # Cache-Control が無いと、ホーム画面のウェブアプリは古い index.html をキャッシュから
    # 開き続け、更新した画面が出ない。
    index_headers = {"Cache-Control": "no-cache"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(request: Request, full_path: str) -> FileResponse:
        """SPA のフォールバック。実ファイルがあればそれを、無ければ index.html を返す。"""
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")

        candidate = (static_dir / full_path).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
        except ValueError:  # パストラバーサル対策
            raise HTTPException(status_code=404, detail="Not Found") from None

        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file, headers=index_headers)


def get_app() -> FastAPI:
    """uvicorn の ``--factory`` 用エントリポイント。"""
    return create_app()
