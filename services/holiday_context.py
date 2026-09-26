"""长假前后的历史表现(收盘复盘里的事实提示)。

临近长假(春节/国庆, 休市 ≥7 个自然日)前 7 个交易日、或节后 5 个交易日内, 给出:
  - 距长假还有几个交易日 / 节后第几天
  - 同一个假期历年(指数日K, 2014 起)的: 节前 5 日累计、节前最后一天、节后 5 日累计、节后第 2~4 日, 逐年列出 + 涨跌次数
指数用中证1000(小盘, 情绪更敏感)与上证指数; 交易日历来自 chinese_calendar(与 _is_a_share_trading_day 同源)。
只摆历史分布, 每个假期每年一次, 样本只有十来个 —— 不是择时信号。
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

_cache: dict = {}
_TTL = 6 * 3600
INDEXES = (("sh000852", "中证1000"), ("sh000001", "上证指数"))
PRE_WINDOW, POST_WINDOW = 7, 5


def _name(before: date, gap: int) -> str | None:
    if gap < 7:
        return None
    if before.month in (1, 2):
        return "春节"
    if before.month in (9, 10):
        return "国庆"
    return "长假"


def _trading_days(start: date, n_ahead: int = 40) -> list[date]:
    from services.market_data import _is_a_share_trading_day
    out, d = [], start
    while len(out) < n_ahead:
        if _is_a_share_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _break_near(today: date) -> dict | None:
    """今天之后 PRE_WINDOW 个交易日内的长假, 或今天在长假后 POST_WINDOW 个交易日内。"""
    from services.market_data import _is_a_share_trading_day
    ahead = _trading_days(today, PRE_WINDOW + 2)
    for i in range(len(ahead) - 1):
        gap = (ahead[i + 1] - ahead[i]).days
        nm = _name(ahead[i], gap)
        if nm and i < PRE_WINDOW:
            return {"phase": "pre", "name": nm, "last_day": ahead[i].isoformat(), "resume": ahead[i + 1].isoformat(),
                    "closed_days": gap - 1, "trading_days_left": i + 1}
    # 节后: 从今天往回数交易日, 相邻两个交易日之间隔着长假 → 今天是节后第 j+1 个交易日
    back, d = [], today
    while len(back) < POST_WINDOW + 1 and (today - d).days <= 40:
        if _is_a_share_trading_day(d):
            back.append(d)
        d -= timedelta(days=1)
    for j in range(len(back) - 1):
        g = (back[j] - back[j + 1]).days
        nm = _name(back[j + 1], g)
        if nm and j < POST_WINDOW:
            return {"phase": "post", "name": nm, "last_day": back[j + 1].isoformat(), "resume": back[j].isoformat(),
                    "closed_days": g - 1, "day_after": j + 1}
    return None


def _history(bars: list[dict], name: str) -> list[dict]:
    closes = [float(b["close"]) for b in bars]
    days = [date.fromisoformat(str(b["date"])[:10]) for b in bars]
    out = []
    for i in range(5, len(days) - 1):
        gap = (days[i + 1] - days[i]).days
        if _name(days[i], gap) != name:
            continue
        c = closes
        row = {"year": days[i].year, "last_day": days[i].isoformat(),
               "pre5_pct": round((c[i] / c[i - 5] - 1) * 100, 2),
               "last_day_pct": round((c[i] / c[i - 1] - 1) * 100, 2)}
        if i + 5 < len(c):
            row["post5_pct"] = round((c[i + 5] / c[i] - 1) * 100, 2)
            row["post2_4_pct"] = round((c[i + 4] / c[i + 1] - 1) * 100, 2)   # 节后第 2~4 个交易日
        out.append(row)
    return out


def _summary(rows: list[dict]) -> dict:
    def cnt(k):
        xs = [r[k] for r in rows if r.get(k) is not None]
        if not xs:
            return None
        s = sorted(xs)
        return {"n": len(xs), "up": sum(1 for x in xs if x > 0), "median": s[len(s) // 2], "mean": round(sum(xs) / len(xs), 2)}
    return {k: cnt(k) for k in ("pre5_pct", "last_day_pct", "post5_pct", "post2_4_pct")}


async def build(today: date | None = None) -> dict:
    from services.market_data import _cst_now, _kline_for_symbol
    today = today or _cst_now().date()
    near = _break_near(today)
    if not near:
        return {"active": False}
    ck = (near["name"], today.isoformat())
    hit = _cache.get(ck)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    idx = []
    for sym, label in INDEXES:
        try:
            bars = await asyncio.to_thread(_kline_for_symbol, sym, 3200)
        except Exception:  # noqa: BLE001
            bars = []
        rows = [r for r in _history(bars, near["name"]) if r["last_day"] < today.isoformat() or near["phase"] == "post"]
        if near["phase"] == "post":                                   # 本次节后还没走完的那一行不进统计
            rows = [r for r in rows if r["last_day"] != near["last_day"]]
        if rows:
            idx.append({"symbol": sym, "label": label, "rows": rows, "summary": _summary(rows),
                        "since": rows[0]["year"]})
    out = {"active": True, **near, "indexes": idx,
           "note": f"{near['name']}前后指数历年表现(每年一次, 样本只有十来个), 只是历史分布, 不代表这一次。"
                   "节前 5 日 = 假前最后一个交易日收盘相对 5 个交易日前; 节后 5 日 = 节后第 5 个交易日收盘相对假前收盘;"
                   "节后第 2~4 日 = 节后第 4 个交易日收盘相对节后第 1 个交易日收盘。"}
    _cache[ck] = (out, time.time())
    return out
