"""去劣筛选: 用 7 条硬指标排除"不是一流公司", 而不是挑出"该买什么"。

出处
----
搬自 ai-berkshire 的 quality-screen(MIT)。7 条门限与 3 条豁免规则原样沿用 —— 它们是**他们的
约定, 不是我们测出来的结论**, 所以每次输出都把门限连同这句话一起给出去, 别让人以为 8% 的
ROE 线是本项目验证过的。

为什么值得搬: 排除法比选择法可靠。"这家不够一流"是可以用公开财报证伪的质地判断, 而"这家
值得买"不是。所以这个模块只出前者。

关键的自制部分: 第 7 条股本膨胀
--------------------------------
原始规则写"5年股本膨胀 > 20%(非并购)"。直接拿股本数相比会把**送股/转股**误判成稀释 ——
10送1.43 让股本涨 14.3%, 但每个股东手里的比例一点没变, 这不是稀释。所以这里把送转贡献
单独算出来扣掉, 只留真实稀释(增发/股权激励/可转债转股/发股并购)。中国巨石实测: 股本
5 年 +14.3%, 全部由 2020 年报的 10送1.43 解释, 真实稀释 0%。

刻意不做
--------
不出评分, 不出排名, 不给买卖。**通过 7 条 ≠ 值得买** —— 它只说"没被这 7 条排除掉"。
数据不足时标注"判不了", 不当成通过, 也不当成排除(这是原规则里"宁可漏网不可误杀"的意思)。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import math

# 门限来自 ai-berkshire, 原样沿用。(名称, 指标键, 比较方向, 门限, 衡量维度)
_CRITERIA = (
    ("10年平均ROE", "ROE10均%", "lt", 8.0, "资本效率"),
    ("5年累计自由现金流", "FCF5累计亿", "neg", None, "现金真实性"),
    ("利息覆盖倍数", "利息覆盖倍", "lt", 2.0, "偿债安全"),
    ("长期毛利率", "毛利率长期均%", "lt", 15.0, "定价权"),
    ("经营现金流/净利润", "OCF比NI长期均", "lt", 0.7, "利润质量"),
    ("长期净利率", "净利率长期均%", "lt", 5.0, "抗风险能力"),
    ("5年真实股本膨胀", "真实稀释%", "gt", 20.0, "股权稀释"),
)
_ROE_YEARS = 10            # 第 1 条的窗口
_LONG_YEARS = 10           # 「长期」= 近 10 个年报期(不足则用可得的, 并写明用了几年)
_FCF_YEARS = 5
_DILUTION_YEARS = 5
# 银行/保险: 利息支出是主营成本而非融资成本, 利息覆盖倍数对它们没有意义。
_NO_INTEREST_TEST = ("银行", "保险", "多元金融")


# ── 判定(纯函数, 不联网) ────────────────────────────────

def evaluate(m: dict) -> dict:
    """按 7 条逐项判定。m 是 metrics(见 measure/screen 的返回), 缺项传 None。

    每条只有四种结果: 通过 / 排除 / 判不了 / 豁免。**没有"大概通过"这一档** ——
    数据不足就是判不了, 混进通过里等于用缺失冒充合格。
    """
    ex = _exemptions(m)
    rows, applied = [], {}
    for name, key, op, thr, dim in _CRITERIA:
        v = m.get(key)
        row = {"指标": name, "维度": dim, "值": v, "门限": _thr_text(op, thr)}
        if name == "利息覆盖倍数" and _is_financial(m.get("行业")):
            row.update(结论="不适用", 说明=f"{m.get('行业')}: 利息支出是主营成本, 这条对它没有意义")
        elif v is None or (isinstance(v, float) and math.isnan(v)):
            row.update(结论="判不了", 说明=_why_missing(m, key))
        elif _hits(op, v, thr) and key == "真实稀释%" and _looks_like_merger(m):
            # 原规则的第 7 条写的是"股本膨胀>20%(**非并购**)"。发股并购不是稀释 —— 多出来的股换回了
            # 别人的资产和收入。我们分不清定增/股权激励与发股并购(要读公告), 但能测一个代理量:
            # 并购会把营收一起带进来, 定增只带来现金。营收涨得比股本还快 → 疑似并购 → 判不了。
            # 实测中国船舶: 5年股本 +68.3%(吸收合并中国重工), 同期营收 552→1520亿(+175%),
            # 不做这个区分就会把一次并购判成严重稀释。
            row.update(结论="判不了",
                       说明=f"真实稀释 {v}% 超过门限, 但同期营收增长 {m.get('营收增幅%')}% "
                            f"(快于股本), 疑似发股并购而非稀释 —— 原规则把并购排除在外。"
                            f"本项目分不清定增/股权激励/发股并购(要读公告), 所以不下结论, 请自行核一次。")
        elif _hits(op, v, thr):
            row.update(结论="排除", 说明=_hit_text(name, v, op, thr))
        else:
            row.update(结论="通过")
        # 豁免只在这条**本来会被排除或判不了**的时候才动它。给一条已经自己过了的指标盖上
        # "豁免"章, 是把"靠实力过的"说成"靠豁免过的" —— 反而更难看清质地。
        if row["结论"] in ("排除", "判不了") and key in ex:
            row.update(结论="豁免", 原判=row["结论"], 说明=ex[key])
            applied[key] = ex[key]
        rows.append(row)

    out_cnt = [r for r in rows if r["结论"] == "排除"]
    unknown = [r for r in rows if r["结论"] == "判不了"]
    if out_cnt:
        verdict = "排除"
        why = "被这些条排除: " + "、".join(r["指标"] for r in out_cnt)
    elif unknown:
        verdict = "判不了"
        why = "没有一条把它排除掉, 但这些条缺数据: " + "、".join(r["指标"] for r in unknown)
    else:
        verdict = "未被排除"
        why = "7 条都没排除它。"
    return {
        "结论": verdict, "说明": why, "逐条": rows,
        "豁免": [{"条": k, "依据": v} for k, v in applied.items()],
        "口径": ("门限沿用 ai-berkshire 的 7 条去劣指标(10年ROE<8%/5年FCF为负/利息覆盖<2倍/"
                 "毛利率<15%/OCF比NI<0.7/净利率<5%/5年真实稀释>20%), 不是本项目实测得出的阈值。"
                 f"「长期」取近 {_LONG_YEARS} 个年报期(不足则按可得年数算并在值旁标注)。"
                 "第7条已扣除送转贡献 —— 送股转股不改变持股比例, 不算稀释。"),
        "note": ("这是**排除法**: 结论只有'被排除/未被排除/判不了'。"
                 "**未被排除 ≠ 值得买** —— 它只表示这 7 条没抓住它, 与估值、时点、买卖完全无关。"
                 "判不了的条目要如实说明缺什么, 不能当成通过。"),
    }


def _exemptions(m: dict) -> dict:
    """3 条豁免规则 → {被豁免的指标键: 依据}。原规则的意思是宁可漏网不可误杀。"""
    ex = {}
    gross = m.get("毛利率长期均%")
    roe = m.get("ROE长期均%")
    ocfni = m.get("OCF比NI长期均")
    yrs = m.get("年报期数") or 0
    net2 = [x for x in (m.get("近2年净利率%") or []) if x is not None]
    # A(第1条 ROE): 上市不足10年 + 毛利率>30% + 近2年经营现金流为正
    if yrs < _ROE_YEARS and (gross or 0) > 30 and m.get("近2年经营现金流为正") is True:
        ex["ROE10均%"] = (f"豁免A: 只有 {yrs} 个年报期(不足10年)、长期毛利率 {gross}%>30%、"
                          "近2年经营现金流为正")
    # B(第6条 净利率): 毛利率>30% + 近2年净利率≥5% 或明确上升趋势
    if (gross or 0) > 30 and (
            (len(net2) == 2 and all(x >= 5 for x in net2)) or m.get("净利率上升趋势") is True):
        ex["净利率长期均%"] = (f"豁免B: 长期毛利率 {gross}%>30%, "
                            + ("近2年净利率均≥5%" if net2 and all(x >= 5 for x in net2) else "净利率呈上升趋势"))
    # C(第4、6条): ROE>20% + OCF/NI>1.0 + 会员/平台/薄利模式
    # 前两条是数字, 第三条是定性判断 —— 这里按数字条件给出豁免, 但把"商业模式未经核实"写进依据,
    # 让人能自己否掉。不标注就是假精度: 我们并没有验证它是不是薄利平台模式。
    if (roe or 0) > 20 and (ocfni or 0) > 1.0:
        note = (f"豁免C: 长期ROE {roe}%>20% 且 OCF/NI {ocfni}>1.0 —— "
                "原规则还要求「会员/平台/薄利模式」, 这一条是定性判断, 本项目未核实, 请自行确认")
        ex.setdefault("毛利率长期均%", note)
        ex.setdefault("净利率长期均%", note)
    return ex


def _looks_like_merger(m: dict) -> bool:
    """同一窗口里营收增幅不低于真实稀释幅度 → 疑似发股并购。

    这是**代理量不是证据**: 一家营收三倍增长的公司同时做了定增, 也会落进这里。方向是刻意的 ——
    原规则的取向是宁可漏网不可误杀, 所以宁可标"判不了"让人去看公告, 也不冤枉一次并购。
    """
    rev = m.get("营收增幅%")
    dil = m.get("真实稀释%")
    if rev is None or dil is None:
        return False
    return rev >= dil


def _is_financial(industry) -> bool:
    return any(k in (industry or "") for k in _NO_INTEREST_TEST)


def _hits(op: str, v: float, thr) -> bool:
    if op == "lt":
        return v < thr
    if op == "gt":
        return v > thr
    return v < 0                      # neg


def _thr_text(op: str, thr) -> str:
    return {"lt": f"< {thr} 即排除", "gt": f"> {thr} 即排除", "neg": "为负即排除"}[op]


def _hit_text(name: str, v, op: str, thr) -> str:
    if op == "neg":
        return f"{name} {v} 为负"
    sign = "<" if op == "lt" else ">"
    return f"{name} {v} {sign} {thr}"


def _why_missing(m: dict, key: str) -> str:
    if key == "ROE10均%":
        return f"只有 {m.get('年报期数') or 0} 个年报期, 算不出 10 年平均"
    if key == "FCF5累计亿":
        return "自由现金流或股本数缺失, 累计不出来"
    if key == "利息覆盖倍":
        return "利润表的利息费用没取到"
    if key == "真实稀释%":
        return "股本历史或送转记录没取到, 分不清稀释与送转"
    return "该项财务数据缺失"


# ── 取数 ────────────────────────────────────────────────

async def measure(code: str) -> tuple[dict, list[str]]:
    """取 7 条所需的多年财务量。返回 (metrics, 缺口说明)。

    三个源各管一段, 单个失败只让对应的条目变成"判不了", 不连累其他:
      abstract(akshare)      → ROE/毛利率/净利率/OCF比NI/每股自由现金流 的年报序列
      profit sheet(东财年报) → 利润总额 + 利息费用 → 利息覆盖倍数
      分红送配(东财)         → 逐年总股本 + 送转比例 → 真实稀释
    """
    bare = (code or "").split(".")[-1]
    ab, pf, fh, val = await asyncio.gather(
        asyncio.to_thread(_abstract_series, bare),
        asyncio.to_thread(_interest_cover, bare),
        asyncio.to_thread(_share_history, bare),
        asyncio.to_thread(_industry, bare),
        return_exceptions=True,
    )
    missing: list[str] = []
    m: dict = {}

    if isinstance(ab, dict) and ab.get("年报期"):
        m.update(_from_abstract(ab))
    else:
        missing.append(f"多年财务摘要({_err(ab)})")

    if isinstance(pf, dict) and pf.get("利息覆盖倍") is not None:
        m["利息覆盖倍"] = pf["利息覆盖倍"]
        m["利息费用万"] = pf.get("利息费用万")
    else:
        missing.append(f"利润表利息费用({_err(pf)})")

    if isinstance(fh, dict) and fh.get("真实稀释%") is not None:
        m.update({k: fh[k] for k in ("股本膨胀%", "送转贡献%", "真实稀释%", "股本窗口") if k in fh})
        if isinstance(ab, dict):                 # 有股本才能把每股FCF折成金额
            m["FCF5累计亿"] = _fcf_total(ab, fh)
            m["营收增幅%"] = _rev_growth(ab, fh.get("股本窗口"))
    else:
        missing.append(f"股本与送转历史({_err(fh)})")

    m["行业"] = val if isinstance(val, str) else ""
    if not m["行业"]:
        missing.append("行业分类(判不了银行/保险的利息覆盖是否适用)")
    return m, missing


def _err(x) -> str:
    return f"{type(x).__name__}: {x}" if isinstance(x, BaseException) else "接口无数据"


def _from_abstract(ab: dict) -> dict:
    ann = ab["年报期"]
    roe = ab.get("ROE", [])
    gross = ab.get("毛利率", [])
    net = ab.get("净利率", [])
    ocfni = ab.get("OCF比NI", [])
    ocf = ab.get("经营现金流", [])
    out = {
        "年报期数": len(ann),
        "最新年报期": ann[0] if ann else None,
        "ROE10均%": _avg(roe[:_ROE_YEARS], need=_ROE_YEARS),
        "ROE长期均%": _avg(roe[:_LONG_YEARS]),
        "毛利率长期均%": _avg(gross[:_LONG_YEARS]),
        "净利率长期均%": _avg(net[:_LONG_YEARS]),
        "OCF比NI长期均": _avg(ocfni[:_LONG_YEARS], nd=2),
        "长期用了几年": len([x for x in roe[:_LONG_YEARS] if x is not None]),
        "近2年净利率%": net[:2],
        "近2年经营现金流为正": (len([x for x in ocf[:2] if x is not None]) == 2
                            and all(x > 0 for x in ocf[:2] if x is not None)) or None,
    }
    n3 = [x for x in net[:3] if x is not None]
    out["净利率上升趋势"] = (len(n3) == 3 and n3[0] > n3[1] > n3[2]) or None
    return out


def _avg(xs, need: int = 0, nd: int = 2):
    """均值。need>0 时要求样本足够, 否则返回 None —— "10年平均"用 4 年算出来不叫 10 年平均。"""
    vs = [float(x) for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not vs or (need and len(vs) < need):
        return None
    return round(sum(vs) / len(vs), nd)


def _fcf_total(ab: dict, fh: dict):
    """5 年自由现金流累计(亿元) = Σ 每股企业自由现金流 × 当年总股本。

    不直接把每股数相加: 期间股本变过, 每股数的和不对应任何一笔真实的钱。
    """
    fps = ab.get("每股FCF", [])[:_FCF_YEARS]
    ann = ab.get("年报期", [])[:_FCF_YEARS]
    shares = fh.get("逐年股本") or {}
    tot, used = 0.0, 0
    for d, v in zip(ann, fps):
        s = shares.get(str(d)[:4])
        if v is None or not s:
            continue
        tot += float(v) * float(s)
        used += 1
    return round(tot / 1e8, 2) if used >= 3 else None      # 少于3年不算"5年累计"


def _rev_growth(ab: dict, window) -> float | None:
    """股本对比窗口的**同期**营收增幅。必须同窗口 —— 拿五年营收去对三年股本, 比出来的东西没有意义。"""
    if not window or "→" not in str(window):
        return None
    try:
        a, b = [w.split("(")[0] for w in str(window).split("→")]
        ys, ye = a[:4], b[:4]
    except Exception:
        return None
    ann = [str(x)[:4] for x in ab.get("年报期", [])]
    rev = ab.get("营收", [])
    idx = {y: i for i, y in enumerate(ann)}
    if ys not in idx or ye not in idx:
        return None
    v0, v1 = rev[idx[ys]], rev[idx[ye]]
    if not v0 or not v1 or v0 <= 0:
        return None
    return round((v1 / v0 - 1) * 100, 1)


def _annual_cols(df) -> list[str]:
    cols = [str(c) for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    return sorted([c for c in cols if c.endswith("1231")], reverse=True)


def _abstract_series(code: str) -> dict:
    """akshare 财务摘要 → 各指标的**年报序列**(最新在前)。

    必须只取 1231: 一季报的 ROE 是当季累计值, 混进来算"10年平均ROE"会把均值系统性拉低。
    """
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_financial_abstract(symbol=code)
    if df is None or df.empty:
        return {}
    ann = _annual_cols(df)
    if not ann:
        return {}

    def series(names: tuple) -> list:
        """按指标名取一行。同名指标在表里出现多次(关键指标区/盈利能力区各一份),
        取**取到数最多**的那一行, 免得挑中一行空的。"""
        best = None
        for _, r in df.iterrows():
            if str(r["指标"]).strip() not in names:
                continue
            vals = [_num(r.get(c)) for c in ann]
            if best is None or sum(v is not None for v in vals) > sum(v is not None for v in best):
                best = vals
        return best or [None] * len(ann)

    return {
        "年报期": ann,
        "ROE": series(("净资产收益率(ROE)", "净资产收益率")),
        "毛利率": series(("毛利率",)),
        "净利率": series(("销售净利率",)),
        "OCF比NI": series(("经营活动净现金/归属母公司的净利润",)),
        "经营现金流": series(("经营现金流量净额",)),
        "每股FCF": series(("每股企业自由现金流量",)),
        "营收": series(("营业总收入",)),
        "归母净利润": series(("归母净利润",)),
        "每股净资产": series(("每股净资产",)),
    }


def _num(v):
    if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _em_prefix(code: str) -> str:
    c = (code or "").lstrip("shSHszSZbjBJ")
    if c[:3] == "920" or c[:1] in ("4", "8"):
        return "BJ"
    return "SH" if c[:1] in ("6", "9") else "SZ"


def _interest_cover(code: str) -> dict:
    """利息覆盖倍数 = 息税前利润 / 利息费用 = (利润总额 + 利息费用) / 利息费用。

    用 FE_INTEREST_EXPENSE(财务费用里的利息费用)而不是 FINANCE_EXPENSE(财务费用总额) ——
    后者含汇兑损益与手续费, 会把覆盖倍数算歪。
    """
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_profit_sheet_by_yearly_em(symbol=f"{_em_prefix(code)}{code}")
    if df is None or df.empty:
        return {}
    r = df.iloc[0]
    ie = _num(r.get("FE_INTEREST_EXPENSE"))
    tp = _num(r.get("TOTAL_PROFIT"))
    if ie is None or tp is None:
        return {}
    if ie <= 0:                       # 没有利息支出 → 无偿债压力, 这条不构成排除
        return {"利息覆盖倍": 999.0, "利息费用万": 0.0}
    return {"利息覆盖倍": round((tp + ie) / ie, 1), "利息费用万": round(ie / 1e4, 1)}


def _share_history(code: str) -> dict:
    """逐年总股本 + 送转比例 → 5 年真实稀释。

    真实稀释 = 实际股本增长 − 送转贡献。送转用**除权除息日**落在窗口内来归属, 不用报告期:
    年报的送转在次年中期才实施, 按报告期归会错一年。
    """
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_fhps_detail_em(symbol=code)
    if df is None or df.empty:
        return {}
    rows = []
    for _, r in df.iterrows():
        rd = str(r.get("报告期") or "")[:10]
        sh = _num(r.get("总股本"))
        ratio = _num(r.get("送转股份-送转总比例")) or 0.0
        ex = str(r.get("除权除息日") or "")[:10]
        if rd:
            rows.append({"报告期": rd, "总股本": sh, "送转比例": ratio, "除权日": ex})
    ann = sorted([x for x in rows if x["报告期"].endswith("12-31") and x["总股本"]],
                 key=lambda x: x["报告期"], reverse=True)
    if len(ann) < 2:
        return {}
    end = ann[0]
    start = next((x for x in ann if int(end["报告期"][:4]) - int(x["报告期"][:4]) >= _DILUTION_YEARS),
                 ann[-1])
    grow = (end["总股本"] / start["总股本"] - 1) * 100
    # 窗口内实施的送转累乘。除权日缺失时退回"报告期次年"的惯例。
    factor = 1.0
    for x in rows:
        if not x["送转比例"]:
            continue
        eff = x["除权日"] or f"{int(x['报告期'][:4]) + 1}-06-30"
        if start["报告期"] < eff <= end["报告期"]:
            factor *= (1 + x["送转比例"] / 10)
    bonus = (factor - 1) * 100
    real = (end["总股本"] / start["总股本"] / factor - 1) * 100
    return {"股本膨胀%": round(grow, 1) + 0.0, "送转贡献%": round(bonus, 1) + 0.0,
            "真实稀释%": round(real, 1) + 0.0,      # +0.0 把 -0.0 收成 0.0
            "股本窗口": f"{start['报告期']}({start['总股本']/1e8:.2f}亿股)→{end['报告期']}({end['总股本']/1e8:.2f}亿股)",
            "逐年股本": {x["报告期"][:4]: x["总股本"] for x in ann}}


def _industry(code: str) -> str:
    from services.stock_agent import _fetch_valuation_sync
    return (_fetch_valuation_sync(code) or {}).get("行业") or ""


async def screen(code: str, name: str = "") -> dict:
    """取数 + 判定。取数缺口原样带出去, 不在缺口上编结论。"""
    m, missing = await measure(code)
    r = evaluate(m)
    if _is_financial(m.get("行业")):
        # 银行/保险的现金流量表与工商企业不是一个口径(存贷款吞吐算经营现金流), 毛利率也是拼出来的。
        # 这套筛选本来是给工商企业设计的, 用在金融股上有几条天生对不上, 得说在前面。
        # 单独一个字段而不是塞进 note: note 带 markdown 强调符只给模型, 界面要按纯文本摆出来。
        r["适配提醒"] = (str(m.get("行业")) + "属金融: 现金流量表口径与工商企业不同(存贷吞吐计入经营现金流)、"
                       "毛利率并非真实定价权指标 —— 这套 7 条对金融股适配有限, 判不了的条目不要当成瑕疵。")
        r["note"] = "【适配性提醒】" + r["适配提醒"] + "\n" + r["note"]
    r.update({"code": (code or "").split(".")[-1], "name": name,
              "行业": m.get("行业"), "最新年报期": m.get("最新年报期"),
              "年报期数": m.get("年报期数"), "长期用了几年": m.get("长期用了几年"),
              "股本口径": m.get("股本窗口"),
              "取数缺口": missing,
              "取于": _dt.date.today().isoformat()})
    if missing:
        r["说明"] += f" 另有 {len(missing)} 项取数失败, 相关条目已标判不了。"
    return r
