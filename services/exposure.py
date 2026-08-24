"""穿透敞口(同源风险): 把基金拆到底层个股, 算"真实持有多少", 而不是按名字猜。

替掉的是什么: 原来的同源检测是前端一段关键词正则 —— 股票名里带「白银/黄金/有色/铜铝
锌镍」才认得出, 而且只有当一个"家族"同时出现在 A股 和 基金两个大类里才报警。它看不见
的东西比看得见的多:
  · 「盛达资源」是银矿、「兴业银锡」是银锡矿 —— 名字里没有"白银"三个字, 一个都认不出
  · 金属之外整个不存在: 半导体 / 光模块 / AI算力 / 军工 / 白酒 都没有家族
  · 基金那侧只看基金名分出的粗类目(silver/gold/commodity/overseas/aindex), 不看它
    季报里到底拿了什么票

而真正的同源风险恰恰藏在底层持仓里。实测这个账本(2026-08):
  · 易方达信息行业精选(019024) 与 易方达信息产业混合(019018) 前十大几乎是同一份 ——
    新易盛 6.0/6.1、中际旭创 5.5/5.5、三环集团 4.7/4.9。买两只 = 下同一注
  · 中际旭创 同时出现在 3 只基金里; 台积电/超威半导体 横跨 3 只 QDII
  这些名字里既没有金属也没有主题词, 关键词那套一条都报不出来。

口径与诚实边界(这些必须跟着数字一起给出去, 不然穿透反而更误导):
  · 只有季报前十大。前十大合计占净值 24%~50%(实测), 所以穿透覆盖不到整只基金 —— 报
    coverage, 不假装是全量
  · 季报有滞后, 带上季度标签
  · 联接基金/债基/货币基金拉不到股票明细(实测 华夏黄金ETF联接C 返回 0 条) —— 单独
    列进 uncovered, 不是"敞口为 0"
"""
from __future__ import annotations

import asyncio
import re

# 十大权重合计的下限: 低于这个值说明这只基金几乎没被穿透到, 结论只能当线索
_THIN_COVER = 0.15
# 两只基金重叠度(十大内 Σmin(w_i,w_j))到这个程度, 基本可以认为是同一注
_PAIR_OVERLAP_WARN = 0.20
# 单一标的穿透后占总资产的报警线
_SINGLE_WARN_PCT = 5.0
_SINGLE_HIGH_PCT = 8.0


# 份额类别后缀: 同一只基金的 A/C 份额是同一个投资组合, 只是费率不同。不归并的话
# 「XX混合A」与「XX混合C」会被当成"两只基金重叠 45%"报出来 —— 那不是发现, 是废话,
# 还会把同一个底层标的的来源数算成两个。
_CLASS_SUFFIX = re.compile(r"(人民币|美元(现汇|现钞)?)?[ABCDEIH]类?$")


def _fund_family(name: str) -> str:
    return _CLASS_SUFFIX.sub("", (name or "").strip()).strip() or (name or "")


def _is_cn(market: str) -> bool:
    return str(market or "").upper().startswith("CN")


def _key_of(h: dict) -> str:
    """标的归并键。A股用代码(最稳), 港美股用中文名 —— 同一家公司在不同源的代码写法不一
    (台积电 在 QDII 报表里可能是 TSM 也可能是 2330), 名字反而一致。"""
    code = str(h.get("code") or "").strip()
    if _is_cn(h.get("market")) and code:
        return f"CN:{code}"
    return "X:" + str(h.get("name") or code).strip()


async def _fund_underlyings(code: str) -> list[dict]:
    from services.fund_holdings import get_fund_top10
    try:
        return await get_fund_top10(code) or []
    except Exception:
        return []


