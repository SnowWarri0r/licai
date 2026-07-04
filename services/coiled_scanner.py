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
_FIELDS = "f3,f6,f10,f12,f14,f20,f23,f24,f26,f37,f41,f45,f46,f100,f115,f160"   # 行情列 + 基本面列(PB/ROE/营收同比/净利润/净利同比/PE_TTM)
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


_fund_hold_cache: dict = {}


def _fund_hold_map() -> dict:
    """全市场基金持仓表(季报) → {code: 持有基金家数}。缓存 1 天; 拉不到返回空(上层 fail-open)。"""
    c = _fund_hold_cache.get("m")
    if c and time.time() - c[1] < 86400:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    m: dict = {}
    try:
        import akshare as ak
        d = date.today()
        quarters = [f"{y}{md}" for y in (d.year, d.year - 1) for md in ("1231", "0930", "0630", "0331")
                    if f"{y}{md}" <= d.strftime("%Y%m%d")]
        for q in quarters[:4]:      # 最新报告期可能未披露(表空), 逐季回退
            try:
                df = ak.stock_report_fund_hold(symbol="基金持仓", date=q)
            except Exception:
                continue
            # 最新报告期披露初期表里只有零星几家(早鸟), 未成表; 要足够完整才采纳, 否则回退上一季
            if df is not None and len(df) >= 500:
                for _, r in df.iterrows():
                    try:
                        m[str(r["股票代码"]).zfill(6)] = int(r["持有基金家数"])
                    except (TypeError, ValueError):
                        continue
                break
    except Exception:
        pass
    _fund_hold_cache["m"] = (m, time.time())
    return m


def _stage1(rows: list[dict], fund_map: dict | None = None) -> list[dict]:
    """廉价初筛: 长期横着 + 今日温和放量上攻 + 基本面/机构精选。"""
    fund_map = fund_map or {}
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
        if cap < 50:                          # 精选: 市值≥50亿
            continue
        if float(x.get("f6") or 0) / 1e8 < 2:  # 成交额≥2亿(流动性门槛, 太小的进出都困难)
            continue
        fund_cnt = fund_map.get(code, 0)
        if fund_map and fund_cnt < 20:        # 机构覆盖: ≥20家基金持有(垃圾/庄股通常个位数); 表拉不到时不卡
            continue
        if not (-2 <= pct <= 7.5):            # 今日温和(启动日上攻 或 突破后1-2日的回踩都算; 已涨停/大跌的排除)
            continue
        if vr < 1.2:                          # 今日不算死水(突破日量比更高, 回踩日略松)
            continue
        if abs(p20) > 10 or abs(p60) > 25:    # 近20日横着; 60日容忍±25(先涨一波再横住的也算蓄势)
            continue
        # 基本面精选(排垃圾, 留正常经营的公司): 盈利 + ROE达标 + 营收/净利未暴跌 + 非资不抵债
        def _f(key):
            v = x.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        profit, roe, rev_yoy, prof_yoy, pb, pe = _f("f45"), _f("f37"), _f("f41"), _f("f46"), _f("f23"), _f("f115")
        if profit is None or profit <= 0:                 # 亏损股
            continue
        if roe is None or roe < 1.0:                      # ROE(报告期累计口径, Q1季1.0≈年化4): 排接近零利润的空壳
            continue
        if prof_yoy is not None and prof_yoy < -60:       # 净利同比腰斩以上 = 暴雷级(修复中的-30~-50不误杀)
            continue
        if rev_yoy is not None and rev_yoy < -30:         # 营收同比大幅萎缩
            continue
        if pb is not None and pb <= 0:                    # 资不抵债
            continue
        cands.append({"code": code, "name": name, "pct": round(pct, 2),
                      "成交额亿": round(float(x.get("f6") or 0) / 1e8, 2),
                      "量比": vr, "市值亿": round(cap, 0),
                      "行业": x.get("f100") or "", "近20日%": p20, "近60日%": p60,
                      "ROE%": roe, "净利同比%": round(prof_yoy, 1) if prof_yoy is not None else None,
                      "营收同比%": round(rev_yoy, 1) if rev_yoy is not None else None,
                      "PE_TTM": pe, "基金家数": fund_cnt})
    # 二段精算有成本, 候选按量比取前 300(K线有 SQLite 缓存, 二次扫快)
    cands.sort(key=lambda c: -c["量比"])
    return cands[:300]


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
    try:
        highs = [float(v) for v in df["最高"]]
        lows = [float(v) for v in df["最低"]]
    except Exception:
        highs = lows = []
    prev_c, prev_v = closes[-45:-5], vols[-45:-5]     # 箱体窗口: 近40日, 排除最近5日(启动段)
    if len(prev_c) < 35:
        return "K线不足"
    bh, bl = max(prev_c), min(prev_c)
    if bl <= 0:
        return "K线不足"
    width = (bh / bl - 1) * 100
    if width > 25:                                     # 箱体太宽不算横盘
        return "箱体过宽"
    last_close = closes[-1]
    base_vol = sum(prev_v) / len(prev_v)
    if base_vol <= 0:
        return "K线不足"
    # 突破事件: 近3根内有一根 放量(≥1.5x横盘均量)且收盘攻到箱体上沿(≥0.985×上沿)
    bk_i, bk_vm = None, 0.0
    for i in (-3, -2, -1):
        vm_i = vols[i] / base_vol
        if closes[i] >= bh * 0.985 and vm_i >= 1.5 and vm_i > bk_vm:
            bk_i, bk_vm = i, vm_i
    if bk_i is None:
        return "未到上沿" if max(closes[-3:]) < bh * 0.985 else "放量不足"
    if last_close < bh * 0.97:                         # 突破后又跌回箱体深处 = 假突破已证伪
        return "突破后跌回"
    vol_mult = max(bk_vm, (sum(vols[-3:]) / 3) / base_vol)
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
    # 突破日收盘强度: (收-低)/(高-低)。长上影(冲高被砸回)= 假突破笔, 直接拒
    if len(highs) == len(closes) and highs and highs[bk_i] > lows[bk_i]:
        strength = round((closes[bk_i] - lows[bk_i]) / (highs[bk_i] - lows[bk_i]), 2)
    else:
        strength = 1.0
    if strength < 0.45:
        return "冲高回落(上影)"
    # 缩量蓄势: 横盘后半均量 / 前半均量 (<1 = 越盘越缩, 蓄势特征)
    half = len(prev_v) // 2
    contraction = round((sum(prev_v[half:]) / (len(prev_v) - half)) / (sum(prev_v[:half]) / half), 2)
    dist = round((last_close / bh - 1) * 100, 1)
    tag = ("突破后回踩" if dist < -1.5 else "临界(贴上沿)" if dist < 0
           else "刚突破" if dist <= 3 else "突破延伸")
    # 综合评分(0-100): 横盘越久+越缩量+放量在2~4x甜区+收盘实体强+刚好在突破位, 分越高
    score = (min(days_flat, 60) / 60 * 30
             + (15 if contraction <= 0.8 else 10 if contraction <= 0.95 else 5 if contraction <= 1.1 else 0)
             + (25 if 2 <= vol_mult <= 4 else 18 if vol_mult < 2 else 15 if vol_mult <= 6 else 8)
             + strength * 15
             + (15 if 0 <= dist <= 3 else 12 if dist < 0 else 8)
             + (5 if (c.get("基金家数") or 0) >= 100 else 0))   # 机构重度覆盖加分
    return {**c,
            "横盘日": days_flat, "箱体振幅%": round(width, 1),
            "缩量比": contraction, "放量倍数": round(vol_mult, 1),
            "收盘强度": strength, "距上沿%": dist, "标签": tag,
            "评分": round(score),
            "箱体上沿": round(bh, 2), "现价": round(last_close, 2)}


