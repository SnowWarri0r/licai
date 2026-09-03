"""逐只涨停档案: 封单额 / 首封时刻 / 炸板次数。

**为什么加这个**: 我们原来只会**数**涨停 —— sentiment_history 记「今天 52 个涨停」, 可
52 个涨停配 57 亿封单和配 20 亿封单是两个完全不同的盘, 只数一样根本看不出来。封单额是
买一挂单额, 是真实盘口上的量(不是按单笔金额切的"主力/大单"档), 直接读的就是「有多少钱
愿意在涨停价上排队」—— 量价即共识, 这是共识强度最直接的一个读数。

**两个源, 分工是实测出来的, 不是拍的**:
  · 东财涨停股池(push2ex getTopicZTPool?date=) —— 日常与实时。字段最全: 除封单额外还有
    炸板次数 / 换手 / 流通市值 / N天M板, 而且是我们本来就在用的源(不会因为某个 app 改版
    整块死掉)。**但历史只有滚动约 3 周**: 实测 8-13 起有数, 8-06 及更早一律返回 0 只。
  · 开盘啦(apphis DailyLimitPerformance, 按 PidType=连板数 逐档取) —— **只做一次性历史
    回填**。实测能回到 2024-06-18。字段少(没有炸板次数/换手/流通市值), 所以约定它不许
    覆盖东财那份(见 database.save_limit_up_pool 的冲突规则)。灌完这批数据就归我们了,
    之后不再依赖它。

**两源交叉验证过, 不是二选一的赌**: 8-20 那天东财 79 只 / 开盘啦 79 只, 股票集合完全
一致, 封单额 79 只全等; 首封时刻在分钟级一致(开盘啦精到秒 09:25:59, 东财截到 09:25:00,
所以逐秒比会有约 10 只"不符", 实际是同一分钟)。东财的封单额另外拿新浪盘口买一(量×价)
逐只验过, 49 只零偏离。

开盘啦那份是数组、字段没有名字, 下标是拿东财逐只比出来的(79/79 命中):
    [0]代码 [1]名称 [4]首封unix时间戳 [5]主题 [6]封单额 [11]成交额 [12]概念
    [15]连板数 [19]板块代码 [22]涨跌幅;  PidType 恒等于连板数
炸板次数/换手/流通市值 在那份里匹配率低(<20%), 所以**不猜、留空**。
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

from database import (save_limit_up_pool, get_limit_up_pool, get_next_bars,
                      limit_up_pool_coverage, list_limit_up_days)

_CST = timezone(timedelta(hours=8))

_EM_HOSTS = ["push2ex.eastmoney.com"]
_EM_UT = "7eea3edcaed734bea9cbfc24409ed989"
_KPL_HOST = "apphis.longhuvip.com"
_KPL_VER = "5.21.0.2"                 # app 版本号写死在协议里, 哪天 400 了就是它过期
_KPL_MAX_PID = 12                     # 连板高度上限; 连续 _KPL_DRY 档空了就收工
_KPL_DRY = 3                          # 8-20 实测 首板75/2板3/**3板0**/4板1 —— 中间会断档,
                                      # 所以不能见到一个空档就停

# 一字板: 集合竞价就封住(9:25 撮合出的开盘价即涨停价)。这类封板的共识最硬。
_OPEN_SEAL = "09:26:00"
_LATE_SEAL = "14:30:00"               # 尾盘偷袭: 剩半小时才封, 次日承接最没保证


def _session():
    import requests as _rq
    s = _rq.Session()
    s.trust_env = False               # 不吃系统代理: 本机带 proxy 时 push2 会直接断连
    s.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
    return s


def _hhmmss(v) -> str | None:
    """东财给的是整数 92500 / 143012, 不是字符串, 前导零得自己补回来。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    s = f"{n:06d}"
    return f"{s[:2]}:{s[2:4]}:{s[4:6]}"


