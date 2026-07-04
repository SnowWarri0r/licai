"""横盘蓄势扫描: 在"龙头池"里找"横盘很久 + 放量启动(突破前后)"的结构。

设计(v3, 挑好的而非排差的):
  1) 龙头池: 全市场(含北交所)按 市值≥100亿 + ≥30家基金持有 + 盈利 圈定 —— 机构认可的
     百亿公司, 天然排掉垃圾/庄股/微盘, 不再需要"今天必须放量"这种入口闸。
  2) 整池日K体检(结构阈值放低): 箱体≤30%、横盘≥15日、近5日内有放量攻上沿、未跌回箱体。
     回踩日/安静日也能看到, 不只突破当天。
  3) 综合评分排序: 横盘时长 + 箱体越窄越好 + 缩量蓄势 + 放量甜区 + 收盘实体 + 突破位置。

产出纯客观结构描述(标签: 突破后回踩/临界/刚突破/突破延伸), 不构成任何买卖建议。
整池首扫 ~1 分钟(日K有 SQLite 缓存, 之后快), 由后台预热兜底 + 10 分钟缓存。
"""
from __future__ import annotations
import asyncio
import time
from datetime import date

_cache: dict = {}
_TTL = 600   # 10 分钟

_FS = "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81"   # 沪深A + 北交所
_FIELDS = "f3,f6,f10,f12,f14,f20,f23,f24,f26,f37,f41,f45,f46,f100,f115,f160"
_HOSTS = ["push2.eastmoney.com", "push2delay.eastmoney.com", "1.push2.eastmoney.com"]


def _clist_pool(pages: int = 20) -> list[dict]:
    """按市值降序拉前 N 页(百亿以上公司约 1100 家, 20 页够覆盖)。"""
    import requests
    s = requests.Session(); s.trust_env = False
    out: list[dict] = []
    for pn in range(1, pages + 1):
        p = {"pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2", "invt": "2",
             "fid": "f20", "fs": _FS, "fields": _FIELDS}
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
        # 已翻到百亿以下就停
        try:
            if float(got[-1].get("f20") or 0) / 1e8 < 100:
                break
        except (TypeError, ValueError):
            pass
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
        for q in quarters[:4]:      # 最新报告期可能未披露(表里只有零星早鸟), 要足够完整才采纳
            try:
                df = ak.stock_report_fund_hold(symbol="基金持仓", date=q)
            except Exception:
                continue
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


def _build_universe(rows: list[dict], fund_map: dict | None = None) -> list[dict]:
    """龙头池: 百亿市值 + 机构重仓 + 盈利的正常经营公司。"""
    fund_map = fund_map or {}
    today = int(date.today().strftime("%Y%m%d"))
    out = []
    for x in rows:
        def _f(key):
            v = x.get(key)
            try:
                return float(v)
            except (TypeError, ValueError):
                return None      # 停牌/缺数字段是 "-"
        code = str(x.get("f12") or ""); name = str(x.get("f14") or "")
        if not code or "ST" in name.upper() or "退" in name:
            continue
        pct, cap, amount = _f("f3"), _f("f20"), _f("f6")
        if pct is None or cap is None:          # 停牌/无行情
            continue
        cap /= 1e8
        try:
            ipo = int(x.get("f26") or 0)
        except (TypeError, ValueError):
            ipo = 0
        if ipo and today - ipo < 10000:       # 上市满一年(新股无横盘史)
            continue
        if cap < 100:                          # 龙头门槛: 百亿市值
            continue
        if (amount or 0) / 1e8 < 1:            # 今日成交额≥1亿(流动性)
            continue
        fund_cnt = fund_map.get(code, 0)
        if fund_map and fund_cnt < 30:         # 机构重仓: ≥30家基金持有
            continue
        if pct < -5:                           # 今天正在崩的不列(结构再好也先让它跌完)
            continue
        profit, roe, rev_yoy, prof_yoy, pb, pe = _f("f45"), _f("f37"), _f("f41"), _f("f46"), _f("f23"), _f("f115")
        if profit is None or profit <= 0:      # 盈利中
            continue
        if roe is None or roe < 1.0:           # ROE(报告期累计口径): 排接近零利润的
            continue
        if prof_yoy is not None and prof_yoy < -60:   # 暴雷级下滑
            continue
        if pb is not None and pb <= 0:
            continue
        out.append({"code": code, "name": name, "pct": round(pct, 2),
                    "成交额亿": round((amount or 0) / 1e8, 2),
                    "量比": _f("f10"), "市值亿": round(cap, 0),
                    "行业": x.get("f100") or "",
                    "ROE%": roe, "净利同比%": round(prof_yoy, 1) if prof_yoy is not None else None,
                    "营收同比%": round(rev_yoy, 1) if rev_yoy is not None else None,
                    "PE_TTM": pe, "基金家数": fund_cnt})
    # 整池体检, 按基金家数排(机构最认可的优先算)
    out.sort(key=lambda c: -(c.get("基金家数") or 0))
    return out[:900]


