"""横盘蓄势扫描: 找"横盘很久 + 刚开始放量上攻"的结构(箱体突破前后)。

两段式:
  1) 东财 clist 全市场廉价初筛(区间涨幅字段直接给): 近20日/60日都横着 + 今日温和放量上攻
     + 非ST/非新股(上市满一年)/市值≥30亿。
  2) 候选拉日K(走 get_historical_data 缓存)精算: 箱体振幅、横盘天数、缩量蓄势比、
     是否贴近/突破箱体上沿、放量倍数。

产出纯客观结构描述(横盘N日/振幅X%/放量Y倍/距上沿Z%), 不构成任何买卖建议。
"""
from __future__ import annotations
import asyncio
import time
from datetime import date

_cache: dict = {}
_TTL = 600   # 10 分钟

_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23"   # 沪深A股(不含北交/B)
_FIELDS = "f3,f6,f10,f12,f14,f20,f24,f26,f100,f160"   # 今日/成交额/量比/代码/名称/市值/60日/上市日/行业/20日
_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com", "1.push2.eastmoney.com"]


def _clist_pool(pages: int = 25) -> list[dict]:
    """按量比降序拉前 N 页(今天在放量的票都在前排), 避免全市场 54 页扫穿。"""
    import requests
    s = requests.Session(); s.trust_env = False
    out: list[dict] = []
    for pn in range(1, pages + 1):
        p = {"pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
             "fid": "f10", "fs": _FS, "fields": _FIELDS}
        got = None
        for h in _HOSTS:
            try:
                d = s.get(f"https://{h}/api/qt/clist/get", params=p, timeout=7).json().get("data")
                if d and d.get("diff"):
                    got = d["diff"]; break
            except Exception:
                continue
        if not got:
            break
        out.extend(got)
        if len(got) < 100:
            break
    return out


def _stage1(rows: list[dict]) -> list[dict]:
    """廉价初筛: 长期横着 + 今日温和放量上攻。"""
    today = int(date.today().strftime("%Y%m%d"))
    cands = []
    for x in rows:
        try:
            code = str(x.get("f12") or ""); name = str(x.get("f14") or "")
            if not code or "ST" in name.upper() or "退" in name:
                continue
            pct = float(x.get("f3")); vr = float(x.get("f10") or 0)
            p20, p60 = x.get("f160"), x.get("f24")
            if p20 in (None, "-") or p60 in (None, "-"):
                continue
            p20, p60 = float(p20), float(p60)
            ipo = int(x.get("f26") or 0)
            cap = float(x.get("f20") or 0) / 1e8
        except (TypeError, ValueError):
            continue
        if ipo and today - ipo < 10000:      # 上市满一年(日期数字差跨一年)
            continue
        if cap < 30:                          # 排微盘
            continue
        if not (0.5 <= pct <= 7.5):           # 今日温和上攻(启动中; 已涨停的追不上"准备窜"定义)
            continue
        if vr < 1.5:                          # 今日放量
            continue
        if abs(p20) > 10 or abs(p60) > 25:    # 近20日横着; 60日容忍±25(先涨一波再横住的也算蓄势)
            continue
        cands.append({"code": code, "name": name, "pct": round(pct, 2),
                      "成交额亿": round(float(x.get("f6") or 0) / 1e8, 2),
                      "量比": vr, "市值亿": round(cap, 0),
                      "行业": x.get("f100") or "", "近20日%": p20, "近60日%": p60})
    # 二段精算有成本, 候选按量比取前 150(K线有 SQLite 缓存, 二次扫快)
    cands.sort(key=lambda c: -c["量比"])
    return cands[:150]


