"""买点回看(复盘): 每一笔主动买入放回当时的 K 线, 按「买入前能看到的状态」分组, 统计你之后的实际结果。

纯客观回顾已发生的交易, 不对未来操作下结论。
- 覆盖: A股个股(position_actions) + 场内 ETF/LOF(external_asset_actions)。场外基金是定投/申购, 没有盘中
  决策时点, 也没有 K 线, 不纳入。
- 一只票同一天的多笔买入 = 一次决策(按成交量加权均价)。
- 口径: 信号日 = 买入日的前一交易日(收盘可见的状态); 结果 = 买入价 → 买入日起第 5/20 个交易日收盘
  (买入日记为第 1 天, 与研究的「T+1 开盘入场, 持有 h 日到收盘」对齐)。
- 成交价是当时的真实价, K 线是前复权 —— 用当天不复权收盘(TDX /api/kline-all)换算到前复权标度
  (⚠️ TDX /api/kline-history 是前复权, 不能用); ETF 另外先按账本 SPLIT 行折算份额拆分(拆分在清仓后的没有 SPLIT 行, 靠前者)。
  换算后必须落在当天前复权高低区间(±3%)内才用, 否则试原价, 再不行改用当天前复权收盘当入场价(标 approx)。
- 分组一「价格状态」(个股 + ETF): 买入价较前收涨跌、120 日区间位置、前 20 日涨跌。
- 分组二「量价形态 / 暴跌风险」(仅个股): 由分析插件(services/analyzers.review_all)判定; 全市场历史统计
  来自插件的离线校准, 与你的结果并排给出。ETF 不适用这套个股统计。
"""
from __future__ import annotations

import asyncio
import statistics
import time
from datetime import date as _date

_cache: dict = {}
_TTL = 3600
MARKET = "sh000852"          # 中证1000: 个股超额的对照基准


async def _decisions() -> list[dict]:
    """[{code, name, kind: stock|etf, date, price, shares}] 按日期升序。"""
    from database import get_all_holdings, get_position_actions, list_external_actions
    from services.market_data import _is_etf_lof
    from services.position_ledger import ACQUIRE

    names = {h["stock_code"]: h.get("stock_name") or "" for h in await get_all_holdings()}
    raw = []
    for a in await get_position_actions(None, limit=100000):
        at = (a.get("action_type") or "").upper()
        px, sh = float(a.get("price") or 0), float(a.get("shares") or 0)
        if at in ACQUIRE and px > 0 and sh > 0:
            raw.append((a["stock_code"], "stock", (a.get("trade_date") or "")[:10], px, sh, sh))
    try:
        from api.assets_routes import list_assets
        for asset in (await list_assets()).get("assets") or []:
            code = str(asset.get("code") or "")
            if asset.get("asset_type") != "FUND" or not _is_etf_lof(code):
                continue
            names.setdefault(code, asset.get("name") or "")
            acts = [x for x in await list_external_actions(asset["id"]) if (x.get("status") or "confirmed") == "confirmed"]
            # 份额拆分(SPLIT 行, shares = 拆分倍数): 流水里是拆分前的真实成交价, 前复权 K 线是拆分后标度
            splits = [(str(x.get("trade_date") or "")[:10], float(x.get("shares") or 0))
                      for x in acts if (x.get("action_type") or "").upper() == "SPLIT" and float(x.get("shares") or 0) > 0]
            for x in acts:
                if (x.get("action_type") or "").upper() not in ("BUY", "ADD"):
                    continue
                px, sh = float(x.get("unit_price") or 0), float(x.get("shares") or 0)
                d = str(x.get("trade_date") or "")[:10]
                if px > 0 and sh > 0:
                    k = 1.0
                    for sd, f in splits:
                        if sd > d:
                            k *= f
                    raw.append((code, "etf", d, px / k, sh * k, sh))
    except Exception as e:  # noqa: BLE001
        print(f"[entry-review] ETF 流水读取失败: {e}")
    agg: dict = {}
    for code, kind, d, px, sh, sh0 in raw:          # px/sh: 拆分折算后; sh0: 流水原始份额(展示原始均价用)
        if len(d) != 10:
            continue
        g = agg.setdefault((code, d), {"code": code, "kind": kind, "date": d, "pv": 0.0, "shares": 0.0, "sh0": 0.0})
        g["pv"] += px * sh
        g["shares"] += sh
        g["sh0"] += sh0
    out = []
    for g in agg.values():
        out.append({"code": g["code"], "kind": g["kind"], "date": g["date"], "name": names.get(g["code"]) or g["code"],
                    "price": g["pv"] / g["shares"], "shares": g["shares"], "price_orig": g["pv"] / g["sh0"]})
    return sorted(out, key=lambda x: (x["date"], x["code"]))


