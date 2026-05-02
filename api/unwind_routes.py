"""Unwind cockpit REST endpoints."""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import config
from database import (
    get_all_holdings, get_holding, update_holding,
    get_unwind_plan, get_all_unwind_plans, save_unwind_plan, delete_unwind_plan,
    update_unwind_used_budget,
    get_tranches, get_tranche, add_tranche, clear_tranches, mark_tranche_executed,
    mark_tranche_sold_back, clear_tranche_sold_back,
    log_position_action, get_position_actions,
)
from services.position_ledger import compute_position_state
from services.market_data import (
    get_realtime_quotes, get_historical_data, get_commodity_for_stock,
    get_benchmark_return, is_a_share, normalize_stock_code,
)
from services.economics import (
    real_cost as calc_real_cost,
    opportunity_cost,
    daily_opportunity_cost,
    hold_vs_cut_npv,
    estimate_recovery_probability,
)
from services.unwind_planner import (
    compute_priority, allocate_budgets, generate_tranches, check_tranche_feasibility,
    minimum_required_budget, FUNDAMENTAL_WEIGHTS,
)
from services.fundamental_score import fetch_health_snapshot
from services.technical_analysis import get_full_analysis
from models import UnwindPlanSave, TrancheExecute, TrancheItem

router = APIRouter(prefix="/api/unwind", tags=["unwind"])


def _days_held(created_at_str: str) -> int:
    """Compute days from created_at string to now. Default 1 if missing/malformed."""
    if not created_at_str:
        return 1
    try:
        s = str(created_at_str).replace("T", " ").split(".")[0]
        dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        return max(1, (datetime.now() - dt).days)
    except Exception:
        return 1


