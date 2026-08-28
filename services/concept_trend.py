"""概念线的几日走势: 谁在接力, 谁在退潮。

榜单聚堆解决的是"今天钱堆在哪条线上", 但轮动是时间上的事 —— 这条线是刚起来的, 还是已经
连着热了三天开始退? 一天的快照答不了。

历史哪来的: 本想直接用东财的概念板块日K(BK 代码那套), 实测这台机器上 push2his 连不上,
连仓库里那条已经在跑的个股日K 也一样被掐(不是 secid 写错, 是源在拦), 所以不拿它当地基。
改用手上已有的: 本地 kline_cache 里存着逐票逐日成交额, 实测成交额榜 100 只里 92 只有缓存、
89 只近一周有数据。把这条线上今天这批票的成交额按日加起来, 就是这条线的资金曲线。

三条口径必须跟着数字一起给出去, 不然这个曲线会骗人:
  · 成分是**今天**的。前几天那批票不完全是这批, 所以这是"今天这条线上的票, 前几天多少钱",
    不是"这条线当时多少钱"。看几天的轮动够用, 别当板块指数。
  · 只算**全程都有缓存**的那些成分股(basket), 缺一天的整只剔掉 —— 否则某天少几只就成了假跌。
    每条线都报 basket_n/total_n。
  · 今天那一格用实时榜单的数, 但同样只加 basket 里的票, 跟历史同口径; 不然覆盖率差异会
    在最后一天造出一个假跳空。
份额(share)= 这条线当日成交额 / 榜单全体当日成交额: 全市场放量缩量时, 绝对值一起涨跌,
份额才看得出资金是真往这条线上挪。堆之间互相重叠(一只票挂多个概念), 所以份额加起来会超 100%,
只看各自的变化, 别横向加总。
"""
from __future__ import annotations

import asyncio
import datetime as _dt

_UP_PP = 1.5        # 份额较上一日 +1.5 个百分点算"资金在往这条线挪"
_DOWN_PP = -1.5
# 覆盖率门槛。实测 8-28: 成交额榜 79/100 只有本地日线(能算), 涨幅榜只有 5/100 —— 涨幅榜
# 全是没人看过的小票, 缓存里根本没有。拿 2 只票代表一条 15 只票的线, 还敢标"在退潮",
# 那不是信息是噪音。宁可这条线不给结论。
_MIN_BASKET = 3           # 一条线至少要有 3 只成分股有全程日线
_MIN_BASKET_RATIO = 0.5   # 且要覆盖这条线成分的一半
_MIN_OVERALL = 0.3        # 整张榜覆盖不到三成, 整个曲线都不出


def _label(d1_share_pp: float | None, d1_amt_pct: float | None) -> str:
    """给一句人话。份额优先(全市场放量时绝对值都涨, 份额才说明问题)。"""
    if d1_share_pp is None:
        return "新上榜"
    if d1_share_pp >= _UP_PP:
        return "资金在进"
    if d1_share_pp <= _DOWN_PP:
        return "在退潮"
    if d1_amt_pct is not None and d1_amt_pct >= 30:
        return "跟着大盘放量"
    return "持平"


async def warm_cache(limit: int = 80) -> dict:
    """收盘后把今天两张榜里"本地还没有日线"的票补进缓存(eod loop 调用)。

    为什么要它: 缓存里只有被看过的票, 而涨幅榜天天换一批没人看过的小票 —— 实测 8-28
    涨幅榜 100 只里只有 5 只有日线, 那条线的资金曲线根本算不出来。每天收盘补一次, 几天后
    覆盖就够了。逐只串行 + 失败跳过: 这是后台补数, 慢没关系, 别把数据源打急了。
    """
    from database import get_cached_amounts
    from services import market_review
    from services.market_data import get_historical_data

    rk = await asyncio.to_thread(market_review.top_rankings, 100)
    if rk.get("error"):
        return {"error": rk["error"]}
    codes = list(dict.fromkeys([r["code"] for r in (rk.get("by_amount") or [])]
                               + [r["code"] for r in (rk.get("gainers") or [])]))
    today = (rk.get("as_of") or "")[:10] or _dt.date.today().isoformat()
    since = (_dt.date.fromisoformat(today) - _dt.timedelta(days=20)).isoformat()
    hist = await get_cached_amounts(codes, since)
    have = {c for day in hist.values() for c in day}
    missing = [c for c in codes if c not in have][:max(1, int(limit))]
    ok = 0
    for c in missing:
        try:
            df = await get_historical_data(c, days=30)
            if df is not None and len(df):
                ok += 1
        except Exception:
            continue
        await asyncio.sleep(0.25)
    return {"missing": len(missing), "fetched": ok, "already": len(have), "ranked": len(codes)}


