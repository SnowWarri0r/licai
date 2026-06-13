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
    holdings = [h for h in holdings if is_a_share(h["stock_code"])]
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
    """A 股全板块扫描: 90 个 THS 板块的 1d/5d/30d 涨幅 + 持仓标记 + 兜底 ETF."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings if is_a_share(h["stock_code"])] if holdings else []
    return await scan_sectors(held_codes, force=force)


@router.get("/scan-us")
async def scan_us(force: bool = False):
    """美股板块扫描: 11 个 GICS 板块 (SPDR Sector ETFs)."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings if str(h.get("stock_code", "")).upper().startswith("US.")] if holdings else []
    return await scan_us_sectors(held_codes, force=force)


@router.get("/scan-hk")
async def scan_hk(force: bool = False):
    """港股板块扫描: 12 个恒生综合行业指数."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings if str(h.get("stock_code", "")).upper().startswith("HK.")] if holdings else []
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
