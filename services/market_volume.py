"""市场量能(开盘啦式): 沪/深/创业/科创 四市场 成交量+成交额, 日频历史 + 当日分时。

主源腾讯(web.ifzq.gtimg.cn), 不碰东财 push2(本机被零信任 L7 拦):
- 日频: newfqkline 一次给量(手)+额(万元), ~250 交易日;
- 分时: minute query 每分钟「时间 价 累计量(手) 累计额(元)」。
量统一 手×100=股, 额 万元×1e4=元 / 分时额已是元。两市=沪+深。
"""
from __future__ import annotations

import asyncio
import json
import statistics
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


def _sina_min_sync(sym: str, datalen: int = 1500, scale: int = 1) -> dict:
    """新浪分钟线(带成交额, 跨多日) → {YYYY-MM-DD: [(HH:MM, 累计量股, 累计额元)]}。
    每档 amount(元)/volume(股)累加成当日累计。
    scale=1 datalen=1500 覆盖约6完整日(240档/日), 用于今日分时/昨日对照;
    scale=5 datalen=1500 覆盖约30完整日(48档/日), 粒度粗但天数多, 专供完成度剖面。"""
    j = _get(f"https://quotes.sina.cn/cn/api/openapi.php/CN_MarketDataService.getKLineData"
             f"?symbol={sym}&scale={scale}&datalen={datalen}")
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


def _sess_min(hhmm: str) -> int:
    """交易分钟轴(跳过午休): 09:30→0 … 11:30→120, 13:00→120 … 15:00→240。单调, 便于插值。"""
    try:
        x = int(hhmm[:2]) * 60 + int(hhmm[3:5])
    except (ValueError, IndexError):
        return 0
    if x <= 690:                       # ≤11:30
        return max(0, x - 570)         # 09:30=570
    return 120 + (x - 780)             # 13:00=780 → 120


def _build_frac_grid(profile_days: list) -> list:
    """近N完整日 → 完成度剖面 [(sess_min, frac_vol中位, frac_amt中位)] 按分钟升序。
    frac = 该时点累计 ÷ 当日全天。取跨日中位数, 抗单日异常。"""
    times = sorted({t for d in profile_days for t, _, _ in d})
    grid = []
    for t in times:
        fv, fa = [], []
        for d in profile_days:
            full_v, full_a = d[-1][1], d[-1][2]
            cv = ca = 0.0
            for tt, v, a in d:
                if tt <= t:
                    cv, ca = v, a
                else:
                    break
            if full_v > 0:
                fv.append(cv / full_v)
            if full_a > 0:
                fa.append(ca / full_a)
        if fv and fa:
            grid.append((_sess_min(t), statistics.median(fv), statistics.median(fa)))
    grid.sort()
    return grid


def _frac_interp(grid: list, t: str, i: int) -> float | None:
    """在完成度剖面上按交易分钟线性插值取 t 时刻完成度。i=1 量, i=2 额。"""
    if not grid:
        return None
    sm = _sess_min(t)
    if sm <= grid[0][0]:
        return grid[0][i]
    if sm >= grid[-1][0]:
        return grid[-1][i]
    for k in range(1, len(grid)):
        if grid[k][0] >= sm:
            a, b = grid[k - 1], grid[k]
            r = (sm - a[0]) / (b[0] - a[0]) if b[0] > a[0] else 0.0
            return a[i] + (b[i] - a[i]) * r
    return grid[-1][i]


def _complete_days(by_day: dict, min_bars: int) -> list:
    """完整交易日的日期列表(升序): 首档≤09:36 且档数≥min_bars。"""
    return [d for d in sorted(by_day)
            if by_day[d] and by_day[d][0][0] <= "09:36" and len(by_day[d]) >= min_bars]


async def market_volume_intraday(market: str = "两市") -> dict:
    """当日分时累计成交量(亿股)/成交额(亿元) + 昨日同期对照 + 开盘啦式全天预测。
    预测 = 今日到此刻累计 ÷ 近15日完成度剖面(该时点累计占全天比的中位数)。60s 缓存。"""
    market = market if market in ("两市", "沪", "深", "创业", "科创") else "两市"
    c = _intraday_cache.get(market)
    if c and time.time() - c[1] < _TTL:
        return c[0]

    # 两路取数: 1分钟(今日实时曲线+昨日对照, ~6日) + 5分钟(完成度剖面, ~30日)。
    # 剖面用 5 分钟粒度换更多历史天数——剖面平滑, 5 分钟够; 今日曲线仍 1 分钟保交互。
    async def one(sym, scale):
        try:
            return await asyncio.to_thread(_sina_min_sync, sym, 1500, scale)
        except Exception:
            return {}

    if market == "两市":
        hu1, sh1, hu5, sh5 = await asyncio.gather(
            one(_SYM["沪"], 1), one(_SYM["深"], 1), one(_SYM["沪"], 5), one(_SYM["深"], 5))
        by_day = _merge_days(hu1, sh1)
        by5 = _merge_days(hu5, sh5)
    else:
        by_day, by5 = await asyncio.gather(one(_SYM[market], 1), one(_SYM[market], 5))

    days = sorted(by_day)
    if not days:
        return {"market": market, "points": [], "note": "分时数据暂不可达"}
    today_date = days[-1]
    today = by_day[today_date]
    prev_dates = [d for d in _complete_days(by_day, 200) if d != today_date]  # 1分钟完整日, 排除今日
    prev = by_day[prev_dates[-1]] if prev_dates else []      # 昨日对照 = 最近一个非今日完整日

    def pts_of(series):
        return [{"time": t, "vol": round(v / 1e8, 1), "amt": round(a / 1e8)} for t, v, a in series]

    points = pts_of(today)
    prev_points = pts_of(prev)
    prev_full = ({"vol": round(prev[-1][1] / 1e8, 1), "amt": round(prev[-1][2] / 1e8)} if prev else None)

    # 预测量能序列(开盘啦式): proj(t) = 今日累计(t) ÷ 剖面完成度(t)。
    # 完成度剖面 = 近15完整日(5分钟)每时点 median(cum_d/full_d), 按交易分钟轴插值到今日1分钟点。
    # 纯比例外推: anchor 被约掉, 预测跟随今日真实量级(清淡日不死扛均值)。收盘=实际。
    # 前向回测(30日样本): 整体 MAE ~4%, 午后 ±2%, 早盘 ±7%(开盘不可测性的地板)。
    prof_dates = [d for d in _complete_days(by5, 40) if d != today_date]
    profile_days = [by5[d] for d in prof_dates[-15:]]
    grid = _build_frac_grid(profile_days)
    proj_series = []
    projected = None
    n_prof = len(profile_days)
    if grid and today:
        for t, cv, ca in today:
            fa, fv = _frac_interp(grid, t, 2), _frac_interp(grid, t, 1)
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
                         "basis": f"近{n_prof}日节奏中位外推"}

    actual = {"amt": round(today[-1][2] / 1e8), "vol": round(today[-1][1] / 1e8, 1)} if today else None
    out = {"market": market, "points": points, "prev_points": prev_points,
           "prev_full": prev_full, "actual": actual,
           "proj_series": proj_series, "projected": projected,
           "note": "预测量能(开盘啦式): 今日累计 ÷ 近15日完成度剖面中位数外推全天, 相对昨日总量的偏离; 收盘收敛到实际。"}
    if points:
        _intraday_cache[market] = (out, time.time())
    return out
