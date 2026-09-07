"""逻辑漂移: 你的买入理由改过几次, 每次是跟着**事实**改的, 还是跟着**股价**改的。

出处与取舍
----------
灵感来自 ai-berkshire 的 thesis-drift(「分清事实变化与措辞变化」)。这里把它拆成三档而不是
两档, 因为最值得抓的那一档在两档划分里是隐形的:

    基本面变了               → 跟事实走。正常, 该改。
    基本面没变, 只有价格变了  → **跟股价走**。跌了之后把理由换成一个能解释当前股价的说法。
    什么都没变               → 只改了说法。措辞漂移。

第二档是事后合理化的指纹。它在"事实 vs 措辞"的二分里会被塞进"事实变化"(价格确实变了),
于是看起来完全正当 —— 而它恰恰是最该被指出来的那种改写。

判定只用**客观量**: 相邻两版之间价格/估值/报告期/同比/ROE/负债率的变化, 加上改写发生那一刻
的浮动盈亏。语义那一层(这两句是同义改写, 还是换成了另一个理由)不在这里做模型调用 —— agent
读到的就是这份逐句 diff, 它本身就是语义层, 再嵌一次调用只是多花钱多一个失败点。前端拿同一份
JSON 直接摆出来, 让人自己看见。

刻意不做
--------
不给"逻辑还成不成立"的结论, 不打分, 不提示买卖。漂移是**行为**事实, 不是估值结论。
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import difflib
import json
import re

# ── 事实轴: 哪些数字算"变了" ─────────────────────────────
# 门限是约定不是测量结论: 个股一个月波动 10% 很常见, 15% 才是"这只票的处境不一样了"的量级;
# 净利同比天生比营收波动大, 所以门限给到 20pp。改门限只改这里, 判定逻辑读这张表。
_PRICE_RULES = (
    ("价格", "pct", 15.0),
    ("PE_TTM", "pct", 30.0),
    ("PB", "pct", 30.0),
)
_FUND_RULES = (
    ("报告期", "eq", None),            # 换了报告期 = 期间出过新财报, 这是硬事实
    ("营收同比增长%", "abs", 10.0),
    ("净利同比增长%", "abs", 20.0),
    ("ROE%", "abs", 3.0),
    ("资产负债率%", "abs", 5.0),
)
# 总市值不进规则: 它是 价格 × 股本, 与"价格"同一件事, 两个都算会把一次涨跌数成两处变化。


async def snapshot(code: str) -> dict:
    """取"此刻的客观事实", 存进修订。取不到的项直接不放键 —— 不放和放 None 在下游都算缺, 但
    不放能让 JSON 小一点; 关键是**别用 0 占位**, 0 会被当成一个真实数值参与门限比较。"""
    from services.market_data import get_realtime_quotes, normalize_stock_code
    bare = (code or "").split(".")[-1]
    full = normalize_stock_code(code or "")
    out: dict = {"取于": _today().isoformat()}

    q, fund, cost = await asyncio.gather(
        get_realtime_quotes([full]),
        _fundamentals(bare),
        _cost_of(full),
        return_exceptions=True,
    )
    if isinstance(q, dict):
        row = q.get(bare) or q.get(full) or {}
        if row.get("price"):
            out["价格"] = round(float(row["price"]), 3)
    if isinstance(fund, dict):
        val = fund.get("valuation") or {}
        fin = fund.get("financials") or {}
        for k in ("PE_TTM", "PB", "总市值亿"):
            if val.get(k) is not None:
                out[k] = val[k]
        for k in ("报告期", "营收同比增长%", "净利同比增长%", "ROE%", "资产负债率%"):
            if fin.get(k) is not None:
                out[k] = fin[k]
    if isinstance(cost, (int, float)) and cost and out.get("价格"):
        out["成本"] = round(float(cost), 4)
        out["浮动盈亏%"] = round((out["价格"] - float(cost)) / float(cost) * 100, 2)
    return out


async def _fundamentals(bare: str) -> dict:
    from services.stock_agent import _tool_fundamentals
    try:
        return await _tool_fundamentals(bare)
    except Exception:
        return {}


async def _cost_of(code: str) -> float | None:
    """当前持仓段的综合成本(含费、分红摊薄), 与看板/券商同一口径。已清仓或没流水 → None。

    不直接读 holdings.cost_price: 那一列对已清仓的票是陈旧值(见 _active_holdings 的同一坑),
    拿它算浮亏会得出一个凭空的数。"""
    try:
        from database import get_holding, get_position_actions
        from services.position_ledger import compute_position_state
        from services.dividends import dilute_state
        from api.portfolio_routes import _broker_stock_fee
        h = await get_holding(code)
        acts = await get_position_actions(code, limit=500)
        if not acts:
            return None
        rate, mn = await _broker_stock_fee((h or {}).get("broker"))
        st = compute_position_state(acts, stock_code=code, commission_rate=rate, commission_min=mn)
        st = await dilute_state(code, st)
        return st.get("cost_price") if float(st.get("shares") or 0) > 0 else None
    except Exception:
        return None


def _today() -> _dt.date:
    return (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).date()


def _loads(s) -> dict:
    if isinstance(s, dict):
        return s
    try:
        d = json.loads(s or "")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def fact_delta(prev, cur) -> dict:
    """两份快照的差。任一份为空 → 缺快照, 明说"判不了", 不许当成"没变化"。"""
    a, b = _loads(prev), _loads(cur)
    if not a or not b:
        return {"可判": False, "原因": "缺事实快照", "价格轴": [], "基本面轴": []}
    out = {"可判": True, "价格轴": [], "基本面轴": []}
    for bucket, rules in (("价格轴", _PRICE_RULES), ("基本面轴", _FUND_RULES)):
        for key, kind, thr in rules:
            x, y = a.get(key), b.get(key)
            if x is None or y is None:
                continue
            if kind == "eq":
                if str(x) != str(y):
                    out[bucket].append({"项": key, "从": x, "到": y})
                continue
            try:
                x, y = float(x), float(y)
            except (TypeError, ValueError):
                continue
            if kind == "pct":
                if x == 0:
                    continue
                d = (y - x) / abs(x) * 100
                if abs(d) >= thr:
                    out[bucket].append({"项": key, "从": round(x, 3), "到": round(y, 3),
                                        "变化%": round(d, 1)})
            else:                                        # abs: 百分点
                d = y - x
                if abs(d) >= thr:
                    out[bucket].append({"项": key, "从": round(x, 2), "到": round(y, 2),
                                        "变化pp": round(d, 1)})
    return out


# ── 文本轴: 逐句 diff ───────────────────────────────────
_SEP = re.compile(r"[。；;！!？?\n，,、]+")
_REWRITE_MIN = 0.55        # 相似度到这个份上算"同一条理由改了几个字", 低于此算换了一条


def _clauses(t: str) -> list[str]:
    """按分句符切到**分句**粒度 —— 一条理由通常就是一个分句("国产算力龙头""看好数据中心需求"),
    整段比对只会告诉你"改了", 分句比对才说得出改的是哪一条。"""
    return [p.strip() for p in _SEP.split(t or "") if len(p.strip()) >= 2]


def text_delta(prev: str, cur: str) -> dict:
    """保留 / 改写 / 删除 / 新增。改写是跨位置贪心配对出来的 —— 换个位置重写的同一条理由,
    在 difflib 的 opcode 里会分裂成一删一增, 那样就看不出它其实是同一条。"""
    old, new = _clauses(prev), _clauses(cur)
    sm = difflib.SequenceMatcher(None, old, new)
    kept, dropped, added = [], [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            kept += old[i1:i2]
        else:
            dropped += old[i1:i2]
            added += new[j1:j2]
    rewrites = []
    for o in list(dropped):
        best, ratio = None, 0.0
        for n in added:
            r = difflib.SequenceMatcher(None, o, n).ratio()
            if r > ratio:
                best, ratio = n, r
        if best is not None and ratio >= _REWRITE_MIN:
            rewrites.append({"旧": o, "新": best, "相似度": round(ratio, 2)})
            dropped.remove(o)
            added.remove(best)
    return {"整体相似度": round(difflib.SequenceMatcher(None, prev or "", cur or "").ratio(), 2),
            "保留": kept, "改写": rewrites, "删除": dropped, "新增": added}


def _verdict(fd: dict, td: dict, pnl_before) -> dict:
    """三档判定。顺序是刻意的: 基本面优先 —— 基本面和价格同时变时, 改逻辑归因到事实。"""
    if not fd.get("可判"):
        return {"档": "判不了", "说明": "缺当时的事实快照(老数据或取数失败), 只能看文本变化。"}
    if fd["基本面轴"]:
        items = "、".join(f"{c['项']}{_arrow(c)}" for c in fd["基本面轴"])
        return {"档": "跟事实走", "说明": f"这一版之前基本面确实变了({items}), 改逻辑有据。"}
    if fd["价格轴"]:
        items = "、".join(f"{c['项']}{_arrow(c)}" for c in fd["价格轴"])
        tail = ""
        if isinstance(pnl_before, (int, float)):
            tail = f"改写发生在浮动盈亏 {pnl_before:+.1f}% 的时候。"
        换 = bool(td.get("删除") or td.get("新增"))
        return {"档": "跟股价走",
                "说明": f"基本面(报告期/同比/ROE/负债率)在门限内没动, 变的只有价格与估值({items})。"
                        f"{'理由被换掉/加了新的' if 换 else '只是措辞'}。{tail}"}
    return {"档": "只改了说法",
            "说明": "价格与基本面都在门限内没动, 改的是措辞。"
                    + ("加/删了理由。" if (td.get("删除") or td.get("新增")) else "同义改写。")}


def _arrow(c: dict) -> str:
    if "变化%" in c:
        return f" {c['从']}→{c['到']}({c['变化%']:+}%)"
    if "变化pp" in c:
        return f" {c['从']}→{c['到']}({c['变化pp']:+}pp)"
    return f" {c['从']}→{c['到']}"


async def drift(code: str) -> dict:
    """一只票的漂移全景: 逐次修订的判定 + 最后一版到现在的事实缺口 + 未复核天数。"""
    from database import get_thesis_revisions, get_thesis
    bare = (code or "").split(".")[-1]
    revs = await get_thesis_revisions(bare)
    t = await get_thesis(bare)
    if not revs:
        return {"code": bare, "有记录": False,
                "note": "没记过买入逻辑, 也就无从谈漂移。"}

    changes = []
    for prev, cur in zip(revs, revs[1:]):
        fd = fact_delta(prev.get("facts"), cur.get("facts"))
        td = text_delta(prev.get("thesis") or "", cur.get("thesis") or "")
        # 浮亏取 cur 那一版的快照: 改写发生在写下这一版的时刻, prev 的快照是上一版写的时候
        pnl = _loads(cur.get("facts")).get("浮动盈亏%")
        changes.append({"rev": cur.get("rev"), "日期": str(cur.get("created_at") or "")[:10],
                        "判定": _verdict(fd, td, pnl), "事实变化": fd, "文本变化": td,
                        "改写时浮动盈亏%": pnl})

    last = revs[-1]
    days = _days_since(str(last.get("created_at") or "")[:10])
    now = await snapshot(bare)
    since = fact_delta(last.get("facts"), now)
    stale = None
    if since.get("可判"):
        moved = since["基本面轴"] + since["价格轴"]
        stale = ("自上次修订以来, 这些已经变了(逻辑没跟着动): "
                 + "、".join(f"{c['项']}{_arrow(c)}" for c in moved)) if moved else \
                "自上次修订以来价格与基本面都在门限内没动, 逻辑对得上当时的事实。"

    return {"code": bare, "有记录": True, "name": (t or {}).get("name", ""),
            "修订次数": len(revs), "首版日期": str(revs[0].get("created_at") or "")[:10],
            "末版日期": str(last.get("created_at") or "")[:10], "距末版天数": days,
            "逐次改写": changes,
            "改写时浮亏轨迹": _pnl_track(revs),
            "自末版以来的事实变化": since, "自末版以来": stale,
            "现在的事实": now,
            "口径": f"共 {len(revs)} 版; 判定门限: 价格±15%/PE·PB±30%/营收同比±10pp/净利同比±20pp/"
                    f"ROE±3pp/负债率±5pp, 换报告期算基本面变化。缺快照的版本标为判不了, 不算作没变化。",
            "note": "这是行为记录不是估值结论 —— 只说逻辑改过几次、每次跟着什么改, 不判断逻辑对不对、"
                    "也不给买卖。'跟股价走' 值得当面问自己一句: 是先有了新的事实, 还是先有了要解释的股价。"}


def _pnl_track(revs: list[dict]) -> dict:
    """每次改写时的浮动盈亏序列。逐次走低 = 逻辑跟着亏损在改 —— 这个信号不需要读文本就能看出来。"""
    seq = [{"rev": r.get("rev"), "日期": str(r.get("created_at") or "")[:10],
            "浮动盈亏%": _loads(r.get("facts")).get("浮动盈亏%")} for r in revs]
    vals = [s["浮动盈亏%"] for s in seq if isinstance(s["浮动盈亏%"], (int, float))]
    worse = len(vals) >= 2 and all(b < a for a, b in zip(vals, vals[1:]))
    return {"序列": seq, "逐次走低": worse,
            "说明": "每一版都记在更深的浮亏上 —— 改逻辑与亏损同步。" if worse else ""}


def _days_since(iso: str) -> int | None:
    try:
        return (_today() - _dt.date.fromisoformat(iso)).days
    except Exception:
        return None
