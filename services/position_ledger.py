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
_TRANSFER_RATE = 0.00001     # 0.001% both sides, Shanghai stocks only (6xxxxx)
# 规费 (双向收, 沪深都有)
_EXCHANGE_HANDLE_RATE = 0.0000341  # 经手费 万0.341 (2025-07-01 起下调)
_REGULATORY_FEE_RATE  = 0.00002    # 证管费 万0.2 (证监会)


def _is_shanghai(stock_code: str) -> bool:
    return stock_code.startswith("6") or stock_code.startswith("9")


def estimate_trade_fee(action_type: str, price: float, shares: int, stock_code: str = "") -> float:
    """Estimate A-share trading fees (commission + stamp + transfer + regulatory).

    Returns total fee in yuan. Used to adjust cost basis to match broker display.
    """
    if stock_code and not is_a_share(stock_code):
        return 0.0
    # 非买入/卖出 (DIVIDEND 分红 / BONUS 送股 等) 不收费
    if action_type not in ACQUIRE and action_type not in RELEASE:
        return 0.0
    amount = price * shares
    if amount <= 0:
        return 0.0
    commission = max(amount * _COMMISSION_RATE, _COMMISSION_MIN)
    stamp = amount * _STAMP_RATE if action_type in RELEASE else 0.0
    transfer = amount * _TRANSFER_RATE if _is_shanghai(stock_code) else 0.0
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

    # Pass 1: 累计金额 / 份数 / 手续费 (用于综合成本法 + 实现盈亏的费用摊销)
    total_buy_amt = 0.0
    total_sell_amt = 0.0
    total_fees = 0.0
    total_buy_shares = 0
    total_buy_fees = 0.0
    total_sell_fees = 0.0
    for a in sorted_actions:
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = int(a.get("shares", 0))
        # action.fee 非 NULL = 用户手填覆盖, 否则按券商费率自动估
        override = a.get("fee")
        if t in ACQUIRE and shares > 0:
            total_buy_amt += price * shares
            total_buy_shares += shares
            if stock_code:
                fee = float(override) if override is not None else estimate_trade_fee(t, price, shares, stock_code)
                total_fees += fee
                total_buy_fees += fee
        elif t in RELEASE and shares > 0:
            total_sell_amt += price * shares
            if stock_code:
                fee = float(override) if override is not None else estimate_trade_fee(t, price, shares, stock_code)
                total_fees += fee
                total_sell_fees += fee

    # Pass 2: FIFO 配对 lots, 顺便累计 realized_pnl_excl_fees + matched_buy_shares
    lots: list[dict] = []  # each: {shares, price, trade_date}
    realized_pnl_excl_fees = 0.0
    income_realized = 0.0    # 现金分红累计 (DIVIDEND), 不进 FIFO 配对
    matched_buy_shares = 0
    for a in sorted_actions:
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = int(a.get("shares", 0))
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE and shares > 0:
            # BONUS 是送股: price=0 → lot 的 shares 累加但 fifo_total 不变,
            # cost_price 被动摊薄 (除数变大). estimate_trade_fee 在 amount<=0 时返回 0.
            lots.append({"shares": shares, "price": price, "trade_date": ad})
        elif t in RELEASE and shares > 0:
            remaining = shares
            while remaining > 0 and lots:
                lot = lots[0]
                consumed = min(lot["shares"], remaining)
                realized_pnl_excl_fees += (price - lot["price"]) * consumed
                matched_buy_shares += consumed
                lot["shares"] -= consumed
                remaining -= consumed
                if lot["shares"] == 0:
                    lots.pop(0)
        elif t in INCOME:
            # 现金分红: 写法兼容两种
            #   显式 amount (前端有 amount 字段时)
            #   或 price × shares (price=每股股息, shares=持股数, 跟"10 派 2 元 × 100 股 = 20 元"对齐)
            amt = float(a.get("amount") or 0)
            if amt <= 0:
                amt = float(a.get("price") or 0) * int(a.get("shares") or 0)
            income_realized += amt

    # 已实现盈亏 = FIFO 配对结果 - 卖出手续费 - 买入手续费按 matched / 总买 比例摊销 + 现金分红
    realized_fees = total_sell_fees
    if total_buy_shares > 0:
        realized_fees += total_buy_fees * (matched_buy_shares / total_buy_shares)
    realized_pnl = round(realized_pnl_excl_fees - realized_fees + income_realized, 2)

    total_shares = sum(l["shares"] for l in lots)
    if total_shares <= 0:
        return {
            "shares": 0,
            "cost_price": 0.0,
            "fifo_cost_price": 0.0,
            "weighted_days": 0,
            "lots": [],
            "realized_pnl": realized_pnl,
            "income_realized": round(income_realized, 2),
        }

    # FIFO cost — avg of remaining lots only
    fifo_total = sum(l["shares"] * l["price"] for l in lots)
    fifo_cost = fifo_total / total_shares

    net_invested = total_buy_amt - total_sell_amt + total_fees
    net_cost = net_invested / total_shares if total_shares > 0 else 0.0
    # If user sold at extreme profit and net_invested becomes negative, fall back to fifo
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
        "income_realized": round(income_realized, 2),   # 累计现金分红 (DIVIDEND)
        "lots": [
            {"shares": l["shares"], "price": l["price"], "trade_date": l["trade_date"].isoformat()}
            for l in lots
        ],
    }
