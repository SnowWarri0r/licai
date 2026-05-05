"""月度现金流: 收入 / 固定开销 / 可自由支配 — 与持仓 / 资产端解耦, 纯记账."""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from database import (
    upsert_cashflow,
    get_cashflow,
    list_cashflow,
    delete_cashflow,
    get_config,
    set_config,
)


_RATE_KEY = "cashflow_savings_rate_target"
_DEFAULT_RATE = 0.30

router = APIRouter(prefix="/api/cashflow", tags=["cashflow"])


class CashflowEntry(BaseModel):
    month: str  # YYYY-MM
    income: float = 0
    fixed_cost: float = 0
    discretionary: float = 0
    notes: Optional[str] = ""


def _net(row: dict) -> float:
    return float(row.get("income") or 0) - float(row.get("fixed_cost") or 0) - float(row.get("discretionary") or 0)


def _enrich(row: dict) -> dict:
    if not row:
        return row
    row = dict(row)
    row["net_savings"] = round(_net(row), 2)
    return row


@router.get("")
async def list_recent(months: int = 12):
    rows = await list_cashflow(months)
    return {"entries": [_enrich(r) for r in rows], "count": len(rows)}


async def _get_rate() -> float:
    raw = await get_config(_RATE_KEY)
    if raw is None:
        return _DEFAULT_RATE
    try:
        rate = float(raw)
        if rate < 0 or rate > 0.95:
            return _DEFAULT_RATE
        return rate
    except (TypeError, ValueError):
        return _DEFAULT_RATE


def _safe_avg(rows: list[dict], key: str) -> float:
    if not rows:
        return 0.0
    return sum(float(r.get(key) or 0) for r in rows) / len(rows)


@router.get("/summary")
async def summary(window: int = 6):
    """最近 N 月平均 + 当月对比 + 储蓄率目标推出的可支配上限 + 3 月均值."""
    window = max(1, min(int(window), 24))
    rows = await list_cashflow(window)
    rate = await _get_rate()
    cur_month = datetime.now().strftime("%Y-%m")
    current = await get_cashflow(cur_month)

    if not rows:
        return {
            "window": 0,
            "avg_income": 0, "avg_fixed": 0, "avg_disc": 0, "avg_net": 0,
            "current_month": cur_month,
            "current": None,
            "savings_rate_target": rate,
            "hard_cap": None,
            "soft_avg": None,
        }

    n = len(rows)
    avg_income = round(_safe_avg(rows, "income"), 2)
    avg_fixed  = round(_safe_avg(rows, "fixed_cost"), 2)
    avg_disc   = round(_safe_avg(rows, "discretionary"), 2)
    avg_net    = round(avg_income - avg_fixed - avg_disc, 2)

    # 硬上限: 优先用当月真实收入/固定; 没填就退回平均
    base_income = (current or {}).get("income") or avg_income
    base_fixed  = (current or {}).get("fixed_cost") or avg_fixed
    cap_raw = float(base_income) * (1 - rate) - float(base_fixed)
    hard_cap = round(max(0.0, cap_raw), 2) if base_income > 0 else None
    cap_source = "current" if current and current.get("income") else ("rolling_avg" if avg_income > 0 else None)

    # 软参考: 排除当月的最近 3 月可支配均值
    past = [r for r in rows if r["month"] != cur_month][:3]
    soft_avg = round(_safe_avg(past, "discretionary"), 2) if past else None

    return {
        "window": n,
        "avg_income": avg_income, "avg_fixed": avg_fixed, "avg_disc": avg_disc, "avg_net": avg_net,
        "current_month": cur_month,
        "current": _enrich(current) if current else None,
        "savings_rate_target": rate,
        "hard_cap": hard_cap,
        "hard_cap_source": cap_source,
        "soft_avg": soft_avg,
    }


class RateConfig(BaseModel):
    savings_rate_target: float


@router.get("/config")
async def get_config_route():
    return {"savings_rate_target": await _get_rate()}


@router.post("/config")
async def set_config_route(cfg: RateConfig):
    if cfg.savings_rate_target < 0 or cfg.savings_rate_target > 0.95:
        raise HTTPException(400, "储蓄率目标必须在 0-0.95 之间")
    await set_config(_RATE_KEY, str(cfg.savings_rate_target))
    return {"savings_rate_target": cfg.savings_rate_target}


@router.get("/{month}")
async def get_one(month: str):
    row = await get_cashflow(month)
    if not row:
        raise HTTPException(404, f"月份 {month} 无记录")
    return _enrich(row)


@router.post("")
async def upsert(entry: CashflowEntry):
    if not entry.month or len(entry.month) != 7 or entry.month[4] != '-':
        raise HTTPException(400, "month 必须是 YYYY-MM 格式")
    await upsert_cashflow(
        entry.month,
        entry.income, entry.fixed_cost, entry.discretionary,
        entry.notes or "",
    )
    row = await get_cashflow(entry.month)
    return {"message": "已保存", "entry": _enrich(row) if row else None}


@router.delete("/{month}")
async def remove(month: str):
    await delete_cashflow(month)
    return {"message": "已删除"}
