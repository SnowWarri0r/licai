"""归类层: 把每天的票钉进固定的几个格子, 归一次落库, 下游都读同一份。

**为什么要这一层**。项目里原来有五套各自为政的归类 —— concept_tags(概念) /
market_review(行业+概念) / limit_up_pool(行业+题材) / sector_share(行业) /
concept_trend(概念) —— 维度不统一, 而且一处都没落库。后果不是难看, 是**问不出问题**:
同一只票在五个面板里可能落进五个不同名字; 想问"这只票昨天在哪个格子里""从首板格子挪到
二连板格子的有几只", 没有一处能答。

这跟本项目已经踩过两次的味道一样: 同一件事有多份定义(「同一张委托」判过两次, 归类判了五次)。
所以这里只做一件事 —— 定义格子、落库、给迁移查询, 不做展示。

四个轴:
    板  当日连板数(1=首板), 来自涨停档案
    进  「N天M板」, 本模块自算(见下), 区分"昨天刚封"与"这波已经反复涨了九天"
    钱  全市场成交额位次, **不看是否涨停**(一只票可以只在钱轴上有格子)
    题  题材/行业, 统一成一套口径。存 3 条概念, 按**今天资金扎堆的强度**排序 ——
        东财 f103 的原始顺序是任意的(中际旭创排最前的是"节能环保", 而它今天真正在的线是
        CPO/光通信/算力), 取前几个等于随机取标签。
        ⚠ 第一条不等于最有信息量的那条: 宽概念(通信技术)天生成员多、成交额大, 永远排在
        窄概念(CPO概念)前面。要"主线"时往后取一条更具体的, 别只读第一条就下结论。

**各轴覆盖不一样齐, 别混着用**:
  · 板/进/题 覆盖涨停档案的全部 244 天(纯回放, 不需要外部数据);
  · 钱轴只有当日榜单能给。历史那 244 天拉不到全市场成交额排名 —— kline_cache 只有 31%
    的代码, 拿它排会排出一个**假榜**, 所以历史一律留空, 从现在起每天收盘攒。
    宁可留空也不排假榜: 假榜看不出是假的。

**「进」这个轴的口径与命中率**(必须跟着数字走, 不能假装等于东财):
  规则 = 从当日往前, 断 ≤2 个交易日就算同一波, 最多回溯 20 个交易日。
  拿有东财 zttj 标注的 16 个交易日(1065 行)当验证集网格搜出来的:
      完全命中(N 与 M 都对)      1015/1065 = 95.3%
      「是不是新面孔」这一项              97.8%   (只看连板数是 90.8%)
  失败方向单一: **我们少算**。宝鼎科技 8-20 档案里 07-31~08-20 有 8 个涨停, 但 08-13~08-19
  断了 5 个交易日, 我们切成两波(1天1板), 东财算一波(15天8板)。所以自算版会把少数高位老票
  读成新面孔 —— 恰好是这个轴最该防的错, 用的时候记着这 4.7%。
  试过但扔掉的简化版: 「近 20 日涨停次数」, 一致率只有 72.6%, 反而比只看连板数还差 ——
  东财的一波很短, 15 天前涨停过、今天又涨停算两波, 20 天窗口把它们捏成了一波。
"""
from __future__ import annotations
import asyncio
from datetime import datetime, timedelta, timezone

from database import (get_limit_up_pool, list_limit_up_days, save_stock_tags,
                      get_stock_tags, get_tag_dates, stock_tag_coverage)

_CST = timezone(timedelta(hours=8))

# 「进」的窗口规则。改这两个数就等于改口径 —— 上面那组命中率是照着 (2, 20) 测出来的。
_JJ_MAX_GAP = 2
_JJ_LOOKBACK = 20

_AMT_TOP = 100          # 钱轴取前 100 名(与「资金归类: 全市场成交额前100」对齐)


def jinji(zt_days: list[set], i: int, code: str) -> tuple[int, int, bool]:
    """从逐日涨停名单回放某只票在第 i 天的「N天M板」。

    zt_days[j] = 第 j 个交易日的涨停代码集合(按日期升序)。返回 (N, M, 窗口是否不全)。
    窗口撞到档案起点时 partial=True —— 档案最早那 20 个交易日必然如此, 那几天的 进 不可信。
    """
    m, first, gap = 0, i, 0
    lo = i - _JJ_LOOKBACK + 1
    for j in range(i, max(-1, lo) - 1, -1):
        if j < 0:
            break
        if code in zt_days[j]:
            m += 1
            first = j
            gap = 0
        else:
            gap += 1
            if gap > _JJ_MAX_GAP:
                break
    partial = (first - _JJ_MAX_GAP) < 0 or lo < 0
    return (i - first + 1), m, partial


