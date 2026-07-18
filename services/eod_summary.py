"""收盘持仓小结: 交易日 15:10 自动生成并推飞书(纯数据拼装, 不走 LLM)。

内容 = 持仓今日涨跌(按贡献排) + 触发的事件(上龙虎榜/涨停跌停/业绩预告今日披露)
+ 大盘一行(指数/涨跌家数/涨停跌停)。纯客观数据, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import datetime


def _limit_hit(code: str, pct: float | None) -> str:
    """按板块涨跌幅限制近似判涨停/跌停(留 0.2% 容差)。"""
    if pct is None:
        return ""
    c = str(code)
    lim = 20.0 if (c.startswith("688") or c.startswith("30")) else 30.0 if c[0] in "84" else 10.0
    if pct >= lim - 0.2:
        return "涨停"
    if pct <= -(lim - 0.2):
        return "跌停"
    return ""


async def build_eod_summary() -> dict:
    from database import list_watchlist
    from services.market_data import get_realtime_quotes
    from api.portfolio_routes import list_holdings   # 持仓口径与看板一致(流水现算, 已剔清仓)

    holdings = await list_holdings()
    pos = {}                                    # code -> name(在持)
    rows = []
    for h in holdings:
        pct, mv = h.price_change_pct, h.market_value
        if not h.shares:
            continue
        pos[h.stock_code] = h.stock_name
        chg = round(mv * pct / (100 + pct)) if (mv and pct is not None and pct > -100) else None
        rows.append({"code": h.stock_code, "name": h.stock_name,
                     "pct": pct, "今日浮动": chg, "limit": _limit_hit(h.stock_code, pct)})
    watch_codes = [w["code"] for w in await list_watchlist() if w["code"] not in pos]
    quotes = await get_realtime_quotes(watch_codes) if watch_codes else {}
    rows.sort(key=lambda r: abs(r["今日浮动"] or 0), reverse=True)
    total_chg = round(sum(r["今日浮动"] or 0 for r in rows))

    # 事件: 持仓/自选 上龙虎榜
    events = []
    try:
        from services.lhb_detail import lhb_daily
        today = datetime.date.today().isoformat()
        ld = await lhb_daily()
        if ld.get("date") == today:
            for r in ld.get("rows") or []:
                if r["code"] in pos or r["code"] in watch_codes:
                    tag = "持仓" if r["code"] in pos else "自选"
                    events.append(f"{r['name']}({tag}) 上龙虎榜 净买{r['净买额亿']:+.2f}亿 · {r['解读'] or r['上榜原因']}")
    except Exception:
        pass
    # 事件: 涨停/跌停
    for r in rows:
        if r["limit"]:
            events.append(f"{r['name']}(持仓) {r['limit']}")
    for code in watch_codes:
        q = quotes.get(code) or {}
        lm = _limit_hit(code, q.get("change_pct"))
        if lm:
            events.append(f"{q.get('stock_name') or code}(自选) {lm}")
    # 事件: 业绩预告今日披露
    try:
        from services.coiled_scanner import _forecast_map
        fc = await asyncio.to_thread(_forecast_map)
        today = datetime.date.today().isoformat()
        for code in list(pos) + watch_codes:
            f = fc.get(code)
            if f and f.get("日期") == today:
                who = "持仓" if code in pos else "自选"
                chg = f.get("幅度%")
                nm = pos.get(code) or (quotes.get(code) or {}).get("stock_name") or code
                events.append(f"{nm}({who}) 今日披露{f.get('期', '')}预告: {f.get('类型', '')}"
                              + (f" {chg:+.0f}%" if chg is not None else ""))
    except Exception:
        pass

    # 大盘一行
    mkt = ""
    try:
        from api.market_routes import market_sentiment
        s = await market_sentiment()
        b = s.get("breadth") or {}
        mkt = (f"涨停{s.get('n_zt')} 跌停{s.get('n_dt')} 炸板率{s.get('zbl_rate')}%"
               + (f" · 全市场 {b.get('上涨')}涨/{b.get('下跌')}跌" if b else "")
               + f" · {s.get('mood', '')}")
    except Exception:
        pass

    d_cn = datetime.date.today().strftime("%m-%d") + "(周" + "一二三四五六日"[datetime.date.today().weekday()] + ")"
    lines = [f"{d_cn} 收盘小结",
             f"股票持仓今日浮动 {total_chg:+,.0f} 元" if rows else "当前无股票持仓(自选与大盘照报)"]
    for r in rows[:10]:
        if r["pct"] is None:
            continue
        lines.append(f"· {r['name']} {r['pct']:+.2f}%" + (f" ({r['今日浮动']:+,.0f})" if r["今日浮动"] else ""))
    if len(rows) > 10:
        rest = sum(r["今日浮动"] or 0 for r in rows[10:])
        lines.append(f"· 其余{len(rows) - 10}只合计 {rest:+,.0f}")
    if events:
        lines.append("— 事件 —")
        lines += [f"- {e}" for e in events[:8]]
    if mkt:
        lines.append(f"大盘: {mkt}")
    lines.append("纯客观数据, 不构成任何买卖建议")
    return {"date": datetime.date.today().isoformat(), "text": "\n".join(lines),
            "total_change": total_chg, "rows": rows, "events": events}


async def push_eod_summary() -> dict:
    from services import feishu_notify
    r = await build_eod_summary()
    if feishu_notify.is_enabled():
        await feishu_notify.send_text(r["text"])
        r["pushed"] = True
    else:
        r["pushed"] = False
    return r