async def _bars(code: str, n: int) -> list[dict]:
    from services.market_data import _cst_now, _is_a_share_trading_day, get_historical_data
    df = await get_historical_data(code, n)
    if df is None or df.empty:
        return []
    bars = [{"date": str(r["日期"])[:10], "open": float(r["开盘"]), "high": float(r["最高"]),
             "low": float(r["最低"]), "close": float(r["收盘"]), "volume": float(r["成交量"])}
            for _, r in df.iterrows()]
    cst = _cst_now()
    if (bars and bars[-1]["date"] == cst.strftime("%Y-%m-%d") and _is_a_share_trading_day(cst.date())
            and cst.hour * 60 + cst.minute < 15 * 60 + 5):
        bars = bars[:-1]                       # 盘中未收盘的今日 bar 不参与(量和收盘都还没定)
    return bars


async def _raw_close(code: str) -> dict:
    from services import tdx_client
    try:
        return await tdx_client.raw_daily_close(code)
    except Exception:  # noqa: BLE001
        return {}


def _entry_facts(bars: list[dict], i: int, price: float, raw_close: float | None) -> dict:
    """第 i 根是买入日。返回换算后的入场价与各项状态/结果。"""
    b = bars[i]
    ok = lambda p: b["low"] * 0.97 <= p <= b["high"] * 1.03  # noqa: E731
    approx = False
    if raw_close and ok(price * b["close"] / raw_close):
        entry = price * b["close"] / raw_close
    elif ok(price):
        entry = price
    else:
        entry, approx = b["close"], True
    prev = bars[i - 1]["close"] if i >= 1 else None
    f = {"entry_qfq": round(entry, 4), "approx": approx,
         "chg_at_buy": (entry / prev - 1) if prev else None, "pos120": None, "ret20_before": None}
    if i >= 120:
        w = bars[i - 120:i]
        lo, hi = min(x["low"] for x in w), max(x["high"] for x in w)
        f["pos120"] = (prev - lo) / (hi - lo) if hi > lo else None
    if i >= 21:
        f["ret20_before"] = prev / bars[i - 21]["close"] - 1
    for h in (5, 20):
        j = i + h - 1
        f[f"r{h}"] = (bars[j]["close"] / entry - 1) if j < len(bars) else None
        f[f"end{h}"] = bars[j]["date"] if j < len(bars) else None
    return f


def _group(label: str, rows: list[dict], desc: str = "", base: dict | None = None, key_ex: bool = False) -> dict:
    """一组买入的结果汇总。key_ex=True 时胜率/均值用相对中证1000的超额(与全市场统计同口径)。"""
    k20 = "ex20" if key_ex else "r20"
    k5 = "ex5" if key_ex else "r5"
    done = [r[k20] for r in rows if r.get(k20) is not None]
    d5 = [r[k5] for r in rows if r.get(k5) is not None]
    return {
        "label": label, "desc": desc, "n": len(rows), "n_done": len(done),
        "mean20_pct": round(statistics.mean(done) * 100, 2) if done else None,
        "median20_pct": round(statistics.median(done) * 100, 2) if done else None,
        "win20": round(sum(1 for x in done if x > 0) / len(done), 3) if done else None,
        "mean5_pct": round(statistics.mean(d5) * 100, 2) if d5 else None,
        "win5": round(sum(1 for x in d5 if x > 0) / len(d5), 3) if d5 else None,
        "base": base, "metric": "相对中证1000超额" if key_ex else "绝对涨跌",
    }


def _highlights(total: dict, price_groups: list, risk_groups: list, stock_rows: list) -> list[str]:
    """与「全部买入」差距最大的几组(已满 20 日 ≥10 次才算), 直接写成事实句。"""
    base = total.get("mean20_pct")
    if base is None:
        return []
    cand = []
    for pg in price_groups:
        for g in pg["groups"]:
            if g["n_done"] >= 10 and g["mean20_pct"] is not None:
                cand.append((g["mean20_pct"] - base, f"{pg['title']}「{g['label']}」", g))
    cand.sort(key=lambda x: x[0])
    out = []
    for diff, name, g in cand[:2] + ([cand[-1]] if len(cand) > 2 and cand[-1][0] > 0 else []):
        out.append(f"{name}: {g['n']} 次, 之后 20 日平均 {g['mean20_pct']:+.1f}%, 赚钱的占 {g['win20']:.0%}"
                   f"(全部买入平均 {base:+.1f}%, 差 {diff:+.1f} 个百分点)")
    hi = next((g for g in risk_groups if g["label"].startswith("第 9")), None)
    if hi and stock_rows:
        share = hi["n"] / max(1, sum(1 for r in stock_rows if r["risk_decile"] is not None))
        if hi["n"] >= 5:
            out.append(f"个股买入时处在暴跌风险最高两档的占 {share:.0%}({hi['n']} 次); 这些买入之后 20 日相对中证1000 "
                       f"平均 {hi['mean20_pct']:+.1f}%, 跑赢的占 {hi['win20']:.0%}" if hi["mean20_pct"] is not None else
                       f"个股买入时处在暴跌风险最高两档的占 {share:.0%}({hi['n']} 次)")
    return out


