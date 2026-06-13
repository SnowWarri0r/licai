"""Market data REST endpoints."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter

from services.market_data import (
    get_realtime_quotes, get_historical_data, get_intraday_data,
    get_market_indices, normalize_stock_code, get_macro_quotes,
    get_macro_klines,
)

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/trading-day")
async def trading_day_status():
    """Whether today (CST) is an A-share trading day. Excludes weekends + 法定假日.
    Also returns the next trading day for UX hints.
    """
    try:
        import chinese_calendar as cc
    except ImportError:
        # Fallback: weekend-only detection
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        is_weekend = today.weekday() >= 5
        return {
            "date": str(today),
            "is_trading_day": not is_weekend,
            "is_weekend": is_weekend,
            "is_holiday": False,
            "next_trading_day": None,
            "fallback": True,
        }

    cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    is_holiday = cc.is_holiday(cst_today)
    is_weekend = cst_today.weekday() >= 5
    # A 股交易日: 必须工作日且非周末. chinese_calendar 的 is_workday 在 调休补班的
    # 周六/日 也返 True, 但 A 股交易所节假日调休时不开市, 周末永远不交易.
    is_a_share_trading_day = cc.is_workday(cst_today) and not is_weekend

    # Find next A-share trading day (skip weekends + holidays + 调休 weekends)
    next_td = None
    if not is_a_share_trading_day:
        d = cst_today + timedelta(days=1)
        for _ in range(14):
            if cc.is_workday(d) and d.weekday() < 5:
                next_td = str(d)
                break
            d += timedelta(days=1)

    HOLIDAY_CN = {
        "New Year's Day": "元旦",
        "Spring Festival": "春节",
        "Tomb-sweeping Day": "清明",
        "Labour Day": "劳动节",
        "Dragon Boat Festival": "端午",
        "National Day": "国庆",
        "Mid-autumn Festival": "中秋",
        "Anti-Fascist 70th Day": "抗战胜利纪念",
    }
    holiday_name = ""
    # 法定节假日 (含调休关联) 都尝试取名字, 包括"调休补班但 A 股不开市"的周末
    try:
        _, name = cc.get_holiday_detail(cst_today)
        if name:
            holiday_name = HOLIDAY_CN.get(name, name)
            if is_weekend and cc.is_workday(cst_today):
                holiday_name = f"{holiday_name}调休补班"
    except Exception:
        pass

    return {
        "date": str(cst_today),
        "is_trading_day": is_a_share_trading_day,
        "is_weekend": is_weekend,
        "is_holiday": is_holiday and not is_weekend,
        "holiday_name": holiday_name,
        "next_trading_day": next_td,
    }


@router.get("/quote/{stock_code}")
async def get_quote(stock_code: str):
    stock_code = normalize_stock_code(stock_code)
    quotes = await get_realtime_quotes([stock_code])
    if stock_code not in quotes:
        return {"error": f"无法获取 {stock_code} 的行情数据"}
    return quotes[stock_code]


@router.get("/history/{stock_code}")
async def get_history(stock_code: str, days: int = 60):
    stock_code = normalize_stock_code(stock_code)
    df = await get_historical_data(stock_code, days)
    if df.empty:
        return []
    # Return simplified format for chart consumption
    result = []
    for _, r in df.iterrows():
        result.append({
            "time": str(r.get("日期", ""))[:10],
            "open": float(r.get("开盘", 0)),
            "high": float(r.get("最高", 0)),
            "low": float(r.get("最低", 0)),
            "close": float(r.get("收盘", 0)),
            "volume": float(r.get("成交量", 0)),
        })
    return result


@router.get("/intraday/{stock_code}")
async def get_intraday(stock_code: str):
    stock_code = normalize_stock_code(stock_code)
    df = await get_intraday_data(stock_code)
    if df.empty:
        return []
    records = df.to_dict("records")
    for r in records:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = str(v)
    return records


@router.get("/indices")
async def get_indices():
    return await get_market_indices()


@router.get("/macro")
async def get_macro(with_kline: bool = False):
    """宏观仪表盘: 全球指数 / 汇率 / 商品. 30s 缓存.

    返回 { group: [{symbol, name, price, prev_close, change_pct, kline?}, ...] }
    group ∈ {a_index, hk_index, us_index, fx, commodity_intl, commodity_cn}
    """
    quotes = await get_macro_quotes()
    if not with_kline or not quotes:
        return quotes
    syms = [it["symbol"] for items in quotes.values() for it in items]
    klines = await get_macro_klines(syms)
    out = {}
    for grp, items in quotes.items():
        out[grp] = [
            {**it, "kline": klines.get(it["symbol"], [])}
            for it in items
        ]
    return out


@router.get("/macro/kline/{symbol}")
async def get_macro_kline(symbol: str, days: int = 60):
    """单个 symbol 的 K 线 (展开详情图用, 默认 60 日)."""
    from services.market_data import _kline_for_symbol
    import asyncio as _asyncio
    data = await _asyncio.to_thread(_kline_for_symbol, symbol, days)
    return {"symbol": symbol, "kline": data}


# ============================================================
# 爱在冰川式 市场情绪温度计 (打板情绪: 涨停/连板/炸板/赚钱效应)
# ============================================================
import time as _time
_senti_cache: dict = {}
_SENTI_TTL = 300


def _fetch_sentiment_sync():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    import collections
    today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    zt = None
    d = None
    for back in range(0, 8):
        dd = (today - timedelta(days=back)).strftime("%Y%m%d")
        try:
            z = ak.stock_zt_pool_em(date=dd)
            if z is not None and len(z):
                zt, d = z, dd
                break
        except Exception:
            pass
    if zt is None:
        return None

    def _safe(fn):
        try:
            r = fn(date=d)
            return r if r is not None and len(r) else None
        except Exception:
            return None

    dt_pool = _safe(ak.stock_zt_pool_dtgc_em)
    zb = _safe(ak.stock_zt_pool_zbgc_em)
    prev = _safe(ak.stock_zt_pool_previous_em)

    n_zt = len(zt)
    n_dt = len(dt_pool) if dt_pool is not None else 0
    n_zb = len(zb) if zb is not None else 0
    zbl_rate = round(n_zb / (n_zt + n_zb) * 100) if (n_zt + n_zb) else 0

    ladder, max_lb = [], 1
    if "连板数" in zt.columns:
        vc = collections.Counter(int(x) for x in zt["连板数"].fillna(1))
        max_lb = max(vc.keys()) if vc else 1
        ladder = [{"lb": k, "count": vc[k]} for k in sorted(vc.keys(), reverse=True) if k >= 2]
    # 最高连板的票名(空间龙头)
    leaders = []
    if "连板数" in zt.columns and "名称" in zt.columns:
        top = zt[zt["连板数"] == max_lb]
        leaders = [str(x) for x in top["名称"].head(4)]

    # 板块热点: 涨停所属行业分布 + 每个行业的代表票
    hot_sectors = []
    if "所属行业" in zt.columns:
        sc = collections.Counter(str(x) for x in zt["所属行业"] if str(x) not in ("", "nan", "None"))
        for name, cnt in sc.most_common(10):
            names = [str(n) for n in zt[zt["所属行业"] == name]["名称"].head(5)]
            hot_sectors.append({"name": name, "count": cnt, "stocks": names})

    # 量能: 今日两市成交额(Sina 实时) + 较5日均放量/缩量(akshare 综指日成交量)
    volume = None
    try:
        import requests as _rq
        import re as _re
        rr = _rq.get("https://hq.sinajs.cn/list=sh000001,sz399106",
                     headers={"Referer": "https://finance.sina.com.cn"}, timeout=6)
        rr.encoding = "gbk"
        amt_today, vol_today = 0.0, 0.0
        for line in rr.text.strip().split("\n"):
            m = _re.match(r'var hq_str_\w+="(.*)";', line.strip())
            if m:
                b = m.group(1).split(",")
                if len(b) > 9:
                    vol_today += float(b[8] or 0)
                    amt_today += float(b[9] or 0)
        # 放缩量: 纯用 akshare 综指日成交量(同源同单位), 最近完整交易日 vs 前5日均
        # 量能趋势/放缩量: 用上证综指(沪市)日成交量。两市综指 akshare 更新不同步
        # (深证综指常滞后一天), 求和会缺腿; 沪市单源可靠且当日收盘后即全, 作市场量能代表。
        ratio, vlabel, trend = None, None, []
        try:
            df = ak.stock_zh_index_daily(symbol="sh000001")
            ordered = [(str(r.get("date"))[:10], float(r.get("volume") or 0)) for _, r in df.tail(7).iterrows()]
            seq = [v for _, v in ordered]
            if len(seq) >= 6:
                latest, prev5 = seq[-1], sum(seq[-6:-1]) / 5
                if prev5:
                    ratio = round((latest / prev5 - 1) * 100)
                    vlabel = "放量" if ratio >= 8 else ("缩量" if ratio <= -8 else "平量")
            trend = [{"date": ds[5:], "vol": round(vv / 1e8)} for ds, vv in ordered[-6:]]
        except Exception:
            pass
        volume = {
            "amount_yi": round(amt_today / 1e8),         # 今日两市成交额(亿)
            "amount_wy": round(amt_today / 1e12, 2),     # 万亿
            "ratio": ratio, "label": vlabel,             # 沪市最新成交量 较前5日均
            "trend": trend,                              # 近6日沪市成交量(亿股)
        }
    except Exception:
        volume = None

    money_eff, red_rate = None, None
    if prev is not None and "涨跌幅" in prev.columns:
        vals = [float(x) for x in prev["涨跌幅"] if x == x]
        if vals:
            money_eff = round(sum(vals) / len(vals), 2)
            red_rate = round(sum(1 for v in vals if v > 0) / len(vals) * 100)

    # 情绪定性 (纯客观, 看赚钱效应+连板高度, 不给买卖建议)
    if money_eff is None:
        mood, desc = "数据不足", ""
    elif money_eff >= 3 and max_lb >= 4:
        mood = "情绪高潮"
        desc = f"昨日涨停今天平均 {money_eff:+.1f}%, 接力能赚, 最高 {max_lb} 连板, 资金敢打高位"
    elif money_eff >= 1:
        mood = "回暖/进攻"
        desc = f"昨日涨停今天平均 {money_eff:+.1f}%, 接力有肉, 情绪偏暖"
    elif money_eff > -1:
        mood = "分歧/震荡"
        desc = f"昨日涨停今天平均 {money_eff:+.1f}%, 多空分歧, 追高赚钱效应一般"
    else:
        mood = "退潮/亏钱效应"
        desc = f"昨日涨停今天平均 {money_eff:+.1f}%, 接力被埋, 炸板率 {zbl_rate}%, 高位危险"

    return {
        "date": d, "n_zt": n_zt, "n_dt": n_dt, "n_zb": n_zb, "zbl_rate": zbl_rate,
        "max_lianban": max_lb, "ladder": ladder, "leaders": leaders,
        "money_effect": money_eff, "red_rate": red_rate,
        "mood": mood, "mood_desc": desc,
        "hot_sectors": hot_sectors, "volume": volume,
    }


@router.get("/sentiment")
async def market_sentiment():
    """爱在冰川式市场情绪温度计: 涨停家数/连板高度/炸板率/赚钱效应。
    纯客观情绪指标, 不构成买卖建议。看市场是高潮还是退潮的宏观参考。5min 缓存。"""
    import asyncio
    c = _senti_cache.get("s")
    if c and _time.time() - c[1] < _SENTI_TTL:
        return c[0]
    r = await asyncio.to_thread(_fetch_sentiment_sync)
    if r:
        _senti_cache["s"] = (r, _time.time())
        return r
    return {"date": None, "mood": "数据不足", "n_zt": 0}


# ============================================================
# 爱在冰川式 资金热度榜 (东财人气榜代理; 买/卖/锁仓三分榜的买卖源 push2 被墙, 只用人气)
# ============================================================
_hot_cache: dict = {}
_HOT_TTL = 300


def _fetch_hot_sync():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    try:
        h = ak.stock_hot_rank_em()
    except Exception:
        return None
    if h is None or not len(h):
        return None
    rows = []
    for _, r in h.iterrows():
        raw = str(r.get("代码") or "")          # SH600378
        code = raw[2:] if raw[:2].upper() in ("SH", "SZ", "BJ") else raw
        try:
            pct = float(r.get("涨跌幅"))
        except Exception:
            pct = None
        rows.append({
            "rank": int(r.get("当前排名") or 0), "code": code,
            "name": str(r.get("股票名称") or ""), "price": r.get("最新价"),
            "pct": round(pct, 2) if pct is not None else None,
        })
    return rows


@router.get("/hot-rank")
async def hot_rank(top: int = 20):
    """爱在冰川式资金热度榜: 东财人气榜(资金/散户关注度)。标出你的持仓在不在榜、排第几。
    纯客观人气数据, 不构成买卖建议。5min 缓存。"""
    import asyncio
    c = _hot_cache.get("h")
    if c and _time.time() - c[1] < _HOT_TTL:
        rows = c[0]
    else:
        rows = await asyncio.to_thread(_fetch_hot_sync)
        if rows:
            _hot_cache["h"] = (rows, _time.time())
    if not rows:
        return {"items": [], "mine": [], "count": 0}

    from database import get_all_holdings
    held_codes = {h["stock_code"] for h in await get_all_holdings()}
    for r in rows:
        r["mine"] = r["code"] in held_codes
    mine = [r for r in rows if r["mine"]]
    return {"items": rows[:top], "mine": mine, "count": len(rows)}
