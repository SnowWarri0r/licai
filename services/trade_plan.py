"""交易计划: 收盘后用户定好次日计划 → 盘中盯价触发就推送 → 收盘后对照实际成交复盘。

计划由用户定: 标的、方向、触发条件、数量、理由、失效条件都取用户原话, 系统只负责记下、
盯价、提醒和对账。执行在券商客户端里由用户自己下单。

计划只对 plan_date 那一个交易日有效, 过了自动过期 —— 隔夜的盘面变了, 旧计划不该悄悄延续。
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from database import get_db

_CST = ZoneInfo("Asia/Shanghai")
OPS = ("<=", ">=")
SIDES = {"BUY": "买入", "SELL": "卖出"}
STATUS_CN = {"active": "等待触发", "triggered": "已触发", "invalidated": "已失效",
             "cancelled": "已取消", "expired": "已过期"}

TOOL_SAVE = {
    "name": "save_trade_plan",
    "description": "记录用户自己说出的交易计划(次日或今天盘中), 盘中价格到达触发条件或失效条件时系统会推送提醒, 收盘后可对照实际成交复盘。"
                   "每条计划的方向、触发价、数量、理由、失效条件一律取用户原话; 用户没给出触发价或方向时, 先问清再记。"
                   "plan_date 是计划生效的交易日(YYYY-MM-DD), 收盘后定的计划填下一个交易日。",
    "input_schema": {"type": "object", "properties": {"plans": {"type": "array", "items": {
        "type": "object", "properties": {
            "code": {"type": "string", "description": "代码(先 resolve_stock 取到)"},
            "side": {"type": "string", "enum": ["BUY", "SELL"]},
            "trigger_op": {"type": "string", "enum": list(OPS), "description": "<= 跌到触发价及以下; >= 涨到及以上"},
            "trigger_price": {"type": "number"},
            "qty": {"type": "integer", "description": "计划股数/份数, 用户没说就不填"},
            "reason": {"type": "string", "description": "用户说的理由"},
            "invalid_op": {"type": "string", "enum": list(OPS)},
            "invalid_price": {"type": "number", "description": "失效条件价位, 用户没说就不填"},
            "plan_date": {"type": "string"},
            "replace_id": {"type": "integer", "description": "用户改了某条已有计划时, 传那条的 id(get_trade_plans 返回里有), 旧的会标为取消"}},
        "required": ["code", "side", "trigger_op", "trigger_price", "plan_date"]}}},
        "required": ["plans"]},
}

TOOL_GET = {
    "name": "get_trade_plans",
    "description": "取某个交易日的交易计划及执行对照: 每条计划的状态(等待触发/已触发/已失效…)、触发时刻与价格, "
                   "以及与当天实际成交的逐条对照(按计划执行/没到触发价就成交/触发未执行/未触发/失效后仍成交)和计划外成交。"
                   "用户问'我的计划/今天按计划做了吗/复盘执行'时用。date 留空=今天。",
    "input_schema": {"type": "object", "properties": {"date": {"type": "string"}}},
}


def now_cst() -> datetime:
    return datetime.now(_CST)


def today() -> str:
    return now_cst().strftime("%Y-%m-%d")


def _hit(op: str, price: float, level: float) -> bool:
    return price <= level if op == "<=" else price >= level


def _fmt(p: dict) -> str:
    s = f"{SIDES.get(p['side'], p['side'])} {p['name']}({p['code']}) 触发 {p['trigger_op']} {p['trigger_price']}"
    if p.get("qty"):
        s += f", {p['qty']} 股"
    if p.get("invalid_price"):
        s += f"; 失效 {p['invalid_op']} {p['invalid_price']}"
    return s


async def _name_of(code: str) -> str:
    try:
        import services.stock_agent as sa
        r = await sa._EXECUTORS["resolve_stock"]({"query": code})
        return (r or {}).get("name") or ""
    except Exception:
        return ""


async def add_plans(items: list[dict], session_id: int | None = None) -> dict:
    saved, bad = [], []
    db = await get_db()
    try:
        for it in items or []:
            code = re.sub(r"^(sh|sz|bj)", "", str(it.get("code") or "").strip(), flags=re.I)
            side = str(it.get("side") or "").upper()
            op = it.get("trigger_op")
            try:
                price = float(it.get("trigger_price"))
            except (TypeError, ValueError):
                price = 0
            day = str(it.get("plan_date") or "")[:10]
            if not code or side not in SIDES or op not in OPS or price <= 0 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                bad.append({"plan": it, "why": "代码/方向/触发条件/触发价/日期 缺失或格式不对"})
                continue
            inv_op = it.get("invalid_op") if it.get("invalid_op") in OPS else None
            inv = it.get("invalid_price")
            inv = float(inv) if inv not in (None, "") and inv_op else None
            name = await _name_of(code) or code
            if it.get("replace_id"):
                await db.execute("UPDATE trade_plan SET status='cancelled' WHERE id=? AND status IN ('active','triggered')",
                                 (int(it["replace_id"]),))
            cur = await db.execute(
                "INSERT INTO trade_plan (plan_date, code, name, side, trigger_op, trigger_price, qty, reason, "
                "invalid_op, invalid_price, session_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (day, code, name, side, op, price, it.get("qty"), (it.get("reason") or "")[:300],
                 inv_op, inv, session_id))
            saved.append({"id": cur.lastrowid, "code": code, "name": name, "side": side, "trigger_op": op,
                          "trigger_price": price, "qty": it.get("qty"), "invalid_op": inv_op,
                          "invalid_price": inv, "plan_date": day})
        await db.commit()
    finally:
        await db.close()
    return {"saved": saved, "rejected": bad,
            "note": "已记下, 当天交易时段内价格到达触发或失效条件会推送提醒" if saved else ""}


async def list_plans(date: str | None = None, status: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM trade_plan WHERE 1=1", []
    if date:
        sql += " AND plan_date = ?"
        args.append(date)
    if status:
        sql += " AND status = ?"
        args.append(status)
    db = await get_db()
    try:
        rows = await (await db.execute(sql + " ORDER BY plan_date DESC, id", args)).fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


async def cancel(plan_id: int) -> bool:
    db = await get_db()
    try:
        cur = await db.execute("UPDATE trade_plan SET status='cancelled' WHERE id=? AND status IN ('active','triggered')",
                               (plan_id,))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def expire_old() -> int:
    db = await get_db()
    try:
        cur = await db.execute("UPDATE trade_plan SET status='expired' WHERE status='active' AND plan_date < ?",
                               (today(),))
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


def evaluate(plans: list[dict], quotes: dict) -> list[tuple[dict, str, float]]:
    """纯函数: 按现价判断哪些计划这次该变状态。返回 [(计划, 新状态, 现价)]。

    失效优先: 同一个价同时满足触发和失效(比如跳空), 按失效处理 —— 计划的前提已经不在了。
    已触发的计划仍盯失效条件, 触发后没执行、价格又走到失效位, 也要告诉用户。
    """
    out = []
    for p in plans:
        q = quotes.get(p["code"]) or {}
        price = q.get("price")
        if not price:
            continue
        if p.get("invalid_price") and p.get("invalid_op") and _hit(p["invalid_op"], price, p["invalid_price"]):
            out.append((p, "invalidated", price))
        elif p["status"] == "active" and _hit(p["trigger_op"], price, p["trigger_price"]):
            out.append((p, "triggered", price))
    return out


async def _set_status(plan_id: int, status: str, price: float) -> None:
    db = await get_db()
    try:
        col = "triggered" if status == "triggered" else "invalidated"
        await db.execute(f"UPDATE trade_plan SET status=?, {col}_at=?, {col}_price=? WHERE id=?",
                         (status, now_cst().strftime("%H:%M:%S"), price, plan_id))
        await db.commit()
    finally:
        await db.close()


async def monitor_once(notify) -> list[dict]:
    """盯一轮: 今天的 active/triggered 计划取实时价, 状态有变就落库并推送。notify(text, event)。"""
    from services.market_data import get_realtime_quotes
    plans = [p for p in await list_plans(today()) if p["status"] in ("active", "triggered")]
    if not plans:
        return []
    quotes = await get_realtime_quotes(sorted({p["code"] for p in plans}))
    events = []
    for p, status, price in evaluate(plans, quotes):
        await _set_status(p["id"], status, price)
        head = "计划触发" if status == "triggered" else "计划失效"
        text = f"【{head}】{_fmt(p)}\n现价 {price}"
        if p.get("reason"):
            text += f"\n你的理由: {p['reason']}"
        text += "\n(提醒你自己写下的计划, 是否执行由你决定)"
        ev = {"type": "plan_alert", "status": status, "plan_id": p["id"], "code": p["code"],
              "name": p["name"], "price": price, "text": text}
        events.append(ev)
        await notify(text, ev)
    return events


async def _fills(date: str) -> list[dict]:
    """当天实际成交(A 股流水 + 场内 ETF 流水), 方向归一成 BUY/SELL。"""
    db = await get_db()
    try:
        a = await (await db.execute(
            "SELECT a.stock_code, a.action_type, a.price, a.shares, a.trade_time, COALESCE(h.stock_name, '') "
            "FROM position_actions a LEFT JOIN holdings h ON h.stock_code = a.stock_code WHERE a.trade_date=?",
            (date,))).fetchall()
        e = await (await db.execute(
            "SELECT x.code, a.action_type, a.unit_price, a.shares, a.trade_time, x.name FROM external_asset_actions a "
            "JOIN external_assets x ON x.id = a.asset_id WHERE a.trade_date=? AND x.code GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]' "
            "AND COALESCE(a.note, '') NOT LIKE 'DCA%'", (date,))).fetchall()   # 定投自动扣款不算交易
    finally:
        await db.close()
    out = []
    for code, act, price, shares, t, name in list(a) + list(e):
        side = "BUY" if act in ("BUY", "ADD") else "SELL" if act in ("SELL", "REDEEM") else None
        if side and price:
            out.append({"code": code, "name": name or code, "side": side, "price": float(price),
                        "shares": float(shares or 0), "time": t or ""})
    return out


def compare(plans: list[dict], fills: list[dict]) -> dict:
    """纯函数: 计划 vs 实际成交逐条对照。"""
    used = set()
    rows = []
    for p in plans:
        if p["status"] == "cancelled":
            continue
        mine = [i for i, f in enumerate(fills) if f["code"] == p["code"] and f["side"] == p["side"]]
        used.update(mine)
        fs = [fills[i] for i in mine]
        qty = sum(f["shares"] for f in fs)
        avg = round(sum(f["price"] * f["shares"] for f in fs) / qty, 4) if qty else None
        if fs:
            ok_price = all(_hit(p["trigger_op"], f["price"], p["trigger_price"]) for f in fs)
            if p["status"] == "invalidated" and p.get("invalidated_at") and \
                    any(f["time"] and f["time"] >= p["invalidated_at"][:5] for f in fs):
                verdict = "失效后仍成交"
            elif ok_price:
                verdict = "按计划执行"
            else:
                verdict = "没到触发价就成交"
        else:
            verdict = {"triggered": "触发未执行", "invalidated": "失效未执行"}.get(p["status"], "未触发")
        row = {"id": p["id"], "计划": _fmt(p), "code": p["code"], "name": p["name"], "状态": STATUS_CN.get(p["status"], p["status"]),
               "对照": verdict, "触发时刻": p.get("triggered_at"), "触发时价格": p.get("triggered_price"),
               "成交笔数": len(fs), "成交数量": qty or None, "成交均价": avg, "理由": p.get("reason") or ""}
        if fs and p.get("qty"):
            row["数量偏差"] = qty - p["qty"]
        if avg:
            row["均价较触发价%"] = round((avg / p["trigger_price"] - 1) * 100, 2)
        rows.append(row)
    off = [f for i, f in enumerate(fills) if i not in used]
    counts: dict = {}
    for r in rows:
        counts[r["对照"]] = counts.get(r["对照"], 0) + 1
    return {"plans": rows, "计划外成交": off, "汇总": counts}


async def review(date: str | None = None) -> dict:
    day = date or today()
    plans = await list_plans(day)
    fills = await _fills(day)
    out = compare(plans, fills)
    out["date"] = day
    if not fills and any(p["status"] in ("triggered", "invalidated") for p in plans):
        out["提示"] = "当天成交还没同步进账本(券商当日成交需导入), 触发后的执行情况暂时对不上"
    if not plans:
        out["提示"] = "这一天没有记录交易计划"
    return out


def next_trading_day(day: str | None = None) -> str:
    from services.market_data import _is_a_share_trading_day
    d = datetime.strptime(day or today(), "%Y-%m-%d").date() + timedelta(days=1)
    for _ in range(15):
        if _is_a_share_trading_day(d):
            break
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")
