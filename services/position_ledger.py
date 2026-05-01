"""Compute current position state from chronological buy/sell actions.

FIFO cost basis:
- BUY / ADD / T_BUY → append a lot
- SELL / REDUCE / T_SELL → consume from oldest lots first

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

ACQUIRE = {"BUY", "ADD", "T_BUY"}
RELEASE = {"SELL", "REDUCE", "T_SELL"}

# A-share standard transaction fees
_COMMISSION_RATE = 0.0001854  # 万1.854 (user's broker rate)
_COMMISSION_MIN = 5.0         # ¥5 per trade minimum
_STAMP_RATE = 0.0005         # 0.05% sell side only (since 2023-08)
_TRANSFER_RATE = 0.00001     # 0.001% both sides, Shanghai stocks only (6xxxxx)


def _is_shanghai(stock_code: str) -> bool:
    return stock_code.startswith("6") or stock_code.startswith("9")


def estimate_trade_fee(action_type: str, price: float, shares: int, stock_code: str = "") -> float:
    """Estimate A-share trading fees (commission + stamp + transfer).

    Returns total fee in yuan. Used to adjust cost basis to match broker display.
    """
    if stock_code and not is_a_share(stock_code):
        return 0.0
    amount = price * shares
    if amount <= 0:
        return 0.0
    commission = max(amount * _COMMISSION_RATE, _COMMISSION_MIN)
    stamp = amount * _STAMP_RATE if action_type in RELEASE else 0.0
    transfer = amount * _TRANSFER_RATE if _is_shanghai(stock_code) else 0.0
    return commission + stamp + transfer


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

    lots: list[dict] = []  # each: {shares, price, trade_date}
    for a in sorted_actions:
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = int(a.get("shares", 0))
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE and shares > 0:
            lots.append({"shares": shares, "price": price, "trade_date": ad})
        elif t in RELEASE and shares > 0:
            remaining = shares
            while remaining > 0 and lots:
                lot = lots[0]
                if lot["shares"] <= remaining:
                    remaining -= lot["shares"]
                    lots.pop(0)
                else:
                    lot["shares"] -= remaining
                    remaining = 0
            # If remaining > 0 and no lots, we've "oversold" — ignored for now

    total_shares = sum(l["shares"] for l in lots)
    if total_shares <= 0:
        return {
            "shares": 0,
            "cost_price": 0.0,
            "fifo_cost_price": 0.0,
            "weighted_days": 0,
            "lots": [],
        }

    # FIFO cost — avg of remaining lots only
    fifo_total = sum(l["shares"] * l["price"] for l in lots)
    fifo_cost = fifo_total / total_shares

    # 综合成本法 (broker display) — (总买 - 总卖 + 所有手续费) / 当前持股
    total_buy_amt = 0.0
    total_sell_amt = 0.0
    total_fees = 0.0
    for a in sorted_actions:
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = int(a.get("shares", 0))
        if t in ACQUIRE and shares > 0:
            total_buy_amt += price * shares
            if stock_code:
                total_fees += estimate_trade_fee(t, price, shares, stock_code)
        elif t in RELEASE and shares > 0:
            total_sell_amt += price * shares
            if stock_code:
                total_fees += estimate_trade_fee(t, price, shares, stock_code)
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
        "lots": [
            {"shares": l["shares"], "price": l["price"], "trade_date": l["trade_date"].isoformat()}
            for l in lots
        ],
    }
