"""区间盈亏归因: 逐资产算 [起始快照日, 现在] 的真实盈亏, 净掉期间买卖现金流。

盈亏 = 现市值 - 起始日收盘市值 - 期间净流入(买入-卖出)
起始日市值取 portfolio_snapshots(收盘后落的真实市值), 现市值走看板同一套 enrich。
A股现金流用 price*shares±fee 自算(position_actions 没有 amount 列)。
用法: python scripts/interval_pnl.py 2026-07-29
"""
from __future__ import annotations

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BUY_ACTS = {"BUY", "ADD", "DEPOSIT"}
SELL_ACTS = {"SELL", "REDUCE", "REDEEM", "WITHDRAW"}


async def main(start: str) -> None:
    import aiosqlite
    from config import config
    from database import list_portfolio_snapshots
    DB_PATH = config.db_path

    snaps = {r["snap_date"]: json.loads(r["by_asset"] or "{}")
             for r in await list_portfolio_snapshots(limit=730)}
    if start not in snaps:
        print(f"没有 {start} 的快照, 已有: {sorted(snaps)[-8:]}")
        return
    base = snaps[start]

    # 现市值
    now: dict[str, float] = {}
    names: dict[str, str] = {}
    from api.portfolio_routes import list_holdings
    for h in await list_holdings():
        h = h if isinstance(h, dict) else h.model_dump()
        if (h.get("shares") or 0) > 0 and h.get("stock_code"):
            k = f"A:{h['stock_code']}"
            now[k] = float(h.get("market_value") or 0)
            names[k] = h.get("stock_name") or h["stock_code"]
    from api.assets_routes import list_assets
    manual: set = set()   # 手填市值的桶(现金/理财): 差额多半是转账, 不是盈亏
    for a in ((await list_assets()).get("assets") or []):
        if a.get("id") is None:
            continue
        k = f"EXT:{a['id']}"
        now[k] = float(a.get("current_value") or 0)
        names[k] = a.get("name") or k
        if (a.get("asset_type") or "") in ("CASH", "WEALTH"):
            manual.add(k)

    # 期间现金流(start 之后到今天, 含今天)
    flow: dict[str, float] = {}
    pend: dict[str, float] = {}
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT stock_code,action_type,price,shares,COALESCE(fee,0) fee,stock_code "
            "FROM position_actions WHERE trade_date > ?", (start,)) as cur:
            for r in await cur.fetchall():
                k = f"A:{r['stock_code']}"
                gross = float(r["price"]) * float(r["shares"])
                act = (r["action_type"] or "").upper()
                if act in BUY_ACTS:
                    flow[k] = flow.get(k, 0) + gross + float(r["fee"])
                elif act in SELL_ACTS:
                    flow[k] = flow.get(k, 0) - (gross - float(r["fee"]))
        async with db.execute(
            "SELECT asset_id,action_type,COALESCE(amount,0) amount,status,shares "
            "FROM external_asset_actions WHERE trade_date > ?", (start,)) as cur:
            for r in await cur.fetchall():
                k = f"EXT:{r['asset_id']}"
                act = (r["action_type"] or "").upper()
                amt = float(r["amount"])
                if (r["status"] or "confirmed") != "confirmed":
                    pend[k] = pend.get(k, 0) + amt
                    continue
                if act in BUY_ACTS:
                    flow[k] = flow.get(k, 0) + amt
                elif act in SELL_ACTS:
                    flow[k] = flow.get(k, 0) - amt

    rows, noise = [], []
    for k in set(base) | set(now) | set(flow):
        b, n, f = base.get(k, 0.0), now.get(k, 0.0), flow.get(k, 0.0)
        pnl = n - b - f
        if abs(pnl) < 0.005 and abs(b) < 0.005 and abs(n) < 0.005:
            continue
        rec = (pnl, k, names.get(k, k), b, n, f, pend.get(k, 0.0))
        # 现金/理财桶没有转账流水(市值手填), 差额只能当噪声单列, 混进合计会假亏假赚
        (noise if (k in manual and abs(pnl) > 1) else rows).append(rec)
    rows.sort()
    noise.sort()

    def _dump(rs):
        for pnl, k, nm, b, n, f, p in rs:
            mark = f"  [未确认定投 {p:.0f}]" if p else ""
            print(f"{pnl:>11,.2f}  {b:>10,.0f} {n:>10,.0f} {f:>10,.0f}  {nm}{mark}")

    print(f"\n区间 {start} 收盘 → 现在   逐资产盈亏")
    print(f"{'盈亏':>11}  {'起始市值':>10} {'现市值':>10} {'净流入':>10}  资产")
    _dump(rows)
    tot = sum(r[0] for r in rows)
    print(f"{'-' * 60}\n{tot:>11,.2f}  投资盈亏合计 ({len(rows)} 项)")
    if noise:
        print("\n以下是现金/理财桶的市值变动(手填, 无转账流水 → 多为存取转账, 不算盈亏):")
        _dump(noise)
        print(f"{sum(r[0] for r in noise):>11,.2f}  转账噪声合计")
    tp = sum(r[6] for r in rows) + sum(r[6] for r in noise)
    if tp:
        print(f"\n注: 期间有 {tp:,.0f} 元定投流水仍 pending(份额未确认), 未计入现金流;")
        print("    这部分钱若已实际扣款, 上面的合计会偏乐观。")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "2026-07-29"))
