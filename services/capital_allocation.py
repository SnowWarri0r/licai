"""资本配置台账: 这家公司从股东手里**拿走**了多少现金, 又**还回**了多少。

出处与大幅取舍
--------------
搬自 ai-berkshire 的 management-deep-dive, 但只搬第 4 步(资本配置)。原 skill 九步里另外八步
是: 从股东信/业绩会/访谈提取 CEO 五年来的预测再对照结果、Glassdoor 员工评分、App Store 评分、
承诺兑现率, 最后按「诚信 35% + 战略执行 25% + 资本配置 25% + 治理 15%」加权打 1-5 分, 再给
段永平三问的星级。

那八步我没搬, 原因不是嫌麻烦:
  · A 股没有股东信, 业绩说明会记录零散且多为套话 —— 「五年预测 vs 结果」在这里做出来是编故事;
  · Glassdoor / App Store 对 A 股制造业公司基本无信号;
  · 「诚信打 3 分」这种输出本项目不出 —— 打分把不可测的东西装进一个可比的数字, 是最典型的假精度;
  · 段永平三问 + 星级的终点是「买不买」, 撞我们的硬护栏。

留下资本配置, 是因为它是这九步里**唯一整段能用公开硬数据落地**的: 分红、融资、回购都是钱的
流向, 不需要任何定性判断。原 skill 自己也写了「资本配置是终极考验 —— 赚钱比把钱用好容易」。

关键的自制部分: 融资额不能用发行额算
--------------------------------------
第一版想用增发公告的 发行总数 × 发行价格 当融资额。这在 A 股是错的: 吸收合并/资产注入也走
「定向增发」, 发的是股换的是资产, 一分现金都不进来。中国船舶 2025-09 定增 30.53 亿股 ×
37.59 元 = 1148 亿, 按发行额算它是 A 股史上最能"抽血"的公司之一 —— 实际那是换股吸收合并
中国重工, 现金流量表里「吸收投资收到的现金」当年是空的。

所以融资额一律取**现金流量表的「吸收投资收到的现金」**, 再减掉「子公司吸收少数股东投资收到
的现金」(那是别人往子公司里投钱, 不是上市公司股东掏的)。换股并购自然就不计入了。

刻意不做
--------
不打分, 不排名, 不给买卖, 不评价管理层人品。台账只陈述钱的流向; 「拿得多还得少」是一个事实,
不是一个结论 —— 重资产扩张期的公司本来就该融资。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import math

_WINDOW = 10                # 台账窗口: 近 10 个年报期(取三个源都能覆盖到的年份)


async def ledger(code: str, name: str = "") -> dict:
    """拿/还台账 + 分红率 + 回购逐笔。取数缺口原样带出, 不在缺口上补结论。"""
    from services.quality_screen import _abstract_series
    bare = (code or "").split(".")[-1]
    ab, cf, fh, rp, val = await asyncio.gather(
        asyncio.to_thread(_abstract_series, bare),
        asyncio.to_thread(_equity_cash_in, bare),
        asyncio.to_thread(_dividends, bare),
        asyncio.to_thread(_buybacks, bare),
        asyncio.to_thread(_valuation, bare),
        return_exceptions=True,
    )
    missing: list[str] = []
    ab = ab if isinstance(ab, dict) else {}
    cf = cf if isinstance(cf, dict) else {}
    fh = fh if isinstance(fh, dict) else {}
    rp = rp if isinstance(rp, list) else []
    val = val if isinstance(val, dict) else {}
    if not ab.get("年报期"):
        missing.append("多年财务摘要")
    if not cf:
        missing.append("现金流量表(算不出股权融资实际到账)")
    if not fh:
        missing.append("分红送配记录")

    years = _window_years(ab, cf, fh)
    raise_by = {y: cf.get(y, 0.0) for y in years}
    div_by = {y: fh.get(y, 0.0) for y in years}
    profit_by = _profit_by_year(ab, years)

    raised = round(sum(raise_by.values()) / 1e8, 2)
    divided = round(sum(div_by.values()) / 1e8, 2)
    bought = round(sum(b["金额亿"] for b in rp if b.get("金额亿")), 2)
    profit = round(sum(v for v in profit_by.values() if v) / 1e8, 2)

    out = {
        "code": bare, "name": name, "行业": val.get("行业"),
        "窗口": f"{years[0]}-{years[-1]}({len(years)} 个年报期)" if years else None,
        "拿": {"股权融资到账亿": raised, "逐年": _rows(raise_by),
              "口径": "现金流量表「吸收投资收到的现金」减「子公司吸收少数股东投资收到的现金」。"
                      "换股吸收合并/资产注入虽走定向增发但不带来现金, 按这个口径自然不计入 ——"
                      "拿发行总数×发行价当融资额会把一次换股并购算成天量抽血。"},
        "还": {"现金分红亿": divided, "回购亿": bought, "逐年分红": _rows(div_by),
              "口径": "分红按报告期归属(每10股派现×当期总股本/10), 不是当年实际付现日 ——"
                      "年报分红在次年才付, 按付现年归会把分红和它对应的利润错开一年。"
                      "回购只计已实施金额, 不含仅有预案的部分。"},
        "净还给股东亿": round(divided + bought - raised, 2),
        "窗口内归母净利润亿": profit,
        "分红率%": round(divided / profit * 100, 1) if profit and profit > 0 else None,
        "回购逐笔": rp,
        "当前PB": val.get("PB"),
        "取数缺口": missing,
        "取于": _dt.date.today().isoformat(),
    }
    out["一句话"] = _summary(out)
    out["note"] = (
        "这是**台账不是评价**: 只陈述窗口内钱的流向, 不打分、不排名、不给买卖、不评价管理层人品。"
        "「拿得多还得少」本身不是缺点 —— 产能扩张期的公司本来就该融资, 关键看融来的钱变成了什么"
        "(这一步要你自己看在建工程与产能落地, 台账答不了)。"
        "分红率的分母是**窗口内累计**归母净利润, 与单年分红率不是一个数。"
        "回购均价是当时的名义价, **不要直接和现价比涨跌** —— 中间的分红送转会让这个比较失真; "
        "要看回购贵不贵, 用返回里的 回购PB 和 当前PB 比。"
    )
    return out


def _summary(o: dict) -> str:
    """一句客观陈述。刻意不加形容词 —— 「抽血」「厚道」这类词是评价, 台账不出评价。"""
    if not o.get("窗口"):
        return "取数不足, 台账建不起来。"
    net = o["净还给股东亿"]
    body = (f"{o['窗口']}: 从股东拿到现金 {o['拿']['股权融资到账亿']} 亿, "
            f"分红 {o['还']['现金分红亿']} 亿" +
            (f" + 回购 {o['还']['回购亿']} 亿" if o["还"]["回购亿"] else "") +
            f", 净{'还给' if net >= 0 else '取自'}股东 {abs(net)} 亿。")
    if o.get("分红率%") is not None:
        body += f" 窗口内累计分红占累计归母净利润 {o['分红率%']}%。"
    return body


def _rows(by_year: dict) -> list:
    return [{"年": y, "亿": round(v / 1e8, 2)} for y, v in sorted(by_year.items(), reverse=True) if v]


def _window_years(ab: dict, cf: dict, fh: dict) -> list:
    """三个源都覆盖到的年份, 升序。

    不取并集: 某一年只有分红没有融资数据的话, 台账两侧的窗口就不一样, 「净还给股东」会凭空
    多出或少掉一整年。宁可窗口短一点, 也要两侧同窗口。
    """
    ann = [str(x)[:4] for x in (ab.get("年报期") or [])]
    if not ann:
        return []
    have = set(cf.keys()) | set(fh.keys())
    if not have:
        return []
    # 以现金流量表能覆盖的年份为准(它通常最短), 与年报期取交集
    lo = min(cf.keys()) if cf else min(have)
    ys = sorted([y for y in ann if y >= lo])[:]
    return ys[-_WINDOW:] if len(ys) > _WINDOW else ys


def _profit_by_year(ab: dict, years: list) -> dict:
    ann = [str(x)[:4] for x in (ab.get("年报期") or [])]
    prof = ab.get("归母净利润") or []
    idx = {y: i for i, y in enumerate(ann)}
    return {y: (prof[idx[y]] if y in idx and idx[y] < len(prof) else None) for y in years}


def _num(v):
    if v is None or v == "" or (isinstance(v, float) and math.isnan(v)):
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _no_proxy():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)


def _em_symbol(code: str) -> str:
    from services.quality_screen import _em_prefix
    return f"{_em_prefix(code)}{code}"


def _equity_cash_in(code: str) -> dict:
    """{年份: 上市公司股东实际投入的现金}。

    吸收投资收到的现金 − 子公司吸收少数股东投资收到的现金。后者是别人往子公司里投钱, 算进来
    会把「向股东融资」放大 —— 中国船舶 2018/2019 的吸收投资 39/66.9 亿全部来自子公司少数股东,
    母公司股东一分没掏。
    """
    _no_proxy()
    import akshare as ak
    df = ak.stock_cash_flow_sheet_by_yearly_em(symbol=_em_symbol(code))
    if df is None or df.empty:
        return {}
    out = {}
    for _, r in df.iterrows():
        y = str(r.get("REPORT_DATE") or "")[:4]
        if not y.isdigit():
            continue
        total = _num(r.get("ACCEPT_INVEST_CASH")) or 0.0
        minor = _num(r.get("SUBSIDIARY_ACCEPT_INVEST")) or 0.0
        out[y] = max(total - minor, 0.0)
    return out


def _dividends(code: str) -> dict:
    """{报告期年份: 该期现金分红总额}。

    按**报告期**归属而不是付现年份: 2024 年报的分红在 2025 年中实施, 按付现年归会让 2024 的
    分红去对 2025 的利润, 分红率整体错开一年。
    """
    _no_proxy()
    import akshare as ak
    df = ak.stock_fhps_detail_em(symbol=code)
    if df is None or df.empty:
        return {}
    out = {}
    for _, r in df.iterrows():
        rd = str(r.get("报告期") or "")[:10]
        y = rd[:4]
        if not y.isdigit():
            continue
        per10 = _num(r.get("现金分红-现金分红比例"))
        shares = _num(r.get("总股本"))
        if not per10 or not shares:
            continue
        out[y] = out.get(y, 0.0) + shares / 10 * per10     # 中期+年报同年累加
    return out


def _buybacks(code: str) -> list:
    """已实施回购逐笔 + 回购PB(均价 / 回购起始年份的每股净资产)。

    为什么给 PB 而不是「回购至今涨跌」: 回购均价是当时的名义价, 现价也是名义价, 中间几年的
    分红送转会让两者不可比 —— 而回购均价与同期每股净资产是同一时点的两个名义量, 相除有意义。
    """
    _no_proxy()
    import akshare as ak
    from services.quality_screen import _abstract_series
    df = ak.stock_repurchase_em()
    if df is None or df.empty:
        return []
    rows = df[df["股票代码"] == code]
    if rows.empty:
        return []
    ab = _abstract_series(code)
    ann = [str(x)[:4] for x in (ab.get("年报期") or [])]
    bvps = ab.get("每股净资产") or []
    bv_by = {y: bvps[i] for i, y in enumerate(ann) if i < len(bvps)}
    out = []
    for _, r in rows.iterrows():
        amt = _num(r.get("已回购金额"))
        qty = _num(r.get("已回购股份数量"))
        start = str(r.get("回购起始时间") or "")[:10]
        if not amt or not qty:
            continue                            # 只有预案没实施的不入账
        avg = amt / qty
        # 今年的回购还没有本年年报, 退回最近一期每股净资产并标明用的是哪一年 ——
        # 不标的话 PB 会被当成回购当年的口径读, 而它可能差了一年的净资产增厚。
        y = start[:4]
        bv, bv_year = bv_by.get(y), y
        if bv is None and ann:
            bv, bv_year = bv_by.get(ann[0]), ann[0]
        out.append({"起始": start, "进度": r.get("实施进度"),
                    "金额亿": round(amt / 1e8, 2), "股数万": round(qty / 1e4, 1),
                    "均价": round(avg, 2),
                    "回购PB": round(avg / bv, 2) if bv else None,
                    "PB基准年": bv_year if bv else None,
                    "价区间": f"{r.get('已回购股份价格区间-下限')}~{r.get('已回购股份价格区间-上限')}"})
    return sorted(out, key=lambda x: x["起始"], reverse=True)


def _valuation(code: str) -> dict:
    from services.stock_agent import _fetch_valuation_sync
    return _fetch_valuation_sync(code) or {}
