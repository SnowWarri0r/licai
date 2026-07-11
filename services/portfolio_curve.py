"""组合净值曲线: 从流水 + 行情历史逐日重建组合市值, 算 TWR(时间加权收益)并对比基准。

口径:
- A股/场内ETF: 前复权收盘(主K线链路 EM→腾讯→缓存, 与全应用一致) × 折算到现行标度的份额
  (份额×其后所有拆分因子之积)——任一价源都是前复权, 标度天然一致, 不依赖原始价源的可用性
- 场外基金: 官方单位净值 × 当日份额
- 现金/理财/加密/机器人: 成本基线(累计净投入 + 累计利息分红), 无市场波动, 属近似
- TWR: r_t = (V_t − V_{t−1} − F_t) / (V_{t−1} + F_t); F=当日净外部投入
  (买入/申购/转入为正, 卖出/赎回/转出为负; 利息/分红是收益不是流入)
- 基准: 沪深300, 与 TWR 同起点归一到 100

TWR 消除了出入金对收益率的干扰——中途加仓不会被当成"赚了", 减仓不会被当成"亏了"。
纯客观展示, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import time
from bisect import bisect_left
from collections import defaultdict
from datetime import date

_cache: dict = {}
_price_cache: dict = {}
_TTL = 3600
_PRICE_TTL = 6 * 3600

_IN = {"BUY", "ADD", "DEPOSIT"}
_OUT = {"REDEEM", "WITHDRAW", "SELL", "REDUCE"}
_INCOME = {"INTEREST", "DIVIDEND"}


def _d(x) -> str:
    return str(x or "")[:10]


# ---------- 价格历史 ----------

def _no_proxy():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)


def _otc_nav_hist_sync(code: str) -> dict:
    """场外基金官方净值 date→nav。"""
    _no_proxy()
    import akshare as ak
    for attempt in range(3):
        try:
            df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
            if df is not None and not df.empty:
                return {_d(r["净值日期"]): float(r["单位净值"]) for _, r in df.iterrows()}
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return {}


async def _px_close_hist(code: str, days: int) -> dict:
    """A股/场内ETF 前复权收盘 date→close(主K线链路: EM qfq → 腾讯 → SQLite 缓存)。"""
    from services.market_data import get_historical_data
    try:
        df = await get_historical_data(code, days=days)
        if df is not None and len(df):
            return {_d(r["日期"]): float(r["收盘"]) for _, r in df.iterrows()}
    except Exception:
        pass
    return {}


async def _price_hist(kind: str, code: str, days: int) -> dict:
    ck = (kind, code)
    c = _price_cache.get(ck)
    if c and time.time() - c[1] < _PRICE_TTL:
        return c[0]
    if kind == "otc":
        h = await asyncio.to_thread(_otc_nav_hist_sync, code)
    else:
        h = await _px_close_hist(code, days)
    if h:
        _price_cache[ck] = (h, time.time())
    return h


def _ffill(hist: dict, dates: list[str]) -> list[float | None]:
    """按轴日期取最近已知价(向前填充); 首个已知价之前为 None。"""
    ks = sorted(hist.keys())
    out = []
    for dt in dates:
        i = bisect_left(ks, dt)
        if i < len(ks) and ks[i] == dt:
            out.append(hist[ks[i]])
        elif i > 0:
            out.append(hist[ks[i - 1]])
        else:
            out.append(None)
    return out


# ---------- 份额/成本时间线(纯函数, 可测) ----------

def share_balance_series(actions: list[dict], dates: list[str]) -> list[float]:
    """流水 → 每个轴日期收盘后的净份额(已确认)。SPLIT 行按因子折算既有余额。"""
    evs = sorted(
        (a for a in actions if (a.get("status") or "confirmed") == "confirmed"),
        key=lambda a: (_d(a.get("trade_date")), a.get("id") or 0))
    out, net, j = [], 0.0, 0
    for dt in dates:
        while j < len(evs) and _d(evs[j].get("trade_date")) <= dt:
            a = evs[j]
            at = (a.get("action_type") or "").upper()
            sh = abs(float(a.get("shares") or 0))
            if at == "SPLIT":
                if sh > 0:
                    net *= sh
            elif at in _IN:
                net += sh
            elif at in _OUT:
                net -= sh
            j += 1
        out.append(net)
    return out


def adjust_to_final_scale(actions: list[dict], dates: list[str],
                          bal: list[float]) -> list[float]:
    """把逐日原始份额折算到现行(最终)标度: 每个日期的份额 × 其后所有拆分因子之积。
    与前复权价同标度——前复权把拆分前价格÷F, 这里把拆分前份额×F, 市值不变。"""
    splits = [(_d(a.get("trade_date")), abs(float(a.get("shares") or 0)))
              for a in actions
              if (a.get("action_type") or "").upper() == "SPLIT"
              and (a.get("status") or "confirmed") == "confirmed"
              and float(a.get("shares") or 0) > 0]
    if not splits:
        return bal
    out = []
    for dt, b in zip(dates, bal):
        fac = 1.0
        for sd, f in splits:
            if sd > dt:
                fac *= f
        out.append(b * fac)
    return out


def cost_basis_series(actions: list[dict], dates: list[str]) -> list[float]:
    """无价史资产(现金/理财/加密/机器人): 累计净投入 + 累计利息分红 的时间线。"""
    evs = sorted(
        (a for a in actions if (a.get("status") or "confirmed") == "confirmed"),
        key=lambda a: (_d(a.get("trade_date")), a.get("id") or 0))
    out, val, j = [], 0.0, 0
    for dt in dates:
        while j < len(evs) and _d(evs[j].get("trade_date")) <= dt:
            a = evs[j]
            at = (a.get("action_type") or "").upper()
            amt = float(a.get("amount") or 0)
            if at in _IN or at in _INCOME:
                val += amt
            elif at in _OUT:
                val -= amt
            j += 1
        out.append(max(val, 0.0))
    return out


def day_flows(actions: list[dict], dates: list[str], stock: bool = False) -> dict:
    """当日净外部投入 F_t(按轴日期; 非交易日的流水滚到下一个轴日)。"""
    flows: dict[str, float] = defaultdict(float)
    for a in actions:
        if (a.get("status") or "confirmed") != "confirmed":
            continue
        at = (a.get("action_type") or "").upper()
        if stock:
            sh = abs(float(a.get("shares") or 0))
            px = float(a.get("price") or 0)
            fee = float(a.get("fee") or 0)
            amt = sh * px + (fee if at in ("BUY", "ADD") else -fee)
        else:
            amt = float(a.get("amount") or 0)
        if at in _IN:
            f = amt
        elif at in _OUT:
            f = -amt
        else:
            continue
        dt = _d(a.get("trade_date"))
        i = bisect_left(dates, dt)
        if i >= len(dates):
            continue
        flows[dates[i]] += f
    return flows


def twr_series(values: list[float], flows: list[float]) -> list[float]:
    """时间加权净值(起点=100)。分母≤0 或起点无持仓的日子收益记 0。"""
    nav, out = 100.0, []
    for i, v in enumerate(values):
        if i == 0:
            out.append(nav)
            continue
        base = values[i - 1] + flows[i]
        r = (v - values[i - 1] - flows[i]) / base if base > 1e-6 else 0.0
        nav *= (1 + r)
        out.append(round(nav, 4))
    return out


def max_drawdown(series: list[float]) -> float:
    peak, mdd = float("-inf"), 0.0
    for v in series:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return round(mdd * 100, 2)


# ---------- 主入口 ----------

async def build_curve(days: int = 120) -> dict:
    days = max(20, min(int(days or 120), 500))
    ck = f"curve_{days}"
    c = _cache.get(ck)
    if c and time.time() - c[1] < _TTL:
        return c[0]

    from services.market_data import _fetch_benchmark_history
    bench_df = await asyncio.to_thread(_fetch_benchmark_history, "sh000300", days + 10)
    if bench_df is None or bench_df.empty:
        return c[0] if c else {"error": "基准指数暂不可达"}
    axis = [_d(r["date"]) for _, r in bench_df.iterrows()][-days:]
    bench_close = {_d(r["date"]): float(r["close"]) for _, r in bench_df.iterrows()}

    from database import get_all_holdings, get_position_actions, list_external_assets, list_external_actions
    from services.external_assets import _is_onchain_etf

    # 资产清单: (kind, code, actions)
    tasks = []
    a_codes = sorted({h.get("stock_code") for h in await get_all_holdings() if h.get("stock_code")})
    for code in a_codes:
        acts = await get_position_actions(code, limit=1000)
        for a in acts:
            a.setdefault("status", "confirmed")
        tasks.append(("stock", code, acts))
    for x in await list_external_assets():
        acts = await list_external_actions(x["id"])
        code = str(x.get("code") or "")
        at = (x.get("asset_type") or "").upper()
        if at == "FUND" and code:
            kind = "px" if _is_onchain_etf(code) else "otc"
            tasks.append((kind, code, acts))
        else:
            tasks.append(("cost", f"{at}:{x['id']}", acts))

    sem = asyncio.Semaphore(4)

    async def _one(kind: str, code: str, acts: list[dict]):
        if kind == "cost":
            return cost_basis_series(acts, axis), day_flows(acts, axis), None
        async with sem:
            hist = await _price_hist(kind, code, days + 60)
        fl = day_flows(acts, axis, stock=(kind == "stock"))
        if not hist:
            # 价史拉空: 该资产退回成本基线(市值=累计净投入), 保证与流量口径一致——
            # 否则"钱进来了市值恒0"会制造±20%级的假日收益
            cum, run = [], 0.0
            for dt in axis:
                run += fl.get(dt, 0.0)
                cum.append(max(run, 0.0))
            return cum, fl, code
        bal = share_balance_series(acts, axis)
        if kind == "px":
            bal = adjust_to_final_scale(acts, axis, bal)   # 与前复权价同标度
        px = _ffill(hist, axis)
        vals = [(b * p) if (p is not None and b > 0) else 0.0 for b, p in zip(bal, px)]
        return vals, fl, None

    parts = await asyncio.gather(*[_one(k, cd, ac) for k, cd, ac in tasks], return_exceptions=True)
    values = [0.0] * len(axis)
    flows = [0.0] * len(axis)
    idx = {dt: i for i, dt in enumerate(axis)}
    skipped, degraded = 0, []
    for p in parts:
        if isinstance(p, Exception):
            skipped += 1
            continue
        vals, fl, degr = p
        if degr:
            degraded.append(degr)
        for i in range(len(axis)):
            values[i] += vals[i]
        for dt, f in fl.items():
            flows[idx[dt]] += f

    # 起点 = 第一个有持仓的轴日
    start = next((i for i, v in enumerate(values) if v > 1), None)
    if start is None:
        return {"error": "窗口内没有持仓记录"}
    axis, values = axis[start:], values[start:]
    flows = flows[start:]
    twr = twr_series(values, flows)
    bpx = _ffill(bench_close, axis)
    b0 = next((p for p in bpx if p), None)
    bench = [round(p / b0 * 100, 4) if (p and b0) else None for p in bpx]

    ret = round(twr[-1] - 100, 2)
    bret = round((bench[-1] or 100) - 100, 2)
    out = {
        "as_of": time.strftime("%Y-%m-%d %H:%M"),
        "dates": axis,
        "value": [round(v, 2) for v in values],
        "twr": twr,
        "bench": {"name": "沪深300", "series": bench},
        "metrics": {
            "区间收益%": ret,
            "最大回撤%": max_drawdown(twr),
            "基准收益%": bret,
            "超额%": round(ret - bret, 2),
            "起点": axis[0],
            "当前总市值": round(values[-1], 2),
        },
        "note": "TWR=时间加权收益(出入金已剥离, 与基金净值同口径), 与沪深300同起点归一100。"
                "现金/理财/加密/机器人按成本基线近似(无市场波动)。"
                + (f" {skipped}项资产异常已跳过。" if skipped else "")
                + (f" 价史暂缺按成本基线近似: {'、'.join(degraded[:5])}。" if degraded else "")
                + " 纯客观展示, 不构成任何买卖建议。",
    }
    _cache[ck] = (out, time.time())
    return out
