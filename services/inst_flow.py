"""机构席位动向(龙虎榜机构买卖统计): 机构在买什么/卖什么, 买完之后走势如何。

数据 = 东财数据中心「机构买卖每日统计」——个股上龙虎榜时披露的机构专用席位买卖额。
市场说的"机构被套在山顶", 底层就是这份数据: 大额净买入日之后股价回撤。
注意: 只有上榜日才有披露(非机构全量持仓变动), 属抽样视角。
这里只给客观数字与日期(净买额/上榜日/距上榜日涨跌), 判断留给用户;
纯客观展示, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

_cache: dict = {}
_TTL = 1800
_WINDOW_DAYS = 30


def _no_proxy():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)


def _fetch_rows_sync() -> list[dict]:
    """近 N 天机构买卖统计, 按 (代码, 上榜日) 去重(同日多条上榜原因, 金额相同)。"""
    _no_proxy()
    import akshare as ak
    end = date.today()
    start = end - timedelta(days=_WINDOW_DAYS)
    df = None
    for attempt in range(3):
        try:
            df = ak.stock_lhb_jgmmtj_em(start_date=start.strftime("%Y%m%d"),
                                        end_date=end.strftime("%Y%m%d"))
            if df is not None and len(df):
                break
        except Exception:
            time.sleep(0.6 * (attempt + 1))
    if df is None:
        return []
    seen, out = set(), []
    for _, r in df.iterrows():
        code = str(r.get("代码") or "").zfill(6)
        d = str(r.get("上榜日期") or "")[:10]
        if not code or not d or (code, d) in seen:
            continue
        seen.add((code, d))
        try:
            out.append({
                "code": code, "name": str(r.get("名称") or ""),
                "date": d,
                "close": float(r.get("收盘价") or 0),
                "净买额": float(r.get("机构买入净额") or 0),
                "占成交%": round(float(r.get("机构净买额占总成交额比") or 0), 2),
                "原因": str(r.get("上榜原因") or "")[:30],
            })
        except (TypeError, ValueError):
            continue
    return out


def aggregate_inst_flow(events: list[dict], quotes: dict) -> list[dict]:
    """按票聚合(纯函数可测): 累计净买额 + 最近/首次上榜日 + 距上榜日至今涨跌。
    quotes: {code: {"price": ..}}"""
    by: dict[str, list[dict]] = {}
    for e in events:
        by.setdefault(e["code"], []).append(e)
    rows = []
    for code, evs in by.items():
        evs.sort(key=lambda x: x["date"])
        first, last = evs[0], evs[-1]
        cur = (quotes.get(code) or {}).get("price")
        def _since(ev):
            if cur and ev.get("close"):
                return round((cur / ev["close"] - 1) * 100, 1)
            return None
        rows.append({
            "code": code, "name": last["name"],
            "上榜次数": len(evs),
            "机构净买亿": round(sum(e["净买额"] for e in evs) / 1e8, 2),
            "最近上榜": last["date"], "最近上榜日收盘": last["close"],
            "距最近上榜%": _since(last),
            "首次上榜": first["date"], "距首次上榜%": _since(first),
            "现价": cur,
            "events": [{"date": e["date"], "净买亿": round(e["净买额"] / 1e8, 2),
                        "占成交%": e["占成交%"], "收盘": e["close"],
                        "至今%": _since(e)} for e in evs],
        })
    return rows


async def inst_flow(top: int = 25) -> dict:
    """主入口: 近30天机构净买入/净卖出榜(带距上榜日涨跌)。30min 缓存。"""
    c = _cache.get("flow")
    if not c or time.time() - c[1] >= _TTL:
        events = await asyncio.to_thread(_fetch_rows_sync)
        if not events:
            return c[0] if c else {"error": "机构买卖统计暂不可达(东财抖动)"}
        codes = sorted({e["code"] for e in events})
        quotes = {}
        try:
            from services.market_data import get_realtime_quotes
            for i in range(0, len(codes), 60):
                q = await get_realtime_quotes(codes[i:i + 60])
                quotes.update({k: v for k, v in (q or {}).items() if v})
        except Exception:
            pass
        rows = aggregate_inst_flow(events, quotes)
        _cache["flow"] = (rows, time.time())
    rows = _cache["flow"][0]
    buys = sorted([r for r in rows if r["机构净买亿"] > 0], key=lambda r: -r["机构净买亿"])
    sells = sorted([r for r in rows if r["机构净买亿"] < 0], key=lambda r: r["机构净买亿"])
    strip = lambda rs: [{k: v for k, v in r.items() if k != "events"} for r in rs]
    return {
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "window_days": _WINDOW_DAYS,
        "net_buy": strip(buys[:top]), "net_sell": strip(sells[:top]),
        "note": f"近{_WINDOW_DAYS}天龙虎榜机构专用席位统计(上榜日才披露, 抽样非全量)。"
                "距最近/首次上榜% = 现价相对上榜日收盘的涨跌: 大额净买入 + 至今大跌 = 市场说的"
                "'机构接在山顶'; 净卖出 + 至今大跌 = '机构跑对了'。纯客观数字, 不构成任何买卖建议。",
    }


async def inst_flow_for(code: str) -> dict:
    """单票: 该股近30天机构席位事件时间线。"""
    await inst_flow(1)      # 确保缓存
    c = _cache.get("flow")
    if not c:
        return {"error": "机构买卖统计暂不可达"}
    for r in c[0]:
        if r["code"] == code:
            return {**r, "note": "该股近30天龙虎榜机构席位记录; 至今%=现价较该上榜日收盘。"
                                 "上榜才披露, 没记录≠机构没动作。不构成买卖建议。"}
    return {"code": code, "events": [], "note": "近30天该股没有龙虎榜机构席位披露记录(上榜才披露, 不代表机构没动作)。"}