async def look_through(min_pct: float = 0.5) -> dict:
    """穿透一遍全账本。min_pct: 结果里只留穿透后占总资产 ≥ 这个百分比的标的。"""
    from database import get_all_holdings
    from services.market_data import get_realtime_quotes

    # ── 1) A股/港美股直持 ──
    stocks: list[dict] = []
    try:
        hs = [h for h in await get_all_holdings() if (h.get("shares") or 0) > 0]
    except Exception:
        hs = []
    if hs:
        quotes = await get_realtime_quotes([h["stock_code"] for h in hs])
        for h in hs:
            q = quotes.get(h["stock_code"]) or {}
            px = q.get("price") or h.get("current_price") or 0
            fx = h.get("fx_rate") or 1.0
            mv = float(px) * float(h.get("shares") or 0) * float(fx)
            if mv > 0:
                stocks.append({"code": h["stock_code"], "name": h.get("stock_name") or h["stock_code"],
                               "market": "CN" if (h.get("market") or "A") in ("A", "CN") else h.get("market"),
                               "mv": mv})

    # ── 2) 基金(场内ETF + 场外)直持 ──
    funds: list[dict] = []
    total_other = 0.0
    try:
        from api.assets_routes import list_assets
        data = await list_assets()
        for a in (data.get("assets") or []):
            v = float(a.get("current_value") or 0)
            total_other += v
            if a.get("asset_type") == "FUND" and v > 0 and (a.get("code") or ""):
                funds.append({"code": str(a["code"]).strip(), "name": a.get("name") or a["code"], "mv": v})
    except Exception:
        pass

    # 同一只基金的不同份额(A/C)合成一行: 组合相同, 分开算等于把同一注数两遍
    merged: dict[str, dict] = {}
    for f in funds:
        fam = _fund_family(f["name"])
        m = merged.setdefault(fam, {"code": f["code"], "name": fam, "mv": 0.0, "codes": []})
        m["mv"] += f["mv"]
        m["codes"].append(f["code"])
    funds = list(merged.values())

    total = sum(s["mv"] for s in stocks) + total_other
    if total <= 0:
        return {"error": "还没有可穿透的持仓"}

    # ── 3) 逐只基金拆到底层 ──
    async def _first_nonempty(codes: list[str]) -> list[dict]:
        """同族基金随便哪个代码都能代表这个组合; A 类拉不到就试 C 类。"""
        for c in codes:
            rows = await _fund_underlyings(c)
            if rows:
                return rows
        return []

    tops = await asyncio.gather(*[_first_nonempty(f["codes"]) for f in funds])
    uncovered, thin = [], []
    exposure: dict[str, dict] = {}          # key -> {name, code, market, direct, via[]}

    for s in stocks:
        k = _key_of(s)
        e = exposure.setdefault(k, {"name": s["name"], "code": s["code"], "market": s["market"],
                                    "direct": 0.0, "via": []})
        e["direct"] += s["mv"]

    for f, rows in zip(funds, tops):
        cover = sum(float(r.get("weight") or 0) for r in rows)
        if not rows:
            uncovered.append({"code": f["code"], "name": f["name"], "mv": round(f["mv"], 2),
                              "why": "拉不到季报股票明细(联接基金/债基/货币基金常见)"})
            continue
        if cover < _THIN_COVER:
            thin.append({"code": f["code"], "name": f["name"], "cover_pct": round(cover * 100, 1)})
        for r in rows:
            w = float(r.get("weight") or 0)
            if w <= 0:
                continue
            k = _key_of(r)
            e = exposure.setdefault(k, {"name": r.get("name") or r.get("code"), "code": r.get("code"),
                                        "market": r.get("market"), "direct": 0.0, "via": []})
            # 同一只基金里同一家公司可能占两行 —— 实测易方达全球成长同时拿了台积电的
            # 美股 ADR(TSM 8.88%) 和台股(2330 5.54%)。敞口该合并, 而"来源数"更不能因此
            # 把一只基金算成两只。所以按基金代码累加, 并记下它占了几行。
            hit = next((v for v in e["via"] if v["fund_code"] == f["code"]), None)
            if hit:
                hit["weight_pct"] = round(hit["weight_pct"] + w * 100, 2)
                hit["mv"] = round(hit["mv"] + f["mv"] * w, 2)
                hit["lines"] = hit.get("lines", 1) + 1
            else:
                e["via"].append({"fund": f["name"], "fund_code": f["code"], "lines": 1,
                                 "weight_pct": round(w * 100, 2), "mv": round(f["mv"] * w, 2)})

    # ── 4) 汇总每个标的的真实敞口 ──
    items = []
    for k, e in exposure.items():
        indirect = sum(v["mv"] for v in e["via"])
        tot = e["direct"] + indirect
        pct = tot / total * 100
        if pct < min_pct:
            continue
        items.append({
            "key": k, "name": e["name"], "code": e["code"], "market": e["market"],
            "direct_mv": round(e["direct"], 2), "indirect_mv": round(indirect, 2),
            "total_mv": round(tot, 2), "pct": round(pct, 2),
            "n_sources": (1 if e["direct"] > 0 else 0) + len(e["via"]),
            "multi_line": any(v.get("lines", 1) > 1 for v in e["via"]),
            "via": sorted(e["via"], key=lambda v: -v["mv"]),
        })
    items.sort(key=lambda x: -x["total_mv"])

    # ── 4.5) 行业层面的穿透敞口 ──
    # 这一层才是原来那套关键词最致命的漏洞: 「山东黄金」能靠名字认出是黄金, 「兴业银锡」
    # 认不出, 于是两只加起来近四成的贵金属敞口一条都不报。这里按东财行业归并, 名字里有
    # 没有金属字样都不影响。港美股底层拿不到 A 股行业表, 单独归一档并说明, 不硬凑。
    imap = {}
    try:
        from services.etf_xray import industry_map
        imap = await asyncio.to_thread(industry_map)
    except Exception:
        imap = {}
    inds: dict[str, dict] = {}
    for it in items:
        code = str(it.get("code") or "")
        if _is_cn(it.get("market")) or (code.isdigit() and len(code) == 6):
            ind = (imap.get(code) or ("", "", 0))[1] or "未知行业"
        else:
            ind = f"海外({it.get('market') or '?'}·未归行业)"
        g = inds.setdefault(ind, {"industry": ind, "mv": 0.0, "direct_mv": 0.0,
                                  "indirect_mv": 0.0, "members": []})
        g["mv"] += it["total_mv"]
        g["direct_mv"] += it["direct_mv"]
        g["indirect_mv"] += it["indirect_mv"]
        g["members"].append(it["name"])
    industries = []
    for g in inds.values():
        industries.append({**g, "mv": round(g["mv"], 2), "direct_mv": round(g["direct_mv"], 2),
                           "indirect_mv": round(g["indirect_mv"], 2),
                           "pct": round(g["mv"] / total * 100, 2),
                           "members": g["members"][:8], "n": len(g["members"])})
    industries.sort(key=lambda x: -x["mv"])

    # ── 5) 基金两两重叠度(十大以内): Σ min(w_i, w_j) ──
    pairs = []
    for i in range(len(funds)):
        for j in range(i + 1, len(funds)):
            a, b = tops[i], tops[j]
            if not a or not b:
                continue
            wa = {_key_of(r): float(r.get("weight") or 0) for r in a}
            wb = {_key_of(r): float(r.get("weight") or 0) for r in b}
            same = set(wa) & set(wb)
            if not same:
                continue
            ov = sum(min(wa[k], wb[k]) for k in same)
            if ov <= 0:
                continue
            names = [next(r.get("name") for r in a if _key_of(r) == k) for k in same]
            pairs.append({"a": funds[i]["name"], "a_code": funds[i]["code"],
                          "b": funds[j]["name"], "b_code": funds[j]["code"],
                          "overlap_pct": round(ov * 100, 1), "n_same": len(same),
                          "same": names[:6],
                          "mv": round(funds[i]["mv"] + funds[j]["mv"], 2)})
    pairs.sort(key=lambda p: -p["overlap_pct"])

    return {
        "total": round(total, 2),
        "n_stocks": len(stocks), "n_funds": len(funds),
        "items": items,
        "industries": industries,
        "fund_pairs": pairs,
        "coverage": {
            "penetrated_funds": len([1 for r in tops if r]),
            "uncovered": uncovered,
            "thin": thin,
            "note": ("穿透只用季报前十大, 前十大合计通常只占基金净值的 25%~50% —— "
                     "所以下面的敞口是**下限**, 不是全量; 季报还有滞后。"
                     "拉不到明细的基金列在 uncovered, 那不等于敞口为 0。"),
        },
        "warnings": _warnings(items, pairs, industries, total),
    }


