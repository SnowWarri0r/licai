"""Sector radar endpoints."""
from __future__ import annotations
import asyncio
import hashlib
import json as _json
from datetime import datetime as _dt
from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel

from database import get_all_holdings
from services.sector_compare import get_sector_compare
from services.sector_matrix import get_sector_matrix
from services.sector_scanner import scan_sectors
from services.sector_us import scan_us_sectors
from services.sector_hk import scan_hk_sectors
from services.market_data import is_a_share
import services.llm_client as _llm
import api.news_routes as _news

router = APIRouter(prefix="/api/sector", tags=["sector"])


@router.get("/compare/{stock_code}")
async def compare_one(stock_code: str, force: bool = False):
    return await get_sector_compare(stock_code, force=force)


@router.get("/compare-all")
async def compare_all(force: bool = False):
    holdings = await get_all_holdings()
    # 只比当前真持仓 (shares>0): 已清仓的票不该出现在板块雷达里
    holdings = [h for h in holdings
                if is_a_share(h["stock_code"]) and float(h.get("shares") or 0) > 0]
    if not holdings:
        return {"holdings": []}
    results = await asyncio.gather(
        *(get_sector_compare(h["stock_code"], force=force) for h in holdings),
        return_exceptions=True,
    )
    out = []
    for h, r in zip(holdings, results):
        if isinstance(r, Exception):
            continue
        r["stock_name"] = h.get("stock_name", "")
        out.append(r)
    return {"holdings": out}


@router.get("/matrix")
async def sector_matrix(days: int = 10, force: bool = False):
    """板块趋势量化矩阵: 板块 × 过去 N 个交易日 日涨跌幅 + N日累计/净流入/动能。"""
    return await get_sector_matrix(days=days, force=force)


_trend_cache: dict = {}


@router.get("/trend-ai")
async def sector_trend_ai(days: int = 10, force: bool = False):
    """基于量化矩阵的板块趋势 AI 分析: 走强/退潮/轮动/资金流向 + 跟持仓关系。纯客观, 不给买卖建议。"""
    import time as _t
    ck = f"trend_{days}"
    c = _trend_cache.get(ck)
    if not force and c and _t.time() - c[1] < 1800:
        return c[0]

    m = await get_sector_matrix(days=days)
    rows = m.get("rows") or []
    if not rows:
        return {"summary": "", "trends": [], "holdings_note": "", "generated_at": None}

    # 用户持仓所在的 A 股板块(二级), 让 AI 点出跟持仓的关系
    from services.market_data import get_stock_sector_detail
    held = [h for h in await get_all_holdings() if is_a_share(h["stock_code"]) and float(h.get("shares") or 0) > 0]
    held_secs = set()
    for h in held:
        try:
            s = await get_stock_sector_detail(h["stock_code"])
            if s:
                held_secs.add(s)
        except Exception:
            pass

    _intraday_note = f"(末列 {m.get('today')} 为今日实时盘中涨跌, 未收盘)" if m.get("intraday") else ""
    lines = [f"过去 {m.get('days')} 个交易日板块矩阵({'/'.join(m.get('dates', []))}){_intraday_note}:"]
    for r in rows:
        lines.append(
            f"{r['name']}: 今日{r['today_pct']:+.1f}% 近{r['n_days']}日累计{r['cum_pct']:+.1f}% "
            f"连涨{r['streak']}天 净流入{r['net_inflow']:+.0f}亿 日序[{','.join(f'{p:+.1f}' for p in [x['pct'] for x in r['daily']])}]"
        )
    data_block = "\n".join(lines)
    held_line = ("我持仓所在板块: " + "、".join(sorted(held_secs))) if held_secs else "（无 A 股持仓）"

    system_prompt = (
        "你是板块趋势分析师。基于给定的'板块×近N交易日涨跌幅矩阵'+净流入+连涨动能, 客观分析板块趋势。\n"
        "若末列标注为今日实时盘中, 要重点描述今日盘中的板块走向/资金切换(并说明尚未收盘)。\n"
        "要点: 哪些板块在走强(持续放量上行/资金流入/连涨), 哪些在退潮(冲高回落/资金流出/转弱), "
        "有没有板块轮动迹象(强弱切换/资金从A板块流向B板块), 资金主线在哪。结合'我持仓所在板块'点出它当前在矩阵里的强弱位置。\n"
        "每条结论都要引用矩阵里的具体数字(板块名/累计涨幅/净流入/连涨/日序)。\n"
        "【硬规则】只做客观趋势描述, 严禁任何买卖/加减仓/该买该卖/目标价/现在适合 等操作建议。不编造矩阵里没有的板块或数字。\n"
        "JSON 输出: {\"summary\":\"一句话概括当前板块格局\", "
        "\"trends\":[{\"type\":\"走强/退潮/轮动/资金主线\",\"detail\":\"用矩阵数字说明\"}], "
        "\"holdings_note\":\"我持仓板块当前在矩阵中的强弱位置(客观)\"}。只输出 JSON。"
    )
    user_prompt = f"{held_line}\n\n{data_block}"
    try:
        raw = await asyncio.to_thread(_llm.call_claude, user_prompt, system_prompt, "claude-sonnet-4-5", 1600)
    except Exception as e:
        return {"summary": "", "trends": [], "holdings_note": "", "error": str(e)}

    txt = (raw or "").strip()
    if txt.startswith("```"):
        import re as _re
        txt = _re.sub(r"^```(json)?", "", txt).strip().rstrip("`").strip()
    try:
        parsed = _json.loads(txt)
    except Exception:
        for tail in ['"}', '"]}', '}]}', '"}]}']:
            try:
                parsed = _json.loads(txt + tail); break
            except Exception:
                parsed = {"summary": "", "trends": [], "holdings_note": ""}
    result = {
        "summary": parsed.get("summary", ""),
        "trends": parsed.get("trends", []) if isinstance(parsed.get("trends"), list) else [],
        "holdings_note": parsed.get("holdings_note", ""),
        "generated_at": _t.time(),
    }
    # 只有真拿到内容才缓存; LLM/代理抖动不污染整段缓存
    if result["summary"]:
        _trend_cache[ck] = (result, _t.time())
    return result


