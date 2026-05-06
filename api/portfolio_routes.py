"""Portfolio management REST endpoints."""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models import HoldingCreate, HoldingUpdate, HoldingResponse
from database import (
    get_all_holdings, get_holding, add_holding, update_holding, delete_holding,
    get_position_actions, add_position_action, update_position_action, delete_position_action,
    get_unwind_plan, get_tranches, mark_tranche_executed,
)
from services.market_data import (
    get_realtime_quotes, get_stock_name, get_stock_sector,
    normalize_stock_code, split_stock_code, get_fx_info, is_a_share,
)
from services.position_ledger import compute_position_state

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class ActionCreate(BaseModel):
    action_type: str  # BUY / SELL / ADD / REDUCE / T_BUY / T_SELL
    price: float
    shares: int
    trade_date: Optional[str] = None  # YYYY-MM-DD
    note: Optional[str] = ""


class ActionUpdate(BaseModel):
    action_type: Optional[str] = None
    price: Optional[float] = None
    shares: Optional[int] = None
    trade_date: Optional[str] = None
    note: Optional[str] = None


async def _recompute_holding(stock_code: str):
    """Rebuild holding shares/cost_price from FIFO ledger."""
    actions = await get_position_actions(stock_code, limit=500)
    state = compute_position_state(actions, stock_code=stock_code)
    if state["shares"] > 0:
        await update_holding(stock_code, shares=state["shares"], cost_price=state["cost_price"])
    else:
        # No shares left — keep holding row but set shares=0
        await update_holding(stock_code, shares=0, cost_price=0)


@router.get("")
async def list_holdings() -> list[HoldingResponse]:
    holdings = await get_all_holdings()
    if not holdings:
        return []

    codes = [h["stock_code"] for h in holdings]
    quotes = await get_realtime_quotes(codes)

    # 行业并行查 (24h cache, 首次会发外网请求)
    import asyncio
    sector_results = await asyncio.gather(
        *(get_stock_sector(c) for c in codes), return_exceptions=True,
    )
    sector_map = {}
    for code, sec in zip(codes, sector_results):
        sector_map[code] = sec if (isinstance(sec, str) and sec) else None

    result = []
    for h in holdings:
        code = h["stock_code"]
        q = quotes.get(code)
        current_price = q["price"] if q else None
        change_pct = q["change_pct"] if q else None
        market = (q or {}).get("market") or split_stock_code(code)[0]
        currency = (q or {}).get("currency") or ("HKD" if market == "HK" else "USD" if market == "US" else "CNY")
        fx_info = {
            "rate": (q or {}).get("fx_rate") or 1.0,
            "source": (q or {}).get("fx_source") or ("CNY" if currency == "CNY" else ""),
            "time": (q or {}).get("fx_time") or "",
        }
        if currency != "CNY" and not (q or {}).get("fx_rate"):
            fx_info = get_fx_info(currency)
        fx_rate = float(fx_info.get("rate") or 1.0)

        # Auto-fix empty stock name
        if not h["stock_name"] and q and q.get("stock_name"):
            await update_holding(code, stock_name=q["stock_name"])
            h["stock_name"] = q["stock_name"]

        unrealized_pnl = None
        pnl_pct = None
        market_value = None
        original_cost_value = round(h["cost_price"] * h["shares"], 2)
        original_market_value = None
        cost_value = round(original_cost_value * fx_rate, 2)
        if current_price and current_price > 0:
            original_market_value = round(current_price * h["shares"], 2)
            market_value = round(original_market_value * fx_rate, 2)
            unrealized_pnl = round((original_market_value - original_cost_value) * fx_rate, 2)
            if h["cost_price"] > 0:
                pnl_pct = round((current_price - h["cost_price"]) / h["cost_price"] * 100, 2)

        result.append(HoldingResponse(
            stock_code=code,
            stock_name=h["stock_name"] or (q["stock_name"] if q else ""),
            market=market,
            currency=currency,
            shares=h["shares"],
            cost_price=h["cost_price"],
            current_price=current_price,
            fx_rate=fx_rate,
            fx_time=fx_info.get("time") or "",
            fx_source=fx_info.get("source") or "",
            price_change_pct=change_pct,
            unrealized_pnl=unrealized_pnl,
            pnl_pct=pnl_pct,
            original_cost_value=original_cost_value,
            original_market_value=original_market_value,
            cost_value=cost_value,
            market_value=market_value,
            sector=sector_map.get(code),
        ))

    return result


