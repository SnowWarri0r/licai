"""交易计划: 列表 / 手动新增 / 取消 / 计划对照复盘。盯价循环在 run.py 的 plan_monitor_loop。"""
from __future__ import annotations
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import trade_plan as tp

router = APIRouter(prefix="/api/plans", tags=["plans"])


class PlanIn(BaseModel):
    code: str
    side: str
    trigger_op: str
    trigger_price: float
    plan_date: Optional[str] = None      # 空 = 下一个交易日(收盘后定次日计划是常态)
    qty: Optional[int] = None
    reason: Optional[str] = ""
    invalid_op: Optional[str] = None
    invalid_price: Optional[float] = None


@router.get("")
async def list_plans(date: str = ""):
    """date 空: 今天和下一个交易日的计划(盘中看今天的, 收盘后看明天的)。"""
    if date:
        return {"plans": await tp.list_plans(date)}
    t, n = tp.today(), tp.next_trading_day()
    return {"today": t, "next": n, "plans": await tp.list_plans(t) + await tp.list_plans(n)}


@router.post("")
async def add_plan(p: PlanIn):
    item = p.model_dump()
    item["plan_date"] = item.get("plan_date") or tp.next_trading_day()
    r = await tp.add_plans([item])
    if r["rejected"]:
        raise HTTPException(400, r["rejected"][0]["why"])
    return r["saved"][0]


@router.delete("/{plan_id}")
async def cancel_plan(plan_id: int):
    return {"ok": await tp.cancel(plan_id)}


@router.get("/review")
async def review(date: str = ""):
    return await tp.review(date or None)
