"""外部资产 FIFO 记账 — 给基金/加密/理财/现金算实现盈亏 + 当前持有成本.

设计:
- FUND/CRYPTO: 按 shares 配对 (BUY/ADD 加 lot, REDEEM 消耗 lot)
- WEALTH/CASH: 按 principal CNY 配对 (DEPOSIT 加 lot, WITHDRAW 消耗 lot, INTEREST/DIVIDEND 直接进 realized)
- BOT: 不走 ledger (OKX 端管理)

每次 REDEEM/WITHDRAW 触发 realized_pnl += proceeds - matched_lot_cost.
"""
from __future__ import annotations
from datetime import date, datetime
from typing import Iterable


SHARE_BASED = ("FUND", "CRYPTO")
PRINCIPAL_BASED = ("WEALTH", "CASH")

ACQUIRE_TYPES = {"BUY", "ADD", "DEPOSIT"}
RELEASE_TYPES = {"REDEEM", "WITHDRAW"}
INCOME_TYPES = {"INTEREST", "DIVIDEND"}  # 直接进 realized, 不消耗 lot


def _parse_date(s: str | None) -> date:
    if not s:
        return date.today()
    s = str(s)[:10]
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return date.today()


def compute_external_state(
    actions: Iterable[dict],
    asset_type: str,
) -> dict:
    """从流水算 cost_amount / shares / realized_pnl / 剩余 lots.

    返回:
      cost_amount: 当前持仓的累计成本 (CNY, 综合本金法)
      shares: 当前份额 (仅 FUND/CRYPTO 有意义)
      realized_pnl: 截至今天的累计已实现盈亏 (含 INTEREST/DIVIDEND)
      lots: 剩余 lots
      total_acquired_cost: 历史总投入成本 (含已赎回部分, 用于审计)
      total_release_proceeds: 历史总赎回 (审计)
    """
    if asset_type == "BOT":
        # BOT 不走 ledger
        return {
            "cost_amount": 0.0,
            "shares": 0.0,
            "realized_pnl": 0.0,
            "lots": [],
            "total_acquired_cost": 0.0,
            "total_release_proceeds": 0.0,
            "income_realized": 0.0,
        }

    # 只算 confirmed 状态的 action; pending (OTC 基金 T+1 待确认) 不进 ledger
    confirmed = [a for a in actions if (a.get("status") or "confirmed") == "confirmed"]
    sorted_actions = sorted(confirmed, key=lambda a: (
        _parse_date(a.get("trade_date") or a.get("created_at")),
        a.get("id") or 0,
    ))

    is_share_based = asset_type in SHARE_BASED

    # lots: each = {amount, shares, unit_price, date}
    lots: list[dict] = []
    realized_pnl = 0.0
    income_realized = 0.0
    total_acquired_cost = 0.0
    total_release_proceeds = 0.0

    for a in sorted_actions:
        t = (a.get("action_type") or "").upper()
        amount = float(a.get("amount") or 0)
        shares = float(a.get("shares")) if a.get("shares") is not None else None
        unit_price = float(a.get("unit_price")) if a.get("unit_price") is not None else None
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE_TYPES:
            if is_share_based:
                # 必须知道 shares (没填 unit_price 时由 amount/shares 推)
                lot_shares = shares
                if lot_shares is None and unit_price and unit_price > 0 and amount > 0:
                    lot_shares = amount / unit_price
                if lot_shares is None or lot_shares <= 0:
                    # 没法定 lot, 跳过 (用户后面应该会补 shares)
                    continue
                lot_unit = unit_price if unit_price else (amount / lot_shares if lot_shares > 0 else 0)
                lots.append({"amount": amount, "shares": lot_shares, "unit_price": lot_unit, "date": ad})
            else:
                # principal-based
                if amount <= 0:
                    continue
                lots.append({"amount": amount, "shares": None, "unit_price": None, "date": ad})
            total_acquired_cost += amount

        elif t in RELEASE_TYPES:
            proceeds = amount if amount > 0 else 0
            if is_share_based:
                consume_shares = shares if shares is not None else (
                    proceeds / unit_price if unit_price and unit_price > 0 else None
                )
                if consume_shares is None or consume_shares <= 0:
                    continue
                # FIFO 消耗 lot
                remaining = consume_shares
                consumed_cost = 0.0
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    if lot["shares"] <= remaining + 1e-9:
                        consumed_cost += lot["shares"] * (lot["unit_price"] or 0)
                        remaining -= lot["shares"]
                        lots.pop(0)
                    else:
                        consumed_cost += remaining * (lot["unit_price"] or 0)
                        lot["shares"] -= remaining
                        lot["amount"] = lot["shares"] * (lot["unit_price"] or 0)
                        remaining = 0
                # proceeds 推断 (没填 amount 时用 shares × unit_price)
                if proceeds == 0 and unit_price and unit_price > 0:
                    proceeds = consume_shares * unit_price
                realized_pnl += proceeds - consumed_cost
                total_release_proceeds += proceeds
            else:
                # principal-based: 按 CNY 配对
                if proceeds <= 0:
                    continue
                remaining = proceeds
                consumed_cost = 0.0
                while remaining > 1e-9 and lots:
                    lot = lots[0]
                    if lot["amount"] <= remaining + 1e-9:
                        consumed_cost += lot["amount"]
                        remaining -= lot["amount"]
                        lots.pop(0)
                    else:
                        consumed_cost += remaining
                        lot["amount"] -= remaining
                        remaining = 0
                # 注意: principal-based 下 proceeds 可能 > 配对成本 (利息收益)
                realized_pnl += proceeds - consumed_cost
                total_release_proceeds += proceeds

        elif t in INCOME_TYPES:
            # INTEREST / DIVIDEND 直接进 realized, 不动 lot
            realized_pnl += amount
            income_realized += amount

    cost_amount = sum(l["amount"] for l in lots)
    shares_total = sum(l["shares"] for l in lots if l.get("shares") is not None) if is_share_based else 0.0

    return {
        "cost_amount": round(cost_amount, 4),
        "shares": round(shares_total, 6) if is_share_based else 0.0,
        "realized_pnl": round(realized_pnl, 2),
        "income_realized": round(income_realized, 2),
        "lots": [
            {
                "amount": round(l["amount"], 4),
                "shares": round(l["shares"], 6) if l.get("shares") is not None else None,
                "unit_price": round(l["unit_price"], 6) if l.get("unit_price") is not None else None,
                "date": l["date"].isoformat(),
            } for l in lots
        ],
        "total_acquired_cost": round(total_acquired_cost, 2),
        "total_release_proceeds": round(total_release_proceeds, 2),
    }