@router.get("/realized")
async def realized_pnl():
    """Per-stock 已实现盈亏 (含已清仓 + 部分减仓), 从 position_actions 计算.

    返回每只股票的 realized_pnl 以及 grand total. 用于把割肉记录滚进总盈亏.
    """
    db_helper = get_position_actions  # noqa: F841
    # 拿到 ALL 出现过的 stock_code (不局限于当前 holdings)
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute(
            "SELECT DISTINCT stock_code FROM position_actions ORDER BY stock_code"
        )
        rows = await cursor.fetchall()
        codes = [r[0] for r in rows]
    finally:
        await db.close()

    holdings_map = {h["stock_code"]: h for h in await get_all_holdings()}
    items: list[dict] = []
    total = 0.0
    for code in codes:
        actions = await get_position_actions(code, limit=500)
        if not actions:
            continue
        state = compute_position_state(actions, stock_code=code)
        rp = float(state.get("realized_pnl") or 0)
        total += rp
        h = holdings_map.get(code) or {}
        items.append({
            "stock_code": code,
            "stock_name": h.get("stock_name") or "",
            "realized_pnl": rp,
            "still_holding": (state.get("shares") or 0) > 0,
        })
    items.sort(key=lambda x: x["realized_pnl"])
    return {
        "items": items,
        "total_realized_pnl": round(total, 2),
        "count": len(items),
    }


@router.get("/concentration")
async def sector_concentration():
    """Compute per-sector market value concentration.

    Returns:
        {
            "total_value": ...,
            "sectors": [{"sector": "有色金属", "value": ..., "pct": 1.0, "stocks": ["000630","601212","603993"]}, ...],
            "max_concentration": 1.0,
            "level": "critical" | "warning" | "ok",
            "message": "..."
        }
    """
    holdings = await get_all_holdings()
    holdings = [h for h in holdings if is_a_share(h["stock_code"])]
    if not holdings:
        return {"total_value": 0, "sectors": [], "max_concentration": 0, "level": "ok", "message": ""}

    codes = [h["stock_code"] for h in holdings]
    quotes = await get_realtime_quotes(codes)

    # Look up sectors in parallel
    import asyncio
    sector_tasks = [get_stock_sector(c) for c in codes]
    sectors = await asyncio.gather(*sector_tasks, return_exceptions=True)
    sector_map = {}
    for code, sec in zip(codes, sectors):
        if isinstance(sec, Exception) or not sec:
            sec = "其他"
        sector_map[code] = sec

    total_value = 0.0
    by_sector: dict[str, dict] = {}
    for h in holdings:
        code = h["stock_code"]
        q = quotes.get(code)
        if not q or q["price"] <= 0:
            price = h["cost_price"]
        else:
            price = q["price"]
        mv = price * h["shares"]
        total_value += mv
        sec = sector_map[code]
        if sec not in by_sector:
            by_sector[sec] = {"sector": sec, "value": 0.0, "stocks": []}
        by_sector[sec]["value"] += mv
        by_sector[sec]["stocks"].append({
            "stock_code": code,
            "stock_name": h.get("stock_name", ""),
            "value": round(mv, 2),
        })

    sector_list = sorted(by_sector.values(), key=lambda x: -x["value"])
    for s in sector_list:
        s["pct"] = round(s["value"] / total_value, 4) if total_value > 0 else 0
        s["value"] = round(s["value"], 2)

    max_pct = sector_list[0]["pct"] if sector_list else 0

    if max_pct >= 0.80:
        level = "critical"
        top = sector_list[0]
        message = f"⚠️ {top['sector']} 占比 {int(top['pct']*100)}% — 组合=单一板块，加仓等同加杠杆"
    elif max_pct >= 0.50:
        level = "warning"
        top = sector_list[0]
        message = f"⚠️ {top['sector']} 占比 {int(top['pct']*100)}% — 集中度偏高，考虑分散到其他板块"
    else:
        level = "ok"
        message = ""

    return {
        "total_value": round(total_value, 2),
        "sectors": sector_list,
        "max_concentration": round(max_pct, 4),
        "level": level,
        "message": message,
    }


@router.post("")
async def create_holding(data: HoldingCreate):
    stock_code = normalize_stock_code(data.stock_code)
    existing = await get_holding(stock_code)
    if existing:
        raise HTTPException(400, f"持仓 {stock_code} 已存在")

    name = data.stock_name
    if not name:
        name = await get_stock_name(stock_code)

    # 1) 用裸成交价建持仓 (data.cost_price)
    await add_holding(stock_code, name, data.shares, data.cost_price)
    # 2) 同时写一笔 BUY action,然后重算综合成本 (会自动加佣金/印花税/过户费)
    await add_position_action(
        stock_code, "BUY", data.cost_price, data.shares,
        note="initial (auto)",
        trade_date=data.trade_date,
    )
    await _recompute_holding(stock_code)
    return {"message": "添加成功", "stock_code": stock_code, "stock_name": name}