@router.get("/kline")
async def sector_kline(market: str = "A", key: str = "", days: int = 60):
    """单板块 K线 (OHLC, 支持周期切换). 放大图按 days 拉.
    market=A → key 是 THS 板块名; HK → 指数 symbol; US → SPDR ETF symbol.
    A 股走 THS(可达); HK/US 走 eastmoney(本网络可能不可达, 拉不到回空)。"""
    days = max(10, min(int(days or 60), 250))
    m = (market or "A").upper()
    if not key:
        return {"kline": [], "count": 0}
    try:
        if m == "A":
            from services.sector_compare import _fetch_ths_kline_sync
            rows = await asyncio.to_thread(_fetch_ths_kline_sync, key, days)
        elif m == "HK":
            from services.sector_hk import _fetch_index_kline_sync
            rows = await asyncio.to_thread(_fetch_index_kline_sync, key, days)
        elif m == "US":
            from services.sector_us import _fetch_etf_kline_sync
            rows = await asyncio.to_thread(_fetch_etf_kline_sync, key, days)
        else:
            rows = []
    except Exception as e:
        print(f"[sector-kline] {m}/{key} failed: {e}")
        rows = []
    from services.sector_compare import _ohlc_point
    tail = [_ohlc_point(k) for k in (rows or [])[-days:]]
    return {"kline": tail, "count": len(tail)}


@router.get("/scan")
async def scan(force: bool = False):
    """A 股全板块扫描: 90 个 THS 板块的 1d/5d/30d 涨幅 + 持仓标记 + 兜底 ETF.
    持仓标记同时考虑 A 股个股 + 行业 ETF (基金持仓里名字带 ETF 的)。"""
    holdings = await get_all_holdings()
    # 只算当前真持仓 (shares>0): 已清仓的票不该再标 held
    held_codes = [h["stock_code"] for h in holdings
                  if is_a_share(h["stock_code"]) and float(h.get("shares") or 0) > 0] if holdings else []
    # 行业 ETF: 从外部资产里捞持有中 (shares>0) 且名字带 ETF 的基金, 名字用于映射板块
    from database import list_external_assets
    assets = await list_external_assets()
    etf_names = [a.get("name") or "" for a in (assets or [])
                 if a.get("asset_type") == "FUND" and float(a.get("shares") or 0) > 0
                 and "ETF" in (a.get("name") or "")]
    return await scan_sectors(held_codes, etf_names=etf_names, force=force)