def _ts_hhmmss(v) -> str | None:
    """开盘啦给 unix 秒。固定按东八区解 —— 跟着服务器本地时区走的话, 换台机器时刻就飘了。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n, tz=_CST).strftime("%H:%M:%S")


def _clean_name(s) -> str:
    return str(s or "").replace(" ", "")     # 东财会给 "英 力 特"


def _fetch_em_sync(day: str) -> list[dict]:
    """day = YYYY-MM-DD。返回归一化后的行; 拿不到就空列表(调用方自己决定要不要退回其他源)。"""
    s = _session()
    ymd = day.replace("-", "")
    for h in _EM_HOSTS:
        try:
            r = s.get(f"http://{h}/getTopicZTPool",
                      params={"ut": _EM_UT, "dpt": "wz.ztzt", "Pageindex": "0",
                              "pagesize": "500", "sort": "fbt:asc", "date": ymd, "_": "1"},
                      timeout=12)
            pool = ((r.json() or {}).get("data") or {}).get("pool") or []
        except Exception:
            continue
        out = []
        for p in pool:
            code = str(p.get("c") or "")
            if not code:
                continue
            tj = p.get("zttj") or {}
            out.append({
                "snap_date": day, "stock_code": code, "name": _clean_name(p.get("n")),
                "seal_amount": p.get("fund"), "first_seal": _hhmmss(p.get("fbt")),
                "last_seal": _hhmmss(p.get("lbt")), "lb_count": p.get("lbc"),
                "broken_times": p.get("zbc"), "zt_days": tj.get("days"), "zt_ct": tj.get("ct"),
                "industry": p.get("hybk"), "theme": None, "amount": p.get("amount"),
                "float_mv": p.get("ltsz"), "turnover": p.get("hs"), "pct": p.get("zdp"),
                "source": "em"})
        return out
    return []


def _fetch_kpl_sync(day: str) -> list[dict]:
    """按 PidType(=连板数) 逐档取。一天要打好几次, 只用于一次性回填, 不进日常链路。"""
    import requests as _rq
    import uuid as _uuid
    s = _rq.Session()
    s.trust_env = False
    headers = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
               "User-Agent": "Dalvik/2.1.0 (Linux; U; Android 9; Build/PQ3A.190605.01141736)",
               "Host": _KPL_HOST, "Connection": "Keep-Alive", "Accept-Encoding": "gzip"}
    out: list[dict] = []
    dry = 0
    for pid in range(1, _KPL_MAX_PID + 1):
        try:
            r = s.post(f"https://{_KPL_HOST}/w1/api/index.php",
                       data={"Order": "0", "a": "DailyLimitPerformance", "st": "2000",
                             "c": "HisHomeDingPan", "PhoneOSNew": "1",
                             "DeviceID": str(_uuid.uuid4()), "VerSion": _KPL_VER, "Index": "0",
                             "PidType": str(pid), "apiv": "w42", "Type": "4", "Day": day},
                       headers=headers, timeout=20)      # 证书是好的, 不必关校验(实测过)
            info = (r.json() or {}).get("info") or []
        except Exception:
            info = []
        rows = info[0] if info else []
        if not rows:
            dry += 1
            if dry >= _KPL_DRY:
                break
            continue
        dry = 0
        for a in rows:
            if not isinstance(a, list) or len(a) < 23 or not a[0]:
                continue                       # 数组变短就是协议改了, 宁可丢这条不要错位
            out.append({
                "snap_date": day, "stock_code": str(a[0]), "name": _clean_name(a[1]),
                "seal_amount": a[6], "first_seal": _ts_hhmmss(a[4]), "last_seal": None,
                "lb_count": a[15] or pid, "broken_times": None, "zt_days": None, "zt_ct": None,
                "industry": None, "theme": (a[5] or None) or (a[12] or None),
                "amount": a[11], "float_mv": None, "turnover": None, "pct": a[22],
                "source": "kpl"})
    return out


async def sync_day(day: str, *, allow_kpl: bool = False) -> dict:
    """抓某一天并落库。日常只走东财; allow_kpl=True 时东财空了才退到开盘啦(回填用)。"""
    rows = await asyncio.to_thread(_fetch_em_sync, day)
    src = "em"
    if not rows and allow_kpl:
        rows = await asyncio.to_thread(_fetch_kpl_sync, day)
        src = "kpl"
    if not rows:
        return {"date": day, "n": 0, "source": None, "saved": 0}
    saved = await save_limit_up_pool(rows)
    return {"date": day, "n": len(rows), "source": src, "saved": saved}


async def sync_today() -> dict:
    """收盘钩子用。封单额是盘口快照 —— 收盘后取到的才是定格值, 盘中取到的会变。"""
    today = datetime.now(tz=_CST).strftime("%Y-%m-%d")
    return await sync_day(today)


async def backfill(start: str, end: str, *, sleep: float = 0.25,
                   overwrite: bool = False) -> dict:
    """一次性历史回填: [start, end] 逐日, 东财优先(近 3 周)、更早退开盘啦。

    已经有数的日期默认跳过 —— 回填经常要分几次跑完, 每次都从头刷一遍既慢又白耗对方接口。
    """
    from services.market_data import _is_a_share_trading_day
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    d1 = datetime.strptime(end, "%Y-%m-%d").date()
    have = {r["date"] for r in await list_limit_up_days(limit=10000)} if not overwrite else set()
    days, rows, skipped, empty = 0, 0, 0, []
    d = d0
    while d <= d1:
        cur, d = d, d + timedelta(days=1)
        if not _is_a_share_trading_day(cur):
            continue
        ds = cur.strftime("%Y-%m-%d")
        if ds in have:
            skipped += 1
            continue
        r = await sync_day(ds, allow_kpl=True)
        if r["n"]:
            days += 1
            rows += r["n"]
        else:
            empty.append(ds)          # 交易日却一条没有 = 那天真没涨停, 或者两个源都够不着
        await asyncio.sleep(sleep)
    return {"days": days, "rows": rows, "skipped": skipped,
            "empty_days": empty[:20], "n_empty": len(empty)}


def _fmt_yi(v) -> float:
    return round((v or 0) / 1e8, 2)


async def quality(day: str | None = None) -> dict:
    """某一天涨停的**质量**画像。没有那天的档案就明说, 不编。"""
    if not day:
        day = datetime.now(tz=_CST).strftime("%Y-%m-%d")
    rows = await get_limit_up_pool(day)
    if not rows:
        return {"date": day, "有档案": False,
                "note": f"{day} 没有逐只涨停档案(当天还没收盘落档, 或早于回填区间)"}
    seals = sorted([r["seal_amount"] or 0 for r in rows], reverse=True)
    ratios = [(r["seal_amount"] or 0) / r["amount"] for r in rows if r["amount"]]
    n = len(rows)
    mid = seals[n // 2] if n else 0
    opened = [r for r in rows
              if (r["broken_times"] or 0) > 0
              or (r["first_seal"] and r["last_seal"] and r["first_seal"] != r["last_seal"])]
    one_word = [r for r in rows if (r["first_seal"] or "99") < _OPEN_SEAL]
    late = [r for r in rows if (r["first_seal"] or "00") >= _LATE_SEAL]
    by_ind: dict[str, list] = {}
    for r in rows:
        by_ind.setdefault(r["industry"] or r["theme"] or "未归类", []).append(r)
    ind = sorted(({"名称": k, "只数": len(v),
                   "封单亿": _fmt_yi(sum(x["seal_amount"] or 0 for x in v))}
                  for k, v in by_ind.items()),
                 key=lambda x: -x["封单亿"])[:6]
    # 与上一个有档案的交易日比: 封单总额的方向比绝对值更说明问题
    hist = await list_limit_up_days(limit=2, end=day)
    prev = hist[0] if len(hist) > 1 else None
    return {
        "date": day, "有档案": True, "涨停只数": n,
        "封单合计亿": _fmt_yi(sum(seals)),
        "封单中位数万": round(mid / 1e4),
        "最高连板": max((r["lb_count"] or 0 for r in rows), default=0),
        "一字板只数": len(one_word),
        "尾盘才封只数": len(late),
        "开过板只数": len(opened) or None,
        # 封成比 = 封单额/当日成交额。绝对额受票的大小影响, 这个自带归一, 更能比。
        "封成比中位": (round(sorted(ratios)[len(ratios) // 2], 2) if ratios else None),
        "封单最厚": [{"代码": r["stock_code"], "名称": r["name"],
                      "封单亿": _fmt_yi(r["seal_amount"]), "连板": r["lb_count"],
                      "首封": r["first_seal"],
                      "封成比": (round((r["seal_amount"] or 0) / r["amount"], 2)
                                 if r["amount"] else None)} for r in rows[:5]],
        "封单扎堆": ind,
        "较上一档案日": ({"日期": prev["date"],
                          "封单合计亿": _fmt_yi(prev["seal_sum"]),
                          "只数": prev["n"]} if prev else None),
        "口径": ("封单额=买一挂单额(量×价), 收盘定格值; 一字板=9:25 集合竞价即封住; "
                 "开过板只数只有东财那份能判(开盘啦历史没有炸板次数), 为空表示那天的档案来自回填。"),
    }


_BUCKETS = [(0, 0.5), (0.5, 2), (2, 5), (5, 1e9)]    # 亿
_RATIO_BUCKETS = [(0, 0.1), (0.1, 0.3), (0.3, 0.8), (0.8, 1e9)]   # 封单额 / 当日成交额


async def seal_backtest(days: int = 250, min_per_bucket: int = 20) -> dict:
    """封单额 → 次日兑现度, 附**混淆项对照**。这是这块数据能不能算"信号"的唯一判据。

    兑现怎么量: 以涨停日收盘为基准(打板买进去的成本就在那儿), 看次日**开盘溢价**和
    **收盘涨跌**。开盘高、收盘还守得住才叫接住了; 高开低走是把封单里排队的人抬出去。
    次日行情取自我们自己的 kline_cache, 拉不到的整只剔掉并报覆盖率 —— 覆盖率低的时候
    这张表不作数, 别拿 30% 的样本讲全市场。

    **为什么必须带对照**: 封单额的绝对值跟票的大小高度相关, 大票封单天然厚, 所以"封单越厚
    次日越强"这条单调关系完全可能只是"大票次日更稳"的影子。这里同时给两样:
      · 封成比 = 封单额 / 当日成交额 —— 自带规模归一, 比绝对额更该看;
      · size_control: 把样本按成交额切三档, 每档内部再按封成比中位数对半劈。
        只有"每一档内部高半都赢低半"才说明这不是规模效应。实测三档分别 +1.32 / +1.00 /
        +1.48pp, 对照是过的 —— 但这条结论跟着数据走, 哪天不过了函数会照实报。

    **它不是可交易信号**: 封单厚恰恰意味着你排不进那个队。这是用来解释和复盘盘面强弱的,
    不是用来决定买卖的。
    """
    hist = await list_limit_up_days(limit=days)
    if not hist:
        return {"可用": False, "note": "还没有涨停档案, 先跑 backfill"}
    sample: list[dict] = []
    total, covered = 0, 0
    for h in hist:
        rows = await get_limit_up_pool(h["date"])
        if not rows:
            continue
        codes = [r["stock_code"] for r in rows]
        nxt = await get_next_bars(codes, h["date"])
        base = await _closes_on(codes, h["date"])
        for r in rows:
            total += 1
            code = r["stock_code"]
            b, nb = base.get(code), nxt.get(code)
            if not b or not nb or not nb.get("open"):
                continue
            covered += 1
            sample.append({
                "seal": r["seal_amount"] or 0, "amt": r["amount"] or 0,
                "open": (nb["open"] - b) / b * 100, "close": (nb["close"] - b) / b * 100})

    def _bucket(key, buckets, label, unit):
        out = []
        for lo, hi in buckets:
            g = [x for x in sample if x[key] and lo <= x[key] < hi]
            tag = f"{lo}~{hi if hi < 1e9 else '∞'}{unit}"
            if len(g) < min_per_bucket:
                out.append({label: tag, "样本": len(g), "结论": "样本不足, 不给数"})
                continue
            n = len(g)
            out.append({label: tag, "样本": n,
                        "次日开盘溢价%": round(sum(x["open"] for x in g) / n, 2),
                        "次日收盘涨跌%": round(sum(x["close"] for x in g) / n, 2),
                        "次日收红率%": round(sum(1 for x in g if x["close"] > 0) / n * 100, 1)})
        return out

    for x in sample:
        x["yi"] = x["seal"] / 1e8
        x["ratio"] = (x["seal"] / x["amt"]) if x["amt"] else 0

    # 混淆项对照: 成交额切三档, 每档内按封成比中位数对半劈
    control = []
    amts = sorted(x["amt"] for x in sample if x["amt"])
    if len(amts) >= max(60, min_per_bucket * 3):
        cuts = [amts[0], amts[len(amts) // 3], amts[2 * len(amts) // 3], amts[-1] + 1]
        for i in range(3):
            g = [x for x in sample if x["amt"] and cuts[i] <= x["amt"] < cuts[i + 1]]
            if len(g) < min_per_bucket * 2:
                continue
            med = sorted(x["ratio"] for x in g)[len(g) // 2]
            lo_h = [x for x in g if x["ratio"] < med]
            hi_h = [x for x in g if x["ratio"] >= med]
            if not lo_h or not hi_h:
                continue
            a = sum(x["close"] for x in lo_h) / len(lo_h)
            b2 = sum(x["close"] for x in hi_h) / len(hi_h)
            control.append({"成交额档": f"{cuts[i]/1e8:.2f}~{cuts[i+1]/1e8:.2f}亿", "样本": len(g),
                            "封成比低半次日%": round(a, 2), "封成比高半次日%": round(b2, 2),
                            "高半减低半pp": round(b2 - a, 2)})
    passed = bool(control) and all(c["高半减低半pp"] > 0 for c in control)
    cov = round(covered / total * 100, 1) if total else 0
    return {"可用": cov >= 30, "档案天数": len(hist), "涨停样本": total,
            "有次日行情的": covered, "覆盖率%": cov,
            "按封单额": _bucket("yi", _BUCKETS, "封单档", "亿"),
            "按封成比": _bucket("ratio", _RATIO_BUCKETS, "封成比档", ""),
            "规模对照": control,
            "规模对照通过": passed,
            "口径": ("基准=涨停日收盘; 次日行情来自本地 kline_cache, 缺的整只剔除。"
                     "封成比=封单额/当日成交额(自带规模归一)。规模对照=成交额三等分后每档内按"
                     "封成比对半劈, 三档全为正才算不是规模效应; 规模对照通过=false 时"
                     "「封单越厚次日越强」这句不成立, 别照抄。"
                     "覆盖率<30% 时 可用=false, 整张表别当结论。"
                     "另: 封单厚恰恰意味着排不进队 —— 这是解释盘面强弱的量, 不是买卖依据。"),
            "warning": None if cov >= 30 else f"覆盖率仅 {cov}%, 样本不足以代表全市场"}


async def _closes_on(codes: list[str], day: str) -> dict[str, float]:
    """涨停日自己的收盘 = 涨停价, 作为兑现度的基准。"""
    from database import get_cached_closes
    return await get_cached_closes(codes, day)


async def coverage() -> dict:
    return await limit_up_pool_coverage()