@router.put("/{stock_code}")
async def modify_holding(stock_code: str, data: HoldingUpdate):
    stock_code = normalize_stock_code(stock_code)
    existing = await get_holding(stock_code)
    if not existing:
        raise HTTPException(404, f"持仓 {stock_code} 不存在")

    kwargs = {}
    if data.stock_name is not None:
        kwargs["stock_name"] = data.stock_name
    if data.shares is not None:
        kwargs["shares"] = data.shares
    if data.cost_price is not None:
        kwargs["cost_price"] = data.cost_price

    await update_holding(stock_code, **kwargs)
    return {"message": "更新成功"}


@router.delete("/{stock_code}")
async def remove_holding(stock_code: str):
    stock_code = normalize_stock_code(stock_code)
    existing = await get_holding(stock_code)
    if not existing:
        raise HTTPException(404, f"持仓 {stock_code} 不存在")

    await delete_holding(stock_code)
    return {"message": "删除成功"}


# --- Position Actions (buy/sell history) ---

@router.get("/{stock_code}/actions")
async def list_actions(stock_code: str):
    """List all buy/sell actions for a stock, chronologically."""
    stock_code = normalize_stock_code(stock_code)
    return await get_position_actions(stock_code, limit=500)


_ACQUIRE = {"BUY", "ADD", "T_BUY"}


async def _auto_match_tranche(stock_code: str, action_type: str, price: float) -> dict | None:
    """If the action is an ACQUIRE and a pending tranche's trigger matches closely,
    mark that tranche as executed. Returns the matched tranche dict or None."""
    if action_type not in _ACQUIRE:
        return None
    plan = await get_unwind_plan(stock_code)
    if not plan:
        return None
    tranches = await get_tranches(stock_code)
    pending = [t for t in tranches if t["status"] == "pending"]
    if not pending:
        return None
    # Match within ±5% of trigger, pick closest
    eligible = [
        (abs(t["trigger_price"] - price) / t["trigger_price"], t)
        for t in pending
        if t["trigger_price"] > 0 and abs(t["trigger_price"] - price) / t["trigger_price"] < 0.05
    ]
    if not eligible:
        return None
    eligible.sort(key=lambda x: x[0])
    best = eligible[0][1]
    await mark_tranche_executed(best["id"], price)
    return best


@router.post("/{stock_code}/actions")
async def create_action(stock_code: str, data: ActionCreate):
    """Add a new buy/sell action. Recomputes holding aggregate.

    If this is a BUY/ADD/T_BUY that matches a pending tranche's trigger price
    (within ±5%), auto-mark that tranche as executed so the plan view stays in sync.
    """
    stock_code = normalize_stock_code(stock_code)
    holding = await get_holding(stock_code)
    if not holding:
        raise HTTPException(404, f"持仓 {stock_code} 不存在")
    await add_position_action(
        stock_code=stock_code,
        action_type=data.action_type,
        price=data.price,
        shares=data.shares,
        trade_date=data.trade_date,
        note=data.note or "",
    )
    await _recompute_holding(stock_code)
    matched = await _auto_match_tranche(stock_code, data.action_type, data.price)
    return {
        "message": "记录已添加",
        "matched_tranche": {"idx": matched["idx"], "trigger_price": matched["trigger_price"]} if matched else None,
    }


@router.put("/actions/{action_id}")
async def modify_action(action_id: int, data: ActionUpdate):
    """Edit an existing action. Recomputes holding aggregate."""
    await update_position_action(
        action_id,
        action_type=data.action_type,
        price=data.price,
        shares=data.shares,
        trade_date=data.trade_date,
        note=data.note,
    )
    # Find the stock_code for recomputation
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute("SELECT stock_code FROM position_actions WHERE id = ?", (action_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    if row:
        await _recompute_holding(row["stock_code"])
    return {"message": "已更新"}


@router.delete("/actions/{action_id}")
async def remove_action(action_id: int):
    """Delete an action. Recomputes holding aggregate."""
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute("SELECT stock_code FROM position_actions WHERE id = ?", (action_id,))
        row = await cursor.fetchone()
    finally:
        await db.close()
    await delete_position_action(action_id)
    if row:
        await _recompute_holding(row["stock_code"])
    return {"message": "已删除"}
