"""自选观察池: 在跟踪但未必持有的票, 每天看结构还完不完好。

行=实时行情 + 自选以来涨跌 + 结构形态标签(复用 agent 的 _structure_scan)
+ 业绩预告凭据。纯客观结构描述, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import time

_struct_cache: dict = {}
_STRUCT_TTL = 1800


async def _structure_tags(code: str) -> str:
    """近120日K → 结构形态标签串(阶梯式上行/2B假突破/头肩顶…)。缓存30min。"""
    c = _struct_cache.get(code)
    if c and time.time() - c[1] < _STRUCT_TTL:
        return c[0]
    tags = ""
    try:
        from services.market_data import get_historical_data
        from services.stock_agent import _structure_scan
        df = await get_historical_data(code, 120)
        if df is not None and len(df) >= 30:
            closes = [float(x) for x in df["收盘"]]
            highs = [float(x) for x in df["最高"]]
            lows = [float(x) for x in df["最低"]]
            vols = [float(x) for x in df.get("成交量", df["收盘"] * 0)]
            st = _structure_scan(closes, highs, lows, vols)
            # 只取形态名(值为True的布尔标记); 台阶价位/距支撑%等明细数值不进标签串
            names = [k for k, v in st.items() if v is True]
            if st.get("回调量能"):
                names.append(f"回调{st['回调量能']}")
            tags = " · ".join(names[:3])
    except Exception:
        tags = ""
    _struct_cache[code] = (tags, time.time())
    return tags


async def watchlist_view() -> dict:
    from database import list_watchlist
    from services.market_data import get_realtime_quotes
    rows = await list_watchlist()
    if not rows:
        return {"rows": [], "note": "自选池为空。榜单里点股票右上角 ☆ 加入跟踪。"}

    codes = [r["code"] for r in rows]
    quotes = await get_realtime_quotes(codes)

    fc = {}
    try:
        from services.coiled_scanner import _forecast_map
        fc = await asyncio.to_thread(_forecast_map)
    except Exception:
        fc = {}
    inds = {}
    try:
        from services.etf_xray import industry_map
        inds = await asyncio.to_thread(industry_map)
    except Exception:
        inds = {}

    tags = await asyncio.gather(*(_structure_tags(c) for c in codes))
    out = []
    for r, tg in zip(rows, tags):
        q = quotes.get(r["code"]) or {}
        price = q.get("price")
        since = None
        if price and r.get("added_price"):
            since = round((price / r["added_price"] - 1) * 100, 2)
        f = fc.get(r["code"]) or {}
        fc_txt = ""
        if f:
            chg = f.get("幅度%")
            fc_txt = f"{f.get('期', '')}{f.get('类型', '')}" + (f" {chg:+.0f}%" if chg is not None else "")
        out.append({
            "code": r["code"], "name": q.get("stock_name") or r["name"],
            "pct": q.get("change_pct"), "price": price,
            "added_at": r["added_at"], "added_price": r.get("added_price"),
            "行业": (inds.get(r["code"]) or ("", ""))[1],
            "自选以来%": since, "结构": tg,
            "业绩预告": fc_txt,
        })
    return {"rows": out,
            "note": "自选=纯跟踪清单, 结构标签与业绩预告为客观描述, 不构成任何买卖建议。"}
