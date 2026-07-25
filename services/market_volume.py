"""市场量能(开盘啦式): 沪/深/创业/科创 四市场 近14日 成交量+成交额 双序列。

量: 新浪指数日K(全4市场, 单位股, 交易时段含今日盘中累计) —— 稳定单源。
额: 东财指数日K为主(量额同出); 东财被掐时退 SQLite 档案 + 新浪实时补今日格。
每次东财成功都把历史行回写档案(自愈), 收盘后首次访问会把当日定格进档案。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta

_cache: tuple | None = None
_TTL = 60          # 盘中实时读数, 短缓存

# (名称, 新浪符号, 东财secid, 新浪实时量单位是否为手)
MARKETS = [
    ("沪", "sh000001", "1.000001", True),
    ("深", "sz399106", "0.399106", False),
    ("创业", "sz399102", "0.399102", False),
    ("科创", "sh000680", "1.000680", True),
]

_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def _cst_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


def _sina_daily_sync(sym: str, n: int = 16) -> list:
    """→ [(YYYY-MM-DD, vol股)] 升序, 交易时段末行=今日盘中累计。"""
    import requests
    import json
    url = (f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}")
    r = requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
    return [(str(d["day"])[:10], float(d["volume"] or 0)) for d in json.loads(r.text) or []]


def _em_kline_sync(secid: str, n: int = 16) -> list:
    """东财指数日K → [(YYYY-MM-DD, vol手, amt元)] 升序。被掐时抛异常由上层兜底。"""
    import requests
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"User-Agent": _UA, "Referer": "https://quote.eastmoney.com/"})
    last = None
    for host in ("push2his.eastmoney.com", "push2.eastmoney.com") * 3:
        try:
            url = (f"https://{host}/api/qt/stock/kline/get?secid={secid}"
                   f"&fields1=f1&fields2=f51,f56,f57&klt=101&fqt=0&end=20500101&lmt={n}")
            j = s.get(url, timeout=8).json()
            kl = ((j or {}).get("data") or {}).get("klines") or []
            if kl:
                out = []
                for ln in kl:
                    p = ln.split(",")
                    out.append((p[0][:10], float(p[1] or 0), float(p[2] or 0)))
                return out
        except Exception as e:
            last = e
            continue
    raise last or RuntimeError("EM index kline empty")


def _sina_realtime_sync() -> dict:
    """→ {市场名: (vol股, amt元)} 实时快照。沪系指数实时量单位=手(×100), 深系=股。"""
    import requests
    import re
    syms = ",".join(m[1] for m in MARKETS)
    r = requests.get(f"https://hq.sinajs.cn/list={syms}",
                     headers={"Referer": "https://finance.sina.com.cn"}, timeout=6)
    r.encoding = "gbk"
    got = {}
    for line in r.text.strip().split("\n"):
        m = re.match(r'var hq_str_(\w+)="(.*)";', line.strip())
        if not m:
            continue
        sym, b = m.group(1), m.group(2).split(",")
        if len(b) <= 9:
            continue
        for name, s_sym, _sec, is_hand in MARKETS:
            if s_sym == sym:
                vol = float(b[8] or 0) * (100 if is_hand else 1)
                got[name] = (vol, float(b[9] or 0))
    return got


async def archive_today() -> int:
    """收盘后定格今日四市场量额(新浪实时, 不依赖东财)。eod 循环每交易日调一次,
    成交额档案就此逐日累积——即便东财 push2 长期不可达也能往前攒满。返回入档市场数。"""
    from database import save_market_volume_history
    cst = _cst_now()
    today = cst.strftime("%Y-%m-%d")
    try:
        rt = await asyncio.to_thread(_sina_realtime_sync)
    except Exception:
        return 0
    n = 0
    for name, (vol, amt) in rt.items():
        if amt:
            await save_market_volume_history(name, [(today, vol, amt)])
            n += 1
    global _cache
    _cache = None   # 让下次读取带上今日新档
    return n


async def market_volume() -> dict:
    """→ {markets: {两市/沪/深/创业/科创: {trend: [{date, vol, amt}]}}, intraday}
    trend 15行(前端画后14根, 首行作前一日参照), vol=亿股, amt=亿元(拿不到为 None)。"""
    global _cache
    if _cache and time.time() - _cache[1] < _TTL:
        return _cache[0]

    from database import get_market_volume_history, save_market_volume_history

    cst = _cst_now()
    today = cst.strftime("%Y-%m-%d")
    try:
        from services.market_data import _is_a_share_trading_day
        trading = _is_a_share_trading_day(cst.date())
    except Exception:
        trading = cst.weekday() < 5
    opened = trading and (cst.hour * 60 + cst.minute) >= 570
    closed = trading and (cst.hour * 60 + cst.minute) >= 905   # 15:05 后当日额可定格

    # 1) 量: 新浪日K(全市场稳定)
    vols: dict = {}
    for name, sym, _sec, _h in MARKETS:
        try:
            vols[name] = await asyncio.to_thread(_sina_daily_sync, sym, 16)
        except Exception:
            vols[name] = []

    # 2) 额: 东财优先, 成功回写档案; 失败读档案
    amts: dict = {}
    em_ok = False
    for name, _sym, secid, _h in MARKETS:
        try:
            rows = await asyncio.to_thread(_em_kline_sync, secid, 16)
            amts[name] = {d: a for d, _v, a in rows}
            em_ok = True
            # 回写档案(今日盘中不定格, 收盘后才算数)
            fin = [(d, v * 100, a) for d, v, a in rows if d < today or closed]
            await save_market_volume_history(name, fin)
        except Exception:
            amts[name] = {}
    if not em_ok:
        try:
            arch = await get_market_volume_history([m[0] for m in MARKETS], 20)
            for name, rows in arch.items():
                amts[name] = {d: a for d, a in rows if a}
        except Exception:
            pass

    # 2b) 实时快照(新浪, 全市场量额): 无论东财通不通都拿, 既作今日格、也作当前实时读数。
    # 盘中=此刻累计、收盘后=当日收盘值。今日成交量也以它为准(比新浪日K末根更即时)。
    realtime: dict = {}
    if opened:
        try:
            rt = await asyncio.to_thread(_sina_realtime_sync)
            for name, (v, a) in rt.items():
                realtime[name] = {"vol": round(v / 1e8, 1), "amt": round(a / 1e8) if a else None}
                if a:
                    amts.setdefault(name, {})[today] = a
                if v and vols.get(name):
                    # 今日量以实时为准: 覆盖/补上新浪日K的末根(盘中更即时)
                    if vols[name] and vols[name][-1][0] == today:
                        vols[name][-1] = (today, v)
                    else:
                        vols[name].append((today, v))
                if closed and a:
                    await save_market_volume_history(name, [(today, v, a)])
        except Exception:
            pass
    # 两市实时 = 沪+深
    if "沪" in realtime and "深" in realtime:
        h, s = realtime["沪"], realtime["深"]
        realtime["两市"] = {"vol": round(h["vol"] + s["vol"], 1),
                            "amt": (h["amt"] + s["amt"]) if (h["amt"] and s["amt"]) else None}

    # 3) 组装 trend; 两市=沪+深 逐日求和(创业/科创分别是深/沪子集, 不并入)
    def rows_of(name):
        out = []
        for d, v in vols.get(name, [])[-15:]:
            a = (amts.get(name) or {}).get(d)
            out.append({"date": d[5:], "vol": round(v / 1e8, 1), "amt": round(a / 1e8) if a else None})
        return out

    markets = {name: {"trend": rows_of(name)} for name, *_ in MARKETS}
    hu, shen = markets["沪"]["trend"], markets["深"]["trend"]
    shen_by = {r["date"]: r for r in shen}
    both = []
    for r in hu:
        s = shen_by.get(r["date"])
        if not s:
            continue
        both.append({"date": r["date"], "vol": round(r["vol"] + s["vol"], 1),
                     "amt": (r["amt"] + s["amt"]) if (r["amt"] and s["amt"]) else None})
    out = {"markets": {"两市": {"trend": both}, **markets},
           "realtime": realtime,                         # 各市场当前量额(亿), 盘中实时/收盘定格
           "intraday": opened and not closed, "em_ok": em_ok}
    if any(m["trend"] for m in out["markets"].values()):
        _cache = (out, time.time())
    return out
