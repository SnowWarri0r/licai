"""Compute current position state from chronological buy/sell actions.

FIFO cost basis:
- BUY / ADD / BONUS → append a lot (BONUS = 送股, price=0 amount=0 只加 shares)
- SELL / REDUCE → consume from oldest lots first
- DIVIDEND → 现金分红, 直接进 realized_pnl (不动 lot 不动 cost_price)

Derived quantities:
- shares        : current total shares
- cost_price    : 综合成本法 (net invested + fees) / current shares — matches broker display
- fifo_cost_price: average price of remaining FIFO lots (no fees)
- weighted_days : capital-weighted holding days (for TVM calculations)
- lots          : surviving lots with (shares, price, trade_date)
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Iterable

from services.market_data import is_a_share

ACQUIRE = {"BUY", "ADD", "BONUS"}     # 买入 / 加仓 / 送股转增 (BONUS price=0 amount=0 只加 shares)
RELEASE = {"SELL", "REDUCE"}          # 卖出 / 减仓
INCOME  = {"DIVIDEND"}                # 现金分红 → realized_pnl (不动 lot, 不动 cost_price)

# A-share standard transaction fees
_COMMISSION_RATE = 0.0001854  # 万1.854 (user's broker rate)
_COMMISSION_MIN = 5.0         # ¥5 per trade minimum
_STAMP_RATE = 0.0005         # 0.05% sell side only (since 2023-08)
_TRANSFER_RATE = 0.00001     # 过户费 万0.1, 沪深双向都收 (深市 2022-04-29 起也收, 此前仅沪市)
# 规费 (双向收, 沪深都有)
_EXCHANGE_HANDLE_RATE = 0.0000341  # 经手费 万0.341 (2025-07-01 起下调)
_REGULATORY_FEE_RATE  = 0.00002    # 证管费 万0.2 (证监会)


def estimate_trade_fee(action_type: str, price: float, shares: int, stock_code: str = "",
                       commission_rate: float | None = None,
                       commission_min: float | None = None) -> float:
    """Estimate A-share trading fees (commission + stamp + transfer + regulatory).

    Returns total fee in yuan. Used to adjust cost basis to match broker display.
    commission_rate / commission_min default to the 招商证券 constants when not passed.
    """
    if stock_code and not is_a_share(stock_code):
        return 0.0
    # 非买入/卖出 (DIVIDEND 分红 / BONUS 送股 等) 不收费
    if action_type not in ACQUIRE and action_type not in RELEASE:
        return 0.0
    amount = price * shares
    if amount <= 0:
        return 0.0
    c_rate = _COMMISSION_RATE if commission_rate is None else commission_rate
    c_min = _COMMISSION_MIN if commission_min is None else commission_min
    commission = max(amount * c_rate, c_min)
    stamp = amount * _STAMP_RATE if action_type in RELEASE else 0.0
    # 过户费沪深双向都收 (函数开头已对非 A 股 return 0; stock_code 为空时默认按 A 股算)
    transfer = amount * _TRANSFER_RATE
    regulatory = amount * (_EXCHANGE_HANDLE_RATE + _REGULATORY_FEE_RATE)
    return commission + stamp + transfer + regulatory


def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return date.today()


def compute_position_state(
    actions: Iterable[dict],
    today: date | None = None,
    stock_code: str = "",
    commission_rate: float | None = None,
    commission_min: float | None = None,
) -> dict:
    """Process actions in chronological order using FIFO.

    Each action must have: action_type, price (float), shares (int), trade_date (str).
    Actions with missing trade_date are ordered by created_at fallback (today).

    If stock_code is provided, applies A-share trading fees (佣金/印花税/过户费) to the
    broker-style cost_price so it matches what the brokerage app displays.
    """
    if today is None:
        today = date.today()

    def sort_key(a):
        return _parse_date(a.get("trade_date") or a.get("created_at"))

    sorted_actions = sorted(actions, key=sort_key)

    def _fee_of(a, t, price, shares):
        # action.fee 非 NULL = 用户手填覆盖, 否则按券商费率自动估。无 stock_code 不计费。
        if not stock_code:
            return 0.0
        override = a.get("fee")
        return float(override) if override is not None else estimate_trade_fee(
            t, price, shares, stock_code, commission_rate, commission_min)

    # 单次按时间顺序遍历: 同时跑 FIFO + 按"持仓段"算综合成本。
    # 一段持仓 = 从份数 0→正 到再次归 0。完全清仓后再买入会开启全新一段,
    # 旧段的已实现盈亏沉淀进 realized_carry, 不再摊进新段成本 (对齐券商 App)。
    lots: list[dict] = []          # 当前段的 FIFO lots (清仓时自然清空)
    income_realized = 0.0          # 现金分红累计 (DIVIDEND), 不进 FIFO 配对
    realized_carry = 0.0           # 已平仓段的已实现 (不在当前浮动里)
    total_fees = 0.0               # 全周期手续费 (展示用)
    # 当前段累计 (清仓时重置)
    ep_buy_amt = ep_sell_amt = ep_fees = 0.0
    ep_buy_shares = ep_matched = 0
    ep_buy_fees = ep_sell_fees = 0.0
    ep_realized_excl_fees = 0.0
    running_shares = 0

    def _episode_realized():
        rf = ep_sell_fees + (ep_buy_fees * (ep_matched / ep_buy_shares) if ep_buy_shares > 0 else 0.0)
        return ep_realized_excl_fees - rf

    for a in sorted_actions:
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = int(a.get("shares", 0))
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE and shares > 0:
            # BONUS 是送股: price=0 → lot 的 shares 累加但 fifo_total 不变, cost_price 被动摊薄。
            lots.append({"shares": shares, "price": price, "trade_date": ad})
            f = _fee_of(a, t, price, shares)
            ep_buy_amt += price * shares
            ep_buy_shares += shares
            ep_buy_fees += f
            ep_fees += f
            total_fees += f
            running_shares += shares
        elif t in RELEASE and shares > 0:
            remaining = shares
            while remaining > 0 and lots:
                lot = lots[0]
                consumed = min(lot["shares"], remaining)
                ep_realized_excl_fees += (price - lot["price"]) * consumed
                ep_matched += consumed
                lot["shares"] -= consumed
                remaining -= consumed
                if lot["shares"] == 0:
                    lots.pop(0)
            f = _fee_of(a, t, price, shares)
            ep_sell_amt += price * shares
            ep_sell_fees += f
            ep_fees += f
            total_fees += f
            running_shares -= shares
            if running_shares <= 0:
                # 本段平仓: 沉淀已实现到 carry, 清空本段累计 (开启新段)
                realized_carry += _episode_realized()
                running_shares = 0
                lots = []
                ep_buy_amt = ep_sell_amt = ep_fees = 0.0
                ep_buy_shares = ep_matched = 0
                ep_buy_fees = ep_sell_fees = 0.0
                ep_realized_excl_fees = 0.0
        elif t in INCOME:
            # 现金分红: 显式 amount, 或 price(每股股息) × shares(持股数)
            amt = float(a.get("amount") or 0)
            if amt <= 0:
                amt = float(a.get("price") or 0) * int(a.get("shares") or 0)
            income_realized += amt

    # 总已实现 = 已平仓段 + 当前段已实现 + 现金分红
    realized_pnl = round(realized_carry + _episode_realized() + income_realized, 2)
    # carry = 不在当前浮动里的已实现 (已平仓段 + 分红) → 顶部总盈亏用它补, 避免和浮动重复
    realized_carry_out = round(realized_carry + income_realized, 2)

    total_shares = sum(l["shares"] for l in lots)
    if total_shares <= 0:
        return {
            "shares": 0,
            "cost_price": 0.0,
            "fifo_cost_price": 0.0,
            "weighted_days": 0,
            "lots": [],
            "realized_pnl": realized_pnl,
            "realized_carry": realized_carry_out,
            "income_realized": round(income_realized, 2),
        }

    # FIFO cost — avg of remaining lots only (当前段)
    fifo_total = sum(l["shares"] * l["price"] for l in lots)
    fifo_cost = fifo_total / total_shares

    # 综合成本法只算当前段 (清仓后重置), 不把旧段已实现摊进新成本
    net_invested = ep_buy_amt - ep_sell_amt + ep_fees
    net_cost = net_invested / total_shares if total_shares > 0 else 0.0
    # 当前段内卖在极高位导致 net_invested 变负时回退到 fifo
    if net_cost <= 0:
        net_cost = fifo_cost

    # Capital-weighted days on FIFO lots (each lot has a concrete date)
    capital_days_sum = sum(
        l["shares"] * l["price"] * max(0, (today - l["trade_date"]).days)
        for l in lots
    )
    weighted_days = capital_days_sum / fifo_total if fifo_total > 0 else 0

    return {
        "shares": total_shares,
        "cost_price": round(net_cost, 4),       # primary: 综合成本法 (matches broker)
        "fifo_cost_price": round(fifo_cost, 4), # for reference
        "total_fees": round(total_fees, 2),
        "weighted_days": int(round(weighted_days)),
        "realized_pnl": realized_pnl,
        "realized_carry": realized_carry_out,            # 已平仓段+分红 (不在浮动里, 供顶部总盈亏补)
        "income_realized": round(income_realized, 2),   # 累计现金分红 (DIVIDEND)
        "lots": [
            {"shares": l["shares"], "price": l["price"], "trade_date": l["trade_date"].isoformat()}
            for l in lots
        ],
    }
