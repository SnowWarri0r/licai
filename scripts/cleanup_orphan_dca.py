"""清理孤儿定投: 资产已删但计划还在跑 / 流水挂在不存在的 asset_id 上。

删资产以前不连带清计划(已在 database.delete_external_asset 修好), 历史上攒下的
垃圾要一次性扫掉。

默认清理范围只含"零信息量"的部分: 孤儿计划 + 孤儿 pending 流水(从未确认过, 份额
金额都是空的)。已确认的孤儿流水是真实成交记录, 只报告不动 —— 要一起清加 --all。
默认 dry-run, 加 --apply 才落库。
用法: ./venv/bin/python scripts/cleanup_orphan_dca.py [--apply] [--all]
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


async def main(apply: bool, purge_confirmed: bool) -> None:
    import aiosqlite
    from config import config

    async with aiosqlite.connect(config.db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id FROM external_assets") as cur:
            live = {r["id"] for r in await cur.fetchall()}

        async with db.execute(
            "SELECT id, asset_id, mode, value, status, next_due FROM dca_schedules") as cur:
            scheds = [dict(r) for r in await cur.fetchall()]
        orphan_scheds = [s for s in scheds if s["asset_id"] not in live]

        async with db.execute(
            "SELECT asset_id, COUNT(*) n, MIN(trade_date) d0, MAX(trade_date) d1, "
            "ROUND(SUM(amount),2) amt, SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) np "
            "FROM external_asset_actions GROUP BY asset_id") as cur:
            groups = [dict(r) for r in await cur.fetchall()]
        orphan_groups = [g for g in groups if g["asset_id"] not in live]

        if not orphan_scheds and not orphan_groups:
            print("没有孤儿数据。")
            return

        if orphan_scheds:
            print("孤儿定投计划(资产已不存在):")
            for s in orphan_scheds:
                print(f"  dca#{s['id']} → asset#{s['asset_id']}  {s['mode']} {s['value']}  "
                      f"status={s['status']}  next_due={s['next_due']}")
        junk = [g for g in orphan_groups if g["np"]]                 # 有 pending 的
        keep = [g for g in orphan_groups if g["n"] > g["np"]]        # 有已确认的
        if junk:
            print("\n孤儿 pending 流水(从未确认, 无份额无净值 → 垃圾):")
            for g in junk:
                print(f"  asset#{g['asset_id']}  {g['np']} 条 pending  "
                      f"{g['d0']}…{g['d1']}  名义 {g['amt']:,.2f} 元")
        if keep:
            print(f"\n孤儿已确认流水({'一起删' if purge_confirmed else '真实成交记录, 保留不动'}):")
            for g in keep:
                print(f"  asset#{g['asset_id']}  {g['n'] - g['np']} 条已确认  "
                      f"{g['d0']}…{g['d1']}  合计 {g['amt']:,.2f} 元")

        n_pend = sum(g["np"] for g in junk)
        n_conf = sum(g["n"] - g["np"] for g in keep) if purge_confirmed else 0
        if not apply:
            print(f"\n[dry-run] 将删除 {len(orphan_scheds)} 个孤儿计划 + {n_pend} 条 pending 流水"
                  + (f" + {n_conf} 条已确认流水" if n_conf else "")
                  + "。确认无误后加 --apply。")
            return

        for s in orphan_scheds:
            await db.execute("DELETE FROM dca_schedules WHERE id = ?", (s["id"],))
        await db.execute(
            "DELETE FROM external_asset_actions WHERE status = 'pending' AND asset_id NOT IN "
            "(SELECT id FROM external_assets)")
        if purge_confirmed:
            await db.execute(
                "DELETE FROM external_asset_actions WHERE asset_id NOT IN "
                "(SELECT id FROM external_assets)")
        await db.commit()
        print(f"\n已删除 {len(orphan_scheds)} 个孤儿计划 + {n_pend} 条 pending 流水"
              + (f" + {n_conf} 条已确认流水" if n_conf else "") + "。")


if __name__ == "__main__":
    asyncio.run(main("--apply" in sys.argv, "--all" in sys.argv))
