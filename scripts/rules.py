#!/usr/bin/env python
"""从历次"用户纠正"里挖候选规则, 人批准后才进 system prompt。

    ./venv/bin/python scripts/rules.py --mine        # 挖掘 + 起草(要调模型)
    ./venv/bin/python scripts/rules.py               # 看队列
    ./venv/bin/python scripts/rules.py --approve 3   # 批准第 3 条 → 进 prompt
    ./venv/bin/python scripts/rules.py --reject 4
    ./venv/bin/python scripts/rules.py --diff        # 看批准后 prompt 会多出什么

pending 的规则一律不进 prompt。批准前建议先跑一遍 evals/run.py, 批准后再跑一遍 ——
一条坏规则会静默影响所有回答, 而回归集只有 8 例, 兜不住。
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_MARK = {"pending": "待审", "active": "已生效", "rejected": "已否"}


async def cmd_mine() -> int:
    from services import rule_forge as rf
    import services.llm_client as llm

    rows = await rf.fetch_messages()
    items = rf.mine_corrections(rows)
    if not items:
        print("没找到带纠正措辞的对话")
        return 0
    print(f"从 {len(rows)} 条消息里挖出 {len(items)} 次纠正, 逐条起草…\n")

    added = skipped = dup = 0
    for it in items:
        try:
            text = await asyncio.to_thread(
                llm.call_claude, rf.draft_prompt(it), rf.draft_system(), "balanced", 3000)
        except Exception as e:
            print(f"  起草失败: {type(e).__name__}: {str(e)[:70]}")
            continue
        d = rf.parse_draft(text)
        if not d:
            print(f"  解析不出 JSON, 跳过: {(text or '')[:60]!r}")
            continue
        if d.get("verdict") != "rule" or not d.get("title") or not d.get("body"):
            skipped += 1
            print(f"  skip · {it['correction'][:28]}… → {d.get('why', '')[:56]}")
            continue
        ok = await rf.add_candidate(d["title"], d["body"], it["correction"], it["session_id"])
        if ok:
            added += 1
            print(f"  候选 · 【{d['title']}】 ← {it['correction'][:26]}…")
        else:
            dup += 1
    print(f"\n新增候选 {added} 条, 判为非规则 {skipped} 条, 同标题已存在 {dup} 条")
    return 0


async def cmd_list() -> int:
    from services import rule_forge as rf
    rules = await rf.list_rules()
    if not rules:
        print("队列是空的。先跑 --mine")
        return 0
    for r in rules:
        print(f"  #{r['id']:<3} [{_MARK.get(r['status'], r['status']):4}] 【{r['title']}】")
        print(f"        {r['body'][:110]}")
        if r["evidence"]:
            print(f"        ← 触发它的纠正: {r['evidence'][:60]}")
    n_active = sum(1 for r in rules if r["status"] == "active")
    print(f"\n共 {len(rules)} 条, 其中 {n_active} 条已进 prompt")
    return 0


async def cmd_diff() -> int:
    from services import rule_forge as rf
    text = rf.render_rules(rf.active_rules_sync())
    print(text or "(当前没有已生效的沉淀规则, prompt 不受影响)")
    return 0


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mine", action="store_true")
    ap.add_argument("--approve", type=int)
    ap.add_argument("--reject", type=int)
    ap.add_argument("--diff", action="store_true")
    ap.add_argument("--clear-pending", action="store_true", help="清空待审队列(迭代用)")
    ap.add_argument("--db", default="")
    args = ap.parse_args()

    import config
    if args.db:
        config.config.db_path = args.db
    print(f"账本={config.config.db_path}\n")

    from services import rule_forge as rf
    if args.clear_pending:
        import aiosqlite
        async with aiosqlite.connect(config.config.db_path) as db:
            cur = await db.execute("DELETE FROM prompt_rule WHERE status = 'pending'")
            await db.commit()
        print(f"清掉 {cur.rowcount} 条待审")
        return 0
    if args.mine:
        return await cmd_mine()
    if args.approve:
        ok = await rf.decide(args.approve, "active")
        print(f"#{args.approve} {'已批准, 下次问答即生效' if ok else '不存在'}")
        return 0 if ok else 2
    if args.reject:
        ok = await rf.decide(args.reject, "rejected")
        print(f"#{args.reject} {'已否' if ok else '不存在'}")
        return 0 if ok else 2
    if args.diff:
        return await cmd_diff()
    return await cmd_list()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
