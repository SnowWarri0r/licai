"""Portfolio management REST endpoints."""
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from models import HoldingCreate, HoldingUpdate, HoldingResponse
from database import (
    get_all_holdings, get_holding, add_holding, update_holding, delete_holding,
    get_position_actions, add_position_action, update_position_action, delete_position_action,
    get_unwind_plan, get_tranches, mark_tranche_executed, list_brokers,
)
from services.market_data import (
    get_realtime_quotes, get_stock_name, get_stock_sector, get_stock_sector_detail,
    normalize_stock_code, split_stock_code, get_fx_info, is_a_share,
)
from services.position_ledger import compute_position_state

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


async def _broker_stock_fee(broker_name):
    """按券商 name 返回 (股票佣金费率, 最低). 找不到→默认券商. 都没有→(None,None) 用模块默认。"""
    brokers = await list_brokers()
    b = next((x for x in brokers if x["name"] == broker_name), None) if broker_name else None
    if b is None:
        b = next((x for x in brokers if x["is_default"]), None) or (brokers[0] if brokers else None)
    if b is None:
        return (None, None)
    return (b["stock_rate"], b["stock_min"])


class ActionCreate(BaseModel):
    action_type: str  # BUY / SELL / ADD / REDUCE / DIVIDEND / BONUS
    price: float
    shares: int
    trade_date: Optional[str] = None  # YYYY-MM-DD
    trade_time: Optional[str] = None  # HH:MM 成交时刻(可选, 留空则用录入时间)
    note: Optional[str] = ""
    fee: Optional[float] = None    # CNY override; None 让后端按券商费率自动算


class ActionUpdate(BaseModel):
    action_type: Optional[str] = None
    price: Optional[float] = None
    shares: Optional[int] = None
    trade_date: Optional[str] = None
    trade_time: Optional[str] = None   # 成交时刻 HH:MM; None=不动, ""=清空, "HH:MM"=设值
    note: Optional[str] = None
    fee: Optional[float] = None
    fee_set: bool = False           # 显式标记"我要改 fee" (用于区分 fee=None=清空 还是 不动)


async def _recompute_holding(stock_code: str):
    """Rebuild holding shares/cost_price from FIFO ledger."""
    actions = await get_position_actions(stock_code, limit=500)
    h = await get_holding(stock_code)
    c_rate, c_min = await _broker_stock_fee((h or {}).get("broker"))
    state = compute_position_state(actions, stock_code=stock_code,
                                   commission_rate=c_rate, commission_min=c_min)
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
        # 现算 shares/cost_price (而非读 holdings 表存的值): 综合成本法按"持仓段"
        # 计算, 清仓后复活会重置成本, 存量值可能是旧算法写的, 现算保证一致。
        try:
            _acts = await get_position_actions(code, limit=500)
            if _acts:
                c_rate, c_min = await _broker_stock_fee(h.get("broker"))
                _st = compute_position_state(_acts, stock_code=code,
                                             commission_rate=c_rate, commission_min=c_min)
                h["shares"] = _st["shares"]
                h["cost_price"] = _st["cost_price"]
        except Exception:
            pass
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
            broker=h.get("broker"),
        ))

    return result


@router.get("/realized")
async def realized_pnl():
    """Per-stock 已实现盈亏 (含已清仓 + 部分减仓), 从 position_actions 计算.

    返回每只股票的 realized_pnl 以及 grand total. 已清仓股票 stock_name
    从 quote 缓存补 (holdings 行可能已删).
    """
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
    total_carry = 0.0
    for code in codes:
        actions = await get_position_actions(code, limit=500)
        if not actions:
            continue
        c_rate, c_min = await _broker_stock_fee((holdings_map.get(code) or {}).get("broker"))
        state = compute_position_state(actions, stock_code=code,
                                       commission_rate=c_rate, commission_min=c_min)
        rp = float(state.get("realized_pnl") or 0)
        # carry = 不在当前浮动里的已实现 (已平仓段 + 分红). 当前持仓段的已实现已被
        # 综合成本法摊进浮动盈亏, 顶部总盈亏只能加 carry, 否则重复算。
        carry = float(state.get("realized_carry") or 0)
        total += rp
        total_carry += carry
        h = holdings_map.get(code) or {}
        name = h.get("stock_name") or ""
        # 已清仓股票 holdings 行可能已删 → 用 quote 缓存或 akshare 补名字
        if not name:
            try:
                name = await get_stock_name(code) or ""
            except Exception:
                name = ""
        items.append({
            "stock_code": code,
            "stock_name": name,
            "realized_pnl": rp,
            "realized_carry": round(carry, 2),
            "still_holding": (state.get("shares") or 0) > 0,
        })
    items.sort(key=lambda x: x["realized_pnl"])
    return {
        "items": items,
        "total_realized_pnl": round(total, 2),
        "total_realized_carry": round(total_carry, 2),
        "count": len(items),
    }