async def _zt_calendar(end: str | None = None) -> tuple[list[str], list[set]]:
    """涨停档案回放成 (日期序列, 每日涨停代码集合)。"""
    days = [d["date"] for d in await list_limit_up_days(limit=10000, end=end)]
    sets = []
    for d in days:
        rows = await get_limit_up_pool(d)
        sets.append({r["stock_code"] for r in rows})
    return days, sets


async def _amt_ranking() -> list[dict]:
    """当日全市场成交额前 N。只有"现在"能取到 —— 所以钱轴是从今天起往后攒的。

    榜单行的键是中文(行业 / 概念 / 成交额亿), 概念还是个**列表**。题轴不生拼那个列表:
    过一遍 concept_tags.clean 去掉噪声标签(指数成分/风格/"涨停"这类描述性词), 只留前几个。
    归类层的意义就在这儿 —— 去噪只做一次、落一份, 而不是五个面板各去一遍各得一个结果。
    """
    from services import market_review
    from services.concept_tags import clean, group_rows
    try:
        rk = await asyncio.to_thread(market_review.top_rankings, _AMT_TOP)
    except Exception:
        return []
    rows = (rk or {}).get("by_amount") or []
    # 题轴按「今天资金扎堆的强度」排, 不按原始顺序取前几个。
    # 东财 f103 的顺序是任意的: 中际旭创排在最前的是"节能环保", 而它今天真正在的线是
    # CPO/光通信/算力。取前几个等于随机取标签 —— 那正是"给了一堆标签、看不出主线"的来源。
    # group_rows 已经会按只数+成交额聚出今天的强线并合掉同义(Jaccard≥0.75), 复用它。
    strength: dict[str, int] = {}
    for i, g in enumerate(group_rows(rows, key="概念", top=25, min_n=2)):
        strength.setdefault(g["name"], i)          # 名次越小越强
    out = []
    for i, r in enumerate(rows[:_AMT_TOP]):
        code = str(r.get("code") or "")
        if not code:
            continue
        cs = clean(r.get("概念") or [])
        # 先按今天的扎堆强度排, 没进强线的排在后面(保持原序)
        cs.sort(key=lambda c: strength.get(c, 10_000))
        out.append({"stock_code": code, "name": r.get("name"), "amt_rank": i + 1,
                    "amt": float(r.get("成交额亿") or 0) * 1e8,
                    "industry": r.get("行业") or None,
                    "theme": "、".join(cs[:3]) or None})
    return out


async def build_day(day: str, *, with_money: bool = False) -> dict:
    """归类某一天并落库。with_money 只在"当天收盘后"为真(榜单只有现在能取)。"""
    days, sets = await _zt_calendar(end=day)
    if not days or days[-1] != day:
        return {"date": day, "rows": 0, "note": "那天没有涨停档案, 归类无从下手"}
    i = len(days) - 1
    pool = await get_limit_up_pool(day)
    tags: dict[str, dict] = {}
    for r in pool:
        n, m, partial = jinji(sets, i, r["stock_code"])
        tags[r["stock_code"]] = {
            "snap_date": day, "stock_code": r["stock_code"], "name": r["name"],
            "lb_count": r["lb_count"], "jj_days": n, "jj_boards": m,
            "jj_partial": 1 if partial else 0,
            "amt_rank": None, "amt": r["amount"],
            "theme": r["theme"], "industry": r["industry"]}
    n_money = 0
    if with_money:
        for a in await _amt_ranking():
            t = tags.get(a["stock_code"])
            if t:                                  # 涨停股同时上了成交额榜
                t["amt_rank"] = a["amt_rank"]
                t["amt"] = a["amt"] or t["amt"]
                t["industry"] = t["industry"] or a["industry"]
                t["theme"] = t["theme"] or a["theme"]
            else:                                  # 没涨停但钱在这儿 —— 钱轴的意义就在这半
                tags[a["stock_code"]] = {
                    "snap_date": day, "stock_code": a["stock_code"], "name": a["name"],
                    "lb_count": None, "jj_days": None, "jj_boards": None, "jj_partial": 0,
                    "amt_rank": a["amt_rank"], "amt": a["amt"],
                    "theme": a["theme"], "industry": a["industry"]}
            n_money += 1
    rows = list(tags.values())
    await save_stock_tags(rows)
    return {"date": day, "rows": len(rows), "涨停": len(pool), "钱轴": n_money}