async def build(force: bool = False) -> dict:
    ck = "all"
    c = _cache.get(ck)
    if c and not force and time.time() - c[1] < _TTL:
        return c[0]
    from services import analyzers as _az
    from services.market_data import _kline_for_symbol

    decs = await _decisions()
    if not decs:
        return {"empty": True, "note": "没有 A股/场内ETF 的买入流水。"}
    first = _date.fromisoformat(decs[0]["date"])
    span = int((_date.today() - first).days * 0.7) + 40            # 交易日 ≈ 自然日×0.7
    n_bars = max(_az.bars_needed(280), 280) + span
    codes = sorted({d["code"] for d in decs})
    sem = asyncio.Semaphore(2)

    async def one(code):
        async with sem:
            return code, await _bars(code, n_bars), await _raw_close(code)

    got = dict((c, (b, r)) for c, b, r in await asyncio.gather(*[one(c) for c in codes]))
    try:
        idx = await asyncio.to_thread(_kline_for_symbol, MARKET, n_bars + 20)
    except Exception:  # noqa: BLE001
        idx = []
    idx_by = {str(x["date"])[:10]: x for x in idx}
    context = {}
    for sym in _az.context_symbols():
        try:
            context[sym] = idx if sym == MARKET else await asyncio.to_thread(_kline_for_symbol, sym, n_bars + 20)
        except Exception:  # noqa: BLE001
            context[sym] = []

    rows = []
    for d in decs:
        bars, raw = got.get(d["code"], ([], {}))
        pos = {b["date"]: k for k, b in enumerate(bars)}
        i = pos.get(d["date"])
        if i is None or i < 1:
            continue
        f = _entry_facts(bars, i, d["price"], raw.get(d["date"]))
        for h in (5, 20):
            e, s = idx_by.get(f[f"end{h}"] or ""), idx_by.get(d["date"])
            f[f"ex{h}"] = (f[f"r{h}"] - (e["close"] / s["open"] - 1)) if (f[f"r{h}"] is not None and e and s and s.get("open")) else None
        rows.append({**d, **f, "patterns": [], "risk_decile": None})

    # 个股: 插件判定买入前一日的形态与风险档
    catalog = {}
    if _az.get_analyzers():
        by_code: dict = {}
        for r in rows:
            if r["kind"] == "stock":
                by_code.setdefault(r["code"], []).append(r)
        for code, rs in by_code.items():
            res = await asyncio.to_thread(_az.review_all, code, got[code][0], [r["date"] for r in rs], rs[0]["name"], context)
            for r in rs:
                for name, pts in res.items():
                    p = pts.get(r["date"]) or {}
                    r["patterns"] += [f"{name}:{x}" for x in p.get("patterns") or []]
                    if p.get("risk_decile") is not None:
                        r["risk_decile"] = p["risk_decile"]
                    r["signal_date"] = p.get("signal_date")
        catalog = _az.review_catalogs()

    # ---- 分组一: 价格状态(个股 + ETF) ----
    chg = lambda lo, hi: [r for r in rows if r["chg_at_buy"] is not None and lo <= r["chg_at_buy"] < hi]  # noqa: E731
    pos = lambda lo, hi: [r for r in rows if r["pos120"] is not None and lo <= r["pos120"] < hi]  # noqa: E731
    run = lambda lo, hi: [r for r in rows if r["ret20_before"] is not None and lo <= r["ret20_before"] < hi]  # noqa: E731
    price_groups = [
        {"title": "买入价相对前一日收盘", "groups": [
            _group("涨 ≥5% 时买", chg(0.05, 9), "盘中已经大涨"),
            _group("涨 2%~5% 时买", chg(0.02, 0.05)),
            _group("±2% 以内买", chg(-0.02, 0.02)),
            _group("跌 ≥2% 时买", chg(-9, -0.02), "盘中下跌"),
        ]},
        {"title": "买入前一日在近 120 日区间的位置", "groups": [
            _group("高位(≥80%)", pos(0.8, 9)),
            _group("中间", pos(0.2, 0.8)),
            _group("低位(≤20%)", pos(-9, 0.2)),
        ]},
        {"title": "买入前 20 个交易日的涨跌", "groups": [
            _group("已涨 ≥20%", run(0.20, 99)),
            _group("-15% ~ +20%", run(-0.15, 0.20)),
            _group("已跌 ≥15%", run(-9, -0.15)),
        ]},
    ]

    # ---- 分组二: 量价形态 / 暴跌风险(仅个股) ----
    stock_rows = [r for r in rows if r["kind"] == "stock"]
    pattern_groups, risk_groups = [], []
    for name, cat in catalog.items():
        pats = cat.get("patterns") or {}
        for pid, st in pats.items():
            hit = [r for r in stock_rows if f"{name}:{pid}" in r["patterns"]]
            if hit:
                pattern_groups.append(_group(st["name"], hit, st.get("definition") or "", key_ex=True, base={
                    "excess20_pct": st.get("excess_20d_pct"), "win20": st.get("win_rate_20d"), "n": st.get("n"),
                    "grade": st.get("grade"), "tone": st.get("tone")}))
        none = [r for r in stock_rows if not any(p.startswith(name + ":") for p in r["patterns"]) and r.get("signal_date")]
        if none:
            pattern_groups.append(_group("无典型形态", none, "买入前一日未命中任何已校准形态", key_ex=True))
        rk = cat.get("risk")
        if rk:
            dec = {x["decile"]: x for x in rk["deciles"]}
            for lab, lo, hi in (("第 9~10 档(高)", 9, 10), ("第 4~8 档", 4, 8), ("第 1~3 档(低)", 1, 3)):
                hit = [r for r in stock_rows if r["risk_decile"] is not None and lo <= r["risk_decile"] <= hi]
                if not hit:
                    continue
                ds = [dec[k] for k in range(lo, hi + 1) if k in dec]
                risk_groups.append(_group(lab, hit, key_ex=True, base={
                    "crash_rate": round(statistics.mean(x["crash_rate"] for x in ds), 4),
                    "median_excess_pct": round(statistics.mean(x["median_excess_pct"] for x in ds), 2),
                    "base_crash": rk.get("base_crash")}))

    names = {f"{n}:{k}": v["name"] for n, cat in catalog.items() for k, v in (cat.get("patterns") or {}).items()}
    entries = []
    for r in sorted(rows, key=lambda x: x["date"], reverse=True):
        entries.append({
            "date": r["date"], "code": r["code"], "name": r["name"], "kind": r["kind"],
            "price": round(r["price_orig"], 4), "approx": r["approx"],
            "chg_at_buy_pct": None if r["chg_at_buy"] is None else round(r["chg_at_buy"] * 100, 2),
            "pos120": None if r["pos120"] is None else round(r["pos120"], 2),
            "ret20_before_pct": None if r["ret20_before"] is None else round(r["ret20_before"] * 100, 1),
            "patterns": [names.get(p, p) for p in r["patterns"]], "risk_decile": r["risk_decile"],
            "r5_pct": None if r["r5"] is None else round(r["r5"] * 100, 2),
            "r20_pct": None if r["r20"] is None else round(r["r20"] * 100, 2),
            "ex20_pct": None if r.get("ex20") is None else round(r["ex20"] * 100, 2),
        })
    total = _group("全部买入", rows)
    out = {
        "empty": False,
        "total": total,
        "highlights": _highlights(total, price_groups, risk_groups, stock_rows),
        "n_stock": len(stock_rows), "n_etf": len(rows) - len(stock_rows),
        "since": decs[0]["date"],
        "price_groups": price_groups,
        "pattern_groups": sorted(pattern_groups, key=lambda g: -g["n"]),
        "risk_groups": risk_groups,
        "has_analyzer": bool(catalog),
        "entries": entries,
        "note": "买入日记为第 1 天, 结果 = 买入价到第 5/20 个交易日收盘; 同一只同一天多笔买入合并为一次。"
                "形态/风险按买入前一交易日收盘判定, 全市场统计是同类形态出现后次日开盘买入持有 20 日相对全市场等权的超额,"
                "你的那一列用相对中证1000的超额对照。样本少的组偶然性很大。纯回顾已发生的交易, 不构成买卖建议。",
    }
    _cache[ck] = (out, time.time())
    return out