@router.get("/scan-us")
async def scan_us(force: bool = False):
    """美股板块扫描: 11 个 GICS 板块 (SPDR Sector ETFs)."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings
                  if str(h.get("stock_code", "")).upper().startswith("US.") and float(h.get("shares") or 0) > 0] if holdings else []
    return await scan_us_sectors(held_codes, force=force)


@router.get("/scan-hk")
async def scan_hk(force: bool = False):
    """港股板块扫描: 12 个恒生综合行业指数."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings
                  if str(h.get("stock_code", "")).upper().startswith("HK.") and float(h.get("shares") or 0) > 0] if holdings else []
    return await scan_hk_sectors(held_codes, force=force)


# ---------------------------------------------------------------------------
# POST /api/sector/why — LLM 解读板块异动原因 (快讯合成 + 缓存 + 降级)
# ---------------------------------------------------------------------------

_WHY_CACHE: dict[str, dict] = {}


class WhyIn(BaseModel):
    market: str
    name: str
    change_1d: Optional[float] = None
    change_5d: Optional[float] = None
    held: bool = False
    leader: Optional[str] = None


_WHY_SYS = (
    "你是板块异动解读助手。只解释板块为什么动, 严禁任何操作建议(买入/卖出/加仓/减仓/目标价/仓位都不许)。"
    "用简体中文输出严格 JSON, 两个键:\n"
    '{"why":"这个板块近期为什么动(1-2句, 结合快讯)","relation":"跟用户持仓/关注什么关系(没有就写\'与你当前持仓无直接关系\')"}'
    "\n只输出 JSON。料不足就直说不确定, 不要编造具体数字或事件。"
)

_MARKET_CN = {"A": "A股", "HK": "港股", "US": "美股"}


@router.post("/why")
async def sector_why(data: WhyIn):
    hour = _dt.now().strftime("%Y-%m-%d-%H")
    key = hashlib.sha1(f"{data.market}|{data.name}|{hour}".encode("utf-8")).hexdigest()
    if key in _WHY_CACHE:
        return {**_WHY_CACHE[key], "cached": True}
    try:
        mn = await _news.market_news()
        heads = [it.get("title", "") for it in (mn.get("items") or [])][:60]
    except Exception:
        heads = []
    news_block = "\n".join(f"- {h}" for h in heads if h) or "(近期无可用快讯)"
    try:
        holdings = await get_all_holdings()
        hold_desc = ", ".join(f"{h['stock_code']}({h.get('stock_name','')})" for h in holdings) or "(无持仓信息)"
    except Exception:
        hold_desc = "(无持仓信息)"
    moves = []
    if data.change_1d is not None:
        moves.append(f"1日 {data.change_1d:+.2f}%")
    if data.change_5d is not None:
        moves.append(f"5日 {data.change_5d:+.2f}%")
    user_prompt = (
        f"用户持仓: {hold_desc}\n\n"
        f"市场: {_MARKET_CN.get(data.market, data.market)}  板块: {data.name}"
        + (f"  领涨股: {data.leader}" if data.leader else "")
        + (f"  近期涨跌: {', '.join(moves)}" if moves else "")
        + "\n\n近期全球财经快讯(标题):\n" + news_block
        + "\n\n请据此按要求输出 JSON。"
    )
    try:
        raw = await asyncio.to_thread(_llm.call_claude, user_prompt, _WHY_SYS, "claude-sonnet-4-20250514", 500)
    except Exception:
        return {"why": "", "relation": "", "error": "解读暂不可用", "cached": False}
    parsed = None
    try:
        s = raw.strip()
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            parsed = _json.loads(s[i:j + 1])
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        parsed = {"why": raw.strip()[:300], "relation": ""}
    out = {
        "why": str(parsed.get("why") or "").strip(),
        "relation": str(parsed.get("relation") or "").strip(),
    }
    _WHY_CACHE[key] = out
    return {**out, "cached": False}
