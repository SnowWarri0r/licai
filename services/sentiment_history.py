"""情绪周期时间轴: 逐交易日的 涨停/跌停/炸板率/最高连板/赚钱效应 序列。

数据 = 东财涨停池系(akshare), 历史日不可变 → SQLite 永久档案, 只回填缺的日子;
今天盘中是"进行中切片"不入库(16点后视为定格)。纯客观指标, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import datetime

_lock = asyncio.Lock()
_no_data: set = set()   # 确认拉不到的历史日(东财涨停池历史窗口有限), 本进程内不再重试


def _fetch_day_sync(d8: str) -> dict | None:
    """某交易日的情绪指标(轻量版, 只拉四个涨停池接口)。全空 → None(数据未出/太久远)。"""
    import os
    import time
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import collections
    import akshare as ak

    def _safe(fn):
        for attempt in range(2):
            try:
                r = fn(date=d8)
                return r if r is not None and len(r) else None
            except Exception:
                time.sleep(0.4 * (attempt + 1))
        return None

    zt = _safe(ak.stock_zt_pool_em)
    if zt is None:
        return None
    dt_pool = _safe(ak.stock_zt_pool_dtgc_em)
    zb = _safe(ak.stock_zt_pool_zbgc_em)
    prev = _safe(ak.stock_zt_pool_previous_em)

    n_zt, n_zb = len(zt), len(zb) if zb is not None else 0
    max_lb = 1
    if "连板数" in zt.columns:
        vc = collections.Counter(int(x) for x in zt["连板数"].fillna(1))
        max_lb = max(vc.keys()) if vc else 1
    money_eff = None
    if prev is not None and "涨跌幅" in prev.columns:
        vals = [float(x) for x in prev["涨跌幅"] if x == x]
        if vals:
            money_eff = round(sum(vals) / len(vals), 2)
    return {"date": f"{d8[:4]}-{d8[4:6]}-{d8[6:]}",
            "n_zt": n_zt, "n_dt": len(dt_pool) if dt_pool is not None else 0,
            "n_zb": n_zb, "zbl_rate": round(n_zb / (n_zt + n_zb) * 100) if (n_zt + n_zb) else 0,
            "max_lb": max_lb, "money_effect": money_eff}


def _recent_trading_days(n: int, include_today: bool) -> list:
    from services.market_data import _is_a_share_trading_day
    out, d = [], datetime.date.today()
    if not include_today:
        d -= datetime.timedelta(days=1)
    while len(out) < n:
        if _is_a_share_trading_day(d):
            out.append(d.strftime("%Y-%m-%d"))
        d -= datetime.timedelta(days=1)
    return out[::-1]   # 旧 → 新


async def sentiment_series(days: int = 30) -> dict:
    """近 N 个交易日的情绪序列(升序)。缺的日子并发回填入库; 今天16点前实时拼尾巴不入库。"""
    from database import list_sentiment_history, save_sentiment_day
    days = max(10, min(int(days or 30), 60))
    now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8)))
    today_final = now.hour >= 16
    async with _lock:
        want = _recent_trading_days(days, include_today=today_final)
        have = {r["date"]: r for r in await list_sentiment_history(limit=days + 40)}
        missing = [d for d in want if d not in have and d not in _no_data]
        if missing:
            sem = asyncio.Semaphore(4)

            async def _one(ds):
                async with sem:
                    return ds, await asyncio.to_thread(_fetch_day_sync, ds.replace("-", ""))

            today_s = now.strftime("%Y-%m-%d")
            for ds, row in await asyncio.gather(*(_one(d) for d in missing)):
                if row:
                    await save_sentiment_day(row)
                    have[ds] = row
                elif ds != today_s:
                    _no_data.add(ds)
        series = [have[d] for d in want if d in have]

    note = "逐交易日收盘档案。"
    today_s = now.strftime("%Y-%m-%d")
    if not today_final and _recent_trading_days(1, include_today=True)[0] == today_s:
        live = await asyncio.to_thread(_fetch_day_sync, today_s.replace("-", ""))
        if live:
            live["partial"] = True
            series = [r for r in series if r["date"] != today_s] + [live]
            note += f" 最后一格({today_s})为盘中进行时数据, 收盘后定格。"
    return {"series": series, "note": note + " 纯客观指标, 不构成任何买卖建议。"}
