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
    from services.market_data import code_currency, get_realtime_quotes, is_a_share, split_stock_code
    wl = await list_watchlist()

    # 持仓自动并入(置顶「持」组): 渲染时现取, 不写自选表——清仓自动消失, 不污染自选口径。
    # 并个股: A 股 6 位代码 + 港股/美股(HK./US. 带前缀); 场外基金/ETF 有各自看板, 不塞这里。
    holds = []
    try:
        from services.stock_agent import _active_holdings
        for h in await _active_holdings():
            c = h.get("stock_code") or ""
            mkt = split_stock_code(c)[0] if c else ""
            if (mkt == "A" and is_a_share(c) and len(c) == 6 and c.isdigit()) or mkt in ("HK", "US"):
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
            "持有天数": h.get("hold_days"), "综合成本": cost, "币种": code_currency(code), "浮盈%": floating,
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
            # 分组是列表(一票多组), 空列表=还没归组; group_orders 是 {组名: 组内位次}
            "分组": list(r.get("groups") or []),
            "sort_order": r.get("sort_order") or 0,
            "group_orders": dict(r.get("group_orders") or {}),
            "自选以来%": since, "结构": tags.get(code, ""), "业绩预告": _fc_txt(code),
        })
    # 已有分组名(供前端下拉/移动菜单), 按名称排; 持仓组是虚拟组不算在内
    groups = sorted({g for r in wl for g in (r.get("groups") or [])})
    return {"rows": out, "n_hold": len(holds), "n_watch": len(wl), "groups": groups,
            "note": "持仓组自动置顶(现取, 清仓即消失); 手动自选=在看未必持有。结构标签与业绩预告为客观描述, 不构成买卖建议。"}


# ── 批量添加: 粘贴文本 → 解析成代码 ──────────────────────────────

_SPLIT_RE = r"[\s,，、;；|/]+"
_NOISE_RE = r"[-+]?\d+(\.\d+)?%?"          # 价格/涨跌幅/数量等纯数字(5/6 位整数代码除外)
_MAX_TOKENS = 200


async def parse_import_text(text: str) -> dict:
    """把一段粘贴文本解析成观察池代码。只认确定的匹配: A 股/场内基金 6 位代码(可带
    sh/sz/bj 前后缀)、港股 5 位或 HK 前缀、美股 US. 前缀或大写 ticker、A 股精确名称;
    其余放进 unresolved, 附上至多 3 个候选让用户自己挑, 不按模糊匹配直接收。
    同一只票出现多次(代码和名称都贴了)只算一次。
    返回 {resolved:[{code,name,token}], unresolved:[{token,suggest:[{code,name}]}], truncated}"""
    import re
    from services.stock_agent import _code_name_maps, _tool_resolve_stock, _us_ticker_hit
    from services.market_data import get_realtime_quotes
    toks = []
    for t in re.split(_SPLIT_RE, text or ""):
        t = t.strip().strip("()（）[]【】\"'“”")
        if not t or (re.fullmatch(_NOISE_RE, t) and not re.fullmatch(r"\d{5,6}", t)):
            continue
        if t not in toks:
            toks.append(t)
    truncated = len(toks) > _MAX_TOKENS
    toks = toks[:_MAX_TOKENS]
    n2c, c2n = await _code_name_maps()

    resolved: dict[str, dict] = {}
    unresolved = []
    hk_pending: list[tuple[str, str]] = []
    for t in toks:
        m = re.fullmatch(r"(?i)(?:sh|sz|bj)?(\d{6})(?:\.(?:sh|sz|bj))?", t)
        if m and m.group(1) in c2n:
            resolved.setdefault(m.group(1), {"code": m.group(1), "name": c2n[m.group(1)], "token": t})
            continue
        m = re.fullmatch(r"(?i)(?:hk\.?)?(\d{1,5})", t)
        if m and (len(t) == 5 or t[:2].lower() == "hk"):
            hk_pending.append((t, f"HK.{int(m.group(1)):05d}"))
            continue
        m = re.fullmatch(r"(?i)us\.([a-z][a-z.]{0,6})", t) or re.fullmatch(r"([A-Z]{1,5})", t)
        if m:
            hit = await _us_ticker_hit(m.group(1).upper())
            if hit:
                resolved.setdefault(hit["code"], {"code": hit["code"], "name": hit["name"], "token": t})
                continue
        if t in n2c:
            resolved.setdefault(n2c[t], {"code": n2c[t], "name": t, "token": t})
            continue
        suggest = []
        try:
            r = await _tool_resolve_stock(t)
            if r.get("code") and (r.get("name") == t) and not r.get("asset_class"):
                resolved.setdefault(r["code"], {"code": r["code"], "name": t, "token": t})
                continue
            cands = r.get("candidates") or ([{"code": r["code"], "name": r.get("name")}] if r.get("code") else [])
            suggest = [{"code": str(c["code"]), "name": c.get("name") or str(c["code"])}
                       for c in cands if not c.get("asset_class")][:3]
        except Exception:
            pass
        unresolved.append({"token": t, "suggest": suggest})
    if hk_pending:
        quotes = await get_realtime_quotes([c for _, c in hk_pending])
        for t, c in hk_pending:
            q = quotes.get(c) or {}
            if q.get("price"):
                resolved.setdefault(c, {"code": c, "name": q.get("stock_name") or c, "token": t})
            else:
                unresolved.append({"token": t, "suggest": []})
    return {"resolved": list(resolved.values()), "unresolved": unresolved, "truncated": truncated}