@router.get("/trade-review")
async def trade_review():
    """A 股交易复盘报告 (纯客观, 不给建议): 用 position_actions 算每只的
    已实现/浮动/全周期盈亏 + 买卖次数(做T频率) + 持有天数, 再汇总胜率/盈亏榜/做T榜。"""
    from database import get_db
    from services.position_ledger import ACQUIRE, RELEASE
    from services.market_data import get_realtime_quotes

    db = await get_db()
    try:
        cursor = await db.execute("SELECT DISTINCT stock_code FROM position_actions ORDER BY stock_code")
        codes = [r[0] for r in await cursor.fetchall()]
    finally:
        await db.close()

    holdings_map = {h["stock_code"]: h for h in await get_all_holdings()}
    stats = []
    for code in codes:
        actions = await get_position_actions(code, limit=500)
        if not actions:
            continue
        c_rate, c_min = await _broker_stock_fee((holdings_map.get(code) or {}).get("broker"))
        state = compute_position_state(actions, stock_code=code, commission_rate=c_rate, commission_min=c_min)
        n_buy = sum(1 for a in actions if (a.get("action_type") or "").upper() in ACQUIRE)
        n_sell = sum(1 for a in actions if (a.get("action_type") or "").upper() in RELEASE)
        name = (holdings_map.get(code) or {}).get("stock_name") or ""
        if not name:
            try:
                name = await get_stock_name(code) or code
            except Exception:
                name = code
        stats.append({
            "code": code, "name": name,
            "realized": round(float(state.get("realized_pnl") or 0), 2),
            "shares": float(state.get("shares") or 0),
            "cost_price": float(state.get("cost_price") or 0),
            "hold_days": int(state.get("weighted_days") or 0),
            "n_buy": n_buy, "n_sell": n_sell,
        })

    # 浮动 (在持仓的): 实时价 - 成本
    held = [s for s in stats if s["shares"] > 0]
    quotes = await get_realtime_quotes([s["code"] for s in held]) if held else {}
    for s in stats:
        floating = 0.0
        if s["shares"] > 0:
            price = (quotes.get(s["code"]) or {}).get("price") or 0
            if price:
                floating = (price - s["cost_price"]) * s["shares"]
        s["floating"] = round(floating, 2)
        s["total_pnl"] = round(s["realized"] + floating, 2)

    n = len(stats)
    n_win = sum(1 for s in stats if s["total_pnl"] > 0)
    n_loss = sum(1 for s in stats if s["total_pnl"] < 0)
    by_real = sorted(stats, key=lambda s: s["realized"])
    best = [{"name": s["name"], "realized": s["realized"]} for s in reversed(by_real[-3:]) if s["realized"] > 0]
    worst = [{"name": s["name"], "realized": s["realized"]} for s in by_real[:3] if s["realized"] < 0]
    active = sorted([s for s in stats if s["n_sell"] > 0], key=lambda s: -(s["n_buy"] + s["n_sell"]))[:5]
    active_t = [{"name": s["name"], "n_buy": s["n_buy"], "n_sell": s["n_sell"], "realized": s["realized"]} for s in active]
    held_days = [s["hold_days"] for s in held if s["hold_days"]]
    avg_hold = round(sum(held_days) / len(held_days)) if held_days else 0

    obs = []
    if n:
        obs.append(f"交易过 {n} 只 · {n_win} 赚 {n_loss} 亏 · 胜率 {round(n_win / n * 100)}%")
    if active_t:
        a0 = active_t[0]
        tone = "净赚" if a0["realized"] >= 0 else "净亏"
        obs.append(f"做T最频繁: {a0['name']} ({a0['n_buy']}买{a0['n_sell']}卖), 这只{tone} {abs(a0['realized']):.0f}")
    losers_active = [a for a in active_t if a["realized"] < 0]
    if active_t:
        winners_active = len(active_t) - len(losers_active)
        obs.append(f"做T(反复买卖)的票里 {len(losers_active)} 只净亏、{winners_active} 只净赚")
    if avg_hold and avg_hold <= 15:
        obs.append(f"当前持仓平均才拿 {avg_hold} 天 — 偏短线")

    return {
        "overview": {
            "n_stocks": n, "n_win": n_win, "n_loss": n_loss,
            "win_rate": round(n_win / n, 3) if n else 0,
            "total_realized": round(sum(s["realized"] for s in stats), 2),
            "avg_hold_days": avg_hold,
        },
        "best": best, "worst": worst, "active_t": active_t,
        "observations": obs,
        "stats": stats,  # per-stock 全量 (held=shares>0 / closed): AI 复盘要区分死扛 vs 已止损
    }


@router.get("/trade-journal")
async def trade_journal(limit: int = 80):
    """逐笔交易复盘: 每笔买/卖对照现价, 标命中(买便宜了/卖高了)。
    买入命中 = 现价 > 买入价 (这笔买在低位); 卖出命中 = 现价 < 卖出价 (卖完躲过下跌)。"""
    from database import get_db
    from services.position_ledger import ACQUIRE, RELEASE
    from services.market_data import get_realtime_quotes

    db = await get_db()
    try:
        cursor = await db.execute("SELECT DISTINCT stock_code FROM position_actions")
        codes = [r[0] for r in await cursor.fetchall()]
    finally:
        await db.close()

    holdings_map = {h["stock_code"]: h for h in await get_all_holdings()}
    quotes = await get_realtime_quotes(codes) if codes else {}
    name_cache = {}
    trades = []
    for code in codes:
        cur = (quotes.get(code) or {}).get("price") or 0
        if not cur:
            continue
        name = (holdings_map.get(code) or {}).get("stock_name") or name_cache.get(code)
        if not name:
            try:
                name = await get_stock_name(code) or code
            except Exception:
                name = code
            name_cache[code] = name
        for a in await get_position_actions(code, limit=500):
            t = (a.get("action_type") or "").upper()
            kind = "buy" if t in ACQUIRE else ("sell" if t in RELEASE else None)
            price = float(a.get("price") or 0)
            if not kind or price <= 0:
                continue
            pct = round((cur - price) / price * 100, 2)   # 买/卖后股价至今涨跌
            hit = (cur > price) if kind == "buy" else (cur < price)
            trades.append({
                "date": (a.get("trade_date") or "")[:10], "code": code, "name": name,
                "kind": kind, "price": round(price, 3), "shares": float(a.get("shares") or 0),
                "current": round(cur, 3), "pct": pct, "hit": hit, "asset_class": "stock",
            })

    # ---- 基金/ETF 交易 (external_asset_actions): 场内ETF 当个股看, 场外基金当定投看 ----
    from database import list_external_assets, list_external_actions
    from services.external_assets import get_fund_quote, _is_onchain_etf
    for a in await list_external_assets():
        if a.get("asset_type") != "FUND":
            continue
        fcode = str(a.get("code") or "")
        fname = a.get("name") or fcode
        cls = "etf" if _is_onchain_etf(fcode) else "fund"   # 场内ETF / 场外基金
        try:
            fq = await get_fund_quote(fcode)
        except Exception:
            fq = None
        fcur = float((fq or {}).get("nav") or (fq or {}).get("est_nav") or 0)
        for act in await list_external_actions(a["id"]):
            if (act.get("status") or "confirmed") != "confirmed":
                continue
            at = (act.get("action_type") or "").upper()
            kind = "buy" if at in ("BUY", "ADD") else ("sell" if at == "REDEEM" else None)
            price = float(act.get("unit_price") or 0)
            if not kind or price <= 0:
                continue
            pct = round((fcur - price) / price * 100, 2) if fcur else None
            hit = ((fcur > price) if kind == "buy" else (fcur < price)) if fcur else None
            trades.append({
                "date": (act.get("trade_date") or "")[:10], "code": fcode, "name": fname,
                "kind": kind, "price": round(price, 4), "shares": abs(float(act.get("shares") or 0)),
                "current": round(fcur, 4) if fcur else None, "pct": pct, "hit": hit,
                "asset_class": cls, "amount": round(float(act.get("amount") or 0), 2),
            })

    trades.sort(key=lambda x: x["date"], reverse=True)
    # 命中率统计保持 A 股口径 (基金定投不适用"买在低位"这套), 只统计个股
    stock_trades = [t for t in trades if t.get("asset_class") == "stock"]
    buys = [t for t in stock_trades if t["kind"] == "buy"]
    sells = [t for t in stock_trades if t["kind"] == "sell"]
    return {
        "trades": trades[:limit],
        "buy_count": len(buys),
        "buy_hit": sum(1 for t in buys if t["hit"]),
        "buy_hit_rate": round(sum(1 for t in buys if t["hit"]) / len(buys), 3) if buys else 0,
        "sell_count": len(sells),
        "sell_hit": sum(1 for t in sells if t["hit"]),
        "sell_hit_rate": round(sum(1 for t in sells if t["hit"]) / len(sells), 3) if sells else 0,
        "total": len(trades),
    }