async def _build_plan_response(h: dict, q: dict) -> dict:
    """Build the full plan response payload for one holding.

    Uses FIFO ledger for accurate weighted holding days and cost basis.
    """
    code = h["stock_code"]
    current = q["price"] if q else 0.0

    # Prefer FIFO-derived state from position_actions (accurate over multiple buys)
    actions = await get_position_actions(code, limit=500)
    first_buy_date = None
    if actions:
        state = compute_position_state(actions, stock_code=code)
        shares = state["shares"] if state["shares"] > 0 else h["shares"]
        cost = state["cost_price"] if state["shares"] > 0 else h["cost_price"]
        holding_days = state["weighted_days"] or 1
        # Earliest buy among remaining lots is the right anchor for benchmark comparison
        lots = state.get("lots") or []
        if lots:
            first_buy_date = min(l["trade_date"] for l in lots)
    else:
        shares = h["shares"]
        cost = h["cost_price"]
        holding_days = _days_held(h.get("created_at", ""))

    # Economics
    real_c = calc_real_cost(cost, holding_days, config.risk_free_rate)
    trapped = cost * shares
    opp_cost = opportunity_cost(trapped, holding_days, config.risk_free_rate)
    daily_opp = daily_opportunity_cost(trapped, config.risk_free_rate)

    nominal_loss_pct = round((current - cost) / cost * 100, 2) if cost > 0 else 0.0
    real_loss_pct = round((current - real_c) / real_c * 100, 2) if real_c > 0 else 0.0

    # Progress — use 60-day history for lowest; also derive ATR and annualized vol
    hist = await get_historical_data(code, days=120)
    lowest_60d = current
    atr = 0.0
    annualized_vol = 0.0
    if hist is not None and not hist.empty:
        try:
            lowest_60d = float(hist["最低"].astype(float).min())
        except Exception:
            pass
        try:
            analysis = get_full_analysis(hist)
            atr = float(analysis.get("atr", 0) or 0)
        except Exception:
            atr = 0.0
        try:
            import numpy as _np
            closes = hist["收盘"].astype(float).to_numpy()
            if len(closes) >= 20:
                log_returns = _np.diff(_np.log(closes))
                daily_std = float(_np.std(log_returns, ddof=1))
                annualized_vol = daily_std * (252 ** 0.5)
        except Exception:
            annualized_vol = 0.0

    price_progress = 0.0
    if cost > lowest_60d:
        price_progress = max(0.0, min(1.0, (current - lowest_60d) / (cost - lowest_60d)))

    cost_progress = 0.0  # requires historical cost tracking — MVP uses 0

    # Plan & tranches
    plan = await get_unwind_plan(code)
    total_budget = plan["total_budget"] if plan else 0.0
    tranche_rows = await get_tranches(code)

    # used_budget — derived from post-plan position actions (not the stored field),
    # so manual buys entered in history also consume budget, not only tranche executions.
    used_budget = 0.0
    if plan and actions:
        plan_date = str(plan.get("created_at", ""))[:10]
        net_invested = 0.0
        for a in actions:
            ad = str(a.get("trade_date") or a.get("created_at", ""))[:10]
            if plan_date and ad < plan_date:
                continue
            amount = float(a.get("price", 0)) * int(a.get("shares", 0))
            t = a.get("action_type", "")
            if t in {"BUY", "ADD", "T_BUY"}:
                net_invested += amount
            elif t in {"SELL", "REDUCE", "T_SELL"}:
                net_invested -= amount
        used_budget = round(max(0.0, net_invested), 2)

    # Fundamental health
    fundamental = await fetch_health_snapshot(code, h.get("stock_name", ""))

    # Budget feasibility breakdown — so UI can flag overspend or underfunding
    pending_cost = sum(
        t["trigger_price"] * t["shares"]
        for t in tranche_rows if t["status"] == "pending"
    )
    full_plan_cost = sum(t["trigger_price"] * t["shares"] for t in tranche_rows)
    remaining_budget = round(total_budget - used_budget, 2)
    overspent = max(0.0, used_budget - total_budget)
    under_funded = max(0.0, pending_cost - remaining_budget)
    budget_status = "ok"
    if overspent > 0:
        budget_status = "overspent"
    elif under_funded > 0.01:
        budget_status = "underfunded"

    # Sell targets per tranche — 做T 回收 (recycle capital after adding)
    # Multiplier grows with depth: 档1=+1 ATR, 档2=+1.5, 档3=+2, 档4=+3
    sell_multipliers = [1.0, 1.5, 2.0, 3.0]
    tranches_payload = []
    for t in tranche_rows:
        idx = t["idx"]
        k = sell_multipliers[min(idx - 1, len(sell_multipliers) - 1)]
        base = t.get("executed_price") or t["trigger_price"]
        sell_target = round(base + k * atr, 2) if atr > 0 else None
        tranches_payload.append({
            "id": t["id"],
            "idx": idx,
            "trigger_price": t["trigger_price"],
            "shares": t["shares"],
            "requires_health": t["requires_health"],
            "status": t["status"],
            "executed_price": t.get("executed_price"),
            "sold_back_price": t.get("sold_back_price"),
            "sold_back_at": t.get("sold_back_at"),
            "sell_target": sell_target,
            "sell_reason": f"+{k}×ATR 回收做T",
        })

    # Unwind exit price — the TVM-adjusted break-even. Selling at/above this clears the loss.
    unwind_exit_price = round(real_c, 2)
    can_unwind_now = current >= unwind_exit_price if unwind_exit_price > 0 else False

    # --- Benchmark: 沪深300 同期实际表现 (realized) ---
    benchmark = None
    if first_buy_date:
        try:
            benchmark = await get_benchmark_return(str(first_buy_date))
        except Exception:
            benchmark = None
    # capital invested originally, value if it were in 沪深300 instead
    principal = cost * shares  # 近似 "当时投入的钱"
    bench_value = None
    bench_gap = None
    stock_return_pct = None
    if benchmark and benchmark.get("start_close", 0) > 0:
        bench_value = round(principal * (1 + benchmark["return_pct"]), 2)
        current_value_for_bench = current * shares
        bench_gap = round(bench_value - current_value_for_bench, 2)  # +ve = HS300 would be richer
        stock_return_pct = round((current - cost) / cost, 4) if cost > 0 else 0.0

    # --- 回本概率 (基于 GBM 首达模型 + 基本面调整 drift) ---
    drift_map = {"green": 0.15, "yellow": 0.0, "red": -0.10}
    drift = drift_map.get(fundamental["level"], 0.0)
    rec = estimate_recovery_probability(
        current_price=current,
        target_price=real_c,
        annualized_vol=annualized_vol if annualized_vol > 0 else 0.35,  # 默认35%防止空数据
        years=config.patience_years,
        drift=drift,
    )
    recovery_prob = rec["probability"]

    # --- NPV: 继续持有 vs 割肉换指数 ---
    current_value = current * shares
    expected_recovery_value = real_c * shares
    npv = hold_vs_cut_npv(
        current_value=current_value,
        expected_recovery_value=expected_recovery_value,
        recovery_probability=recovery_prob,
        holding_years=config.patience_years,
        index_annual_return=config.index_annual_return,
    )
    npv_analysis = {
        **npv,
        "recovery_probability": recovery_prob,
        "holding_years_assumed": config.patience_years,
        "index_annual_return": config.index_annual_return,
        "current_value": round(current_value, 2),
        "expected_recovery_value": round(expected_recovery_value, 2),
        "recovery_model": rec,
    }

    return {
        "stock_code": code,
        "stock_name": h.get("stock_name", ""),
        "cost_price": cost,
        "current_price": current,
        "shares": shares,
        "holding_days": holding_days,
        "atr": round(atr, 3),
        "nominal_loss_pct": nominal_loss_pct,
        "real_cost": round(real_c, 4),
        "real_loss_pct": real_loss_pct,
        "opportunity_cost_accumulated": round(opp_cost, 2),
        "daily_opportunity_cost": round(daily_opp, 2),
        "price_progress": round(price_progress, 3),
        "cost_progress": round(cost_progress, 3),
        "total_budget": total_budget,
        "used_budget": used_budget,
        "remaining_budget": remaining_budget,
        "pending_tranche_cost": round(pending_cost, 2),
        "full_plan_cost": round(full_plan_cost, 2),
        "overspent": round(overspent, 2),
        "under_funded": round(under_funded, 2),
        "budget_status": budget_status,
        "unwind_exit_price": unwind_exit_price,
        "can_unwind_now": can_unwind_now,
        "tranches": tranches_payload,
        "fundamental": fundamental,
        "npv_analysis": npv_analysis,
        "benchmark": {
            **(benchmark or {}),
            "principal": round(principal, 2) if principal else 0.0,
            "bench_value": bench_value,
            "bench_gap": bench_gap,
            "stock_return_pct": stock_return_pct,
        } if benchmark else None,
    }


