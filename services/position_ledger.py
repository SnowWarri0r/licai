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


def _order_key(a: dict) -> tuple:
    """粗分组键: 代码 + 日期 + 方向。**同一张委托的判定不在这儿** —— 组内还要按
    trade_fills.one_order_clusters(同一分钟 + 首尾 ≤4 跳板)再聚一次。

    为什么不能只按 代码+日期+方向+成交时刻(这是旧写法): 那样完全不看价格, 于是
    9-01 生益科技 148.62 与 148.95 同在 09:32 被算成一张委托, 只收了一次 5 元最低佣金 ——
    可它们差 33 个跳板, 一张委托的分笔成交吃不掉那么多档(用户确认是两次下单)。
    另外没记成交时刻的老流水会整天同方向并成一组, 少算最低佣金, 也是旧写法的已知代价。

    换成"粗分组 + 跳板聚簇"后, 全账本只有 6 组被拆开: 5 组是没记时刻的老行(价差
    15~86 跳板, 显然是多张单)、1 组是生益。零误拆, 总费用 1745.42 → 1795.42 元
    (差额全是 5 元最低佣金该收几次)。

    仍然修不了的方向: 真的分几次下单、而价格恰好落在同一分钟 4 跳板内, 照样会被合掉。
    那一半只有券商交割单里的**合同编号**能判。真实费用可以直接填 action 的 fee 字段覆盖估算。
    """
    t = (a.get("action_type") or "")
    side = "A" if t in ACQUIRE else ("R" if t in RELEASE else "?")
    return (a.get("stock_code") or "", str(a.get("trade_date") or "")[:10], side)


