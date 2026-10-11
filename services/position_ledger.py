"""Compute current position state from chronological buy/sell actions.

FIFO cost basis:
- BUY / ADD / BONUS → append a lot (BONUS = 送股, price=0 amount=0 只加 shares)
- SELL / REDUCE → consume from oldest lots first
- DIVIDEND → 现金分红, 直接进 realized_pnl (不动 lot 不动 cost_price)
- SPLIT → 拆股/合股(港美股): 比例记在 price(新股数/旧股数, >1 拆股、<1 合股), shares 记 0;
  每个 lot 股数×比例、单价÷比例, 成本与现金都不动

港美股账本是原币, 另并行一套人民币口径: 每笔按自己的 fx_rate(成交日汇率, 没有就用当前汇率)折算,
lot 记住买入时的汇率, 于是已实现/持仓成本里含本金的汇兑损益(*_cny 字段)。A 股汇率恒为 1。

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
SPLIT   = "SPLIT"                     # 拆股/合股: 比例见 split_ratio

# A-share standard transaction fees
_COMMISSION_RATE = 0.00025  # 万2.5 (user's broker rate)
_COMMISSION_MIN = 5.0         # ¥5 per trade minimum
_STAMP_RATE = 0.0005         # 0.05% sell side only (since 2023-08)
_TRANSFER_RATE = 0.00001     # 过户费 万0.1, 沪深双向都收 (深市 2022-04-29 起也收, 此前仅沪市)
# 规费 (双向收, 沪深都有)
_EXCHANGE_HANDLE_RATE = 0.0000341  # 经手费 万0.341 (2025-07-01 起下调)
_REGULATORY_FEE_RATE  = 0.00002    # 证管费 万0.2 (证监会)


def estimate_trade_fee(action_type: str, price: float, shares: float, stock_code: str = "",
                       commission_rate: float | None = None,
                       commission_min: float | None = None,
                       trade_date: str | None = None, broker: str | None = None,
                       fee_overrides: str | None = None) -> float:
    """Estimate A-share trading fees (commission + stamp + transfer + regulatory).

    Returns total fee in yuan. Used to adjust cost basis to match broker display.
    commission_rate / commission_min default to the 招商证券 constants when not passed.
    港美股按 trade_date 当时的规费(SEC/TAF 分段)和 broker 的收费方案算, 不传日期才用今天。
    """
    # 非买入/卖出 (DIVIDEND 分红 / BONUS 送股 等) 不收费
    if action_type not in ACQUIRE and action_type not in RELEASE:
        return 0.0
    mkt = _foreign_market(stock_code)
    if mkt:                                      # 港美股: 原币, 按券商收费方案(默认盈立)
        from services.market_fees import order_fee, schedules
        return order_fee(mkt, "sell" if action_type in RELEASE else "buy", price * shares, shares,
                         str(trade_date or "")[:10] or date.today().isoformat(),
                         broker=broker, sched=schedules(fee_overrides))
    if stock_code and not is_a_share(stock_code):
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


def _foreign_market(code: str) -> str | None:
    c = (code or "").upper()
    return "HK" if c.startswith("HK.") else "US" if c.startswith("US.") else None


def _order_key(a: dict) -> tuple:
    """粗分组键: 代码 + 日期 + 方向。**同一张委托的判定不在这儿** —— 组内还要按
    trade_fills.one_order_clusters(同一分钟 + 首尾 ≤4 跳板)再聚一次。

    为什么不能只按 代码+日期+方向+成交时刻(这是旧写法): 那样完全不看价格, 于是
    9-01 长江电力 148.62 与 148.95 同在 09:32 被算成一张委托, 只收了一次 5 元最低佣金 ——
    可它们差 33 个跳板, 一张委托的分笔成交吃不掉那么多档(用户确认是两次下单)。
    另外没记成交时刻的老流水会整天同方向并成一组, 少算最低佣金, 也是旧写法的已知代价。

    换成"粗分组 + 跳板聚簇"后, 全账本只有 6 组被拆开: 5 组是没记时刻的老行(价差
    15~86 跳板, 显然是多张单)、1 组是长电。零误拆, 总费用 1745.42 → 1795.42 元
    (差额全是 5 元最低佣金该收几次)。

    仍然修不了的方向: 真的分几次下单、而价格恰好落在同一分钟 4 跳板内, 照样会被合掉。
    那一半只有券商交割单里的**合同编号**能判。真实费用可以直接填 action 的 fee 字段覆盖估算。
    """
    t = (a.get("action_type") or "")
    side = "A" if t in ACQUIRE else ("R" if t in RELEASE else "?")
    return (a.get("stock_code") or "", str(a.get("trade_date") or "")[:10], side)


def allocate_trade_fees(actions: list[dict], commission_rate: float | None = None,
                        commission_min: float | None = None, fee_overrides: str | None = None) -> list[float]:
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
        mkt = _foreign_market(code)
        if code and not mkt and not is_a_share(code):
            continue
        t0 = (actions[coarse[0]].get("action_type") or "")
        if t0 not in ACQUIRE and t0 not in RELEASE:
            continue
        # 粗分组里再按「同一分钟 + ≤4 跳板」切出真正的委托单(判据与复盘共用一份实现)
        rows = [actions[i] for i in coarse]
        clusters = one_order_clusters(rows, price_of=lambda a: a.get("price"),
                                      time_of=lambda a: a.get("trade_time"),
                                      tick=tick_for_code(code, float(rows[0].get("price") or 0) or None))
        if mkt:                                  # 港美股: 一张委托整体算(平台费/最低佣金/封顶按单), 再按成交额分摊
            from services.market_fees import order_fee, schedules
            sched = schedules(fee_overrides)
            for cl in clusters:
                idxs = [coarse[j] for j in cl]
                amts = [float(actions[i].get("price") or 0) * float(actions[i].get("shares") or 0) for i in idxs]
                total, sh = sum(amts), sum(float(actions[i].get("shares") or 0) for i in idxs)
                if total <= 0:
                    continue
                fee = order_fee(mkt, "sell" if t0 in RELEASE else "buy", total, sh, key[1],
                                broker=actions[idxs[0]].get("broker"), sched=sched)
                for i, amt in zip(idxs, amts):
                    out[i] = fee * (amt / total)
            continue
        for cl in clusters:
            idxs = [coarse[j] for j in cl]
            amts = [float(actions[i].get("price") or 0) * float(actions[i].get("shares") or 0)
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


def split_ratio(a: dict) -> float:
    """SPLIT 行的拆合股比例(新股数/旧股数)。position_actions 记在 price(shares 为 0);
    场外账本(external_asset_actions)沿用 shares 记倍数、price 为空 —— 两种写法都认。"""
    try:
        r = float(a.get("price") or 0) or float(a.get("shares") or 0)
    except (TypeError, ValueError):
        return 0.0
    return r if r > 0 else 0.0


def _action_fx(a: dict, fx_now) -> float:
    """这笔流水折人民币的汇率: 有成交日汇率用它, 否则用当前汇率(A 股传进来的 fx_now 是 1)。"""
    try:
        v = float(a.get("fx_rate") or 0)
    except (TypeError, ValueError):
        v = 0.0
    return v if v > 0 else fx_now()


def compute_position_state(
    actions: Iterable[dict],
    today: date | None = None,
    stock_code: str = "",
    commission_rate: float | None = None,
    commission_min: float | None = None,
    cash_div_per_share: float = 0.0,
    fx_now: float | None = None,
) -> dict:
    """Process actions in chronological order using FIFO.

    Each action must have: action_type, price (float), shares (int), trade_date (str).
    Actions with missing trade_date are ordered by created_at fallback (today).

    If stock_code is provided, applies A-share trading fees (佣金/印花税/过户费) to the
    broker-style cost_price so it matches what the brokerage app displays.

    fx_now: 港美股当前汇率(没记 fx_rate 的流水按它折人民币); 不传且用得到时现取 code_fx。
    """
    if today is None:
        today = date.today()

    def sort_key(a):
        return _parse_date(a.get("trade_date") or a.get("created_at"))

    sorted_actions = sorted(actions, key=sort_key)

    foreign = _foreign_market(stock_code or next((a.get("stock_code") for a in sorted_actions
                                                  if a.get("stock_code")), "")) is not None
    _fx_cur: list = [1.0 if not foreign else fx_now]

    def _now_fx() -> float:
        if _fx_cur[0] is None:                    # 懒取: 每笔都记了 fx_rate 就不用打网络
            from services.market_data import code_fx
            _fx_cur[0] = code_fx(stock_code or next(a.get("stock_code") for a in sorted_actions
                                                    if a.get("stock_code")))
        return _fx_cur[0]

    def _fx_of(a) -> float:
        return _action_fx(a, _now_fx) if foreign else 1.0

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
    # 每个金额累计都有一份 _c 孪生(人民币口径, 每笔按自己的汇率折); A 股汇率 1, 两份相等。
    lots: list[dict] = []          # 当前段的 FIFO lots (清仓时自然清空); fx = 买入时汇率
    income_realized = income_realized_c = 0.0   # 现金分红累计 (DIVIDEND, 税后), 不进 FIFO 配对
    dividend_tax = 0.0             # 港美股分红预扣税累计(原币)
    realized_carry = realized_carry_c = 0.0     # 已平仓段的已实现 (不在当前浮动里)
    fx_realized = 0.0              # 已实现里的汇兑部分: 卖出份额的买入本金 × (卖出汇率 − 买入汇率)
    total_fees = 0.0               # 全周期手续费 (展示用)
    # 当前段累计 (清仓时重置)
    ep_buy_amt = ep_sell_amt = ep_fees = 0.0
    ep_buy_amt_c = ep_sell_amt_c = ep_fees_c = 0.0
    ep_buy_shares = ep_matched = 0
    ep_buy_fees = ep_sell_fees = ep_buy_fees_c = ep_sell_fees_c = 0.0
    ep_realized_excl_fees = ep_realized_excl_fees_c = 0.0
    running_shares = 0
    flat_date = None               # 最近一次卖到 0 的日期; 同日买回则本段延续

    def _episode_realized():
        rf = ep_sell_fees + (ep_buy_fees * (ep_matched / ep_buy_shares) if ep_buy_shares > 0 else 0.0)
        return ep_realized_excl_fees - rf

    def _episode_realized_c():
        rf = ep_sell_fees_c + (ep_buy_fees_c * (ep_matched / ep_buy_shares) if ep_buy_shares > 0 else 0.0)
        return ep_realized_excl_fees_c - rf

    for _i, a in enumerate(sorted_actions):
        t = a.get("action_type", "")
        price = float(a.get("price", 0))
        shares = float(a.get("shares") or 0)
        ad = _parse_date(a.get("trade_date") or a.get("created_at"))

        if t in ACQUIRE and shares > 0:
            fx = _fx_of(a)
            # 卖光后再买入: 隔夜才算新一段, 日内买回视为同一段延续 —— 券商就是这么算的
            # (与 external_ledger 的口径统一; 那边注释写明是招商实测行为)。
            # 之前 A股 这边是"卖到 0 立刻结算", 于是日内清仓再买会把本段盈亏踢出浮动
            # 变成落袋, 新仓成本从买回价重新起算, 跟券商 App 对不上。
            if flat_date is not None:
                if ad > flat_date:
                    realized_carry += _episode_realized()
                    realized_carry_c += _episode_realized_c()
                    ep_buy_amt = ep_sell_amt = ep_fees = 0.0
                    ep_buy_amt_c = ep_sell_amt_c = ep_fees_c = 0.0
                    ep_buy_shares = ep_matched = 0
                    ep_buy_fees = ep_sell_fees = ep_buy_fees_c = ep_sell_fees_c = 0.0
                    ep_realized_excl_fees = ep_realized_excl_fees_c = 0.0
                flat_date = None
            # BONUS 是送股: price=0 → lot 的 shares 累加但 fifo_total 不变, cost_price 被动摊薄。
            lots.append({"shares": shares, "price": price, "fx": fx, "trade_date": ad})
            f = _fee_of(a, t, price, shares, _i)
            ep_buy_amt += price * shares
            ep_buy_amt_c += price * shares * fx
            ep_buy_shares += shares
            ep_buy_fees += f
            ep_buy_fees_c += f * fx
            ep_fees += f
            ep_fees_c += f * fx
            total_fees += f
            running_shares += shares
        elif t in RELEASE and shares > 0:
            fx = _fx_of(a)
            remaining = shares
            while remaining > 1e-9 and lots:
                lot = lots[0]
                consumed = min(lot["shares"], remaining)
                ep_realized_excl_fees += (price - lot["price"]) * consumed
                ep_realized_excl_fees_c += (price * fx - lot["price"] * lot["fx"]) * consumed
                fx_realized += lot["price"] * consumed * (fx - lot["fx"])
                ep_matched += consumed
                lot["shares"] -= consumed
                remaining -= consumed
                if lot["shares"] <= 1e-9:
                    lots.pop(0)
            f = _fee_of(a, t, price, shares, _i)
            ep_sell_amt += price * shares
            ep_sell_amt_c += price * shares * fx
            ep_sell_fees += f
            ep_sell_fees_c += f * fx
            ep_fees += f
            ep_fees_c += f * fx
            total_fees += f
            running_shares -= shares
            if running_shares <= 1e-9:   # 碎股卖光时浮点会剩极小的尾数
                # 卖光了: 先只记下平仓日, 本段累计留着 —— 当天买回就接着算同一段,
                # 隔夜没买回才在下次买入(或收尾)时结算进 carry。
                running_shares = 0
                lots = []
                flat_date = ad
        elif t == SPLIT:
            r = split_ratio(a)
            if r > 0 and lots:
                # 拆股/合股: 股数×r、单价÷r, 钱一分不动。份额口径的累计(买入股数/已配对股数)一起换标度,
                # 买入费用按配对比例摊的比值不变。合股零碎股券商折现金(cash in lieu): 零头从最后的 lot
                # 扣掉, 它的成本并进剩下的最后一个 lot(FIFO 与综合成本口径一致); 收到的现金可记一笔 DIVIDEND。
                for lot in lots:
                    lot["shares"] *= r
                    lot["price"] /= r
                ep_buy_shares *= r
                ep_matched *= r
                total = sum(l["shares"] for l in lots)
                # 美股券商支持碎股, 合股后的零头留在账上; 其余市场零头折现金
                whole = total if _foreign_market(a.get("stock_code") or stock_code) == "US" else int(total + 1e-6)
                frac, moved = total - whole, 0.0
                for lot in reversed(lots):
                    if frac <= 1e-9:
                        break
                    take = min(frac, lot["shares"])
                    lot["shares"] -= take
                    moved += take * lot["price"]
                    frac -= take
                lots = [l for l in lots if l["shares"] > 1e-9]
                if lots and moved:
                    lots[-1]["price"] += moved / lots[-1]["shares"]
                running_shares = whole
                if whole <= 1e-9:
                    lots = []
                    flat_date = ad
        elif t in INCOME:
            # 现金分红: 显式 amount, 或 price(每股股息) × shares(持股数) —— 都是税前
            amt = float(a.get("amount") or 0)
            if amt <= 0:
                amt = float(a.get("price") or 0) * float(a.get("shares") or 0)
            # 预扣税: 港美股的分红行用 fee 字段记税(手填为准); 没填按收费方案的默认税率估(美股 W-8BEN 10%, 港股 0)
            tax = 0.0
            mkt = _foreign_market(a.get("stock_code") or stock_code)
            if mkt:
                if a.get("fee") is not None:
                    tax = float(a["fee"])
                else:
                    from services.market_fees import dividend_tax_rate
                    tax = amt * dividend_tax_rate(mkt, a.get("broker"))
            dividend_tax += tax
            income_realized += amt - tax
            income_realized_c += (amt - tax) * _fx_of(a)

    # 收尾: 还停在空仓状态说明这一段确实平掉了(没有当日买回), 结算进 carry。
    # 不结算的话 realized_carry 会漏掉最后一段, 顶栏「已实现」少一块。
    if flat_date is not None and running_shares == 0:
        realized_carry += _episode_realized()
        realized_carry_c += _episode_realized_c()
        ep_buy_amt = ep_sell_amt = ep_fees = 0.0
        ep_buy_amt_c = ep_sell_amt_c = ep_fees_c = 0.0
        ep_buy_shares = ep_matched = 0
        ep_buy_fees = ep_sell_fees = ep_buy_fees_c = ep_sell_fees_c = 0.0
        ep_realized_excl_fees = ep_realized_excl_fees_c = 0.0

    # 总已实现 = 已平仓段 + 当前段已实现 + 现金分红
    realized_pnl = round(realized_carry + _episode_realized() + income_realized, 2)
    # carry = 不在当前浮动里的已实现 (已平仓段 + 分红) → 顶部总盈亏用它补, 避免和浮动重复
    realized_carry_out = round(realized_carry + income_realized, 2)
    cny = {
        "realized_pnl_cny": round(realized_carry_c + _episode_realized_c() + income_realized_c, 2),
        "realized_carry_cny": round(realized_carry_c + income_realized_c, 2),
        "realized_fx_pnl": round(fx_realized, 2),     # 人民币: 已实现里本金的汇兑部分(A 股恒 0)
    }

    total_shares = sum(l["shares"] for l in lots)
    if total_shares <= 1e-9:
        return {
            "shares": 0,
            "cost_price": 0.0,
            "fifo_cost_price": 0.0,
            "weighted_days": 0,
            "lots": [],
            "realized_pnl": realized_pnl,
            "realized_carry": realized_carry_out,
            "income_realized": round(income_realized, 2),
            "cost_value_cny": 0.0,
            **cny,
        }

    # FIFO cost — avg of remaining lots only (当前段)
    fifo_total = sum(l["shares"] * l["price"] for l in lots)
    fifo_cost = fifo_total / total_shares

    # 综合成本法只算当前段 (清仓后重置), 不把旧段已实现摊进新成本
    net_invested = ep_buy_amt - ep_sell_amt + ep_fees
    net_cost = net_invested / total_shares if total_shares > 0 else 0.0
    # 人民币持仓成本: 同一口径(当前段净投入, 每笔按自己的汇率); 回退 fifo 时也用 lot 自己的汇率
    cost_value_cny = ep_buy_amt_c - ep_sell_amt_c + ep_fees_c
    # 当前段内卖在极高位导致 net_invested 变负时回退到 fifo
    if net_cost <= 0:
        net_cost = fifo_cost
        cost_value_cny = sum(l["shares"] * l["price"] * l["fx"] for l in lots)

    # 摊薄成本(对齐券商): 持有期间累计每股现金分红从每股成本里扣
    div_ps = max(0.0, float(cash_div_per_share or 0.0))
    net_cost_diluted = max(0.0, net_cost - div_ps)
    fifo_cost_diluted = max(0.0, fifo_cost - div_ps)
    if div_ps:
        cost_value_cny = max(0.0, cost_value_cny - div_ps * total_shares * (_now_fx() if foreign else 1.0))

    # Capital-weighted days on FIFO lots (each lot has a concrete date)
    capital_days_sum = sum(
        l["shares"] * l["price"] * max(0, (today - l["trade_date"]).days)
        for l in lots
    )
    weighted_days = capital_days_sum / fifo_total if fifo_total > 0 else 0
    whole = int(round(total_shares))

    return {
        "shares": whole if abs(total_shares - whole) < 1e-6 else round(total_shares, 4),
        "cost_price": round(net_cost_diluted, 4),       # primary: 综合成本法 + 分红摊薄 (matches broker)
        "cost_price_raw": round(net_cost, 4),           # 未摊薄(仅买卖+费), 供对照
        "div_per_share": round(div_ps, 4),              # 持有期累计每股现金分红(已摊进 cost_price)
        "fifo_cost_price": round(fifo_cost_diluted, 4), # for reference
        "total_fees": round(total_fees, 2),
        "weighted_days": int(round(weighted_days)),
        "realized_pnl": realized_pnl,
        "realized_carry": realized_carry_out,            # 已平仓段+分红 (不在浮动里, 供顶部总盈亏补)
        "income_realized": round(income_realized, 2),   # 累计现金分红 (DIVIDEND, 税后)
        "dividend_tax": round(dividend_tax, 2),         # 港美股分红预扣税累计(原币)
        "cost_value_cny": round(cost_value_cny, 2),     # 持仓成本(人民币, 按各笔成交日汇率; A 股 = 成本×股数)
        **cny,
        "lots": [
            {"shares": round(l["shares"], 4) if l["shares"] % 1 else int(l["shares"]), "price": l["price"],
             "trade_date": l["trade_date"].isoformat()}
            for l in lots
        ],
    }


# ---------- 带费用/汇率解析的统一入口(异步, 读库) ----------
# 所有要「某只票现在的账」的地方都走 ledger_state: 每笔按自己的券商(没填用持仓券商)估费,
# 港美股收费方案吃 app_config.fee_schedules 的覆盖, 当前汇率懒取。各处自己拼参数容易漏一项
# (曾经 realized / trade-review / today-pnl 都没走自动估费, 港美股的费用回退成默认券商方案)。

async def broker_fee_resolver():
    """一次性读券商表, 返回 (name)->(rate,min) 解析器(找不到→默认券商), 避免每笔查库。"""
    from database import list_brokers
    brokers = await list_brokers()
    default = next((x for x in brokers if x.get("is_default")), None) or (brokers[0] if brokers else None)
    bymap = {x["name"]: (x["stock_rate"], x["stock_min"]) for x in brokers}

    def resolve(name):
        if name and name in bymap:
            return bymap[name]
        return (default["stock_rate"], default["stock_min"]) if default else (None, None)
    return resolve


_UNSET = object()


async def attach_auto_fees(actions: list, stock_code: str, holding_broker, resolve=None,
                           fee_overrides=_UNSET):
    """给每笔流水按自己的 broker(没有则用持仓券商)预算自动手续费 → a['_auto_fee']。

    佣金最低值(5 元起)按「委托单」收一次, 不是按成交笔 —— 一张单分几个价位成交时
    逐笔各取一次最低值会重复收费(实测这份流水多收 101.22 元)。所以先按券商分组,
    组内交给 allocate_trade_fees 合并同一委托再分摊。港美股的收费方案也按这笔实际券商取。
    """
    if resolve is None:
        resolve = await broker_fee_resolver()
    if fee_overrides is _UNSET:
        from database import get_config
        fee_overrides = await get_config("fee_schedules")   # 港美股收费方案的覆盖(JSON), 见 services/market_fees.py
    by_broker: dict = {}
    for a in actions:
        code = a.get("stock_code") or stock_code
        b = a.get("broker") or holding_broker
        r, m = resolve(b)
        # 港美股按券商名分组(方案按名字取); A 股沿用按费率分组
        by_broker.setdefault((r, m, b if _foreign_market(code) else None), []).append(a)
    for (r, m, _b), group in by_broker.items():
        fees = allocate_trade_fees(
            [dict(a, stock_code=a.get("stock_code") or stock_code, broker=a.get("broker") or holding_broker)
             for a in group], r, m, fee_overrides)
        for a, f in zip(group, fees):
            a["_auto_fee"] = f
    return resolve


async def ledger_state(code: str, actions: list | None = None, holding_broker=_UNSET, *,
                       resolve=None, fee_overrides=_UNSET, dilute: bool = True,
                       fx_now: float | None = None) -> dict:
    """一只票的账(compute_position_state 的结果), 费用与汇率都按统一口径解析好。

    actions 不传则读库; holding_broker 不传则读持仓行的 broker。会原地给 actions 挂 _auto_fee。
    dilute=True 再做分红摊薄(对齐券商)。港美股没传 fx_now 时取一次当前汇率(放线程里, 不卡事件循环)。
    """
    import asyncio
    if actions is None:
        from database import get_position_actions
        actions = await get_position_actions(code, limit=1000)
    if holding_broker is _UNSET:
        from database import get_holding
        holding_broker = ((await get_holding(code)) or {}).get("broker")
    resolve = await attach_auto_fees(actions, code, holding_broker, resolve, fee_overrides)
    if fx_now is None and _foreign_market(code) and any(not a.get("fx_rate") for a in actions):
        from services.market_data import code_fx
        fx_now = await asyncio.to_thread(code_fx, code)
    c_rate, c_min = resolve(holding_broker)
    st = compute_position_state(actions, stock_code=code, commission_rate=c_rate, commission_min=c_min,
                                fx_now=fx_now)
    if dilute:
        from services.dividends import dilute_state
        st = await dilute_state(code, st)
    return st