@router.get("/plans")
async def list_plans():
    """Get unwind status for all holdings."""
    holdings = await get_all_holdings()
    holdings = [h for h in holdings if is_a_share(h["stock_code"])]
    if not holdings:
        return []
    codes = [h["stock_code"] for h in holdings]
    quotes = await get_realtime_quotes(codes)

    result = []
    for h in holdings:
        q = quotes.get(h["stock_code"])
        if not q or q["price"] <= 0:
            continue
        result.append(await _build_plan_response(h, q))
    return result


@router.post("/recommend/{stock_code}")
async def recommend(stock_code: str, total_budget: Optional[float] = None):
    """System-generated budget + tranche recommendation for one stock.
    Does NOT save. User must explicitly PUT to save.
    """
    stock_code = normalize_stock_code(stock_code)
    if not is_a_share(stock_code):
        raise HTTPException(400, "解套档位暂只支持 A 股")
    h = await get_holding(stock_code)
    if not h:
        raise HTTPException(404, "Holding not found")

    quotes = await get_realtime_quotes([stock_code])
    q = quotes.get(stock_code)
    if not q or q["price"] <= 0:
        raise HTTPException(400, "No quote available")

    hist = await get_historical_data(stock_code, days=120)
    if hist is None or hist.empty:
        raise HTTPException(400, "No historical data")

    analysis = get_full_analysis(hist)
    atr = analysis.get("atr", 0)
    bb = analysis.get("bollinger", {})
    sr = analysis.get("support_resistance", {})
    ma = analysis.get("ma", {})

    # Priority for this single stock
    cost_gap = max(0, (h["cost_price"] - q["price"]) / h["cost_price"])
    fundamental = await fetch_health_snapshot(stock_code, h.get("stock_name", ""))
    fund_weight = FUNDAMENTAL_WEIGHTS[fundamental["level"]]
    volatility = atr / q["price"] if q["price"] > 0 else 0
    trend = 0.0
    ma5, ma20 = ma.get(5), ma.get(20)
    if ma5 and ma20 and ma5 > q["price"] and ma20 > q["price"]:
        trend = 0.5

    priority = compute_priority(cost_gap, fund_weight, volatility, trend)

    # Default per-stock budget — if user didn't pass total_budget, use config default
    budget_for_this = total_budget if total_budget else config.default_unwind_budget / 3

    # Historical extremes
    try:
        hist_low = float(hist["最低"].astype(float).min())
    except Exception:
        hist_low = q["price"] * 0.7
    try:
        hist_high = float(hist["最高"].astype(float).max())
    except Exception:
        hist_high = q["price"] * 1.5

    tranches = generate_tranches(
        current_price=q["price"],
        atr=atr,
        supports=sr.get("support", []),
        lower_bb=bb.get("lower"),
        historical_low=hist_low,
        budget=budget_for_this,
    )

    # Annotate each tranche with feasibility — cumulative after prior tranches
    cumulative_shares = h["shares"]
    cumulative_cost = h["cost_price"]
    for t in tranches:
        feas = check_tranche_feasibility(
            old_shares=cumulative_shares,
            old_cost=cumulative_cost,
            add_shares=t["shares"],
            add_price=t["trigger_price"],
            historical_high_3y=hist_high,
            patience_years=config.patience_years,
            risk_free_rate=config.risk_free_rate,
        )
        t["feasibility"] = feas
        if feas["feasible"]:
            cumulative_cost = feas["new_cost"]
            cumulative_shares += t["shares"]

    return {
        "stock_code": stock_code,
        "priority": round(priority, 4),
        "recommended_budget": round(budget_for_this, 2),
        "tranches": tranches,
        "fundamental": fundamental,
    }