def allocate_trade_fees(actions: list[dict], commission_rate: float | None = None,
                        commission_min: float | None = None) -> list[float]:
    """按「委托单」算佣金最低值, 再按成交额分摊回每一笔。返回与 actions 等长的费用列表。

    印花税/过户费/规费都是按比例收的, 逐笔算不会出错, 只有最低佣金需要合并。
    """
    c_rate = _COMMISSION_RATE if commission_rate is None else commission_rate
    c_min = _COMMISSION_MIN if commission_min is None else commission_min
    from services.trade_fills import one_order_clusters, tick_for_code
    groups: dict[tuple, list[int]] = {}
    for i, a in enumerate(actions):
        groups.setdefault(_order_key(a), []).append(i)

    out = [0.0] * len(actions)
    for key, coarse in groups.items():
        code = key[0]
        if code and not is_a_share(code):
            continue
        t0 = (actions[coarse[0]].get("action_type") or "")
        if t0 not in ACQUIRE and t0 not in RELEASE:
            continue
        # 粗分组里再按「同一分钟 + ≤4 跳板」切出真正的委托单(判据与复盘共用一份实现)
        rows = [actions[i] for i in coarse]
        clusters = one_order_clusters(rows, price_of=lambda a: a.get("price"),
                                      time_of=lambda a: a.get("trade_time"),
                                      tick=tick_for_code(code))
        for cl in clusters:
            idxs = [coarse[j] for j in cl]
            amts = [float(actions[i].get("price") or 0) * int(actions[i].get("shares") or 0)
                    for i in idxs]
            total = sum(amts)
            if total <= 0:
                continue
            commission = max(total * c_rate, c_min)     # ← 整张委托只收一次最低佣金
            stamp_rate = _STAMP_RATE if t0 in RELEASE else 0.0
            per_unit = stamp_rate + _TRANSFER_RATE + _EXCHANGE_HANDLE_RATE + _REGULATORY_FEE_RATE
            for i, amt in zip(idxs, amts):
                out[i] = commission * (amt / total) + amt * per_unit
    return out


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
    cash_div_per_share: float = 0.0,
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

    # 估算费用先按「委托单」整体算一遍再分摊回每笔 —— 逐笔各取一次 5 元最低佣金
    # 会把同一张委托的分笔成交重复收费(实测这份流水多收 101.22 元)。
    _alloc = allocate_trade_fees(
        [dict(a, stock_code=a.get("stock_code") or stock_code) for a in sorted_actions],
        commission_rate, commission_min) if stock_code else []

    def _fee_of(a, t, price, shares, idx=None):
        # action.fee 非 NULL = 用户手填覆盖; 否则用调用方按每笔 broker 预算的 _auto_fee;
        # 都没有再用分摊后的估算。无 stock_code 不计费。
        if not stock_code:
            return 0.0
        override = a.get("fee")
        if override is not None:
            return float(override)
        if a.get("_auto_fee") is not None:
            return float(a["_auto_fee"])
        return _alloc[idx] if (idx is not None and idx < len(_alloc)) else 0.0

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
    flat_date = None               # 最近一次卖到 0 的日期; 同日买回则本段延续

    def _episode_realized():
        rf = ep_sell_fees + (ep_buy_fees * (ep_matched / ep_buy_shares) if ep_buy_shares > 0 else 0.0)
        return ep_realized_excl_fees - rf

    for _i, a in enumerate(sorted_actions):
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = int(a.get("shares", 0))
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE and shares > 0:
            # 卖光后再买入: 隔夜才算新一段, 日内买回视为同一段延续 —— 券商就是这么算的
            # (与 external_ledger 的口径统一; 那边注释写明是招商实测行为)。
            # 之前 A股 这边是"卖到 0 立刻结算", 于是日内清仓再买会把本段盈亏踢出浮动
            # 变成落袋, 新仓成本从买回价重新起算, 跟券商 App 对不上。
            if flat_date is not None:
                if ad > flat_date:
                    realized_carry += _episode_realized()
                    ep_buy_amt = ep_sell_amt = ep_fees = 0.0
                    ep_buy_shares = ep_matched = 0
                    ep_buy_fees = ep_sell_fees = 0.0
                    ep_realized_excl_fees = 0.0
                flat_date = None
            # BONUS 是送股: price=0 → lot 的 shares 累加但 fifo_total 不变, cost_price 被动摊薄。
            lots.append({"shares": shares, "price": price, "trade_date": ad})
            f = _fee_of(a, t, price, shares, _i)
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
            f = _fee_of(a, t, price, shares, _i)
            ep_sell_amt += price * shares
            ep_sell_fees += f
            ep_fees += f
            total_fees += f
            running_shares -= shares
            if running_shares <= 0:
                # 卖光了: 先只记下平仓日, 本段累计留着 —— 当天买回就接着算同一段,
                # 隔夜没买回才在下次买入(或收尾)时结算进 carry。
                running_shares = 0
                lots = []
                flat_date = ad
        elif t in INCOME:
            # 现金分红: 显式 amount, 或 price(每股股息) × shares(持股数)
            amt = float(a.get("amount") or 0)
            if amt <= 0:
                amt = float(a.get("price") or 0) * int(a.get("shares") or 0)
            income_realized += amt

    # 收尾: 还停在空仓状态说明这一段确实平掉了(没有当日买回), 结算进 carry。
    # 不结算的话 realized_carry 会漏掉最后一段, 顶栏「已实现」少一块。
    if flat_date is not None and running_shares == 0:
        realized_carry += _episode_realized()
        ep_buy_amt = ep_sell_amt = ep_fees = 0.0
        ep_buy_shares = ep_matched = 0
        ep_buy_fees = ep_sell_fees = 0.0
        ep_realized_excl_fees = 0.0

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

    # 摊薄成本(对齐券商): 持有期间累计每股现金分红从每股成本里扣
    div_ps = max(0.0, float(cash_div_per_share or 0.0))
    net_cost_diluted = max(0.0, net_cost - div_ps)
    fifo_cost_diluted = max(0.0, fifo_cost - div_ps)

    # Capital-weighted days on FIFO lots (each lot has a concrete date)
    capital_days_sum = sum(
        l["shares"] * l["price"] * max(0, (today - l["trade_date"]).days)
        for l in lots
    )
    weighted_days = capital_days_sum / fifo_total if fifo_total > 0 else 0

    return {
        "shares": total_shares,
        "cost_price": round(net_cost_diluted, 4),       # primary: 综合成本法 + 分红摊薄 (matches broker)
        "cost_price_raw": round(net_cost, 4),           # 未摊薄(仅买卖+费), 供对照
        "div_per_share": round(div_ps, 4),              # 持有期累计每股现金分红(已摊进 cost_price)
        "fifo_cost_price": round(fifo_cost_diluted, 4), # for reference
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
