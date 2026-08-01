"""当日盈亏(券商口径)。

原来的「今日浮动」= Σ 现市值 × 今日% /(1+今日%), 隐含假设"昨收就已持有当前份额",
两种情况会算错:
  · 今天新建的仓 —— 基准该是买入价而不是昨收(实测 588170 全部今天建仓, 真实 +1994
    被算成 -143, 偏差 +2138);
  · 今天清掉的仓 —— 当前份额为 0, 整笔从统计里消失(歌尔 +1305、建行 -30 全丢)。

改用现金流口径, 对"隔夜持有 / 今天新建 / 今天清仓"三种情形统一成立:

    当日盈亏 = 现市值 + 今日卖出所得 − 昨收市值 − 今日买入成本
    昨收市值 = 昨收单价 × 昨日持有份额
    昨日持有份额 = 现份额 + 今日卖出份额 − 今日买入份额

展开即 (现价−昨收)×昨日份额 + (现价−今日买均价)×今日买入 + (今日卖均价−昨收)×今日卖出。
流水金额本身含手续费, 所以结果是真实到手口径。

只统计 confirmed 且带份额的流水: 定投当天只有金额、份额 T+1 才确认(pending),
那笔钱还没变成份额, 不该当成"今日买入份额"。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

BUY_ACTS = {"BUY", "ADD"}
SELL_ACTS = {"SELL", "REDUCE", "REDEEM"}

_cache: tuple | None = None      # (结果, 时间戳)
_TTL = 12                        # 顶栏与持仓总览各自 20s 轮询, 缓存掉重复的全量 enrich
_lock = asyncio.Lock()


def _today_cst() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


def _flow(actions: list, day: str) -> dict:
    """当日已确认流水 → {bought_shares, bought_amount, sold_shares, sold_amount}。
    SPLIT 只改份额不产生现金流, 但会让"昨日份额"口径失真, 单独标出交给调用方。

    金额口径: 场外账本(external_asset_actions)有 amount 列直接用(已含手续费);
    A股账本(position_actions)只有 price/shares/fee, 需自己算 —— 买入现金流出
    = price×shares+fee, 卖出现金流入 = price×shares−fee。缺了这层换算, 卖出所得
    会被当成 0, 清仓票的当日盈亏直接等于负的昨收市值。
    """
    out = {"bought_shares": 0.0, "bought_amount": 0.0,
           "sold_shares": 0.0, "sold_amount": 0.0, "split": 0.0}
    for a in actions or []:
        if str(a.get("trade_date") or a.get("created_at") or "")[:10] != day:
            continue
        if (a.get("status") or "confirmed") != "confirmed":
            continue                                  # 定投待确认: 钱在途, 还没有份额
        act = str(a.get("action_type") or "").upper()
        sh = float(a.get("shares") or 0)
        amt = float(a.get("amount") or 0)
        fee = float(a.get("fee") or 0)
        px = float(a.get("price") or a.get("unit_price") or 0)
        if act == "SPLIT" and sh > 0:
            out["split"] = sh
        elif act in BUY_ACTS and sh > 0:
            out["bought_shares"] += sh
            out["bought_amount"] += amt if amt > 0 else (px * sh + fee)
        elif act in SELL_ACTS and sh > 0:
            out["sold_shares"] += sh
            out["sold_amount"] += amt if amt > 0 else max(0.0, px * sh - fee)
    return out


def _one(now_unit, prev_unit, cur_shares, f) -> float | None:
    """单个标的的当日盈亏。缺昨收或缺现价时返回 None(不猜, 由调用方标 unknown)。"""
    prev_shares = cur_shares + f["sold_shares"] - f["bought_shares"]
    if f["split"] > 0:
        prev_shares /= f["split"]        # 拆分日: 昨日份额是拆分前口径
    if prev_shares < -1e-6:
        return None                      # 流水与持仓对不上(漏录), 不硬算
    prev_shares = max(0.0, prev_shares)
    if prev_shares > 0 and prev_unit is None:
        return None
    if cur_shares > 0 and now_unit is None:
        return None
    now_mv = (now_unit or 0) * cur_shares
    prev_mv = (prev_unit or 0) * prev_shares
    if f["split"] > 0 and prev_unit is not None:
        prev_mv = prev_unit * prev_shares          # 拆分前单价 × 拆分前份额, 市值可比
    return now_mv + f["sold_amount"] - prev_mv - f["bought_amount"]


async def today_pnl() -> dict:
    """全账户当日盈亏(12s 缓存)。→ {date, total, unknown, items:[...], note}

    锁 + 缓存: 全量 enrich 在报价冷缓存时要 20s+(每个资产各打净值/代理请求), 而顶栏和
    持仓总览是两个独立调用方。并发进来时只让第一个真算, 其余等它的结果。
    """
    global _cache
    if _cache and time.time() - _cache[1] < _TTL:
        return _cache[0]
    async with _lock:
        if _cache and time.time() - _cache[1] < _TTL:     # 等锁期间别人算完了
            return _cache[0]
        out = await _compute()
        _cache = (out, time.time())
        return out


async def _compute() -> dict:
    from database import (get_all_holdings, get_position_actions,
                          list_external_assets, list_external_actions)
    from services.market_data import get_realtime_quotes, normalize_stock_code

    day = _today_cst()
    items: list[dict] = []

    # ---------- A 股 ----------
    holdings = {h["stock_code"]: h for h in await get_all_holdings()}
    acts_all = await get_position_actions(limit=5000)
    by_code: dict[str, list] = {}
    for a in acts_all:
        by_code.setdefault(a.get("stock_code"), []).append(a)
    # 今天有流水的票即使已清仓也要算(清仓的当日盈亏正是从这里来)
    a_codes = set(holdings) | {c for c, arr in by_code.items() if _flow(arr, day)["bought_shares"]
                               or _flow(arr, day)["sold_shares"]}
    quotes = await get_realtime_quotes(list(a_codes)) if a_codes else {}
    for code in sorted(a_codes):
        q = quotes.get(code) or quotes.get(normalize_stock_code(code)) or {}
        f = _flow(by_code.get(code) or [], day)
        cur = float((holdings.get(code) or {}).get("shares") or 0)
        pnl = _one(q.get("price"), q.get("prev_close"), cur, f)
        if pnl is None and not (f["bought_shares"] or f["sold_shares"] or cur):
            continue
        items.append({
            "code": code, "type": "A",
            "name": (holdings.get(code) or {}).get("stock_name") or q.get("stock_name") or code,
            "today_pnl": round(pnl, 2) if pnl is not None else None,
            "shares": cur, "bought": f["bought_shares"], "sold": f["sold_shares"],
            "closed_today": cur <= 0 and f["sold_shares"] > 0,
            "opened_today": f["bought_shares"] >= cur > 0,
        })

    # ---------- 场外资产 / 场内 ETF ----------
    from api.assets_routes import _enrich
    assets = await list_external_assets()
    enriched = await asyncio.gather(*[_enrich(a) for a in assets])
    for a in enriched:
        t = a.get("asset_type")
        if t in ("CASH", "WEALTH"):
            continue                                  # 无份额/单价口径, 当日盈亏不适用
        if t == "BOT":
            # OKX 网格无"份额×单价"口径, 沿用前端原有代理: floatProfit(未实现浮动)×汇率。
            # 严格说不是"当日"而是当前未实现, 标 estimated; 收进来是为了两个前端都只读
            # 这一个总数, 不再各自补算(否则顶栏与持仓总览会因重复/漏算给出两个数)。
            q = a.get("quote") or {}
            fp = q.get("float_profit_usdt")
            if fp is None:
                continue
            items.append({
                "code": a.get("code"), "type": t, "name": a.get("name") or a.get("code"),
                "today_pnl": round(float(fp) * float(q.get("usdcny") or 7.2), 2),
                "shares": 0, "bought": 0, "sold": 0,
                "closed_today": False, "opened_today": False, "estimated": True,
            })
            continue
        q = a.get("quote") or {}
        cur = float(a.get("shares") or 0)
        f = _flow(await list_external_actions(a["id"]), day)
        if not (cur or f["bought_shares"] or f["sold_shares"]):
            continue
        # 现单价: 场内 ETF = 实时市价, 场外 = 官方净值(优先)/盘中估值
        now_unit = q.get("nav") or q.get("est_nav")
        pct = q.get("today_change_pct")
        prev_unit = (now_unit / (1 + pct / 100)) if (now_unit and pct is not None) else None
        pnl = _one(now_unit, prev_unit, cur, f)
        items.append({
            "code": a.get("code"), "type": t,
            "name": a.get("name") or a.get("code"),
            "today_pnl": round(pnl, 2) if pnl is not None else None,
            "shares": cur, "bought": f["bought_shares"], "sold": f["sold_shares"],
            "closed_today": cur <= 0 and f["sold_shares"] > 0,
            "opened_today": f["bought_shares"] >= cur > 0,
            # 净值滞后时今日%是底层代理估的, 当日盈亏同样是估计值, 透出让前端标注
            "estimated": bool(q.get("proxy_change_pct") is not None
                              and q.get("nav_date") and q.get("nav_date") != day),
        })

    known = [i for i in items if i["today_pnl"] is not None]
    unknown = [i for i in items if i["today_pnl"] is None]
    items.sort(key=lambda i: (i["today_pnl"] is None, i["today_pnl"] or 0))
    return {
        "date": day,
        "total": round(sum(i["today_pnl"] for i in known), 2),
        "estimated_part": round(sum(i["today_pnl"] for i in known if i.get("estimated")), 2),
        "unknown": [i["code"] for i in unknown],
        "items": items,
        "note": "当日盈亏 = 现市值 + 今日卖出所得 − 昨收市值 − 今日买入成本; "
                "含今日清仓的已实现, 今日新建仓以买入价为基准。金额含手续费。"
                "场外基金净值 T+1, 标 estimated 的按底层代理估, 净值公布后会修正。",
    }
