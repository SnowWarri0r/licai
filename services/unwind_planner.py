"""Unwind planner: budget allocation + tranche generation + IRR feasibility."""
from __future__ import annotations
from services.economics import required_exit_price


FUNDAMENTAL_WEIGHTS = {"green": 1.0, "yellow": 0.6, "red": 0.2}
SHARE_LOT_SIZE = 100  # A-share minimum lot


def compute_priority(
    cost_gap_pct: float,
    fundamental_score: float,
    volatility_ratio: float,
    trend_strength: float = 0.0,
) -> float:
    """How urgently this stock needs budget allocation.

    Formula:
        priority = (cost_gap_pct × fundamental × volatility) / (1 + 2 × trend_strength)

    Higher priority = gets more budget.
    """
    numerator = max(cost_gap_pct, 0) * fundamental_score * volatility_ratio
    penalty = 1 + 2 * max(trend_strength, 0)
    return numerator / penalty


def allocate_budgets(stocks: list[dict], total_budget: float) -> dict[str, float]:
    """Distribute total_budget across stocks proportional to each stock's priority.

    If all priorities are 0, fall back to even split.
    """
    if not stocks:
        return {}

    total_priority = sum(s["priority"] for s in stocks)
    if total_priority <= 0:
        share = total_budget / len(stocks)
        return {s["stock_code"]: round(share, 2) for s in stocks}

    return {
        s["stock_code"]: round(s["priority"] / total_priority * total_budget, 2)
        for s in stocks
    }


