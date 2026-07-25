"""市场量能(开盘啦式): 沪/深/创业/科创 四市场 成交量+成交额, 日频历史 + 当日分时。

主源腾讯(web.ifzq.gtimg.cn), 不碰东财 push2(本机被零信任 L7 拦):
- 日频: newfqkline 一次给量(手)+额(万元), ~250 交易日;
- 分时: minute query 每分钟「时间 价 累计量(手) 累计额(元)」。
量统一 手×100=股, 额 万元×1e4=元 / 分时额已是元。两市=沪+深。
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta

_cache: tuple | None = None
_TTL = 60
_intraday_cache: dict = {}

# (名称, 腾讯符号)
MARKETS = [("沪", "sh000001"), ("深", "sz399106"), ("创业", "sz399102"), ("科创", "sh000680")]
_SYM = dict(MARKETS)


def _cst_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def _get(url: str) -> dict:
    import requests
    s = requests.Session()
    s.trust_env = False
    r = s.get(url, timeout=8)
    t = r.text.strip()
    if t[:1] not in ("{", "["):
        t = t[t.find("=") + 1:]
    return json.loads(t)


def _tx_daily_sync(sym: str, n: int = 16) -> list:
    """腾讯日线 → [(YYYY-MM-DD, vol股, amt元)] 升序。字段: [日,开,收,高,低,量(手),..,额(万元),..]。"""
    j = _get(f"https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get?param={sym},day,,,{n},qfq")
    k = (j.get("data") or {}).get(sym) or {}
    rows = k.get("qfqday") or k.get("day") or []
    out = []
    for r in rows:
        if len(r) < 6:
            continue
        try:
            vol = float(r[5]) * 100                       # 手 → 股
            amt = float(r[8]) * 1e4 if len(r) > 8 and r[8] else None   # 万元 → 元
            out.append((str(r[0])[:10], vol, amt))
        except (ValueError, TypeError, IndexError):
            continue
    return out


def _tx_minute_sync(sym: str) -> list:
    """腾讯当日分时 → [(HH:MM, 累计量股, 累计额元)]。行式 '0930 价 累计量(手) 累计额(元)'。"""
    j = _get(f"https://web.ifzq.gtimg.cn/appstock/app/minute/query?code={sym}")
    node = ((j.get("data") or {}).get(sym) or {}).get("data") or {}
    lines = node.get("data") or []
    out = []
    for ln in lines:
        p = str(ln).split()
        if len(p) < 4:
            continue
        try:
            t = p[0]
            hhmm = f"{t[:2]}:{t[2:]}" if len(t) == 4 else t
            out.append((hhmm, float(p[2]) * 100, float(p[3])))   # 量 手→股, 额 已是元
        except (ValueError, IndexError):
            continue
    return out


async def market_volume() -> dict:
    """→ {markets: {两市/沪/深/创业/科创: {trend:[{date,vol,amt}]}}, realtime, intraday}
    trend: 近14日+今日, vol=亿股 amt=亿元。realtime: 各市场当前量额(亿), 取当日分时末点。"""
    global _cache
    if _cache and time.time() - _cache[1] < _TTL:
        return _cache[0]

    cst = _cst_now()
    try:
        from services.market_data import _is_a_share_trading_day
        trading = _is_a_share_trading_day(cst.date())
    except Exception:
        trading = cst.weekday() < 5
    opened = trading and (cst.hour * 60 + cst.minute) >= 570
    closed = trading and (cst.hour * 60 + cst.minute) >= 905

    async def one(sym):
        try:
            return await asyncio.to_thread(_tx_daily_sync, sym, 16)
        except Exception:
            return []

    daily = dict(zip([m[0] for m in MARKETS],
                     await asyncio.gather(*(one(_SYM[m[0]]) for m in MARKETS))))

    def rows_of(name):
        return [{"date": d[5:], "vol": round(v / 1e8, 1), "amt": round(a / 1e8) if a else None}
                for d, v, a in daily.get(name, [])[-15:]]

    markets = {name: {"trend": rows_of(name)} for name, _ in MARKETS}
    # 两市 = 沪+深 逐日求和
    shen_by = {r["date"]: r for r in markets["深"]["trend"]}
    both = []
    for r in markets["沪"]["trend"]:
        s = shen_by.get(r["date"])
        if not s:
            continue
        both.append({"date": r["date"], "vol": round(r["vol"] + s["vol"], 1),
                     "amt": (r["amt"] + s["amt"]) if (r["amt"] and s["amt"]) else None})

    realtime = {}
    for name, _ in MARKETS:
        t = markets[name]["trend"]
        if t and (t[-1]["vol"] or t[-1]["amt"] is not None):
            realtime[name] = {"vol": t[-1]["vol"], "amt": t[-1]["amt"]}
    if "沪" in realtime and "深" in realtime:
        h, s = realtime["沪"], realtime["深"]
        realtime["两市"] = {"vol": round(h["vol"] + s["vol"], 1),
                            "amt": (h["amt"] + s["amt"]) if (h["amt"] and s["amt"]) else None}

    out = {"markets": {"两市": {"trend": both}, **markets},
           "realtime": realtime, "intraday": opened and not closed}
    if any(m["trend"] for m in out["markets"].values()):
        _cache = (out, time.time())
    return out


async def market_volume_intraday(market: str = "两市") -> dict:
    """当日分时: 逐分钟累计成交量(亿股)/成交额(亿元)。两市=沪+深按分钟对齐求和。60s 缓存。"""
    market = market if market in ("两市", "沪", "深", "创业", "科创") else "两市"
    c = _intraday_cache.get(market)
    if c and time.time() - c[1] < _TTL:
        return c[0]

    async def one(sym):
        try:
            return await asyncio.to_thread(_tx_minute_sync, sym)
        except Exception:
            return []

    if market == "两市":
        hu, shen = await asyncio.gather(one(_SYM["沪"]), one(_SYM["深"]))
        sh_by = {t: (v, a) for t, v, a in shen}
        pts = []
        for t, v, a in hu:
            sv, sa = sh_by.get(t, (0, 0))
            pts.append({"time": t, "vol": round((v + sv) / 1e8, 1), "amt": round((a + sa) / 1e8)})
    else:
        pts = [{"time": t, "vol": round(v / 1e8, 1), "amt": round(a / 1e8)}
               for t, v, a in await one(_SYM[market])]

    out = {"market": market, "points": pts,
           "note": "当日累计成交量/成交额分时(腾讯), 盘中滚动。"}
    if pts:
        _intraday_cache[market] = (out, time.time())
    return out