async def rebuild(limit: int = 400) -> dict:
    """把涨停档案整段回放成归类层。纯派生, 重跑幂等。

    只跑一遍 _zt_calendar: 逐日各自回放要重读几百次档案, 慢得没必要。
    """
    days, sets = await _zt_calendar()
    total, done, partial = 0, 0, 0
    for i, day in enumerate(days[-limit:], start=max(0, len(days) - limit)):
        pool = await get_limit_up_pool(day)
        if not pool:
            continue
        rows = []
        for r in pool:
            n, m, pt = jinji(sets, i, r["stock_code"])
            partial += 1 if pt else 0
            rows.append({"snap_date": day, "stock_code": r["stock_code"], "name": r["name"],
                         "lb_count": r["lb_count"], "jj_days": n, "jj_boards": m,
                         "jj_partial": 1 if pt else 0, "amt_rank": None, "amt": r["amount"],
                         "theme": r["theme"], "industry": r["industry"]})
        await save_stock_tags(rows)
        total += len(rows)
        done += 1
    return {"days": done, "rows": total, "进_窗口不全": partial}


async def sync_today() -> dict:
    """收盘钩子: 先有涨停档案再归类, 顺手把钱轴(当日成交额榜)钉进去。"""
    today = datetime.now(tz=_CST).strftime("%Y-%m-%d")
    return await build_day(today, with_money=True)


# ── 格子之间的迁移: 这一层真正的用处 ──────────────────────

async def migration(day: str, prev: str | None = None) -> dict:
    """昨天各格子里的票, 今天去哪了。

    这是"归类落一层"之后才问得出的问题: 首板→二连板的转化率、连板梯队是在升还是在塌。
    只用两天的板轴, 不碰价格 —— 所以不受 kline_cache 那 31% 覆盖率的限制。
    """
    dates = await get_tag_dates(limit=10000)
    if day not in dates:
        return {"date": day, "可用": False, "note": f"{day} 还没归类"}
    if prev is None:
        i = dates.index(day)
        if i == 0:
            return {"date": day, "可用": False, "note": "没有上一个归类日, 无从比较"}
        prev = dates[i - 1]
    a = {r["stock_code"]: r for r in await get_stock_tags(prev) if r["lb_count"]}
    b = {r["stock_code"]: r for r in await get_stock_tags(day) if r["lb_count"]}
    # 掉出之后两个交易日内有没有回到涨停名单(反包)。只查名单, 不碰价格。
    i_day = dates.index(day)
    later: set = set()
    for d in dates[i_day + 1:i_day + 3]:
        later |= {r["stock_code"] for r in await get_stock_tags(d) if r["lb_count"]}
    buckets: dict[int, dict] = {}
    for code, r in a.items():
        lb = r["lb_count"] or 0
        s = buckets.setdefault(lb, {"上日只数": 0, "今日仍涨停": 0, "掉出": 0, "掉出后两日内反包": 0})
        s["上日只数"] += 1
        if code in b:
            s["今日仍涨停"] += 1
        else:
            s["掉出"] += 1
            if code in later:
                s["掉出后两日内反包"] += 1
    out = []
    for lb in sorted(buckets):
        s = buckets[lb]
        out.append({"上日连板数": lb, **s,
                    "接力率%": round(s["今日仍涨停"] / s["上日只数"] * 100, 1)})
    fresh_prev = [r for r in a.values() if (r["jj_boards"] or 0) == 1]
    return {"date": day, "上一归类日": prev, "可用": True, "梯队迁移": out,
            "上日新面孔只数": len(fresh_prev),
            "上日新面孔今日仍涨停": sum(1 for r in fresh_prev if r["stock_code"] in b),
            "反包窗口": [d for d in dates[i_day + 1:i_day + 3]] or None,
            "口径": ("只用板轴(当日在不在涨停名单/连板数), 不含价格, 所以不受本地日线 31% "
                     "覆盖率的限制。接力率=上日涨停的票今日仍涨停的比例; 掉出=今日不在涨停名单里"
                     "(可能只是没涨停, 不等于下跌)。注意「晋级」不是独立信息 —— 连板数就是从涨停"
                     "名单派生的, 又涨停必然连板数+1, 所以不单列。反包只在后面还有归类日时才有值。"
                     "「新面孔」按本模块自算的 进 轴判(命中率 97.8%, 会把少数高位老票读成新面孔)。"),
            }


