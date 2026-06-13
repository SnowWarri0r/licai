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
    get_realtime_quotes, get_stock_name, get_stock_sector,
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
    note: Optional[str] = ""
    fee: Optional[float] = None    # CNY override; None 让后端按券商费率自动算


class ActionUpdate(BaseModel):
    action_type: Optional[str] = None
    price: Optional[float] = None
    shares: Optional[int] = None
    trade_date: Optional[str] = None
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
                "current": round(cur, 3), "pct": pct, "hit": hit,
            })

    trades.sort(key=lambda x: x["date"], reverse=True)
    buys = [t for t in trades if t["kind"] == "buy"]
    sells = [t for t in trades if t["kind"] == "sell"]
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


@router.get("/trade-review-ai")
async def trade_review_ai(force: int = 0):
    """LLM 交易纪律复盘: 用真实流水(trade_review + trade_journal 的数据)复盘交易习惯,
    重点指出纪律问题(追高/情绪化反复买卖/不止损/持有过短)。纯客观举证, 严禁任何未来买卖建议。"""
    import time, asyncio, json
    from services import llm_client

    ck = "trade_review_ai"
    if not force:
        c = _ai_review_cache.get(ck)
        if c and time.time() - c[1] < _AI_REVIEW_TTL:
            return c[0]

    review = await trade_review()
    journal = await trade_journal(limit=400)
    o = review.get("overview") or {}
    stats = review.get("stats") or []
    if not o.get("n_stocks"):
        return {"narrative": "", "discipline": [], "summary": "", "generated_at": None}

    held = [s for s in stats if s["shares"] > 0]      # 还拿着的
    closed = [s for s in stats if s["shares"] <= 0]   # 已清仓(割了/卖了)
    held_names = {s["name"] for s in held}

    # 仍持有的票, 每笔买入对现价(只有还持有时"套着"才成立)
    held_buys_under = sorted(
        [t for t in journal["trades"] if t["kind"] == "buy" and not t["hit"] and t["name"] in held_names],
        key=lambda x: x["pct"])[:8]
    # 同股多次买入, 只看仍持有的(已清仓的别拿现价说"还在补/还套着")
    by_stock: dict = {}
    for t in journal["trades"]:
        if t["kind"] == "buy" and t["name"] in held_names:
            by_stock.setdefault(t["name"], []).append(t)
    repeat_buys = {n: sorted(ts, key=lambda x: x["date"]) for n, ts in by_stock.items() if len(ts) >= 3}

    closed_win = [s for s in closed if s["realized"] > 0]
    closed_loss = [s for s in closed if s["realized"] < 0]
    closed_loss_sorted = sorted(closed_loss, key=lambda s: s["realized"])

    lines = [
        f"总览: 交易过 {o['n_stocks']} 只, 已实现合计 {o['total_realized']:.0f}, 当前持仓平均持有 {o['avg_hold_days']} 天",
        f"卖出命中率 {round(journal['sell_hit_rate']*100)}% ({journal['sell_hit']}/{journal['sell_count']} 笔卖完股价确实跌了) — 卖点把握",
        "",
        f"【仍持有 {len(held)} 只】(这些说'浮亏/套着'才成立):",
    ]
    for s in held:
        floating = s.get("floating", 0)
        lines.append(f"  {s['name']}: 持{s['shares']:.0f}股, 浮动{floating:+.0f}, 已实现{s['realized']:+.0f}, {s['n_buy']}买{s['n_sell']}卖")
    if held_buys_under:
        lines.append("  仍套着的买入(对现价): " + "; ".join(
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

    data_block = "\n".join(lines)
    system_prompt = (
        "你是交易复盘教练。基于用户真实的 A 股交易流水, 复盘他的交易纪律, 像一面镜子照清楚他的习惯——"
        "该夸的夸、该点的点, 要平衡客观, 不是单纯挑刺骂人。\n"
        "【最重要·别冤枉他】数据分两类: '仍持有'和'已清仓'。已清仓=他已经卖掉离场了, 亏损票=他割肉止损了, "
        "这是执行了纪律, 绝对不许说他'还在死扛/装死/越套越补/现在还亏着'——他早卖了。"
        "'浮亏/套着/还拿着'这种话只能用在'仍持有'的票上。\n"
        "先认可做对的(已实现赚钱的票、卖出命中率高=卖点准、亏了能割肉止损), 再指出真问题: "
        "追涨追高(仍持有的票越买越高)、情绪性高频做T但净亏、追涨杀跌持有过短。每条都用具体数据举证。\n"
        "【硬规则】严禁任何面向未来的操作指令: 不许出现 该买/该卖/加仓/减仓/止损位/目标价/仓位建议/现在适合。"
        "只复盘已发生的行为, 不指挥下一步。不许编造给定数据里没有的票或数字。\n"
        "用 JSON 输出: {\"summary\":\"一句话客观定性(好坏都讲)\", "
        "\"good\":[\"做对的点(用数据)\", ...], "
        "\"discipline\":[{\"problem\":\"真实存在的问题\",\"evidence\":\"具体数据举证\",\"why\":\"暴露了什么习惯\"}], "
        "\"narrative\":\"2-3段复盘正文, 先肯定再点问题\"}。只输出 JSON。"
    )
    user_prompt = f"以下是我的真实交易数据(已分'仍持有'和'已清仓'), 平衡复盘我的交易纪律, 别把我已经割掉的票当成还在死扛:\n\n{data_block}"

    try:
        raw = await asyncio.to_thread(llm_client.call_claude, user_prompt, system_prompt, "claude-sonnet-4-5", 2800)
    except Exception as e:
        return {"narrative": "", "discipline": [], "summary": "", "error": str(e), "generated_at": None}

    txt = (raw or "").strip()
    if txt.startswith("```"):
        txt = txt.split("```", 2)[1] if txt.count("```") >= 2 else txt.strip("`")
        if txt.startswith("json"):
            txt = txt[4:]
        txt = txt.strip()
    try:
        parsed = json.loads(txt)
    except Exception:
        parsed = {"narrative": raw or "", "discipline": [], "summary": ""}

    result = {
        "summary": parsed.get("summary", ""),
        "good": parsed.get("good", []) if isinstance(parsed.get("good"), list) else [],
        "discipline": parsed.get("discipline", []) if isinstance(parsed.get("discipline"), list) else [],
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
        return None

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
    is_a = stock_code and not stock_code.upper().startswith(("HK.", "US."))
    for a in actions:
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
