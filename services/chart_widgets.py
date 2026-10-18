"""问答答案里嵌的交互组件的数据: 走势对比、股票卡片(打开详情弹窗用)。

agent 在正文里写组件标记(⟦K线:代码⟧ / ⟦分时:代码⟧ / ⟦对比:a,b⟧), 前端就地渲染成真组件。
K线和分时组件复用现成端点自取数; 对比要把几种来源(个股/ETF、指数、板块)对齐成同一把尺子,
放在这里。
"""
from __future__ import annotations
import asyncio
import re

MAX_ITEMS = 6
_INDEX_NAMES = {"sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
                "sh000688": "科创50", "sh000300": "沪深300", "sh000905": "中证500",
                "sh000852": "中证1000", "bj899050": "北证50"}
_MACRO = re.compile(r"^(sh|sz|bj|hk|gb_|us|int_|znb_|nf_|hf_|fx_)", re.I)


async def _resolve(item: str) -> dict:
    """一项 → {kind, key, label}。kind: code(个股/ETF) | macro(指数等带前缀符号) | sector(板块)。"""
    s = (item or "").strip()
    if s.startswith(("板块:", "板块：")):
        name = s[3:].strip()
        return {"kind": "sector", "key": name, "label": name}
    if re.fullmatch(r"\d{6}", s) or re.match(r"^(HK|US)\.", s, re.I):
        return {"kind": "code", "key": s, "label": await _name_of(s) or s}
    if _MACRO.match(s) and re.search(r"\d|_", s):
        return {"kind": "macro", "key": s, "label": _INDEX_NAMES.get(s.lower(), s)}
    # 名字: 走 agent 同一个 resolve(代码表 + 账本)
    import services.stock_agent as sa
    r = await sa._EXECUTORS["resolve_stock"]({"query": s})
    if isinstance(r, dict) and r.get("code"):
        return {"kind": "code", "key": r["code"], "label": r.get("name") or s}
    return {"kind": "unknown", "key": s, "label": s}


async def _name_of(code: str) -> str:
    try:
        import services.stock_agent as sa
        r = await sa._EXECUTORS["resolve_stock"]({"query": code})
        return (r or {}).get("name") or ""
    except Exception:
        return ""


async def _daily(it: dict, days: int) -> list[tuple[str, float]]:
    if it["kind"] == "code":
        from services.market_data import get_historical_data, normalize_stock_code
        df = await get_historical_data(normalize_stock_code(it["key"]), days)
        if df is None or df.empty:
            return []
        return [(str(r["日期"])[:10], float(r["收盘"])) for _, r in df.iterrows() if r.get("收盘")]
    if it["kind"] == "macro":
        from services.market_data import _kline_for_symbol
        rows = await asyncio.to_thread(_kline_for_symbol, it["key"], days)
        return [(str(r["date"])[:10], float(r["close"])) for r in rows or [] if r.get("close")]
    if it["kind"] == "sector":
        from api.sector_routes import sector_kline
        d = await sector_kline(market="A", key=it["key"], days=days)
        return [(str(r["date"])[:10], float(r["close"])) for r in (d or {}).get("kline") or []
                if r.get("close")]
    return []


async def _intraday(it: dict) -> tuple[list[tuple[str, float]], str]:
    """当日分时(个股/ETF), 返回 [(HH:MM, 价)] 与日期。指数和板块没有分时源, 返回空。"""
    if it["kind"] != "code":
        return [], ""
    import services.tdx_client as tdx
    if not tdx.is_enabled():
        return [], ""
    d = await tdx.minute(re.sub(r"\D", "", it["key"])[-6:])
    pts = [(p["time"], float(p["price"])) for p in (d or {}).get("points") or [] if p.get("price")]
    return pts, (d or {}).get("date") or ""


def _normalize(rows: list[tuple[str, float]], start: str | None) -> list[dict]:
    """从 start 那个点起算涨跌幅%。基准取 start 当天(或之后第一天)的收盘。"""
    rows = [r for r in rows if start is None or r[0] >= start]
    if not rows or not rows[0][1]:
        return []
    base = rows[0][1]
    return [{"t": t, "pct": round((v / base - 1) * 100, 2)} for t, v in rows]


async def compare(items: list[str], days: int = 20, mode: str = "日") -> dict:
    items = [x for x in (items or []) if x.strip()][:MAX_ITEMS]
    resolved = await asyncio.gather(*[_resolve(x) for x in items])
    out, notes = [], []
    if mode == "分时":
        fetched = await asyncio.gather(*[_intraday(it) for it in resolved])
        for it, (rows, _day) in zip(resolved, fetched):
            if not rows:
                notes.append(f"{it['label']} 没有分时数据(分时只支持个股/ETF)")
                continue
            out.append({"label": it["label"], "code": it["key"] if it["kind"] == "code" else "",
                        "points": _normalize(rows, None)})
        return {"mode": "分时", "series": out, "notes": notes,
                "basis": "以当日第一笔成交价为 0%"}
    fetched = await asyncio.gather(*[_daily(it, days + 5) for it in resolved])
    # 对齐到所有序列共同的起点, 否则停牌/假期不同的几条线基准日不一样, 没法比
    have = [rows[-days:] for rows in fetched if rows]
    start = max(r[0][0] for r in have) if have else None
    for it, rows in zip(resolved, fetched):
        if not rows:
            notes.append(f"{it['label']} 没取到日线")
            continue
        out.append({"label": it["label"], "code": it["key"] if it["kind"] == "code" else "",
                    "points": _normalize(rows, start)})
    return {"mode": "日", "series": out, "notes": notes, "basis": f"以 {start} 收盘为 0%" if start else ""}


async def stock_card(code: str) -> dict:
    """打开详情弹窗要的字段: 名称、现价、持有时的成本/份额; 场内 ETF 带 asset_id 走资产流水。"""
    from database import get_db
    from services.market_data import get_realtime_quotes, normalize_stock_code
    c = normalize_stock_code(code)
    card = {"stock_code": c, "stock_name": await _name_of(code) or c, "cost_price": 0, "shares": 0}
    db = await get_db()
    try:
        row = await (await db.execute(
            "SELECT stock_name, shares, cost_price FROM holdings WHERE stock_code = ?", (c,))).fetchone()
        if row and (row[1] or 0) > 0:
            card.update(stock_name=row[0] or card["stock_name"], shares=row[1], cost_price=row[2] or 0)
        ext = await (await db.execute(
            "SELECT id, name, shares, cost_amount FROM external_assets WHERE code = ? "
            "AND COALESCE(closed, 0) = 0 ORDER BY id DESC LIMIT 1", (c,))).fetchone()
        if ext:
            card["asset_id"] = ext[0]
            if (ext[2] or 0) > 0:
                card.update(shares=ext[2], cost_price=round((ext[3] or 0) / ext[2], 4))
    finally:
        await db.close()
    try:
        q = (await get_realtime_quotes([c])).get(c) or {}
        card.update(current_price=q.get("price"), price_change_pct=q.get("change_pct"))
    except Exception:
        pass
    return card