async def pools(day: str | None = None, *, with_quotes: bool = True) -> dict:
    """股池: 上一个归类日各格子里的票, 今天怎么样了。

    这是归类层的第一个出口。给的是**真实成分**(代码/名称/封单/连板)加今日实时涨幅, 不是
    "情绪偏强"这种词 —— 池子里有哪几只、今天走成什么样, 自己看得见, 才可能被证伪。

    锚点取"上一个有归类的交易日"而不是硬算昨天: 周末/长假/今天还没收盘落档, 都要能给出
    对的那一天。今日涨幅走实时报价, 所以盘中调用就是盘中实况, 收盘后是定格。
    """
    dates = await get_tag_dates(limit=10000)
    if not dates:
        return {"可用": False, "note": "还没有归类数据"}
    today = datetime.now(tz=_CST).strftime("%Y-%m-%d")
    if day is None:
        # 今天若已落档, 锚点取今天之前那一天(池子问的是"昨天那批今天如何")
        day = dates[-2] if dates[-1] == today and len(dates) > 1 else dates[-1]
    if day not in dates:
        return {"可用": False, "note": f"{day} 没有归类数据"}
    rows = [r for r in await get_stock_tags(day) if r["lb_count"]]
    if not rows:
        return {"可用": False, "note": f"{day} 没有涨停股"}

    quotes: dict = {}
    if with_quotes:
        from services.market_data import get_realtime_quotes
        try:
            quotes = await get_realtime_quotes([r["stock_code"] for r in rows])
        except Exception:
            quotes = {}

    def _one(r: dict) -> dict:
        q = quotes.get(r["stock_code"]) or {}
        return {"代码": r["stock_code"], "名称": r["name"],
                "连板": r["lb_count"],
                "几进几": (f'{r["jj_days"]}天{r["jj_boards"]}板'
                           if r["jj_days"] and not r["jj_partial"] else None),
                "题": (r["theme"] or r["industry"]),
                # 现价一并带出: 前端点开 K 线弹窗时要拿它填表头, 不然价格/涨跌幅都显示成 --
                # (弹窗的现价原本靠 TDX 盘口兜底, TDX 一断连价格也没了)
                "现价": q.get("price"),
                "今日涨幅": q.get("change_pct")}

    groups = [
        ("昨日首板", [r for r in rows if (r["jj_boards"] or 0) == 1]),
        ("昨日连板", [r for r in rows if (r["lb_count"] or 0) >= 2]),
        ("昨日反复板", [r for r in rows if (r["lb_count"] or 0) == 1
                        and (r["jj_boards"] or 0) > 1]),
    ]
    out = []
    for name, sub in groups:
        items = [_one(r) for r in sub]
        got = [x["今日涨幅"] for x in items if x["今日涨幅"] is not None]
        stat = None
        if got:
            got_sorted = sorted(got)
            stat = {"取到行情": len(got), "今日平均%": round(sum(got) / len(got), 2),
                    "今日中位%": round(got_sorted[len(got) // 2], 2),
                    "红盘": sum(1 for x in got if x > 0)}
        out.append({"池": name, "只数": len(sub), "统计": stat,
                    "成分": sorted(items, key=lambda x: (x["今日涨幅"] is None,
                                                         -(x["今日涨幅"] or 0)))})
    return {"可用": True, "锚点日": day, "取数时刻": datetime.now(tz=_CST).strftime("%H:%M"),
            "池": out,
            "口径": ("锚点日=上一个有归类的交易日; 今日涨幅取实时报价(盘中即盘中实况)。"
                     "「昨日首板」按自算的 进 轴判(这波第一次涨停), 不是只看连板数=1 —— "
                     "后者会把'连板数=1 但已经 9天5板'的高位老票混进来(9-02 那天 39 只里混了 8 只)。"
                     "「昨日反复板」就是那批: 昨天不连板, 但这一波已经涨停过几次。"
                     "自算的 进 轴命中率 97.8%, 会把少数高位老票读成新面孔。"
                     "纯客观数据, 不构成买卖建议。")}


async def coverage() -> dict:
    return await stock_tag_coverage()
