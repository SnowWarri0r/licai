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


def _sina_5min_sync(sym: str, datalen: int = 260) -> dict:
    """新浪 5 分钟线(带成交额, 跨多日) → {YYYY-MM-DD: [(HH:MM, 累计量股, 累计额元)]}。
    每档 amount(元)/volume(股)累加成当日累计。用于今日分时 + 昨日同期对照 + 预测。"""
    j = _get(f"https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData"
             f"?symbol={sym}&scale=5&datalen={datalen}")
    rows = (j.get("result") or {}).get("data") or []
    by_day: dict = {}
    for x in rows:
        day = str(x.get("day") or "")[:10]
        hhmm = str(x.get("day") or "")[11:16]
        try:
            v, a = float(x.get("volume") or 0), float(x.get("amount") or 0)
        except (ValueError, TypeError):
            continue
        by_day.setdefault(day, []).append([hhmm, v, a])
    out: dict = {}
    for day, arr in by_day.items():
        cv = ca = 0.0
        cum = []
        for hhmm, v, a in arr:
            cv += v
            ca += a
            cum.append((hhmm, cv, ca))
        out[day] = cum
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


def _merge_days(a: dict, b: dict) -> dict:
    """两市 = 沪+深: 按 (日期, 时刻) 对齐累计量额相加。"""
    out: dict = {}
    for day in set(a) | set(b):
        bb = {t: (v, m) for t, v, m in b.get(day, [])}
        merged = []
        for t, v, m in a.get(day, []):
            bv, bm = bb.get(t, (0, 0))
            merged.append((t, v + bv, m + bm))
        out[day] = merged
    return out


async def market_volume_intraday(market: str = "两市") -> dict:
    """当日分时累计成交量(亿股)/成交额(亿元) + 昨日同期对照 + 开盘啦式全天预测。
    预测 = 今日到此刻累计 × 昨日全天 ÷ 昨日同期累计(按盘中已走节奏外推)。60s 缓存。"""
    market = market if market in ("两市", "沪", "深", "创业", "科创") else "两市"
    c = _intraday_cache.get(market)
    if c and time.time() - c[1] < _TTL:
        return c[0]

    async def one(sym):
        try:
            return await asyncio.to_thread(_sina_5min_sync, sym)
        except Exception:
            return {}

    if market == "两市":
        hu, shen = await asyncio.gather(one(_SYM["沪"]), one(_SYM["深"]))
        by_day = _merge_days(hu, shen)
    else:
        by_day = await one(_SYM[market])

    days = sorted(by_day)
    if not days:
        return {"market": market, "points": [], "note": "分时数据暂不可达"}
    today = by_day[days[-1]]
    hist = [by_day[d] for d in days[:-1]]           # 今日之前的历史日(用于平均节奏)
    prev = hist[-1] if hist else []                 # 昨日(对照曲线)

    def pts_of(series):
        return [{"time": t, "vol": round(v / 1e8, 1), "amt": round(a / 1e8)} for t, v, a in series]

    points = pts_of(today)
    prev_points = pts_of(prev)
    prev_full = ({"vol": round(prev[-1][1] / 1e8, 1), "amt": round(prev[-1][2] / 1e8)} if prev else None)

    # 预测量能序列(开盘啦式): 每个时刻按"近5日该时点平均完成度"把今日已走量外推成全天预测。
    # proj_series[i] = 今日累计(t_i) ÷ 近5日 mean(cum_d(t_i)/full_d)。开盘节奏快→早盘预测高,
    # 随盘中修正一路收敛, 收盘=实际。前端画成相对昨日总量的 % 偏离曲线。
    recent = hist[-5:]
    proj_series = []
    projected = None
    if recent and today:
        # 预计算近5日每个时刻的累计, 便于对齐
        recent_full = [(d[-1][1], d[-1][2]) for d in recent]

        def avg_frac_at(t, idx):
            fr = []
            for di, day in enumerate(recent):
                cum = 0.0
                for row in day:
                    if row[0] <= t:
                        cum = row[idx]
                    else:
                        break
                full = recent_full[di][idx - 1]
                if full > 0:
                    fr.append(cum / full)
            return sum(fr) / len(fr) if fr else None

        for t, cv, ca in today:
            fa, fv = avg_frac_at(t, 2), avg_frac_at(t, 1)
            pa = ca / fa if (fa and fa > 0) else None
            pv = cv / fv if (fv and fv > 0) else None
            if pa:
                proj_series.append({"time": t, "amt": round(pa / 1e8),
                                    "vol": round(pv / 1e8, 1) if pv else None})
        # 收盘后末点预测 = 实际; 用真实全天覆盖末点, 保证曲线收敛到实际
        now_t = today[-1][0]
        if now_t >= "14:57" and proj_series:
            proj_series[-1] = {"time": now_t, "amt": round(today[-1][2] / 1e8),
                               "vol": round(today[-1][1] / 1e8, 1)}
        if proj_series:
            last = proj_series[-1]
            projected = {"amt": last["amt"], "vol": last["vol"], "final": now_t >= "14:57",
                         "basis": f"近{len(recent)}日平均节奏"}

    actual = {"amt": round(today[-1][2] / 1e8), "vol": round(today[-1][1] / 1e8, 1)} if today else None
    out = {"market": market, "points": points, "prev_points": prev_points,
           "prev_full": prev_full, "actual": actual,
           "proj_series": proj_series, "projected": projected,
           "note": "预测量能(开盘啦式): 逐分钟按近5日平均节奏外推全天, 相对昨日总量的偏离; 收盘收敛到实际。"}
    if points:
        _intraday_cache[market] = (out, time.time())
    return out
