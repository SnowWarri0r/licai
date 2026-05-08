"""定投 (DCA) 计划 REST 端点.

每个计划绑一个 asset_id, 按 day_of_month 每月触发一次, 写 pending ADD action.
"""
from __future__ import annotations
from datetime import date
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import (
    list_dca_schedules, get_dca_schedule, add_dca_schedule,
    update_dca_schedule, delete_dca_schedule, get_external_asset,
)
from services.dca import fire_due_dcas, initial_next_due

router = APIRouter(prefix="/api/dca", tags=["dca"])


class DCACreate(BaseModel):
    asset_id: int
    mode: str = "amount"                       # 'amount' | 'shares'
    value: float                               # CNY 金额或份数
    frequency: str = "monthly"                 # 'daily_trading' | 'weekly' | 'monthly'
    day_of_month: Optional[int] = None         # 1-31, frequency=monthly 时必填
    day_of_week: Optional[int] = None          # 1-7 (Mon=1), frequency=weekly 时必填
    note: Optional[str] = ""


class DCAUpdate(BaseModel):
    mode: Optional[str] = None
    value: Optional[float] = None
    frequency: Optional[str] = None
    day_of_month: Optional[int] = None
    day_of_week: Optional[int] = None
    status: Optional[str] = None       # 'active' | 'paused'
    next_due: Optional[str] = None     # 手动改下次扣款日 (调试 / 跳过本期)
    note: Optional[str] = None


def _validate_mode(mode: str):
    if mode not in ("amount", "shares"):
        raise HTTPException(400, "mode 必须是 amount 或 shares")


def _validate_frequency(freq: str):
    if freq not in ("daily_trading", "weekly", "monthly"):
        raise HTTPException(400, "frequency 必须是 daily_trading / weekly / monthly")


def _validate_day_of_month(day: int):
    if not (1 <= int(day) <= 31):
        raise HTTPException(400, "day_of_month 必须在 1-31")


def _validate_day_of_week(day: int):
    if not (1 <= int(day) <= 7):
        raise HTTPException(400, "day_of_week 必须在 1-7 (Mon=1)")


@router.get("")
async def list_all(asset_id: Optional[int] = None):
    rows = await list_dca_schedules(asset_id)
    return {"schedules": rows, "count": len(rows)}


@router.get("/{dca_id}")
async def get_one(dca_id: int):
    row = await get_dca_schedule(dca_id)
    if not row:
        raise HTTPException(404, "not found")
    return row


@router.post("")
async def create(data: DCACreate):
    asset = await get_external_asset(data.asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    if asset["asset_type"] not in ("FUND", "CRYPTO"):
        raise HTTPException(400, "目前仅支持 FUND / CRYPTO 类资产定投")
    _validate_mode(data.mode)
    _validate_frequency(data.frequency)
    if data.frequency == "monthly":
        if data.day_of_month is None:
            raise HTTPException(400, "monthly 频率必须填 day_of_month")
        _validate_day_of_month(data.day_of_month)
    elif data.frequency == "weekly":
        if data.day_of_week is None:
            raise HTTPException(400, "weekly 频率必须填 day_of_week")
        _validate_day_of_week(data.day_of_week)
    if data.value <= 0:
        raise HTTPException(400, "value 必须 > 0")
    next_due = initial_next_due(data.frequency, data.day_of_month, data.day_of_week)
    new_id = await add_dca_schedule(
        asset_id=data.asset_id, mode=data.mode, value=data.value,
        frequency=data.frequency,
        day_of_month=data.day_of_month, day_of_week=data.day_of_week,
        next_due=next_due, note=data.note or "",
    )
    return {"message": "added", "id": new_id, "next_due": next_due}


@router.put("/{dca_id}")
async def update(dca_id: int, data: DCAUpdate):
    existing = await get_dca_schedule(dca_id)
    if not existing:
        raise HTTPException(404, "not found")
    payload = data.model_dump(exclude_unset=True)
    if "mode" in payload:
        _validate_mode(payload["mode"])
    if "frequency" in payload:
        _validate_frequency(payload["frequency"])
    if "day_of_month" in payload and payload["day_of_month"] is not None:
        _validate_day_of_month(payload["day_of_month"])
    if "day_of_week" in payload and payload["day_of_week"] is not None:
        _validate_day_of_week(payload["day_of_week"])
    if "value" in payload and payload["value"] <= 0:
        raise HTTPException(400, "value 必须 > 0")
    if "status" in payload and payload["status"] not in ("active", "paused"):
        raise HTTPException(400, "status 必须是 active 或 paused")
    # 改了 frequency / day → 自动重算 next_due (除非用户也手动指定)
    if ("frequency" in payload or "day_of_month" in payload or "day_of_week" in payload) and "next_due" not in payload:
        merged = {**existing, **payload}
        payload["next_due"] = initial_next_due(
            merged.get("frequency") or "monthly",
            merged.get("day_of_month"),
            merged.get("day_of_week"),
        )
    if payload:
        await update_dca_schedule(dca_id, **payload)
    return {"message": "updated"}


@router.delete("/{dca_id}")
async def remove(dca_id: int):
    await delete_dca_schedule(dca_id)
    return {"message": "deleted"}


@router.post("/fire-due")
async def fire_due():
    """手动触发: 扫所有 active+due 的计划, 写 pending action. 调试 / 漏触发时用."""
    fired = await fire_due_dcas()
    return {"fired": fired, "count": len(fired)}
