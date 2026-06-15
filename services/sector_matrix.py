"""板块趋势量化矩阵: 板块 × 过去 N 个交易日的日涨跌幅, + 资金/动能派生指标。

数据源: 同花顺行业 (akshare)
  - stock_board_industry_summary_ths()        实时榜(今日涨跌/净流入/涨跌家数/领涨股)
  - stock_board_industry_index_ths(symbol,...)  单板块日线 → 算每日涨跌幅
板块全集 = 今日最强 N 个 + 始终纳入的有色系(贴用户持仓) + 今日最弱几个(看退潮)。
重算贵(~每板块一次网络), 缓存 2h + 后台预热。纯客观, 不含任何买卖建议。
"""
from __future__ import annotations
import asyncio
import time
from datetime import datetime, timedelta, timezone

_cache: dict = {}
_TTL = 7200  # 2h

# 始终纳入(贴用户有色/小金属持仓 + 关键题材), 即使今天不在涨幅榜前列
_ALWAYS = ["小金属", "工业金属", "贵金属", "能源金属", "半导体", "光伏设备"]


def _strip_proxy():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)


def _fetch_matrix_sync(days: int) -> dict | None:
    _strip_proxy()
    import akshare as ak

    try:
        summ = ak.stock_board_industry_summary_ths()
    except Exception:
        return None
    if summ is None or not len(summ):
        return None

    # 今日榜: 名称→{涨跌幅, 净流入, 上涨, 下跌, 领涨股}
    today = {}
    for _, r in summ.iterrows():
        nm = str(r.get("板块") or "")
        if not nm:
            continue
        today[nm] = {
            "today_pct": float(r.get("涨跌幅") or 0),
            "net_inflow": round(float(r.get("净流入") or 0), 1),   # 亿
            "up": int(r.get("上涨家数") or 0),
            "down": int(r.get("下跌家数") or 0),
            "leader": str(r.get("领涨股") or ""),
        }
    ranked = sorted(today.items(), key=lambda kv: -kv[1]["today_pct"])
    strongest = [n for n, _ in ranked[:14]]
    weakest = [n for n, _ in ranked[-4:]]
    universe, seen = [], set()
    for n in strongest + _ALWAYS + weakest:
        if n in today and n not in seen:
            seen.add(n); universe.append(n)

    cst = datetime.now(timezone.utc) + timedelta(hours=8)
    end = cst.date()
    start = end - timedelta(days=days + 20)
    sd, ed = start.strftime("%Y%m%d"), end.strftime("%Y%m%d")

    # THS 日线指数滞后约一天(盘中/收盘后都拿不到今日 bar), 但实时榜 today_pct 是当下的。
    # 今天是交易日且已开盘(>=9:30)时, 用实时涨跌幅补出"今日"这一格, 让累计/连涨/AI 都能看到今日走向。
    today_str = end.strftime("%m-%d")
    try:
        from services.market_data import _is_a_share_trading_day
        _trading_today = _is_a_share_trading_day(end)
    except Exception:
        _trading_today = end.weekday() < 5
    _opened = (cst.hour * 60 + cst.minute) >= 570  # 9:30
    add_today = _trading_today and _opened

    def _board_series(name: str):
        try:
            df = ak.stock_board_industry_index_ths(symbol=name, start_date=sd, end_date=ed)
            if df is None or len(df) < 2:
                return None
            closes = [(str(r["日期"])[:10], float(r["收盘价"])) for _, r in df.iterrows() if r.get("收盘价")]
            closes = closes[-(days + 1):]
            daily = []
            for i in range(1, len(closes)):
                d0, c0 = closes[i - 1]; d1, c1 = closes[i]
                daily.append({"date": d1[5:], "pct": round((c1 / c0 - 1) * 100, 2) if c0 else 0})
            return daily
        except Exception:
            return None

    rows = []
    for name in universe:
        daily = _board_series(name)
        if not daily:
            continue
        t = today.get(name, {})
        # 补今日实时格(若日线还没今日 bar)
        if add_today and daily[-1]["date"] != today_str and "today_pct" in t:
            daily = (daily + [{"date": today_str, "pct": round(float(t["today_pct"]), 2)}])[-days:]
        pcts = [d["pct"] for d in daily]
        cum = round((eval_prod(pcts) - 1) * 100, 1)  # N日累计
        # 动能: 末尾连涨天数(>0)
        streak = 0
        for d in reversed(daily):
            if d["pct"] > 0:
                streak += 1
            else:
                break
        up_days = sum(1 for p in pcts if p > 0)
        rows.append({
            "name": name,
            "daily": daily,
            "cum_pct": cum,
            "today_pct": round(t.get("today_pct", daily[-1]["pct"] if daily else 0), 2),
            "net_inflow": t.get("net_inflow", 0),
            "up": t.get("up", 0), "down": t.get("down", 0),
            "leader": t.get("leader", ""),
            "streak": streak,
            "up_days": up_days, "n_days": len(pcts),
        })
    # 默认按 N 日累计涨幅排序(强→弱)
    rows.sort(key=lambda x: -x["cum_pct"])
    dates = rows[0]["daily"] if rows else []
    return {
        "days": days,
        "dates": [d["date"] for d in dates],
        "rows": rows,
        "intraday": bool(add_today),       # 末列是否为今日实时格
        "today": today_str if add_today else "",
        "generated_at": time.time(),
    }


def eval_prod(pcts):
    p = 1.0
    for x in pcts:
        p *= (1 + x / 100)
    return p


async def sector_matrix_prewarm_loop():
    """后台预热板块矩阵(逐板块拉K线慢, ~20-40s), 让用户打开板块tab即秒开。
    启动后稍等再首跑; 盘中每 15min 刷新一次(让"今日"实时格保鲜), 非盘中每小时一次。"""
    await asyncio.sleep(20)
    while True:
        try:
            await get_sector_matrix(days=10, force=True)
        except Exception:
            pass
        try:
            from services.market_data import is_trading_day_active
            interval = 900 if is_trading_day_active() else 3600
        except Exception:
            interval = 3600
        await asyncio.sleep(interval)


async def get_sector_matrix(days: int = 10, force: bool = False) -> dict:
    days = max(5, min(int(days or 10), 20))
    ck = f"matrix_{days}"
    c = _cache.get(ck)
    if not force and c and time.time() - c[1] < _TTL:
        return c[0]
    r = await asyncio.to_thread(_fetch_matrix_sync, days)
    if r:
        _cache[ck] = (r, time.time())
        return r
    return c[0] if c else {"days": days, "dates": [], "rows": []}
