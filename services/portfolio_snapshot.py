"""每日组合市值快照: 收盘后把全组合逐资产真实市值落一条记录。

用途: 机器人/加密/现金/理财没有可回溯的价格历史, 净值曲线只能按成本基线近似;
快照攒起来之后, 这部分改用真实市值序列(快照日之间前向填充), 曲线越来越真。
key: A股="A:<code>", 外部资产="EXT:<id>"。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone


def _bj_now() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=8)


async def take_snapshot() -> dict | None:
    """算当前全组合逐资产市值并落库(同日重复调用覆盖)。"""
    by: dict[str, float] = {}
    # A股(实时行情市值; /api/portfolio 返回的就是持仓行列表)
    try:
        from api.portfolio_routes import list_holdings
        for h in await list_holdings():
            h = h if isinstance(h, dict) else h.model_dump()
            mv = h.get("market_value")
            if mv and (h.get("shares") or 0) > 0 and h.get("stock_code"):
                by[f"A:{h['stock_code']}"] = round(float(mv), 2)
    except Exception:
        pass
    # 外部资产(基金/理财/现金/加密/机器人, 用看板同一套 enrich)
    try:
        from api.assets_routes import list_assets
        d = await list_assets()
        for a in (d.get("assets") or []):
            cv = a.get("current_value")
            if cv is not None and a.get("id") is not None:
                by[f"EXT:{a['id']}"] = round(float(cv), 2)
    except Exception:
        pass
    if not by:
        return None
    total = round(sum(by.values()), 2)
    snap_date = _bj_now().strftime("%Y-%m-%d")
    from database import save_portfolio_snapshot
    await save_portfolio_snapshot(snap_date, total, json.dumps(by, ensure_ascii=False))
    return {"snap_date": snap_date, "total_value": total, "assets": len(by)}


_hk_refreshed: str = ""          # 已在港股收盘后重记过的日期(进程内; 重启后最多多覆盖一次, 同日覆盖写无害)
_HK_REFRESH_AT = 16 * 60 + 10    # 港股 16:00 收盘(港时=北京时间), 留 10 分钟等收盘竞价定格


async def _holds_hk() -> bool:
    try:
        from services.market_data import split_stock_code
        from api.portfolio_routes import list_holdings
        for h in await list_holdings():
            h = h if isinstance(h, dict) else h.model_dump()
            if (h.get("shares") or 0) > 0 and split_stock_code(h.get("stock_code") or "")[0] == "HK":
                return True
    except Exception:
        pass
    return False


def _needs_hk_refresh(now: datetime, holds_hk: bool, hk_trading_day: bool, done_date: str) -> bool:
    """15:05 那份快照里的港股是盘中价; 持有港股且今天港股开市时, 16:10 后同日再覆盖写一次。"""
    return (holds_hk and hk_trading_day
            and now.hour * 60 + now.minute >= _HK_REFRESH_AT
            and done_date != now.strftime("%Y-%m-%d"))


async def maybe_snapshot() -> None:
    """每天 15:05(北京)后记一次; 已记过当天的跳过。非交易日也记(加密/机器人在动)。

    持有港股时 16:10 后再覆盖写当天那条: 快照按日期 INSERT OR REPLACE, 覆盖只是把港股从盘中价
    换成收盘价, A 股收盘价不变 —— A 股那份 15:05 的行为原样保留, 不持港股就完全不多跑。
    美股不等: 它的收盘在北京次日凌晨, 当天快照里的美股本来就是前一晚那场的收盘价, 口径一致。"""
    global _hk_refreshed
    now = _bj_now()
    if now.hour * 60 + now.minute < 15 * 60 + 5:
        return
    from database import list_portfolio_snapshots
    rows = await list_portfolio_snapshots(limit=1)
    today = now.strftime("%Y-%m-%d")
    if rows and rows[0]["snap_date"] == today:
        if now.hour * 60 + now.minute < _HK_REFRESH_AT or _hk_refreshed == today:
            return
        from services.dca import _is_market_trading_day
        if not _needs_hk_refresh(now, await _holds_hk(), _is_market_trading_day(now.date(), "HK"),
                                 _hk_refreshed):
            _hk_refreshed = today        # 不持港股/港股休市: 今天不用再查
            return
        _hk_refreshed = today
        r = await take_snapshot()
        if r:
            print(f"[snapshot] {r['snap_date']} 港股收盘后重记 总市值 {r['total_value']} ({r['assets']} 项)")
        return
    r = await take_snapshot()
    if r:
        if now.hour * 60 + now.minute >= _HK_REFRESH_AT:
            _hk_refreshed = today        # 首次快照就已在港股收盘之后, 无需再覆盖
        print(f"[snapshot] {r['snap_date']} 总市值 {r['total_value']} ({r['assets']} 项)")


async def snapshot_map() -> dict:
    """{date: {key: value}} 供净值曲线覆盖成本基线。"""
    from database import list_portfolio_snapshots
    out: dict[str, dict] = {}
    for r in await list_portfolio_snapshots(limit=730):
        try:
            out[r["snap_date"]] = json.loads(r["by_asset"] or "{}")
        except Exception:
            continue
    return out
