"""設定・スキャン・システム情報の API。"""

from __future__ import annotations

import asyncio
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from kotone import __version__
from kotone.api.deps import Ctx
from kotone.db import current_version
from kotone.library.artists import CANDIDATE_SEPARATORS, analyze_separators
from kotone.library.loudness import LoudnessScanner
from kotone.library.scanner import LibraryScanner
from kotone.settings import spec_dicts
from kotone.stats import estimate_loudness

router = APIRouter(tags=["system"])

#: 変えたら、いま鳴っている音量を計算し直す設定。
#: volume_control（音量スライダーの有効・無効）を含める。無効にしたら
#: その場で最大（×ラウドネス補正）へ戻す
VOLUME_SETTINGS = {
    "volume_control",
    "loudness_enabled", "loudness_mode", "loudness_preamp_db", "loudness_clip_prevention",
}


class SettingsPatch(BaseModel):
    values: dict[str, Any]


class ScanRequest(BaseModel):
    #: True にすると変更が無いファイルも含めて全部読み直す
    full: bool = False


# ============================================================== 設定


@router.get("/settings")
def get_settings(ctx: Ctx) -> dict[str, Any]:
    return {"values": ctx.settings.all(), "specs": spec_dicts()}


@router.patch("/settings")
async def update_settings(ctx: Ctx, body: SettingsPatch) -> dict[str, Any]:
    try:
        values = ctx.settings.update(body.values)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # 再生に影響する設定は即座に MPD へ反映する
    if ctx.mpd.connected:
        if "dsd_output" in body.values:
            # 出力の切り替えと音量の計算し直しまで行う
            await ctx.player.apply_dsd_output()
        elif VOLUME_SETTINGS & set(body.values):
            await ctx.player.refresh_volume()
        await ctx.player.broadcast_state()

    ctx.events.publish("settings", {"values": values})
    return {"values": values, "specs": spec_dicts()}


# ======================================================== 音楽用ドライブ


@router.get("/storage")
async def storage_state(ctx: Ctx) -> dict[str, Any]:
    """音楽用ドライブの状態（その場で確かめ直して返す）。"""
    return (await ctx.storage.check()).as_dict()


# ============================================================== スキャン


@router.get("/scan")
def scan_state(ctx: Ctx) -> dict[str, Any]:
    return ctx.scan.state()


@router.post("/scan", status_code=202)
async def start_scan(ctx: Ctx, body: ScanRequest | None = None) -> dict[str, Any]:
    try:
        await ctx.scan.start(full=bool(body and body.full))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ctx.scan.state()


class LoudnessScanRequest(BaseModel):
    #: True にすると計測済みの曲も測り直す
    remeasure: bool = False


@router.post("/scan/loudness", status_code=202)
async def start_loudness_scan(
    ctx: Ctx, body: LoudnessScanRequest | None = None
) -> dict[str, Any]:
    """ラウドネス計測だけを実行する（要件 3.1）。

    通常のライブラリスキャンとは分離してある。曲数が多いと十数時間かかるため、
    取り込みを先に終わらせて再生できる状態にしてから、任意のタイミングで走らせる。
    未計測の曲だけを対象にするので、中断しても続きから再開できる。
    """
    try:
        await ctx.scan.start_loudness(remeasure=bool(body and body.remeasure))
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return ctx.scan.state()


@router.get("/scan/loudness")
def loudness_progress(ctx: Ctx, remeasure: bool = False) -> dict[str, Any]:
    """計測の残量と、所要時間の見積もり。

    曲数の多いライブラリでは十数時間かかることがあるため、実行前に
    「どれくらいかかるか」を提示して確認できるようにしている。
    見積もりは前回計測時の実測スループットに基づく（初回は既定値による概算）。
    """
    estimate = estimate_loudness(ctx.db, ctx.stats, remeasure=remeasure)
    scanner = LoudnessScanner(
        enabled=True,
        concurrency=int(ctx.settings.get("loudness_concurrency") or 0),
        true_peak=bool(ctx.settings.get("loudness_true_peak")),
    )
    return {
        **estimate.as_dict(),
        "running": ctx.scan.running,
        "backend": scanner.backend,
        "available": scanner.available,
        "state": ctx.scan.state(),
    }


@router.post("/scan/loudness/calibrate")
async def calibrate_loudness(ctx: Ctx, sample_size: int = 6) -> dict[str, Any]:
    """短いサンプルを実測して所要時間の見積もりを実機に合わせる。

    数秒で終わる。十数時間かかりうる計測を始める前に呼ぶことで、
    「約 11 時間」と「約 5 時間」のような大きなずれを避けられる。
    """
    if ctx.scan.running:
        raise HTTPException(status_code=409, detail="スキャンの実行中は較正できません")

    scanner = LibraryScanner(
        db=ctx.db, config=ctx.config, settings=ctx.settings,
    )
    result = await asyncio.to_thread(scanner.calibrate_loudness, max(1, min(sample_size, 30)))
    estimate = estimate_loudness(ctx.db, ctx.stats)
    return {**result, "estimate": estimate.as_dict()}


@router.get("/scan/analyze-artists")
def analyze_artists(ctx: Ctx, limit: int = 5) -> dict[str, Any]:
    """ライブラリ中の ARTIST 文字列から区切り文字の傾向を調べる（要件 3.6）。

    ここで頻度の高い区切り文字を確認し、設定 ``artist_separators`` を
    実データに合わせて調整する。
    """
    rows = ctx.db.query(
        """
        SELECT DISTINCT a.name AS name
        FROM artists a
        """
    )
    reports = analyze_separators(
        (r["name"] for r in rows), CANDIDATE_SEPARATORS, max_examples=max(1, limit)
    )
    return {
        "current": ctx.settings.get("artist_separators"),
        "candidates": [
            {"separator": r.separator, "count": r.count, "examples": r.examples}
            for r in reports
        ],
        "artist_count": len(rows),
    }


# ============================================================ システム情報


@router.get("/system")
async def system_info(ctx: Ctx) -> dict[str, Any]:
    mpd_stats: dict[str, Any] = {}
    mpd_status: dict[str, Any] = {}
    if ctx.mpd.connected:
        try:
            mpd_stats = await ctx.mpd.stats()
            mpd_status = await ctx.mpd.status()
        except Exception:  # pragma: no cover - 接続が切れた直後
            pass

    return {
        "version": __version__,
        "schema_version": current_version(ctx.db.conn),
        "mpd": {
            "connected": ctx.mpd.connected,
            "host": f"{ctx.config.mpd_host}:{ctx.config.mpd_port}",
            "stats": mpd_stats,
            "audio": mpd_status.get("audio"),
        },
        "paths": {
            "music_dir": str(ctx.config.music_dir),
            "data_dir": str(ctx.config.data_dir),
            "artwork_cache": str(ctx.config.artwork_cache_dir),
        },
        "tools": {
            "rsgain": bool(shutil.which("rsgain")),
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "morphological_analyzer": ctx.reading_available,
        },
        "storage": _disk_usage(str(ctx.config.data_dir)),
        "library": ctx.repo.stats(),
    }


@router.get("/health")
def health(ctx: Ctx) -> dict[str, Any]:
    return {
        "status": "ok",
        "mpd_connected": ctx.mpd.connected,
        "scanning": ctx.scan.running,
    }


def _disk_usage(path: str) -> dict[str, int] | None:
    try:
        usage = shutil.disk_usage(path)
    except OSError:  # pragma: no cover
        return None
    return {"total": usage.total, "used": usage.used, "free": usage.free}
