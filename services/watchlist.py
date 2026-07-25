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
    from services.market_data import get_realtime_quotes, is_a_share, normalize_stock_code
    wl = await list_watchlist()

    # 持仓自动并入(置顶「持」组): 渲染时现取, 不写自选表——清仓自动消失, 不污染自选口径。
    # 只并 A 股个股(自选/结构扫描针对个股); 场外基金/ETF 有各自看板, 不塞这里。
    holds = []
    try:
        from services.stock_agent import _active_holdings
        for h in await _active_holdings():
            c = h.get("stock_code") or ""
            if c and is_a_share(normalize_stock_code(c)) and len(c) == 6 and c.isdigit():
                holds.append(h)
    except Exception:
        holds = []
    hold_codes = {h["stock_code"] for h in holds}

    # 手动自选里剔掉已持仓的(持仓组已覆盖), 避免重复行
    wl = [r for r in wl if r["code"] not in hold_codes]
    if not holds and not wl:
        return {"rows": [], "note": "自选池为空。持有个股会自动出现在这里; 也可在榜单点股票右上角 ☆ 手动加入跟踪。"}

    codes = [h["stock_code"] for h in holds] + [r["code"] for r in wl]
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
    tags = dict(zip(codes, await asyncio.gather(*(_structure_tags(c) for c in codes))))

    def _fc_txt(code):
        f = fc.get(code) or {}
        if not f:
            return ""
        chg = f.get("幅度%")
        return f"{f.get('期', '')}{f.get('类型', '')}" + (f" {chg:+.0f}%" if chg is not None else "")

    out = []
    # 1) 持仓组(置顶): 显示浮盈/持有天数, 不显示"自选以来"
    for h in holds:
        code = h["stock_code"]
        q = quotes.get(code) or {}
        price = q.get("price")
        cost = h.get("cost_price")
        floating = round((price / cost - 1) * 100, 2) if (price and cost) else None
        out.append({
            "code": code, "name": q.get("stock_name") or h.get("stock_name") or code,
            "pct": q.get("change_pct"), "price": price, "source": "持仓",
            "行业": (inds.get(code) or ("", ""))[1],
            "持有天数": h.get("hold_days"), "综合成本": cost, "浮盈%": floating,
            "结构": tags.get(code, ""), "业绩预告": _fc_txt(code),
        })
    # 2) 手动自选组: 显示"自选以来"
    for r in wl:
        code = r["code"]
        q = quotes.get(code) or {}
        price = q.get("price")
        since = round((price / r["added_price"] - 1) * 100, 2) if (price and r.get("added_price")) else None
        out.append({
            "code": code, "name": q.get("stock_name") or r["name"],
            "pct": q.get("change_pct"), "price": price, "source": "自选",
            "added_at": r["added_at"], "added_price": r.get("added_price"),
            "行业": (inds.get(code) or ("", ""))[1],
            "自选以来%": since, "结构": tags.get(code, ""), "业绩预告": _fc_txt(code),
        })
    return {"rows": out, "n_hold": len(holds), "n_watch": len(wl),
            "note": "持仓组自动置顶(现取, 清仓即消失); 手动自选=在看未必持有。结构标签与业绩预告为客观描述, 不构成买卖建议。"}
