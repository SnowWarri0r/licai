#!/usr/bin/env python
"""打印工具缺口台账 —— "哪类数据长期取不到"。

    ./venv/bin/python scripts/tool_gaps.py
    ./venv/bin/python scripts/tool_gaps.py --min 3      # 只看命中 3 次以上的
    ./venv/bin/python scripts/tool_gaps.py --clear      # 修完一批后清空重新观察

四类: error(报错) / empty(返回但没内容) / stale(有数但最新时点早于今天) /
zero_fields(关键字段被填成 0, 通常是解析漏读)。
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_KIND_CN = {"error": "报错", "empty": "空", "stale": "过期", "zero_fields": "字段为0"}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min", type=int, default=1, help="最少命中次数")
    ap.add_argument("--clear", action="store_true", help="清空台账")
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    import config
    if args.db:
        config.config.db_path = args.db

    if args.clear:
        import aiosqlite
        async with aiosqlite.connect(config.config.db_path) as db:
            await db.execute("DELETE FROM tool_gap")
            await db.commit()
        print("台账已清空")
        return 0

    from services.tool_gaps import report
    rows = await report(args.min)
    if not rows:
        print(f"账本={config.config.db_path}: 没有命中 {args.min} 次以上的缺口")
        return 0

    print(f"账本={config.config.db_path}  缺口 {len(rows)} 类\n")
    print(f"  {'工具':22} {'类型':8} {'市场':4} {'次数':>4}  说明")
    print(f"  {'-' * 22} {'-' * 8} {'-' * 4} {'-' * 4}  {'-' * 40}")
    for r in rows:
        kind = _KIND_CN.get(r["kind"], r["kind"])
        print(f"  {r['tool']:22} {kind:8} {r['market'] or '-':4} {r['hits']:>4}  "
              f"{(r['detail'] or '')[:46]}")
    print("\n  最近一次入参样本:")
    for r in rows[:5]:
        print(f"    {r['tool']}/{r['kind']}: {r['sample']}  (末次 {r['last_seen']})")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