_IND_WARN_PCT = 25.0
_IND_HIGH_PCT = 40.0


def _warnings(items: list[dict], pairs: list[dict], industries: list[dict], total: float) -> list[dict]:
    """给人看的结论。三类都是关键词那套发现不了的: 行业穿透合计 / 同一标的多来源 / 基金撞车。"""
    out = []
    for g in industries:
        if g["pct"] < _IND_WARN_PCT or g["industry"].startswith("海外") or g["industry"] == "未知行业":
            continue
        via = f", 其中间接 {g['indirect_mv'] / 10000:.2f}万" if g["indirect_mv"] > 0 else ""
        more = f" 等{g['n']}只" if g["n"] > 4 else ""
        out.append({"level": "high" if g["pct"] >= _IND_HIGH_PCT else "med", "kind": "industry",
                    "text": f"「{g['industry']}」穿透后合计 {g['mv'] / 10000:.2f}万"
                            f"(占总资产 {g['pct']:.1f}%){via} —— "
                            + "、".join(g["members"][:4]) + more})
    for it in items:
        if it["n_sources"] < 2:
            continue
        src = []
        if it["direct_mv"] > 0:
            src.append(f"直持 {it['direct_mv'] / 10000:.2f}万")
        if it["via"]:
            src.append("经 " + "、".join(v["fund"] for v in it["via"][:3])
                       + (f" 等{len(it['via'])}只基金" if len(it["via"]) > 3 else "")
                       + f" 间接 {it['indirect_mv'] / 10000:.2f}万")
        level = "high" if it["pct"] >= _SINGLE_HIGH_PCT else ("med" if it["pct"] >= _SINGLE_WARN_PCT else "low")
        out.append({"level": level, "kind": "single_lookthrough",
                    "text": f"「{it['name']}」真实敞口 {it['total_mv'] / 10000:.2f}万"
                            f"(占总资产 {it['pct']:.1f}%), 来自 {' + '.join(src)}"})
    for p in pairs:
        if p["overlap_pct"] < _PAIR_OVERLAP_WARN * 100:
            continue
        out.append({"level": "high" if p["overlap_pct"] >= 30 else "med", "kind": "fund_twins",
                    "text": f"「{p['a']}」与「{p['b']}」前十大重叠 {p['overlap_pct']:.0f}%"
                            f"(共 {p['n_same']} 只: {'、'.join(p['same'][:4])}), "
                            f"合计 {p['mv'] / 10000:.2f}万 —— 两只基金基本是同一注"})
    order = {"high": 0, "med": 1, "low": 2}
    out.sort(key=lambda w: order.get(w["level"], 3))
    return out