def _round_lot(shares: int) -> int:
    """Round shares down to nearest A-share lot (100), minimum 100."""
    return max(SHARE_LOT_SIZE, (shares // SHARE_LOT_SIZE) * SHARE_LOT_SIZE)


def generate_tranches(
    current_price: float,
    atr: float,
    supports: list[float],
    lower_bb: float | None,
    historical_low: float,
    budget: float,
) -> list[dict]:
    """Build a ladder of 3-4 tranches from shallowest to deepest.

    Tranche design:
        档1 (any health):    current - 1 * ATR
        档2 (any health):    nearest verified support level below current
        档3 (yellow+ req):   max(current - 2.5*ATR, lower_bb)
        档4 (green req) opt: historical_low + small buffer (only if budget > 5000)

    Pyramid share allocation: 1:2:3:5 ratio.
    """
    if current_price <= 0 or budget <= 0:
        return []

    candidates = []

    # 档1 — 1 ATR below
    if atr > 0:
        candidates.append({
            "trigger_price": round(current_price - atr, 2),
            "requires_health": "any",
            "source": "-1×ATR",
        })

    # 档2 — nearest support below current
    supports_below = sorted([s for s in supports if s < current_price * 0.995], reverse=True)
    if supports_below:
        candidates.append({
            "trigger_price": round(supports_below[0], 2),
            "requires_health": "any",
            "source": "支撑位",
        })

    # 档3 — deeper (ATR-based or BB lower)
    deep_price = current_price - 2.5 * atr
    if lower_bb and lower_bb < deep_price:
        deep_price = lower_bb
    if deep_price > 0:
        candidates.append({
            "trigger_price": round(deep_price, 2),
            "requires_health": "yellow",
            "source": "深跌位 (2.5×ATR / 布林下轨)",
        })

    # 档4 — historical low area (optional, only if budget big enough)
    if budget > 5000 and historical_low > 0 and historical_low < current_price * 0.7:
        candidates.append({
            "trigger_price": round(historical_low * 1.02, 2),
            "requires_health": "green",
            "source": "历史低点",
        })

    # Dedupe by trigger_price (coarse grain — 0.1 units)
    seen = set()
    unique = []
    for c in candidates:
        key = round(c["trigger_price"], 1)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    # Sort shallowest first (highest price first)
    candidates = sorted(unique, key=lambda x: -x["trigger_price"])

    # Greedy affordability-aware allocation (respects A-share 100-lot minimum).
    # Strategy:
    #   1) Try pyramid-like ratio 1:2:3:5 — deeper tranches get more lots.
    #      Each tranche's shares = ratio × 100. If cumulative cost fits budget, use it.
    #   2) If pyramid can't fit all, fall back to flat 100 shares per tranche,
    #      dropping deepest tranches until it fits.
    #   3) If even 1 lot of the shallowest tranche is unaffordable, return [].
    ratios = [1, 2, 3, 5][:len(candidates)]

    def _try_allocation(per_tranche_shares: list[int]):
        """Return list of tranches that fit in budget, dropping deepest ones as needed."""
        kept = []
        spent = 0.0
        for c, shares in zip(candidates, per_tranche_shares):
            cost = c["trigger_price"] * shares
            if spent + cost <= budget:
                kept.append((c, shares))
                spent += cost
            # Stop at first unaffordable — deeper tranches are progressively costlier anyway
            else:
                break
        return kept

    # Try pyramid first
    pyramid_shares = [r * SHARE_LOT_SIZE for r in ratios]
    kept = _try_allocation(pyramid_shares)

    # If pyramid fits < all tranches, try flat 100-share allocation for full coverage
    if len(kept) < len(candidates):
        flat_shares = [SHARE_LOT_SIZE] * len(candidates)
        flat_kept = _try_allocation(flat_shares)
        if len(flat_kept) > len(kept):
            kept = flat_kept

    result = []
    for idx, (c, shares) in enumerate(kept, start=1):
        result.append({
            "idx": idx,
            "trigger_price": c["trigger_price"],
            "shares": shares,
            "requires_health": c["requires_health"],
            "source": c["source"],
        })
    return result


def minimum_required_budget(
    current_price: float,
    atr: float,
    supports: list[float],
    lower_bb: float | None,
    historical_low: float,
) -> dict:
    """Compute what budget tiers enable different tranche coverage levels.

    Returns:
        {
            "min_1_tranche": cost of 1 lot at shallowest,
            "min_all_flat": cost of 1 lot × each candidate tranche (flat plan),
            "min_all_pyramid": cost of full 1:2:3:5 pyramid,
            "candidate_count": number of distinct tranches available,
        }
    """
    # Replicate candidate selection (without the budget>5000 gate for 档4)
    cands = []
    if atr > 0:
        cands.append(round(current_price - atr, 2))
    sb = sorted([s for s in supports if s < current_price * 0.995], reverse=True)
    if sb:
        cands.append(round(sb[0], 2))
    deep = current_price - 2.5 * atr
    if lower_bb and lower_bb < deep:
        deep = lower_bb
    if deep > 0:
        cands.append(round(deep, 2))
    if historical_low > 0 and historical_low < current_price * 0.7:
        cands.append(round(historical_low * 1.02, 2))
    # Dedupe
    seen = set()
    uniq = []
    for p in cands:
        k = round(p, 1)
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    uniq.sort(reverse=True)  # shallowest first

    if not uniq:
        return {"min_1_tranche": 0.0, "min_all_flat": 0.0, "min_all_pyramid": 0.0, "candidate_count": 0}

    ratios = [1, 2, 3, 5][:len(uniq)]
    min_1 = uniq[0] * SHARE_LOT_SIZE
    min_all_flat = sum(p * SHARE_LOT_SIZE for p in uniq)
    min_all_pyramid = sum(p * r * SHARE_LOT_SIZE for p, r in zip(uniq, ratios))
    return {
        "min_1_tranche": round(min_1, 2),
        "min_all_flat": round(min_all_flat, 2),
        "min_all_pyramid": round(min_all_pyramid, 2),
        "candidate_count": len(uniq),
    }


def check_tranche_feasibility(
    old_shares: int,
    old_cost: float,
    add_shares: int,
    add_price: float,
    historical_high_3y: float,
    patience_years: float = 2.0,
    risk_free_rate: float = 0.03,
) -> dict:
    """Sanity check: after this tranche, can I realistically break even?

    Computes new blended cost + required TVM-adjusted exit price. If that
    price exceeds stock's 3-year high, tranche is unfeasible.
    """
    total = old_shares + add_shares
    if total <= 0:
        return {"feasible": False, "reason": "no position"}
    new_cost = (old_shares * old_cost + add_shares * add_price) / total
    required = required_exit_price(new_cost, patience_years, risk_free_rate)
    return {
        "feasible": bool(required <= historical_high_3y),
        "new_cost": round(float(new_cost), 4),
        "required_price": round(float(required), 2),
        "historical_high_3y": round(float(historical_high_3y), 2),
    }
