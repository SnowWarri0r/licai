#!/usr/bin/env python
"""跑回归评测: 拿真实工具、真实模型跑一遍问答, 用硬断言判。

    ./venv/bin/python evals/run.py                 # 全跑
    ./venv/bin/python evals/run.py --list          # 只列用例
    ./venv/bin/python evals/run.py -c us_premarket # 跑单条(前缀匹配)
    ./venv/bin/python evals/run.py --db portfolio.db   # 换账本(默认 demo)

为什么走真实链路而不是 mock: 这一整天的翻车里 3/4 出在取数(字段没读、源没覆盖),
mock 掉工具就正好把要防的那部分屏蔽了。代价是每跑一次要花模型额度, 所以支持单条跑。

默认账本用 portfolio.demo.db —— 评测会把持仓写进 prompt, 别让真实持仓进日志。
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# akshare 里的 tqdm 进度条用 \r 写同一行, 会把评测结果行盖掉(看着像没输出)。关掉它。
try:
    from functools import partialmethod
    from tqdm import tqdm as _tqdm
    _tqdm.__init__ = partialmethod(_tqdm.__init__, disable=True)
except Exception:
    pass


def _fmt(n) -> str:
    return f"{n:,}" if isinstance(n, int) else str(n)


def _dump(row: dict) -> str:
    """把失败用例的完整问答写到 logs/(已 gitignore, 答案里可能带持仓数字)。"""
    try:
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, f"eval-fail-{row['name']}.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(f"# {row['name']}\n问: {row.get('question', '')}\n")
            f.write(f"工具: {', '.join(row.get('tools') or [])}\n")
            f.write("断言:\n" + "".join(f"  - {m}\n" for m in row.get("fails") or []))
            f.write("\n答:\n" + (row.get("answer") or ""))
        return os.path.relpath(p)
    except Exception:
        return ""


async def run_one(case, verbose=False):
    import services.stock_agent as sa
    import services.llm_client as llm
    from evals.cases import global_checks

    name = case["name"]
    ground = await case["fetch"]()
    if isinstance(ground, dict) and ground.get("skip"):
        return {"name": name, "status": "skip", "why": ground["skip"]}

    t0 = time.time()
    before = llm.get_usage_stats()
    r = await sa.ask_stock(case["question"])
    used = llm.get_usage_stats()
    ans, tools = (r.get("answer") or ""), (r.get("tools_used") or [])

    fails = []
    if r.get("error"):
        fails.append(f"agent 报错: {r['error']}")
    else:
        fails += case["check"](ans, tools, ground)
        fails += global_checks(ans, tools)

    if verbose:
        print(f"\n---- {name} ----\n问: {case['question']}\n答:\n{ans}\n")

    return {
        "name": name, "status": "fail" if fails else "pass", "fails": fails,
        "answer": ans,
        "question": case["question"], "tools": tools, "rounds": r.get("rounds"),
        "secs": round(time.time() - t0, 1),
        "prompt_tokens": used["prompt_total"] - before["prompt_total"],
        "cache_read": used["cache_read"] - before["cache_read"],
        "answer_chars": len(ans),
    }


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--case", default="", help="只跑名字以此开头的用例")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="打印完整问答")
    ap.add_argument("--db", default="portfolio.demo.db")
    args = ap.parse_args()

    import config
    if os.path.exists(args.db):
        # 必须改单例 config, 不是 Config 类属性 —— 类属性只是 dataclass 的默认值,
        # 单例早已实例化, 改它才生效。改错了会静默跑在真实账本上(真实持仓进 prompt)。
        config.config.db_path = args.db
    else:
        print(f"! 找不到 {args.db}, 用默认账本(真实持仓会进 prompt)")

    from evals.cases import CASES
    picked = [c for c in CASES if c["name"].startswith(args.case)]
    if args.list:
        for c in CASES:
            why = (c["check"].__doc__ or "").strip().splitlines()[0]
            print(f"  {c['name']:34} {why}")
        return 0
    if not picked:
        print(f"没有匹配 {args.case!r} 的用例")
        return 2

    print(f"账本={config.config.db_path}  用例={len(picked)}\n")
    rows = []
    for c in picked:
        try:
            rows.append(await run_one(c, args.verbose))
        except Exception as e:
            rows.append({"name": c["name"], "status": "error",
                         "fails": [f"{type(e).__name__}: {e}"],
                         "secs": 0, "prompt_tokens": 0, "cache_read": 0, "tools": []})
        r = rows[-1]
        mark = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "error": "ERR "}[r["status"]]
        extra = f"{r.get('secs', 0)}s {_fmt(r.get('prompt_tokens', 0))}tok" \
            if r["status"] != "skip" else r.get("why", "")
        print(f"  [{mark}] {r['name']:34} {extra}")
        for msg in r.get("fails", []):
            print(f"         · {msg}")
        # 失败的答案落盘。这些用例每跑一条十万 token 且答案不稳定(同一条实测一次挂一次过),
        # 当场留证比"回头重跑看看"靠谱 —— 不然分不清是真答错还是断言误报。
        if r["status"] == "fail" and r.get("answer") and not args.verbose:
            p = _dump(r)
            if p:
                print(f"         · 答案已存 {p}")

    ok = sum(1 for r in rows if r["status"] == "pass")
    bad = [r for r in rows if r["status"] in ("fail", "error")]
    skipped = [r for r in rows if r["status"] == "skip"]
    tok = sum(r.get("prompt_tokens", 0) for r in rows)
    hit = sum(r.get("cache_read", 0) for r in rows)
    line = f"\n通过 {ok} / 失败 {len(bad)} / 跳过 {len(skipped)}"
    if tok:
        line += f"   prompt {_fmt(tok)} tokens, 缓存命中 {hit / tok * 100:.0f}%"
    print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