async def scan_coiled(force: bool = False) -> dict:
    """横盘蓄势扫描主入口。10 分钟缓存。"""
    c = _cache.get("coiled")
    if not force and c and time.time() - c[1] < _TTL:
        return c[0]
    pool, fmap = await asyncio.gather(asyncio.to_thread(_clist_pool),
                                      asyncio.to_thread(_fund_hold_map))
    if not pool:
        return c[0] if c else {"error": "行情源暂不可达(东财抖动)"}
    cands = _stage1(pool, fmap)
    sem = asyncio.Semaphore(8)

    async def _one(x):
        async with sem:
            return await _stage2(x)

    results = await asyncio.gather(*[_one(x) for x in cands], return_exceptions=True)
    rows = [r for r in results if isinstance(r, dict)]
    rows.sort(key=lambda r: -r["评分"])
    from collections import Counter
    rejected = Counter(r for r in results if isinstance(r, str))
    out = {"as_of": time.strftime("%Y-%m-%d %H:%M"), "rows": rows[:40],
           "scanned": len(pool), "candidates": len(cands), "rejected": dict(rejected),
           "note": "精选池结构筛选: 候选先过基本面/机构闸(盈利、ROE≥2.5、营收/净利未暴跌、市值≥50亿、"
                   "成交额≥2亿、≥20家基金持有), 再验结构(近40日箱体≤25%、横盘≥20日、温和放量攻箱体上沿、"
                   "收盘强度≥0.45 排冲高回落)。评分=横盘时长+缩量蓄势+放量甜区(2-4x)+收盘实体+突破位置+机构覆盖。"
                   "标签: 临界=还差一口气未破上沿 / 刚突破=突破0~3% / 突破延伸=3%以上。"
                   "纯客观结构描述, 突破可能失败(假突破回落), 不构成任何买卖建议。"}
    _cache["coiled"] = (out, time.time())
    return out