async def concept_trend(scope: str = "by_amount", kind: str = "概念",
                        days: int = 5, top: int = 10) -> dict:
    """榜单前几堆的近 days 日资金曲线。scope: by_amount|gainers; kind: 概念|行业。"""
    from database import get_cached_amounts
    from services import market_review

    scope = scope if scope in ("by_amount", "gainers") else "by_amount"
    kind = kind if kind in ("概念", "行业") else "概念"
    days = max(2, min(int(days or 5), 20))

    rk = await asyncio.to_thread(market_review.top_rankings, 100)
    if rk.get("error"):
        return {"error": rk["error"]}
    rows = rk.get(scope) or []
    groups = ((rk.get("groups") or {}).get(scope) or {}).get(
        "concepts" if kind == "概念" else "industries") or []
    groups = groups[:top]
    if not rows or not groups:
        return {"scope": scope, "kind": kind, "rows": [], "dates": [],
                "note": "今天这张榜还没聚出堆"}

    today = (rk.get("as_of") or "")[:10] or _dt.date.today().isoformat()
    live = {r["code"]: float(r.get("成交额亿") or 0) for r in rows}
    all_codes = list(live)
    since = (_dt.date.fromisoformat(today) - _dt.timedelta(days=days * 3 + 10)).isoformat()
    hist = await get_cached_amounts(all_codes, since)
    # 今天可能已经落进缓存(收盘后回补), 那一格一律用实时榜单的数, 别让两个源打架
    past = sorted(d for d in hist if d < today)[-(days - 1):]
    dates = past + [today]

    def _amt(code: str, d: str) -> float | None:
        if d == today:
            return live.get(code)
        v = (hist.get(d) or {}).get(code)
        return v / 1e8 if v is not None else None      # 缓存是元, 榜单是亿

    # 全程都有数的票才进篮子: 缺一天就整只剔掉, 否则那天会凭空少一块变成"假退潮"
    full = [c for c in all_codes if all(_amt(c, d) is not None for d in dates)]
    cst = _dt.datetime.utcnow() + _dt.timedelta(hours=8)
    partial = (cst.strftime("%Y-%m-%d") == today and cst.hour * 60 + cst.minute < 15 * 60)
    if len(dates) < 2 or len(full) < max(5, _MIN_OVERALL * len(all_codes)):
        return {"scope": scope, "kind": kind, "dates": dates, "rows": [],
                "today_partial": partial,
                "coverage": {"basket": len(full), "ranked": len(all_codes)},
                "note": (f"本地日线只覆盖到这张榜的 {len(full)}/{len(all_codes)} 只, 不够算这条线的资金曲线 "
                         f"—— 拿两三只票代表一条十几只票的线, 结论是编的。收盘后会把缺的补进来。")}
    total_by_date = {d: sum(_amt(c, d) or 0 for c in full) for d in dates}

    out = []
    for g in groups:
        codes = g.get("codes") or []
        basket = [c for c in codes if c in full]
        # 成分覆盖不够就不给这条线下结论(缺的那几只可能正好是今天最猛的那几只)
        if len(basket) < _MIN_BASKET or (codes and len(basket) / len(codes) < _MIN_BASKET_RATIO):
            continue
        series = []
        for d in dates:
            amt = sum(_amt(c, d) or 0 for c in basket)
            tot = total_by_date.get(d) or 0
            series.append({"date": d, "amt_yi": round(amt, 1),
                           "share_pct": round(amt / tot * 100, 2) if tot > 0 else None})
        cur, prev = series[-1], series[-2]
        d1_share = (round(cur["share_pct"] - prev["share_pct"], 2)
                    if cur["share_pct"] is not None and prev["share_pct"] is not None else None)
        d1_amt = (round((cur["amt_yi"] - prev["amt_yi"]) / prev["amt_yi"] * 100, 1)
                  if prev["amt_yi"] > 0 else None)
        base = [s["amt_yi"] for s in series[:-1]]
        dn_amt = (round((cur["amt_yi"] - sum(base) / len(base)) / (sum(base) / len(base)) * 100, 1)
                  if base and sum(base) > 0 else None)
        out.append({
            "name": g["name"], "aliases": g.get("aliases") or [],
            "series": series, "d1_share_pp": d1_share, "d1_amt_pct": d1_amt,
            "dn_amt_pct": dn_amt, "label": _label(d1_share, d1_amt),
            "basket_n": len(basket), "total_n": len(g.get("codes") or []),
        })
    out.sort(key=lambda x: -(x["series"][-1]["amt_yi"] or 0))
    # partial: 盘中最后一格是半天的量, 绝对值当然比昨天小 —— 标出来, 否则上午看都像"全线退潮"
    return {
        "scope": scope, "kind": kind, "dates": dates, "rows": out,
        "today_partial": partial,
        "coverage": {"basket": len(full), "ranked": len(all_codes)},
        "note": ("成分是今天这张榜上的票(不是当时的板块成分), 只统计全程都有本地日线的那些; "
                 "share=该线当日成交额占榜单全体的比重, 各线互相重叠, 不要横向加总。"
                 "纯客观数据, 不构成任何买卖建议。"),
    }