_ai_review_cache: dict = {}
_AI_REVIEW_TTL = 1800  # 30min, LLM 调用贵


def _parse_llm_json(raw):
    import json, re
    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```(json)?", "", txt).strip()
        if txt.endswith("```"):
            txt = txt[:-3].strip()
    try:
        return json.loads(txt)
    except Exception:
        pass
    # 截断救援: narrative/字段被 max_tokens 切断时, 试着补齐闭合符再解析
    for tail in ['"}', '"]}', '"}]}', '}', ']}', '"}}', '"]}}']:
        try:
            return json.loads(txt + tail)
        except Exception:
            continue
    # 仍失败: 正则尽量抠出已完整的字段, 绝不把原始 JSON 当 narrative 倒出来
    out = {"summary": "", "good": [], "discipline": [], "binchuan": [], "narrative": ""}
    m = re.search(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
    if m:
        out["summary"] = m.group(1)
    return out


@router.get("/trade-review-ai")
async def trade_review_ai(period: str = "all", force: int = 0):
    """LLM 交易纪律复盘。period: all(全周期总览) / day(当日) / week(本周) / month(本月)。
    纯客观举证, 严禁任何未来买卖建议。"""
    import time, asyncio, json, datetime as _dt
    from services import llm_client

    period = period if period in ("all", "day", "week", "month") else "all"
    ck = f"trade_review_ai_{period}"
    if not force:
        c = _ai_review_cache.get(ck)
        if c and time.time() - c[1] < _AI_REVIEW_TTL:
            return c[0]

    # ---- 日/周/月: 只复盘该时间窗内实际发生的买卖动作 ----
    if period != "all":
        journal = await trade_journal(limit=1000)
        trades_all = journal.get("trades", [])
        today = (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).date()
        if period == "day":
            dates = [t["date"] for t in trades_all if t["date"]]
            anchor = max(dates) if dates else today.isoformat()
            start, label, win = anchor, f"{anchor} 当日", lambda t: t["date"] == anchor
        elif period == "week":
            monday = (today - _dt.timedelta(days=today.weekday())).isoformat()
            start, label, win = monday, f"本周({monday}起)", lambda t: t["date"] and t["date"] >= monday
        else:  # month
            first = today.replace(day=1).isoformat()
            start, label, win = first, f"本月({first}起)", lambda t: t["date"] and t["date"] >= first
        ptrades = sorted([t for t in trades_all if win(t)], key=lambda x: x["date"])
        if not ptrades:
            result = {"period": period, "period_label": label, "empty": True,
                      "summary": f"{label}没有交易记录", "good": [], "discipline": [],
                      "binchuan": [], "narrative": "", "generated_at": time.time()}
            _ai_review_cache[ck] = (result, time.time())
            return result
        nb = sum(1 for t in ptrades if t["kind"] == "buy")
        ns = sum(1 for t in ptrades if t["kind"] == "sell")
        _cls_tag = {"stock": "个股", "etf": "场内ETF", "fund": "场外基金"}
        lines = [f"{label} 共 {len(ptrades)} 笔: {nb} 买 {ns} 卖", ""]
        for t in ptrades:
            kd = "买" if t["kind"] == "buy" else "卖"
            tag = _cls_tag.get(t.get("asset_class", "stock"), "个股")
            sh = t.get("shares") or 0
            sh_s = f"{sh:.0f}" if sh == int(sh) else f"{sh:.2f}"
            tail = (f" (现价{t['current']}, 至今{t['pct']:+.1f}%)"
                    if t.get("current") and t.get("pct") is not None else "")
            amt = f" 约¥{t['amount']:.0f}" if t.get("amount") else ""
            lines.append(f"  [{tag}] {t['date']} {kd} {t['name']} @{t['price']}×{sh_s}{amt}{tail}")
        data_block = "\n".join(lines)
        system_prompt = (
            f"你是交易复盘教练。这是用户【{label}】这个时间窗内实际发生的买卖动作, 复盘他这一段的交易节奏和纪律。\n"
            "每笔前面标了资产类型, 不同类型用不同标准评, 别一套尺子量到底:\n"
            "  · [个股]/[场内ETF]: 可做T, 看有没有追高(越买越高)、同一标的反复买卖(频繁做T)、追涨杀跌、情绪化。\n"
            "  · [场外基金]: T+1 净值成交, 不能做T。小额规律买入是【定投】=策略, 不是追高/情绪化, 别拿做T那套骂它; "
            "    它该看的是: 定投有没有乱中断、是否在高位还大额追加、有没有恐慌赎回/追涨赎回。净值滞后, 别用单日表现判对错。\n"
            "'至今X%'是该笔对现价的表现(参考, 别据此判追高对错——可能还持有/趋势未走完; 基金更别看这个下结论)。\n"
            "客观、像老友点评; 该夸的夸(节奏克制/卖点干脆/定投坚持)该点的点(追高/频繁/恐慌操作)。\n"
            "可用进阶交易原则(不点名出处): 买前估空间、卖点纪律、克制贪念、别接最后一棒。\n"
            "【硬规则】严禁任何未来操作指令(该买/该卖/加减仓/止损位/目标价/现在适合), 只复盘已发生, 不编造数字。\n"
            "JSON 输出: {\"summary\":\"一句话点评这段\", \"good\":[\"做对的(用数据)\"], "
            "\"discipline\":[{\"problem\":\"问题\",\"evidence\":\"数据举证\",\"why\":\"什么习惯\"}], "
            "\"binchuan\":[{\"principle\":\"对照的交易原则\",\"verdict\":\"契合/违背\",\"detail\":\"对照\"}], "
            "\"narrative\":\"1-2段复盘正文\"}。只输出 JSON。"
        )
        user_prompt = f"复盘我{label}的交易:\n\n{data_block}"
        try:
            raw = await asyncio.to_thread(llm_client.call_claude, user_prompt, system_prompt, "claude-opus-4-8", 1800)
        except Exception as e:
            return {"period": period, "period_label": label, "error": str(e), "summary": "", "narrative": ""}
        parsed = _parse_llm_json(raw)
        result = {
            "period": period, "period_label": label, "empty": False,
            "summary": parsed.get("summary", ""),
            "good": parsed.get("good", []) if isinstance(parsed.get("good"), list) else [],
            "discipline": parsed.get("discipline", []) if isinstance(parsed.get("discipline"), list) else [],
            "binchuan": parsed.get("binchuan", []) if isinstance(parsed.get("binchuan"), list) else [],
            "narrative": parsed.get("narrative", ""),
            "n_buy": nb, "n_sell": ns, "n_trades": len(ptrades),
            "generated_at": time.time(),
        }
        _ai_review_cache[ck] = (result, time.time())
        return result

    # ---- all: 全周期总览 (完整纪律复盘) ----

    review = await trade_review()
    journal = await trade_journal(limit=400)
    o = review.get("overview") or {}
    stats = review.get("stats") or []
    if not o.get("n_stocks"):
        return {"narrative": "", "discipline": [], "summary": "", "generated_at": None}

    held = [s for s in stats if s["shares"] > 0]      # 还拿着的
    closed = [s for s in stats if s["shares"] <= 0]   # 已清仓(割了/卖了)
    held_names = {s["name"] for s in held}
    # 每只持仓真实总盈亏(浮动+已实现): 这才是判赚亏的唯一标准, 不是拿单笔买入价对现价的快照
    for s in held:
        s["total_now"] = round(s.get("floating", 0) + s.get("realized", 0), 1)
    held_win = [s for s in held if s["total_now"] > 0]    # 当前在赚的持仓(追高追对了也算)
    held_loss = [s for s in held if s["total_now"] < 0]   # 当前真亏的持仓(才轮得到说追高/套牢)
    loss_held_names = {s["name"] for s in held_loss}

    closed_win = [s for s in closed if s["realized"] > 0]
    closed_loss = [s for s in closed if s["realized"] < 0]
    closed_loss_sorted = sorted(closed_loss, key=lambda s: s["realized"])

    cur_by_code = {t["code"]: t["current"] for t in journal["trades"]}
    code_by_name = {s["name"]: s["code"] for s in stats}

    # 按"持仓轮次"切分每只票的买入: 期间卖到 0 即上一轮结束、新建仓算新一轮。
    # 只有当前未平那轮的买入才算"还套着"; 卖掉的旧仓买入不能再当套牢/加码(否则会把
    # '买→卖→后来重新买'误判成'越跌越加码',如格林美 @9.41买完已@9.11卖掉, @7.88是新仓)。
    def _buy_rounds(code):
        ts = sorted([t for t in journal["trades"] if t["code"] == code],
                    key=lambda x: (x["date"], 0 if x["kind"] == "buy" else 1))
        rounds, cur, bal = [], [], 0.0
        for t in ts:
            if t["kind"] == "buy":
                bal += t["shares"]; cur.append(t)
            else:
                bal -= t["shares"]
                if bal <= 1e-6:
                    if cur:
                        rounds.append(cur)
                    cur, bal = [], 0.0
        if cur:
            rounds.append(cur)
        return rounds

    # 当前真亏持仓: 取其"开放那一轮"的买入 (held_loss 一定还持有 → 最后一轮是开放的)
    cur_round_buys = {s["name"]: (_buy_rounds(s["code"])[-1] if _buy_rounds(s["code"]) else [])
                      for s in held_loss}
    # "套着的买入": 只取当前轮里现价低于买价的
    held_buys_under = sorted(
        [t for buys in cur_round_buys.values() for t in buys
         if t.get("current") and t.get("price") and t["current"] < t["price"]],
        key=lambda x: x["pct"])[:8]
    # 当前轮内 ≥3 笔买入才算反复补仓
    repeat_buys = {n: sorted(buys, key=lambda x: x["date"])
                   for n, buys in cur_round_buys.items() if len(buys) >= 3}

    # (b) 已清仓票"卷入周期"(首次买入→末次卖出): 看赚的拿多久 vs 亏的拿多久
    def _closed_span(code):
        ts = [t for t in journal["trades"] if t["code"] == code and t["date"]]
        bd = [t["date"] for t in ts if t["kind"] == "buy"]
        sd = [t["date"] for t in ts if t["kind"] == "sell"]
        if not bd or not sd:
            return None
        try:
            return max(0, (_dt.date.fromisoformat(max(sd)) - _dt.date.fromisoformat(min(bd))).days)
        except Exception:
            return None
    cw_spans = [x for x in (_closed_span(s["code"]) for s in closed_win) if x is not None]
    cl_spans = [x for x in (_closed_span(s["code"]) for s in closed_loss) if x is not None]
    avg_cw = round(sum(cw_spans) / len(cw_spans)) if cw_spans else None
    avg_cl = round(sum(cl_spans) / len(cl_spans)) if cl_spans else None

    # (c) 越跌越加码: 同股【同一持仓轮次内】多次买入价格下行但金额上行 = martingale。
    # 只看最终亏钱的票(当前真亏持仓 + 已清仓亏的); 按轮次评估, 不把跨轮的买→卖→再买当加码。
    loser_names = loss_held_names | {s["name"] for s in closed_loss}
    loser_codes = {code_by_name[n] for n in loser_names if n in code_by_name}
    escalation = []
    for code in loser_codes:
        for ts in _buy_rounds(code):              # 逐轮看, 取第一个构成加码的轮
            if len(ts) < 3:
                continue
            ts = sorted(ts, key=lambda x: x["date"])
            amts = [t["price"] * t["shares"] for t in ts]
            falling = ts[-1]["price"] < ts[0]["price"]
            growing = amts[-1] > amts[0] * 1.2
            nm = ts[0]["name"]
            seq = " → ".join(f"@{t['price']}×{int(t['shares'])}={round(t['price']*t['shares'])}" for t in ts)
            flag = " [越跌越加码!]" if (falling and growing) else (" [越买越高]" if ts[-1]["price"] > ts[0]["price"] else "")
            escalation.append({"name": nm, "seq": seq, "flag": flag, "martingale": falling and growing})
            break

    # (d) 板块集中度(仍持有, 按市值)
    sector_val: dict = {}
    sector_names: dict = {}
    held_total = 0.0
    for s in held:
        val = (cur_by_code.get(s["code"]) or 0) * s["shares"]
        held_total += val
        try:
            sec = await get_stock_sector_detail(s["code"]) or "其他"  # 二级: 小金属/基本金属/贵金属…
        except Exception:
            sec = "其他"
        sector_val[sec] = sector_val.get(sec, 0) + val
        sector_names.setdefault(sec, []).append(s["name"])

    lines = [
        f"总览: 交易过 {o['n_stocks']} 只, 已实现合计 {o['total_realized']:.0f}, 当前持仓平均持有 {o['avg_hold_days']} 天",
        f"卖出命中率 {round(journal['sell_hit_rate']*100)}% ({journal['sell_hit']}/{journal['sell_count']} 笔卖完股价确实跌了) — 卖点把握",
        "",
        f"【仍持有 {len(held)} 只 — 以真实总盈亏(浮动+已实现)判赚亏, 不看单笔买入价对现价的快照】:",
    ]
    for s in held:
        tag = "✅赚" if s["total_now"] > 0 else ("❌亏" if s["total_now"] < 0 else "持平")
        lines.append(f"  {s['name']}: {tag} 总{s['total_now']:+.0f}(浮动{s.get('floating',0):+.0f}+已实现{s['realized']:+.0f}), "
                     f"持{s['shares']:.0f}股, {s['n_buy']}买{s['n_sell']}卖")
    if held_win:
        lines.append("  注: " + "/".join(s["name"] for s in held_win) + " 当前在赚 — 哪怕当初追高买的, 趋势走对了就是对的, 不许当追高错来骂")
    if held_buys_under:
        lines.append("  当前真亏持仓里套着的买入: " + "; ".join(
            f"{t['name']}@{t['price']}(现{t['current']},{t['pct']:+.1f}%)" for t in held_buys_under))
    for n, ts in list(repeat_buys.items())[:6]:
        seq = " → ".join(f"@{t['price']}" for t in ts)
        trend = "越买越高(追)" if ts[-1]["price"] > ts[0]["price"] else "越买越低(补)"
        lines.append(f"  {n} 多次买入: {seq} [{trend}]")
    lines += [
        "",
        f"【已清仓 {len(closed)} 只】(这些已经卖掉离场了, 亏的是已割肉止损, 不许说还在死扛/装死):",
        "  赚着出的: " + ("; ".join(f"{s['name']}+{s['realized']:.0f}" for s in sorted(closed_win, key=lambda x:-x['realized'])) or "无"),
        "  亏着割的: " + ("; ".join(f"{s['name']}{s['realized']:.0f}({s['n_buy']}买{s['n_sell']}卖)" for s in closed_loss_sorted) or "无"),
    ]
    if avg_cw is not None and avg_cl is not None:
        lines.append(f"  持有周期(首次买入→清仓): 赚钱的票平均 {avg_cw} 天清掉, 亏钱的票平均 {avg_cl} 天才割"
                     + (" — 亏的拿得比赚的久(让亏损奔跑/截断利润)" if avg_cl > avg_cw else ""))

    multi = [e for e in escalation if e["flag"]]
    if multi:
        lines += ["", "【同股多次买入·加码轨迹】(看每笔金额是否越亏越大):"]
        for e in multi[:6]:
            lines.append(f"  {e['name']}: {e['seq']}{e['flag']}")

    if sector_val and held_total > 0:
        top = sorted(sector_val.items(), key=lambda x: -x[1])
        lines += ["", "【持仓板块集中度·按市值】:"]
        lines.append("  " + "; ".join(
            f"{sec} {round(v/held_total*100)}%({'/'.join(sector_names[sec])})" for sec, v in top))

    # ---- 基金/ETF 交易聚合 (场外基金按定投评, 场内ETF按个股评; 不逐笔列 225 条定投) ----
    fund_trades = [t for t in journal["trades"] if t.get("asset_class") in ("fund", "etf")]
    fund_lines = []
    if fund_trades:
        grouped: dict = {}
        for t in fund_trades:
            grouped.setdefault((t["name"], t["asset_class"]), []).append(t)
        for (nm, cls), ts in sorted(grouped.items()):
            bs = [t for t in ts if t["kind"] == "buy"]
            ss = [t for t in ts if t["kind"] == "sell"]
            invested = sum((t.get("amount") or t["price"] * t["shares"]) for t in bs)
            redeemed = sum((t.get("amount") or t["price"] * t["shares"]) for t in ss)
            sh_buy = sum(t["shares"] for t in bs)
            avg_cost = (sum(t["price"] * t["shares"] for t in bs) / sh_buy) if sh_buy else 0
            cur = next((t["current"] for t in ts if t.get("current")), None)
            dts = sorted(t["date"] for t in ts if t["date"])
            small = sum(1 for t in bs if (t.get("amount") or 0) and t["amount"] <= 200)
            dca = " [多笔小额=定投]" if small >= 3 else ""
            label = "场内ETF" if cls == "etf" else "场外基金"
            ln = f"  [{label}] {nm}: {len(bs)}买{len(ss)}赎, 投入约¥{invested:.0f}"
            if redeemed:
                ln += f", 赎回约¥{redeemed:.0f}"
            if avg_cost and cur:
                ln += f", 均成本{avg_cost:.3f}→现价{cur:.3f}({(cur/avg_cost-1)*100:+.1f}%)"
            if dts:
                ln += f", {dts[0]}起"
            fund_lines.append(ln + dca)
    if fund_lines:
        lines += ["", "【基金/ETF 交易】(下面这些不是个股, 按各自逻辑评):"] + fund_lines

    data_block = "\n".join(lines)
    _fund_rule = (
        "\n【基金/ETF 评判另一套尺子】数据末尾的'基金/ETF 交易'不是个股, 严禁套用上面个股的追高/做T/越跌加码那套:\n"
        "  · [场外基金]: T+1 净值成交, 根本不能做T。'多笔小额=定投'是纪律性策略, 越跌越买/逢高也买都是定投正常机制, 不是情绪化追高; "
        "    要评就评: 定投有没有乱中断、有没有在明显高位还大额一次性追加、有没有恐慌割在地板/追涨赎回。\n"
        "  · [场内ETF]: 可做T, 跟个股一个标准(追高/频繁做T 可点)。\n"
        "  对基金的肯定也要给(坚持定投/分批摊低成本/止盈干脆); 没有基金交易就忽略这段。\n"
        if fund_lines else ""
    )
    system_prompt = (
        "你是交易复盘教练。基于用户真实的 A 股交易流水, 复盘他的交易纪律, 像一面镜子照清楚他的习惯——"
        "该夸的夸、该点的点, 要平衡客观, 不是单纯挑刺骂人。\n"
        "【最重要·别冤枉他·两条铁律】\n"
        "(1) 仍持有的票, 赚亏只看'真实总盈亏(浮动+已实现)'。当前在赚的持仓(标✅赚), 哪怕当初追高买的、"
        "买入价比某天现价高过, 都算他做对了(追高 + 趋势走对 = 成功), 绝对不许拿单笔买入价对现价的快照说他'追高/套牢/接盘'。"
        "'追高/套着/浮亏'只能点【当前真亏(标❌亏)】的持仓。\n"
        "(2) 已清仓=他已卖掉离场, 亏损票=他割肉止损了, 这是执行纪律, 不许说'还在死扛/装死/越套越补/现在还亏着'。\n"
        "先认可做对的(在赚的持仓含追对的追高、已实现赚钱的票、卖出命中率高、亏了能割肉止损), 再指出真问题。可用维度: "
        "当前真亏持仓里越买越高/越跌越加码(金额越亏越大)=情绪化补仓上头; "
        "亏的票比赚的票拿得久=截断利润/让亏损奔跑; 板块集中度高=押注单一赛道。每条都用具体数据举证。\n"
        "【不要用】单日买入命中率快照判'追高对错'——它隔天就翻面、噪声大, 已从数据里剔除。\n"
        "【交易哲学对照】额外用一套成熟的游资交易哲学对照他的行为(只做客观对照, 不点名出处, 不是让他去打板):\n"
        "  · 大智=买前先估'这票空间/高度到哪', 没判断就别动手; 大勇=空间够才敢上, 但'一步到位/空间透支'的不碰\n"
        "  · 卖点纪律 > 买点: 他说'炒股最难是不会卖', 条件触发就走(不创新高/放量巨阴/反包失败)\n"
        "  · 容错+止损: 留两条命, 错了果断止损, 死扛是大忌\n"
        "  · 克制贪念、复利靠时间: 频繁交易/想一夜回本是反面\n"
        "  · 情绪周期: 氛围差只有小品种活, 高潮别接最后一棒\n"
        "  针对用户数据, 找出他哪些行为【契合】、哪些【违背】这些原则, 各用他的真实数据举证。\n"
        "【硬规则】严禁任何面向未来的操作指令: 不许出现 该买/该卖/加仓/减仓/止损位/目标价/仓位建议/现在适合。"
        "只复盘已发生的行为, 不指挥下一步。不许编造给定数据里没有的票或数字。\n"
        "用 JSON 输出: {\"summary\":\"一句话客观定性(好坏都讲)\", "
        "\"good\":[\"做对的点(用数据)\", ...], "
        "\"discipline\":[{\"problem\":\"真实存在的问题\",\"evidence\":\"具体数据举证\",\"why\":\"暴露了什么习惯\"}], "
        "\"binchuan\":[{\"principle\":\"对照的交易原则(如:卖点纪律>买点)\",\"verdict\":\"契合\"或\"违背\",\"detail\":\"用他的真实数据对照\"}], "
        "\"narrative\":\"2-3段复盘正文, 先肯定再点问题\"}。只输出 JSON。"
        + _fund_rule
    )
    user_prompt = f"以下是我的真实交易数据(已分'仍持有'和'已清仓'), 平衡复盘我的交易纪律, 别把我已经割掉的票当成还在死扛:\n\n{data_block}"

    try:
        raw = await asyncio.to_thread(llm_client.call_claude, user_prompt, system_prompt, "claude-opus-4-8", 4096)
    except Exception as e:
        return {"narrative": "", "discipline": [], "summary": "", "error": str(e), "generated_at": None}

    parsed = _parse_llm_json(raw)

    result = {
        "period": "all", "period_label": "全周期", "empty": False,
        "summary": parsed.get("summary", ""),
        "good": parsed.get("good", []) if isinstance(parsed.get("good"), list) else [],
        "discipline": parsed.get("discipline", []) if isinstance(parsed.get("discipline"), list) else [],
        "binchuan": parsed.get("binchuan", []) if isinstance(parsed.get("binchuan"), list) else [],
        "narrative": parsed.get("narrative", ""),
        "stats": {
            "win_rate": o["win_rate"], "buy_hit_rate": journal["buy_hit_rate"],
            "sell_hit_rate": journal["sell_hit_rate"], "avg_hold_days": o["avg_hold_days"],
            "n_stocks": o["n_stocks"],
        },
        "generated_at": time.time(),
    }
    _ai_review_cache[ck] = (result, time.time())
    return result


@router.get("/benchmark")
async def benchmark_compare(symbol: str = "sh000300", days: int = 0):
    """跑赢基准对照: 假设你 A 股的每次买卖, 同金额同日期都买在基准上 (默认沪深300).

    模型: dollar-matched 等额对比 (避免 IRR 复杂度).
      - 你每次 BUY/ADD (含手续费):  bench_shares += amount/bench_close_当日
      - 你每次 SELL/REDUCE:         bench_shares -= amount/bench_close_当日
      - 你当前 mv  = Σ shares × current_price
      - 你的总收益 = current_mv + sell_total - buy_total
      - 基准总收益 = bench_shares × today_close + sell_total - buy_total
      - α = 你的总收益 - 基准总收益
    手续费已含 (cost_price 是 broker-style 综合成本, 但 action.price 是裸价 — 这里
    用 price × shares + estimate_fee 累入 buy_total, 跟综合成本口径一致).

    days=0 全部历史; 否则只看最近 N 天的 action.
    支持 symbol: sh000300 / sh000001 / sz399006 / sh000688 等.
    """
    import asyncio as _asyncio
    from datetime import date, timedelta
    from services.market_data import _fetch_benchmark_history
    from services.position_ledger import estimate_trade_fee, ACQUIRE, RELEASE

    # 1) 拿基准价格序列 (近 1200 个交易日, ~5 年)
    df = await _asyncio.to_thread(_fetch_benchmark_history, symbol, 1200)
    if df is None or df.empty:
        raise HTTPException(503, f"基准 {symbol} 数据加载失败")
    bench_by_date = {row["date"]: float(row["close"]) for _, row in df.iterrows()}
    bench_dates_sorted = sorted(bench_by_date.keys())
    bench_today = float(df.iloc[-1]["close"])
    bench_last_date = str(df.iloc[-1]["date"])

    def bench_close_on_or_after(d: str) -> float | None:
        if d in bench_by_date:
            return bench_by_date[d]
        for bd in bench_dates_sorted:
            if bd >= d:
                return bench_by_date[bd]
        # d 晚于基准最新数据(今天/最近几天指数日线还没更新): 用最后一个可得收盘兜底,
        # 否则这几天的买卖会被整笔跳过, 导致"已收回/投入"漏算出现假的巨额盈亏。
        return bench_by_date[bench_dates_sorted[-1]] if bench_dates_sorted else None

    # 2) 收集所有 A 股 actions (跳过 HK./US.)
    from database import get_db
    db = await get_db()
    try:
        cursor = await db.execute("SELECT DISTINCT stock_code FROM position_actions")
        codes = [r[0] for r in await cursor.fetchall()]
    finally:
        await db.close()
    a_codes = [c for c in codes if c and not c.upper().startswith(("HK.", "US."))]

    all_actions: list[dict] = []
    for code in a_codes:
        all_actions.extend(await get_position_actions(code, limit=500))
    all_actions.sort(key=lambda a: (a.get("trade_date") or "", a.get("id") or 0))

    cutoff = None
    if days > 0:
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        all_actions = [a for a in all_actions if (a.get("trade_date") or "") >= cutoff]

    # 3) 累计现金流 + 基准等额份额
    buy_total = 0.0
    sell_total = 0.0
    bench_shares_total = 0.0
    skipped = 0
    first_action_date = None
    for a in all_actions:
        td = (a.get("trade_date") or "")[:10]
        if not td:
            skipped += 1; continue
        if first_action_date is None:
            first_action_date = td
        t = a.get("action_type", "")
        price = float(a.get("price") or 0)
        shares_i = int(a.get("shares") or 0)
        amount = price * shares_i
        if amount <= 0:
            continue
        # 手续费 (override or estimate, 跟综合成本口径对齐)
        fee_override = a.get("fee")
        code = a.get("stock_code") or ""
        fee = float(fee_override) if fee_override is not None else estimate_trade_fee(t, price, shares_i, code)
        close = bench_close_on_or_after(td)
        if close is None or close <= 0:
            skipped += 1; continue
        if t in ACQUIRE:
            cash_out = amount + fee   # 买入实际付出
            buy_total += cash_out
            bench_shares_total += cash_out / close
        elif t in RELEASE:
            cash_in = amount - fee    # 卖出实际收回
            sell_total += cash_in
            bench_shares_total -= cash_in / close
        # 其他类型 (DIVIDEND etc) 暂忽略

    # 4) 用户当前 A 股市值
    holdings = await get_all_holdings()
    a_holdings = [h for h in holdings
                  if not str(h.get("stock_code", "")).upper().startswith(("HK.", "US."))]
    user_cur_mv = 0.0
    if a_holdings:
        codes_now = [h["stock_code"] for h in a_holdings]
        try:
            quotes = await get_realtime_quotes(codes_now)
        except Exception:
            quotes = {}
        for h in a_holdings:
            q = quotes.get(h["stock_code"])
            price = float(q["price"]) if q and q.get("price") else 0.0
            if price > 0:
                user_cur_mv += price * int(h.get("shares") or 0)

    # 5) 计算
    user_pnl = user_cur_mv + sell_total - buy_total
    bench_cur_mv = max(0.0, bench_shares_total) * bench_today
    bench_pnl = bench_cur_mv + sell_total - buy_total
    alpha_pnl = user_pnl - bench_pnl

    user_ret_pct = (user_pnl / buy_total * 100) if buy_total > 0 else 0.0
    bench_ret_pct = (bench_pnl / buy_total * 100) if buy_total > 0 else 0.0

    return {
        "symbol": symbol,
        "window_days": days,
        "cutoff_date": cutoff,
        "first_action_date": first_action_date,
        "last_bench_date": bench_last_date,
        "action_count": len(all_actions),
        "skipped": skipped,
        "user": {
            "buy_total": round(buy_total, 2),
            "sell_total": round(sell_total, 2),
            "current_mv": round(user_cur_mv, 2),
            "pnl": round(user_pnl, 2),
            "return_pct": round(user_ret_pct, 2),
        },
        "benchmark": {
            "shares": round(bench_shares_total, 4),
            "today_close": round(bench_today, 2),
            "current_mv": round(bench_cur_mv, 2),
            "pnl": round(bench_pnl, 2),
            "return_pct": round(bench_ret_pct, 2),
        },
        "alpha": {
            "pnl_diff": round(alpha_pnl, 2),
            "pct_diff": round(user_ret_pct - bench_ret_pct, 2),
        },
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
    # 还有持仓 → 真重复, 让用户走「加仓」。shares=0 的已清仓标的允许从这里重新建仓,
    # 复用原有交易历史 (FIFO 续上, 之前的已实现盈亏保留)。
    if existing and (existing.get("shares") or 0) > 0:
        raise HTTPException(400, f"持仓 {stock_code} 已存在，请用「加仓」补仓")

    name = data.stock_name or (existing.get("stock_name") if existing else None)
    if not name:
        name = await get_stock_name(stock_code)

    # 1) 建持仓行 (已清仓标的的行还在, 不重复 INSERT, 顺手更新名字)
    if existing:
        await update_holding(stock_code, stock_name=name)
    else:
        await add_holding(stock_code, name, data.shares, data.cost_price)
    # 1b) 如果前端传了券商, 在 recompute 之前写入, 这样 _recompute_holding 能用正确的费率
    if data.broker:
        await update_holding(stock_code, broker=data.broker)
    # 2) 写一笔 BUY action,然后重算综合成本 (会自动加佣金/印花税/过户费)
    await add_position_action(
        stock_code, "BUY", data.cost_price, data.shares,
        note="re-entry (auto)" if existing else "initial (auto)",
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
    if data.broker is not None:
        kwargs["broker"] = data.broker

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
    """List all buy/sell actions for a stock, chronologically.
    每条附加 fee_effective (override 或估算的实际值) 和 fee_auto (估算值, 用作 UI placeholder).
    """
    stock_code = normalize_stock_code(stock_code)
    from services.position_ledger import estimate_trade_fee
    actions = await get_position_actions(stock_code, limit=500)
    from database import resolve_action_time
    is_a = stock_code and not stock_code.upper().startswith(("HK.", "US."))
    for a in actions:
        a["at_time"] = resolve_action_time(a)    # 成交时刻(供分时图打点)
        if is_a:
            est = estimate_trade_fee(a.get("action_type", ""), float(a.get("price") or 0),
                                     int(a.get("shares") or 0), stock_code)
            a["fee_auto"] = round(est, 2)
            a["fee_effective"] = round(float(a["fee"]) if a.get("fee") is not None else est, 2)
        else:
            a["fee_auto"] = 0.0
            a["fee_effective"] = round(float(a["fee"]) if a.get("fee") is not None else 0, 2)
    return actions


_ACQUIRE = {"BUY", "ADD"}


async def _auto_match_tranche(stock_code: str, action_type: str, price: float,
                              shares: int | None = None) -> dict | None:
    """自动撮合 action ↔ tranche (返回匹配的 tranche 或 None):

    ACQUIRE (BUY/ADD): pending tranche, 价格 ±5% 内取最近, mark executed.
    SELL/REDUCE 不自动撮合 — 用户应该走 UnwindCard 的「卖出回收」按钮,
    避免普通止损/调仓被误标为档位完成.
    """
    plan = await get_unwind_plan(stock_code)
    if not plan:
        return None
    tranches = await get_tranches(stock_code)

    if action_type in _ACQUIRE:
        pending = [t for t in tranches if t["status"] == "pending"]
        if not pending:
            return None
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

    return None


@router.post("/{stock_code}/actions")
async def create_action(stock_code: str, data: ActionCreate):
    """Add a new buy/sell action. Recomputes holding aggregate.

    If this is a BUY/ADD that matches a pending tranche's trigger price
    (within ±5%), auto-mark that tranche as executed so the plan view stays in sync.
    """
    stock_code = normalize_stock_code(stock_code)
    holding = await get_holding(stock_code)
    if not holding:
        raise HTTPException(404, f"持仓 {stock_code} 不存在")
    # 撮合在写 action 之前, 这样 tranche_id 能直接随 action 写入,
    # 避免 action 已写但 tranche 没标 (或反之) 的不一致状态被外部观察.
    matched = await _auto_match_tranche(
        stock_code, data.action_type, data.price, data.shares,
    )
    await add_position_action(
        stock_code=stock_code,
        action_type=data.action_type,
        price=data.price,
        shares=data.shares,
        trade_date=data.trade_date,
        note=data.note or "",
        tranche_id=(matched["id"] if matched else None),
        fee=data.fee,
        trade_time=(data.trade_time or None),
    )
    await _recompute_holding(stock_code)
    return {
        "message": "记录已添加",
        "matched_tranche": {"idx": matched["idx"], "trigger_price": matched["trigger_price"]} if matched else None,
    }


@router.put("/actions/{action_id}")
async def modify_action(action_id: int, data: ActionUpdate):
    """Edit an existing action. Recomputes holding aggregate.
    fee_set=true 时显式写 fee (None 表示清空覆盖, 回退自动估算)."""
    await update_position_action(
        action_id,
        action_type=data.action_type,
        price=data.price,
        shares=data.shares,
        trade_date=data.trade_date,
        note=data.note,
        fee=data.fee,
        fee_explicit=data.fee_set,
        trade_time=data.trade_time,
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