@router.put("/plans/{stock_code}")
async def save_plan(stock_code: str, data: UnwindPlanSave):
    """Save (create or update) an unwind plan and its tranches."""
    h = await get_holding(stock_code)
    if not h:
        raise HTTPException(404, "Holding not found")

    await save_unwind_plan(stock_code, data.total_budget)

    if data.tranches is not None:
        await clear_tranches(stock_code)
        for t in data.tranches:
            await add_tranche(
                stock_code=stock_code,
                idx=t.idx,
                trigger_price=t.trigger_price,
                shares=t.shares,
                requires_health=t.requires_health,
            )

    return {"message": "saved"}


@router.delete("/plans/{stock_code}")
async def delete_plan(stock_code: str):
    await delete_unwind_plan(stock_code)
    return {"message": "deleted"}


@router.get("/fundamental/{stock_code}")
async def fundamental(stock_code: str):
    h = await get_holding(stock_code)
    name = h.get("stock_name", "") if h else ""
    return await fetch_health_snapshot(stock_code, name)


@router.post("/tranches/{tranche_id}/execute")
async def execute_tranche(tranche_id: int, data: TrancheExecute):
    """Mark a tranche as executed. Also updates holding cost and logs action."""
    tranche = await get_tranche(tranche_id)
    if not tranche:
        raise HTTPException(404, "Tranche not found")

    executed_shares = data.executed_shares or tranche["shares"]
    executed_price = data.executed_price

    h = await get_holding(tranche["stock_code"])
    if not h:
        raise HTTPException(404, "Holding not found")

    new_shares = h["shares"] + executed_shares
    new_cost = (h["shares"] * h["cost_price"] + executed_shares * executed_price) / new_shares
    await update_holding(tranche["stock_code"], shares=new_shares, cost_price=round(new_cost, 4))

    await mark_tranche_executed(tranche_id, executed_price)
    await log_position_action(
        tranche["stock_code"],
        action_type="ADD",
        price=executed_price,
        shares=executed_shares,
        tranche_id=tranche_id,
        note=f"Executed tranche #{tranche['idx']}",
    )

    # Bump used_budget
    plan = await get_unwind_plan(tranche["stock_code"])
    if plan:
        new_used = plan["used_budget"] + executed_shares * executed_price
        await update_unwind_used_budget(tranche["stock_code"], new_used)

    return {"message": "executed", "new_cost": round(new_cost, 4), "new_shares": new_shares}


class TrancheSellBack(BaseModel):
    sold_price: float


@router.post("/tranches/{tranche_id}/sell-back")
async def sell_back_tranche(tranche_id: int, data: TrancheSellBack):
    """Record the T-sell (做T 回收) for an executed tranche.

    This:
    - Logs a T_SELL position action at the provided price
    - Stamps sold_back_price/sold_back_at on the tranche (not a state change,
      the tranche stays 'executed' for the buy leg)
    - Reduces holding shares by the tranche's share count
    - FIFO ledger + derived used_budget handle the accounting
    """
    tranche = await get_tranche(tranche_id)
    if not tranche:
        raise HTTPException(404, "Tranche not found")
    if tranche["status"] != "executed":
        raise HTTPException(400, "Tranche buy leg not executed yet")
    if tranche.get("sold_back_price"):
        raise HTTPException(400, "Tranche already marked as sold-back")

    shares = tranche["shares"]
    sold_price = data.sold_price

    h = await get_holding(tranche["stock_code"])
    if not h:
        raise HTTPException(404, "Holding not found")

    # Record the T_SELL in position_actions — FIFO ledger will reconcile holding state
    from datetime import datetime as _dt
    await log_position_action(
        tranche["stock_code"],
        action_type="T_SELL",
        price=sold_price,
        shares=shares,
        tranche_id=tranche_id,
        note=f"Sell-back tranche #{tranche['idx']} @ {sold_price}",
    )
    await mark_tranche_sold_back(tranche_id, sold_price)

    # Recompute holding aggregate from FIFO ledger (primary source of truth)
    from services.position_ledger import compute_position_state
    actions = await get_position_actions(tranche["stock_code"], limit=500)
    state = compute_position_state(actions, stock_code=tranche["stock_code"])
    if state["shares"] >= 0:
        await update_holding(
            tranche["stock_code"],
            shares=state["shares"],
            cost_price=state["cost_price"] if state["shares"] > 0 else 0,
        )

    return {"message": "sold back", "new_shares": state["shares"], "new_cost": state["cost_price"]}