async def _stage2(c: dict) -> dict | str:
    """日K精算: 箱体 + 横盘时长 + 缩量蓄势 + 突破/贴上沿。返回 dict(通过) 或 拒绝原因字符串。"""
    from services.market_data import get_historical_data
    try:
        df = await get_historical_data(c["code"], days=90)
    except Exception:
        return "K线不可达"
    if df is None or len(df) < 50:
        return "K线不足"
    closes = [float(v) for v in df["收盘"] if v]
    vols = [float(v) for v in df["成交量"]]
    if len(closes) < 50 or len(vols) != len(closes):
        return "K线不足"
    prev_c, prev_v = closes[-45:-5], vols[-45:-5]     # 箱体窗口: 近40日, 排除最近5日(启动段)
    if len(prev_c) < 35:
        return "K线不足"
    bh, bl = max(prev_c), min(prev_c)
    if bl <= 0:
        return "K线不足"
    width = (bh / bl - 1) * 100
    if width > 25:                                     # 箱体太宽不算横盘
        return "箱体过宽"
    last_close, last_vol = closes[-1], vols[-1]
    base_vol = sum(prev_v) / len(prev_v)
    if base_vol <= 0:
        return "K线不足"
    vol_mult = max(last_vol / base_vol, (sum(vols[-3:]) / 3) / base_vol)
    if last_close < bh * 0.985:                        # 未贴近/突破箱体上沿
        return "未到上沿"
    if vol_mult < 1.5:                                 # 启动段无放量
        return "放量不足"
    if (last_close / closes[-6] - 1) * 100 > 16:       # 近5日已经飞了, 不是"准备窜"
        return "近5日已飞"
    # 横盘时长: 从启动段前往回数, 收盘都落在箱体(±2%容差)内的连续天数
    lo, hi = bl * 0.98, bh * 1.02
    days_flat = 0
    for cl in reversed(closes[:-5]):
        if lo <= cl <= hi:
            days_flat += 1
        else:
            break
    if days_flat < 20:                                 # 真横盘至少一个月
        return "横盘太短"
    # 缩量蓄势: 横盘后半均量 / 前半均量 (<1 = 越盘越缩, 蓄势特征)
    half = len(prev_v) // 2
    contraction = round((sum(prev_v[half:]) / (len(prev_v) - half)) / (sum(prev_v[:half]) / half), 2)
    return {**c,
            "横盘日": days_flat, "箱体振幅%": round(width, 1),
            "缩量比": contraction, "放量倍数": round(vol_mult, 1),
            "距上沿%": round((last_close / bh - 1) * 100, 1),
            "箱体上沿": round(bh, 2), "现价": round(last_close, 2)}


async def scan_coiled(force: bool = False) -> dict:
    """横盘蓄势扫描主入口。10 分钟缓存。"""
    c = _cache.get("coiled")
    if not force and c and time.time() - c[1] < _TTL:
        return c[0]
    pool = await asyncio.to_thread(_clist_pool)
    if not pool:
        return c[0] if c else {"error": "行情源暂不可达(东财抖动)"}
    cands = _stage1(pool)
    sem = asyncio.Semaphore(8)

    async def _one(x):
        async with sem:
            return await _stage2(x)

    results = await asyncio.gather(*[_one(x) for x in cands], return_exceptions=True)
    rows = [r for r in results if isinstance(r, dict)]
    rows.sort(key=lambda r: (-r["放量倍数"], -r["横盘日"]))
    from collections import Counter
    rejected = Counter(r for r in results if isinstance(r, str))
    out = {"as_of": time.strftime("%Y-%m-%d %H:%M"), "rows": rows[:40],
           "scanned": len(pool), "candidates": len(cands), "rejected": dict(rejected),
           "note": "结构筛选: 近40日箱体≤25%振幅、横盘≥20日 + 今日温和放量上攻(贴近/突破箱体上沿, 未飞)。"
                   "横盘日=收盘连续落在箱体内的天数; 缩量比<1=越盘量越缩(蓄势); 放量倍数=启动量/横盘均量。"
                   "纯客观结构描述, 突破可能失败(假突破回落), 不构成任何买卖建议。"}
    _cache["coiled"] = (out, time.time())
    return out
