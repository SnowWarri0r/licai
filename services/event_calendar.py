"""持仓事件日历: 持仓标的未来已知事件的时间轴(纯客观, 零建议)。

覆盖标的: A股直接持仓 + 在持场内ETF的前十大成分股(权重≥3%, 季报口径)。
事件类型:
- 财报披露: 交易所预约披露时间表(取最新变更后的日期, 已实际披露的不列)
- 除权除息: 分红送配方案的股权登记日/除权除息日(未来的)
- 解禁: 限售股解禁日 + 占流通市值比例

数据 = 东财公开接口(akshare), 预约日期可能变更, 以交易所最新公告为准。
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, timedelta

_cache: dict = {}
_TTL = 3600
_SRC_TTL = 12 * 3600
_src_cache: dict = {}

_HORIZON_DAYS = 75


def _no_proxy():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)


def _disclosure_map_sync() -> dict:
    """全市场财报预约披露表 → {code: 最新预约日期}。已实际披露的剔除。"""
    c = _src_cache.get("yysj")
    if c and time.time() - c[1] < _SRC_TTL:
        return c[0]
    _no_proxy()
    import akshare as ak
    m: dict = {}
    try:
        d = date.today()
        ends = sorted(f"{y}{md}" for y in (d.year, d.year - 1)
                      for md in ("0331", "0630", "0930", "1231"))
        period = max(e for e in ends if e <= d.strftime("%Y%m%d"))
        label = {"0331": "一季报", "0630": "中报", "0930": "三季报", "1231": "年报"}[period[4:]]
        df = ak.stock_yysj_em(symbol="沪深A股", date=period)
        for _, r in (df.iterrows() if df is not None else []):
            code = str(r.get("股票代码") or "").zfill(6)
            if not code.strip("0"):
                continue
            actual = r.get("实际披露时间")
            if actual is not None and str(actual) not in ("NaT", "nan", "None", ""):
                continue                      # 已披露, 不再是未来事件
            latest = None
            for k in ("三次变更日期", "二次变更日期", "一次变更日期", "首次预约时间"):
                v = r.get(k)
                if v is not None and str(v) not in ("NaT", "nan", "None", ""):
                    latest = str(v)[:10]
                    break
            if latest:
                m[code] = {"date": latest, "label": label}
    except Exception:
        pass
    if m:
        _src_cache["yysj"] = (m, time.time())
    return m


def _stock_events_sync(code: str) -> list[dict]:
    """单只 A股: 未来的除权除息 + 解禁。缓存12h。"""
    ck = f"ev_{code}"
    c = _src_cache.get(ck)
    if c and time.time() - c[1] < _SRC_TTL:
        return c[0]
    _no_proxy()
    import akshare as ak
    today = date.today()
    horizon = today + timedelta(days=_HORIZON_DAYS)
    out: list[dict] = []
    try:
        df = ak.stock_fhps_detail_em(symbol=code)
        for _, r in (df.iterrows() if df is not None else []):
            for field, typ in (("股权登记日", "股权登记"), ("除权除息日", "除权除息")):
                v = r.get(field)
                dt = str(v)[:10] if v is not None else ""
                if len(dt) == 10 and today.isoformat() <= dt <= horizon.isoformat():
                    desc = str(r.get("现金分红-现金分红比例描述") or "").strip()
                    out.append({"date": dt, "type": typ,
                                "detail": desc or f"{r.get('报告期', '')} 分红方案"})
    except Exception:
        pass
    try:
        df = ak.stock_restricted_release_queue_em(symbol=code)
        for _, r in (df.iterrows() if df is not None else []):
            v = r.get("解禁时间")
            dt = str(v)[:10] if v is not None else ""
            if len(dt) == 10 and today.isoformat() <= dt <= horizon.isoformat():
                try:
                    pct = round(float(r.get("占流通市值比例") or 0) * 100, 2)
                except (TypeError, ValueError):
                    pct = None
                out.append({"date": dt, "type": "解禁",
                            "detail": f"{r.get('限售股类型') or '限售股'}"
                                      + (f", 占流通市值 {pct}%" if pct is not None else "")})
    except Exception:
        pass
    _src_cache[ck] = (out, time.time())
    return out


async def _watch_list() -> list[dict]:
    """监控标的: A股直持 + 场内ETF前十大成分(≥3%)。[{code, name, via}]"""
    from services.stock_agent import _active_holdings
    from services import etf_xray
    from database import list_external_assets
    from services.external_assets import _is_onchain_etf

    seen: dict[str, dict] = {}
    for h in await _active_holdings():
        code = str(h.get("stock_code") or "")
        if code:
            seen[code] = {"code": code, "name": h.get("stock_name") or code, "via": "直接持有"}

    etfs = [(str(x.get("code")), x.get("name") or "")
            for x in await list_external_assets()
            if (x.get("asset_type") or "").upper() == "FUND"
            and (x.get("shares") or 0) > 0 and _is_onchain_etf(str(x.get("code") or ""))]
    sem = asyncio.Semaphore(3)

    async def _xray(code, name):
        async with sem:
            try:
                return name, await asyncio.to_thread(etf_xray.analyze_etf, code, name)
            except Exception:
                return name, None

    for etf_name, xr in await asyncio.gather(*[_xray(c, n) for c, n in etfs]):
        for h in ((xr or {}).get("前十大") or []):
            if (h.get("权重%") or 0) < 3:
                continue
            code = str(h.get("code") or "")
            if code and code not in seen and code.isdigit() and len(code) == 6:
                seen[code] = {"code": code, "name": h.get("name") or code,
                              "via": f"经由 {etf_name}"}
    return list(seen.values())


async def upcoming_events() -> dict:
    """主入口: 未来 75 天的持仓相关事件时间轴。缓存1h。"""
    c = _cache.get("events")
    if c and time.time() - c[1] < _TTL:
        return c[0]
    watch = await _watch_list()
    if not watch:
        return {"events": [], "note": "当前没有 A股/场内ETF 持仓, 无关联事件。"}
    today = date.today()
    horizon = today + timedelta(days=_HORIZON_DAYS)
    disc = await asyncio.to_thread(_disclosure_map_sync)
    sem = asyncio.Semaphore(3)

    async def _per(w):
        async with sem:
            evs = await asyncio.to_thread(_stock_events_sync, w["code"])
        rows = [{**e, **w} for e in evs]
        di = disc.get(w["code"])
        if di and today.isoformat() <= di["date"] <= horizon.isoformat():
            rows.append({"date": di["date"], "type": "财报披露",
                         "detail": f"{di['label']}预约披露(日期可能变更)", **w})
        return rows

    all_rows = await asyncio.gather(*[_per(w) for w in watch], return_exceptions=True)
    events = [e for rows in all_rows if isinstance(rows, list) for e in rows]
    for e in events:
        e["days"] = (date.fromisoformat(e["date"]) - today).days
    events.sort(key=lambda e: (e["date"], e["code"]))
    out = {"as_of": time.strftime("%Y-%m-%d %H:%M"),
           "watch_count": len(watch), "events": events,
           "note": f"监控 {len(watch)} 只标的(A股直持 + 场内ETF前十大成分≥3%), 未来 {_HORIZON_DAYS} 天。"
                   "财报预约日期以交易所最新公告为准, 可能变更。纯客观信息, 不构成任何买卖建议。"}
    _cache["events"] = (out, time.time())
    return out