@router.delete("/tranches/{tranche_id}/sell-back")
async def undo_sell_back(tranche_id: int):
    """Undo the sell-back: removes the T_SELL action and clears sold_back."""
    tranche = await get_tranche(tranche_id)
    if not tranche:
        raise HTTPException(404, "Tranche not found")
    # Find and remove the T_SELL action linked to this tranche
    from database import get_db
    db = await get_db()
    try:
        await db.execute(
            "DELETE FROM position_actions WHERE tranche_id = ? AND action_type = 'T_SELL'",
            (tranche_id,),
        )
        await db.commit()
    finally:
        await db.close()
    await clear_tranche_sold_back(tranche_id)
    from services.position_ledger import compute_position_state
    actions = await get_position_actions(tranche["stock_code"], limit=500)
    state = compute_position_state(actions, stock_code=tranche["stock_code"])
    if state["shares"] > 0:
        await update_holding(
            tranche["stock_code"],
            shares=state["shares"],
            cost_price=state["cost_price"],
        )
    return {"message": "sell-back undone"}


@router.get("/total-budget")
async def get_total_budget():
    """Get previously saved total unwind budget."""
    from database import get_config
    val = await get_config("total_unwind_budget")
    return {"total_budget": float(val) if val else 0}


@router.post("/apply-allocation")
async def apply_allocation(payload: dict):
    """Save the total budget + per-stock allocations as unwind plans.
    Body: {total_budget: float, allocations: [{stock_code, budget}]}
    """
    from database import set_config
    total = payload.get("total_budget", 0)
    allocations = payload.get("allocations", [])
    await set_config("total_unwind_budget", str(total))
    for a in allocations:
        code = a.get("stock_code")
        budget = a.get("budget", 0)
        if code and budget > 0:
            existing = await get_unwind_plan(code)
            used = existing["used_budget"] if existing else 0
            await save_unwind_plan(code, budget)
            # Preserve used_budget if already had one
            if used > 0:
                await update_unwind_used_budget(code, used)
    return {"message": "applied", "count": len(allocations)}


@router.post("/allocate")
async def allocate(total_budget: float):
    """Recommend per-stock budget allocation across all holdings."""
    holdings = await get_all_holdings()
    holdings = [h for h in holdings if is_a_share(h["stock_code"])]
    if not holdings:
        return []

    codes = [h["stock_code"] for h in holdings]
    quotes = await get_realtime_quotes(codes)

    stocks = []
    for h in holdings:
        code = h["stock_code"]
        q = quotes.get(code)
        if not q or q["price"] <= 0:
            continue
        hist = await get_historical_data(code, days=60)
        if hist is None or hist.empty:
            continue
        analysis = get_full_analysis(hist)
        atr = analysis.get("atr", 0)
        ma = analysis.get("ma", {})

        cost_gap = max(0, (h["cost_price"] - q["price"]) / h["cost_price"])
        fund = await fetch_health_snapshot(code, h.get("stock_name", ""))
        fund_w = FUNDAMENTAL_WEIGHTS[fund["level"]]
        vol = atr / q["price"] if q["price"] > 0 else 0
        trend = 0.0
        ma5, ma20 = ma.get(5), ma.get(20)
        if ma5 and ma20 and ma5 > q["price"] and ma20 > q["price"]:
            trend = 0.5

        stocks.append({
            "stock_code": code,
            "stock_name": h.get("stock_name", ""),
            "priority": compute_priority(cost_gap, fund_w, vol, trend),
        })

    allocation = allocate_budgets(stocks, total_budget)
    for s in stocks:
        s["budget"] = allocation.get(s["stock_code"], 0)
    return stocks
