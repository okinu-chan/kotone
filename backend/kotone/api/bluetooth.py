"""Bluetooth 機器と出力先の API。"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from kotone.api.deps import Ctx
from kotone.bluetooth import BluetoothError

router = APIRouter(tags=["bluetooth"])


class AddressRequest(BaseModel):
    address: str = Field(min_length=11)


class ScanRequest(BaseModel):
    seconds: float = Field(default=12.0, ge=1.0, le=60.0)


class PoweredRequest(BaseModel):
    powered: bool


class OutputRequest(BaseModel):
    id: int


# ------------------------------------------------------------------ 出力先


@router.get("/outputs")
async def list_outputs(ctx: Ctx) -> dict[str, Any]:
    """再生の出力先（USB DAC / Bluetooth）。"""
    if not ctx.mpd.connected:
        return {"items": [], "active": None}
    items = await ctx.outputs.list()
    return {"items": items, "active": next((i for i in items if i["enabled"]), None)}


@router.post("/outputs/select")
async def select_output(ctx: Ctx, body: OutputRequest) -> dict[str, Any]:
    try:
        items = await ctx.outputs.select(body.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await ctx.player.broadcast_state()
    return {"items": items, "active": next((i for i in items if i["enabled"]), None)}


# -------------------------------------------------------------- Bluetooth


@router.get("/bluetooth")
async def bluetooth_state(ctx: Ctx) -> dict[str, Any]:
    """アダプタの状態と、既知の機器の一覧。"""
    adapter = await ctx.bluetooth.adapter_info()
    devices: list[dict[str, Any]] = []
    codec = None
    if adapter.get("available"):
        devices = [d.as_dict() for d in await ctx.bluetooth.devices()]
        if any(d["connected"] for d in devices):
            codec = await ctx.bluetooth.codec_info()
    return {
        "adapter": adapter,
        "scanning": ctx.bluetooth.scanning,
        "devices": devices,
        "codec": codec,
    }


@router.post("/bluetooth/power")
async def set_power(ctx: Ctx, body: PoweredRequest) -> dict[str, Any]:
    await _guard(ctx.bluetooth.set_powered(body.powered))
    return await bluetooth_state(ctx)


@router.post("/bluetooth/scan")
async def scan(ctx: Ctx, body: ScanRequest | None = None) -> dict[str, Any]:
    """近くの機器を探す。探索は数十秒かかることがある。"""
    seconds = body.seconds if body else 12.0
    devices = await _guard(ctx.bluetooth.scan(seconds))
    return {"devices": [d.as_dict() for d in devices]}


@router.post("/bluetooth/pair")
async def pair(ctx: Ctx, body: AddressRequest) -> dict[str, Any]:
    device = await _guard(ctx.bluetooth.pair(body.address))
    codec = await _prefer_quality(ctx)
    return {"device": device.as_dict(), "codec": codec}


@router.post("/bluetooth/connect")
async def connect(ctx: Ctx, body: AddressRequest) -> dict[str, Any]:
    device = await _guard(ctx.bluetooth.connect(body.address))
    codec = await _prefer_quality(ctx)
    return {"device": device.as_dict(), "codec": codec}


@router.get("/bluetooth/codec")
async def codec(ctx: Ctx) -> dict[str, Any]:
    """いま接続中の機器で使われているコーデック。"""
    return {"codec": await ctx.bluetooth.codec_info(fresh=True)}


async def _prefer_quality(ctx: Ctx) -> dict[str, Any] | None:
    """設定が有効なら、使えるうちで一番よいコーデックへ切り替える。

    接続直後は相手がコーデック一覧を出し終えていないことがあるので、
    少し待ってから読む。
    """
    if not ctx.settings.get("bluetooth_prefer_quality"):
        return await ctx.bluetooth.codec_info(fresh=True)
    await asyncio.sleep(1.0)
    await ctx.bluetooth.select_best_codec()
    return await ctx.bluetooth.codec_info(fresh=True)


@router.post("/bluetooth/disconnect")
async def disconnect(ctx: Ctx, body: AddressRequest) -> dict[str, Any]:
    device = await _guard(ctx.bluetooth.disconnect(body.address))
    return {"device": device.as_dict()}


@router.post("/bluetooth/forget")
async def forget(ctx: Ctx, body: AddressRequest) -> dict[str, Any]:
    await _guard(ctx.bluetooth.forget(body.address))
    return await bluetooth_state(ctx)


async def _guard(awaitable):
    """利用者向けの文言をそのまま 400 で返す。"""
    try:
        return await awaitable
    except BluetoothError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
