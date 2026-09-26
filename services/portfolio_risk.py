"""组合风险: 按现有持仓, 下一个交易日 / 未来 5 个交易日在 95% 的情况下最多亏多少, 主要来自哪几只。

口径:
  - 覆盖: A 股个股 + 场内 ETF(日K收盘) + 场外基金(天天基金日增长率, 已含分红); 现金/理财当作不波动;
    加密/网格机器人没有与 A 股日历对齐的日收益序列, 不纳入, 单列金额。
  - 每只持仓近 250 个 A 股交易日的日涨跌 → EWMA 协方差(RiskMetrics, λ=0.94, 近期权重大, 能跟上波动放大/收缩);
    组合 1 日 σ = √(wᵀΣw)(w = 当前市值), 95% 最坏 = 1.645σ; 5 日按 √5 放大(假设日间独立, 趋势市里会低估)。
  - 贡献: 各持仓对组合方差的占比 w_i(Σw)_i / wᵀΣw(同涨同跌的持仓会一起放大)。
  - 回测: 固定「今天的持仓」, 过去 ~190 个交易日里每天只用此前的数据估当天的 95% 最坏, 数实际亏损超过它的比例(应在 5% 左右)。
只描述波动幅度, 不预测方向; 亏损是按市值估的金额, 不含申赎费与滑点。
"""
from __future__ import annotations

import asyncio
import math
import time
from datetime import date

import numpy as np

LAMBDA = 0.94
Z95 = 1.645
_cache: dict = {}
_TTL = 600
_nav_cache: dict = {}          # 场外基金净值日增长率, 按天缓存


async def _series_stock(code: str) -> dict:
    from services.market_data import get_historical_data
    df = await get_historical_data(code, 320)
    if df is None or df.empty:
        return {}
    closes = [(str(r["日期"])[:10], float(r["收盘"])) for _, r in df.iterrows()]
    return {d1: (c1 / c0 - 1) * 100 for (_, c0), (d1, c1) in zip(closes[:-1], closes[1:]) if c0 > 0}


async def _series_otc(code: str) -> dict:
    hit = _nav_cache.get(code)
    if hit and hit[1] == date.today().isoformat():
        return hit[0]
    from services.fund_nav_model import _nav_history_sync
    s = await asyncio.to_thread(_nav_history_sync, code, 300)
    _nav_cache[code] = (s, date.today().isoformat())
    return s


def _ewma_cov(R: np.ndarray, lam: float = LAMBDA) -> np.ndarray:
    """R: T×N 日涨跌(%), 按时间升序。返回用全部 T 天估的 EWMA 协方差(均值按 0, 日频惯例)。"""
    T = len(R)
    w = lam ** np.arange(T - 1, -1, -1)
    w = w / w.sum()
    return (R * w[:, None]).T @ R


async def build(force: bool = False) -> dict:
    c = _cache.get("r")
    if c and not force and time.time() - c[1] < _TTL:
        return c[0]
    from database import get_all_holdings
    from services.market_data import get_realtime_quotes, _is_etf_lof
    from api.assets_routes import list_assets

    pos, excluded = [], []
    hs = [h for h in await get_all_holdings() if float(h.get("shares") or 0) > 0]
    quotes = await get_realtime_quotes([h["stock_code"] for h in hs]) if hs else {}
    for h in hs:
        px = (quotes.get(h["stock_code"]) or {}).get("price") or h.get("current_price") or 0
        mv = float(px) * float(h["shares"])
        if mv > 0:
            pos.append({"code": h["stock_code"], "name": h.get("stock_name") or h["stock_code"], "kind": "stock", "mv": mv})
    safe = 0.0
    for a in (await list_assets()).get("assets") or []:
        v = float(a.get("current_value") or 0)
        if v <= 0:
            continue
        t = a.get("asset_type")
        if t in ("CASH", "WEALTH"):
            safe += v
        elif t == "FUND":
            code = str(a.get("code") or "")
            pos.append({"code": code, "name": a.get("name") or code, "kind": "etf" if _is_etf_lof(code) else "otc", "mv": v})
        else:
            excluded.append({"name": a.get("name"), "type": t, "mv": round(v, 2)})
    if not pos:
        return {"available": False, "note": "没有会波动的持仓"}

    series = await asyncio.gather(*[(_series_stock(p["code"]) if p["kind"] in ("stock", "etf") else _series_otc(p["code"]))
                                    for p in pos])
    keep = [(p, s) for p, s in zip(pos, series) if len(s) >= 60]
    for p, s in zip(pos, series):
        if len(s) < 60:
            excluded.append({"name": p["name"], "type": "历史不足", "mv": round(p["mv"], 2)})
    if not keep:
        return {"available": False, "note": "持仓的历史数据不足"}
    days = sorted({d for _, s in keep for d in s})[-250:]
    R = np.array([[s.get(d, 0.0) for _, s in keep] for d in days])          # 某只那天没数据(停牌/未上市) → 0
    w = np.array([p["mv"] for p, _ in keep])
    Sig = _ewma_cov(R)
    var_p = float(w @ Sig @ w) / 1e4                                          # (元)²; R 是 %
    sd1 = math.sqrt(max(var_p, 0))
    contrib = (w * (Sig @ w) / 1e4) / var_p if var_p > 0 else np.zeros_like(w)
    risky = float(w.sum())

    # 回测: 固定当前持仓, 每天用此前数据估当天
    breaches, n_bt = 0, 0
    for t in range(60, len(days)):
        S_t = _ewma_cov(R[:t])
        sd_t = math.sqrt(max(float(w @ S_t @ w) / 1e4, 0))
        pnl = float(w @ R[t]) / 100
        n_bt += 1
        if pnl < -Z95 * sd_t:
            breaches += 1
    hist_pnl = (R @ w) / 100
    order = np.argsort(-contrib)
    out = {
        "available": True,
        "risky_value": round(risky, 2), "safe_value": round(safe, 2),
        "sd1": round(sd1, 2), "sd1_pct": round(sd1 / risky * 100, 2),
        "var1": round(Z95 * sd1, 2), "var5": round(Z95 * sd1 * math.sqrt(5), 2),
        "worst_day_1y": round(float(hist_pnl.min()), 2), "worst_day_date": days[int(hist_pnl.argmin())],
        "contrib": [{"name": keep[i][0]["name"], "code": keep[i][0]["code"], "mv": round(keep[i][0]["mv"], 2),
                     "share": round(float(contrib[i]), 3)} for i in order[:6]],
        "backtest": {"days": n_bt, "breaches": breaches, "rate": round(breaches / n_bt, 3) if n_bt else None},
        "excluded": excluded,
        "as_of": days[-1],
        "note": "按现有持仓与各自近 250 个交易日的日涨跌(EWMA 协方差, 近期权重大)估算; 95% 最坏 = 1.645σ, 5 日按 √5 放大。"
                "回测: 固定今天的持仓, 过去每天只用此前数据估当天, 实际亏损超过估值的比例应在 5% 左右。"
                "只描述波动幅度, 不预测方向。现金/理财按不波动计, 加密/网格机器人未纳入。",
    }
    _cache["r"] = (out, time.time())
    return out