async def _stage2(c: dict) -> dict | str:
    """日K结构体检: 箱体 + 横盘时长 + 缩量蓄势 + 近5日突破窗口。返回 dict(通过) 或 拒绝原因。"""
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
    if width > 30:                                     # 箱体上限(龙头池内放宽)
        return "箱体过宽"
    # 横盘要求"平": 光在箱体内不够(箱子够宽, 趋势也装得下)。窗口前后半均值漂移小才是横, 爬坡/阴跌都拒
    half_c = len(prev_c) // 2
    drift = (sum(prev_c[half_c:]) / (len(prev_c) - half_c)) / (sum(prev_c[:half_c]) / half_c) - 1
    if abs(drift) * 100 > 6:
        return "窗口内有趋势(非横盘)"
    # 横盘还要求"静": 大开大合的宽幅震荡(日收益σ大)不是蓄势基座。安静基座σ≈1.5-2, 野震荡≥2.5
    rets = [prev_c[i] / prev_c[i - 1] - 1 for i in range(1, len(prev_c))]
    mean_r = sum(rets) / len(rets)
    sd = (sum((r - mean_r) ** 2 for r in rets) / len(rets)) ** 0.5 * 100
    if sd > 2.2:
        return "宽幅震荡(非安静横盘)"
    last_close = closes[-1]
    base_vol = sum(prev_v) / len(prev_v)
    if base_vol <= 0:
        return "K线不足"
    # 突破事件: 近5根内有一根 放量(≥1.3x横盘均量)且收盘攻到箱体上沿(≥0.985×上沿)
    bk_i, bk_vm = None, 0.0
    for i in range(-5, 0):
        vm_i = vols[i] / base_vol
        if closes[i] >= bh * 0.985 and vm_i >= 1.3 and vm_i > bk_vm:
            bk_i, bk_vm = i, vm_i
    if bk_i is None:
        return "未到上沿" if max(closes[-5:]) < bh * 0.985 else "放量不足"
    if last_close < bh * 0.96:                         # 突破后又跌回箱体深处 = 假突破已证伪
        return "突破后跌回"
    vol_mult = max(bk_vm, (sum(vols[-3:]) / 3) / base_vol)
    if (last_close / closes[-6] - 1) * 100 > 20:       # 近5日已经飞了, 不是"准备窜"
        return "近5日已飞"
    # 启动新鲜度: V型反转/多日爬坡(箱底一路拉回上沿)在触到上沿前涨幅已兑现大半, 不是"准备窜"。
    # 近20日最低点算起的累计拉升 ≤12% 才算刚启动; 两半均值查不出V型(前高后高中间低会互相抵消), 用这条兜
    run_up = (last_close / min(closes[-20:]) - 1) * 100
    if run_up > 12:
        return "已拉升多日(非新启动)"
    # 突破日收盘强度: (收-低)/(高-低)。长上影(冲高被砸回)= 假突破笔, 直接拒
    if len(highs) == len(closes) and highs and highs[bk_i] > lows[bk_i]:
        strength = round((closes[bk_i] - lows[bk_i]) / (highs[bk_i] - lows[bk_i]), 2)
    else:
        strength = 1.0
    if strength < 0.4:
        return "冲高回落(上影)"
    # 横盘时长: 从启动段前往回数, 收盘都落在箱体(±2%容差)内的连续天数
    lo, hi = bl * 0.98, bh * 1.02
    days_flat = 0
    for cl in reversed(closes[:-5]):
        if lo <= cl <= hi:
            days_flat += 1
        else:
            break
    if days_flat < 15:                                 # 横盘时长下限(龙头池内放宽)
        return "横盘太短"
    # 缩量蓄势: 横盘后半均量 / 前半均量 (<1 = 越盘越缩, 蓄势特征)
    half = len(prev_v) // 2
    contraction = round((sum(prev_v[half:]) / (len(prev_v) - half)) / (sum(prev_v[:half]) / half), 2)
    dist = round((last_close / bh - 1) * 100, 1)
    tag = ("突破后回踩" if dist < -1.5 else "临界(贴上沿)" if dist < 0
           else "刚突破" if dist <= 3 else "突破延伸")
    # 综合评分(0-110): 横盘越久+箱体越窄+越缩量+放量甜区(2-4x)+收盘实体强+刚好在突破位+机构覆盖
    score = (min(days_flat, 60) / 60 * 25
             + (30 - min(width, 30)) / 30 * 10
             + (15 if contraction <= 0.8 else 10 if contraction <= 0.95 else 5 if contraction <= 1.1 else 0)
             + (25 if 2 <= vol_mult <= 4 else 18 if vol_mult < 2 else 15 if vol_mult <= 6 else 8)
             + strength * 15
             + (15 if 0 <= dist <= 3 else 12 if -1.5 <= dist < 0 else 8)
             + (5 if (c.get("基金家数") or 0) >= 100 else 0))
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
    universe = _build_universe(pool, fmap)
    sem = asyncio.Semaphore(8)

    async def _one(x):
        async with sem:
            return await _stage2(x)

    results = await asyncio.gather(*[_one(x) for x in universe], return_exceptions=True)
    rows = [r for r in results if isinstance(r, dict)]
    rows.sort(key=lambda r: -r["评分"])
    from collections import Counter
    rejected = Counter(r for r in results if isinstance(r, str))
    out = {"as_of": time.strftime("%Y-%m-%d %H:%M"), "rows": rows[:40],
           "scanned": len(pool), "universe": len(universe), "rejected": dict(rejected),
           "note": "龙头池结构筛选: 池=百亿市值+≥30家基金持有+盈利(全市场含北交所, 机构认可的正规公司); "
                   "结构=近40日箱体≤30%、横盘≥15日、近5日内放量(≥1.3x)攻箱体上沿、未跌回、收盘强度≥0.4。"
                   "评分=横盘时长+箱体窄+缩量蓄势+放量甜区(2-4x)+收盘实体+突破位置+机构覆盖。"
                   "标签: 突破后回踩/临界(贴上沿)/刚突破/突破延伸。"
                   "纯客观结构描述, 突破可能失败(假突破回落), 不构成任何买卖建议。"}
    _cache["coiled"] = (out, time.time())
    return out


async def coiled_prewarm_loop():
    """后台预热(整池日K首扫慢), 盘中每 15min 刷新, 非盘中每小时。"""
    await asyncio.sleep(40)
    while True:
        try:
            await scan_coiled(force=True)
        except Exception:
            pass
        try:
            from services.market_data import is_trading_day_active
            interval = 900 if is_trading_day_active() else 3600
        except Exception:
            interval = 3600
        await asyncio.sleep(interval)
