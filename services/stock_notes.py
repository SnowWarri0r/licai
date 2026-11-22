"""个股档案: 记忆分层里的「表示层」—— 一只票一页, 收录过往问答里已经查证过的结构性认识。

问到一只票时, agent 每次都从零检索主营、产业链位置、关联关系, 而这些在以前的会话里多半已经
查过、讲过。档案把它们按票归页(每条带出处会话与当时日期), 由夜间整理从原文里提取, 问答时
按需读取; 系统提示里只放「哪些票有档案」的一行索引, 不整页注入。

一票一页、只由整理这一个写入者追加: 多个写入者共写一张汇总表时容易互相覆盖。
每条必须附原话, 代码核对原话逐字出现在所给编号的消息里、且这只票在那次会话里确实被提到。
行情数字(价格、涨跌、量比、换手、资金流)每天都在变, 档案里不收; 结论类条目注明是当时的判断。

档案内容取自助手当时的回答, 其中可能带有当时联网检索到的第三方文本, 所以读档案的工具
不在画像的可信工具名单里。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from services import memory_dream as md
from services import user_memory as um

logger = logging.getLogger(__name__)

WATERMARK_KEY = "stock_notes_last_id"
LAST_RUN_KEY = "stock_notes_last_run"
CHUNK_CHARS = 28000
MAX_CHUNKS = 4
ASSISTANT_CAP = 4000
MAX_NOTES_PER_CHUNK = 25
MAX_PER_STOCK = 30
INDEX_MAX = 40
QUOTE_MIN, QUOTE_MAX = 6, 160
TEXT_MAX = 200
MODEL = "smart"

KINDS = ("主营", "产业链", "关联", "事件", "结论", "用户")
KIND_CN = {"主营": "主营与赚钱方式", "产业链": "产业链位置", "关联": "关联关系", "事件": "重大事件",
           "结论": "当时的分析结论", "用户": "用户的看法与安排"}

_CODE = re.compile(r"^(\d{6}|\d{5}|[A-Z]{1,5}(\.[A-Z]{1,2})?)$")
# 行情类: 今天对、明天就错, 档案里不收
_VOLATILE = re.compile(r"\d+(\.\d+)?\s*元|量比|换手|涨停|跌停|收涨|收跌|涨幅|跌幅|主力净|资金净|分时|盘中|今日|今天"
                       # 持仓数量、成本、成交: 账本工具随时可查, 而且一买一卖就过时
                       r"|\d\s*(股|份|手)(?![东票本权份])|成本\s*\d|以\s*\d+(\.\d+)?\s*(买|卖|清|加|减)|持有.{0,12}\d")

SYSTEM = (
    "你在为一个理财分析助手维护「个股档案」: 一只票一页, 记录过往问答里已经查证过、下次问到这只票时可以直接复用的认识,"
    " 让助手不必每次从零检索。\n"
    "收录这几类(kind): 主营(靠什么赚钱、主要产品与营收结构)、产业链(处在哪个环节、上下游是谁)、"
    "关联(持股、供应、客户、同属的概念或集团)、事件(带日期的公告、政策、业绩、重大合同)、"
    "结论(当时分析得出的判断, text 里写明「截至某日」)、用户(用户对这只票说过的看法、持有安排、买入理由)。\n"
    "档案只写结构性认识; 价格、涨跌幅、量比、换手率、资金流向这类行情数字每天都在变, 由工具在作答时获取。"
    "用户的持仓数量、成本与买卖成交记录由账本工具提供, 用户类条目只记用户的看法、关注或买入的理由、持有计划与偏好。\n"
    "每条 text 一句话, 写清主语, 200 字以内。依据: msg 为该条信息所在消息的编号, quote 为从该消息中逐字复制的原文(6 到 160 字)。"
    "code 写股票代码(A 股 6 位, 港股 5 位, 美股字母), name 写简称。\n"
    "【已有档案】中被新会话推翻或已经过时的条目, 放进 supersede, 同样给出 msg 与 quote 作为依据。\n"
    "每条都是在预测以后问到这只票时会用到它; 没有值得复用的内容时返回空列表。\n"
    "只输出一行 JSON: {\"notes\": [{\"code\": \"…\", \"name\": \"…\", \"kind\": \"主营\", \"text\": \"…\", "
    "\"msg\": 编号, \"quote\": \"…\"}], \"supersede\": [{\"id\": 档案条目编号, \"msg\": 编号, \"quote\": \"…\"}]}。"
    "JSON 字符串内部需要引号时用「」。"
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


def _code(raw) -> str:
    c = str(raw or "").strip().upper()
    c = re.sub(r"^(SH|SZ|BJ|HK|US)[.:]?", "", c)
    return c


# ── 纯函数 ───────────────────────────────────────────

def validate(out, msgs: dict[int, dict], session_text: dict[int, str],
             existing: dict[int, dict]) -> tuple[list[dict], list[dict], list[str]]:
    """msgs: {msg_id: {content, session_id, date}} —— 本块全部消息(用户与助手);
    session_text: {session_id: 整段会话原文}; existing: {条目id: {code, text}} 生效中的档案条目。
    返回 (新条目, 作废条目, 丢弃原因)。"""
    notes, sups, dropped = [], [], []
    if not isinstance(out, dict):
        return notes, sups, ["输出不是对象"]

    def grounded(op) -> dict | None:
        try:
            m = msgs.get(int(op.get("msg")))
        except (TypeError, ValueError):
            return None
        q = _norm(str(op.get("quote") or ""))
        if not m or not (QUOTE_MIN <= len(q) <= QUOTE_MAX) or q not in _norm(m["content"]):
            return None
        return m

    seen = {(e["code"], _norm(e["text"])) for e in existing.values()}
    for op in (out.get("notes") or [])[:MAX_NOTES_PER_CHUNK * 2]:
        if not isinstance(op, dict):
            dropped.append("格式不对")
            continue
        code, name = _code(op.get("code")), str(op.get("name") or "").strip()[:20]
        kind = str(op.get("kind") or "").strip()
        text = re.sub(r"\s+", " ", str(op.get("text") or "")).strip()[:TEXT_MAX]
        if not _CODE.match(code) or kind not in KINDS or not text:
            dropped.append(f"字段不全或格式不对: {code} {kind}")
            continue
        m = grounded(op)
        if not m:
            dropped.append(f"{code}: 引文不在 msg {op.get('msg')} 原文里")
            continue
        sess = session_text.get(m["session_id"], "")
        if code not in sess and (not name or name not in sess):
            dropped.append(f"{code}: 那次会话没有提到这只票")
            continue
        if _VOLATILE.search(text):
            dropped.append(f"{code}: 含行情数字")
            continue
        if (code, _norm(text)) in seen:
            dropped.append(f"{code}: 与已有条目相同")
            continue
        seen.add((code, _norm(text)))
        notes.append({"code": code, "name": name, "kind": kind, "text": text,
                      "quote": str(op.get("quote")).strip()[:QUOTE_MAX * 2], "msg_id": int(op["msg"]),
                      "session_id": m["session_id"], "said_on": m["date"]})
    for op in out.get("supersede") or []:
        try:
            eid = int(op.get("id"))
        except (TypeError, ValueError, AttributeError):
            dropped.append("supersede: 缺 id")
            continue
        if eid not in existing or not grounded(op):
            dropped.append(f"supersede: #{eid} 不存在或依据不成立")
            continue
        sups.append({"id": eid, "reason": f"{msgs[int(op['msg'])]['date']} 会话 "
                                          f"{msgs[int(op['msg'])]['session_id']}: 「{str(op.get('quote')).strip()}」"})
    return notes[:MAX_NOTES_PER_CHUNK], sups, dropped


def render_page(code: str, rows: list[dict]) -> str:
    """一只票的档案页: 按类成段, 每条带当时日期与出处会话。"""
    if not rows:
        return ""
    name = next((r["name"] for r in rows if r.get("name")), "")
    out = [f"# {name}({code}) 档案"]
    for k in KINDS:
        items = [r for r in rows if r["kind"] == k]
        if items:
            out.append(f"## {KIND_CN[k]}")
            out += [f"- {r['text']}  ({r['said_on']} · 会话 {r['session_id']} · #{r['id']})" for r in items]
    return "\n".join(out)


def render_index(rows: list[tuple]) -> str:
    """rows: [(code, name, n)] 按最近更新排。系统提示里只放这一行索引。"""
    if not rows:
        return ""
    clean = lambda t: re.sub(r"[【】\[\]\n]", "", t or "")[:20]
    names = "、".join(f"{clean(n) or c}({c})" for c, n, _ in rows[:INDEX_MAX])
    return ("\n【个股档案】以下票在过往问答里有整理过的档案(主营、产业链、关联、当时结论, 每条带日期): "
            f"{names}。问到其中某只时, 先用 get_stock_notes 读它的档案, 再用当下工具补行情与最新事实。")


# ── 读写库 ──────────────────────────────────────────

async def _conn():
    import aiosqlite
    return await aiosqlite.connect(um._db_path())


async def entries(code: str = "", status: str = "active") -> list[dict]:
    import aiosqlite
    async with aiosqlite.connect(um._db_path()) as db:
        db.row_factory = aiosqlite.Row
        sql, args = "SELECT * FROM stock_note WHERE status = ?", [status]
        if code:
            sql += " AND code = ?"
            args.append(code)
        cur = await db.execute(sql + " ORDER BY code, id", args)
        return [dict(r) for r in await cur.fetchall()]


def index_sync() -> list[tuple]:
    """给 _system() 用: [(code, name, 条数)], 最近更新的在前。读不到返回空。"""
    try:
        import sqlite3
        con = sqlite3.connect(um._db_path(), timeout=1.0)
        try:
            return con.execute(
                "SELECT code, MAX(name), COUNT(*) FROM stock_note WHERE status = 'active' "
                "GROUP BY code ORDER BY MAX(id) DESC").fetchall()
        finally:
            con.close()
    except Exception:
        return []


async def lookup(query: str) -> dict:
    """按代码或简称取档案页。"""
    q = str(query or "").strip()
    if not q:
        return {"error": "传代码或简称"}
    code = _code(q)
    rows = await entries(code) if _CODE.match(code) else []
    if not rows:
        all_rows = await entries()
        hit = sorted({r["code"] for r in all_rows if q in (r.get("name") or "")})
        if len(hit) > 1:
            return {"error": f"「{q}」对应多只: {', '.join(hit)}, 传代码"}
        rows = [r for r in all_rows if hit and r["code"] == hit[0]]
    if not rows:
        return {"code": code, "page": "", "note": "这只票还没有档案, 按常规用工具查。"}
    return {"code": rows[0]["code"], "n": len(rows), "page": render_page(rows[0]["code"], rows),
            "note": "档案是过往会话里的认识, 每条带当时日期; 引用结论时说明是哪天的判断, 行情与最新事实用当下工具获取。"}


async def delete_entry(eid: int) -> bool:
    db = await _conn()
    try:
        cur = await db.execute("UPDATE stock_note SET status = 'deleted' WHERE id = ? AND status = 'active'", (eid,))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def _save(notes: list[dict], sups: list[dict]) -> int:
    db = await _conn()
    try:
        for s in sups:
            await db.execute("UPDATE stock_note SET status = 'superseded', reason = ? WHERE id = ? "
                             "AND status = 'active'", (s["reason"][:300], s["id"]))
        for n in notes:
            await db.execute(
                "INSERT INTO stock_note (code, name, kind, text, quote, msg_id, session_id, said_on) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (n["code"], n["name"], n["kind"], n["text"], n["quote"], n["msg_id"], n["session_id"], n["said_on"]))
        # 每只票封顶: 超出的按最早的先作废, 页面保持可读
        for code in {n["code"] for n in notes}:
            cur = await db.execute("SELECT id FROM stock_note WHERE code = ? AND status = 'active' ORDER BY id DESC",
                                   (code,))
            extra = [r[0] for r in await cur.fetchall()][MAX_PER_STOCK:]
            for eid in extra:
                await db.execute("UPDATE stock_note SET status = 'superseded', reason = '超出单页上限' "
                                 "WHERE id = ?", (eid,))
        await db.commit()
        return len(notes)
    finally:
        await db.close()


async def run(max_chunks: int = MAX_CHUNKS) -> dict:
    """从水位接着读新会话, 提取档案条目。每块成功后推进水位。"""
    from services import llm_client
    st = {"chunks": 0, "messages": 0, "added": 0, "superseded": 0, "dropped": 0, "failed": 0,
          "reasons": [], "remaining": 0}
    if um._readonly():
        st["note"] = "评测模式, 不整理"
        return st
    last = int((await md._kv(WATERMARK_KEY)) or 0)
    msgs, titles = await md._load(last)
    if not msgs:
        st["note"] = "没有新消息"
        await _save_run(st)
        return st
    sess_text: dict[int, str] = {}
    for m in msgs:
        sess_text[m["session_id"]] = sess_text.get(m["session_id"], "") + "\n" + (m["content"] or "")
    chunks = md.build_chunks(msgs, titles, chunk_chars=CHUNK_CHARS, assistant_cap=ASSISTANT_CAP,
                             assistant_ids=True)
    for chunk in chunks[:max_chunks]:
        active = await entries()
        existing = {r["id"]: r for r in active}
        mentioned = [r for r in active if r["code"] in chunk["text"] or (r["name"] and r["name"] in chunk["text"])]
        prompt = ("【已有档案】(只列本批会话提到的票, 编号可用于 supersede)\n"
                  + ("\n".join(f"#{r['id']} {r['name']}({r['code']}) [{r['kind']}] {r['text']} ({r['said_on']})"
                               for r in mentioned) or "(暂无)")
                  + "\n\n【新会话】\n" + chunk["text"])
        try:
            text = await asyncio.to_thread(llm_client.call_claude, prompt, SYSTEM, MODEL, 6000)
            from services.rule_forge import _parse_any_json
            out = _parse_any_json(text or "")
            if out is None:
                raise ValueError("模型输出不是 JSON")
        except Exception as e:
            logger.warning("stock notes: 第 %d 块失败: %s", st["chunks"] + 1, e)
            st["failed"] += 1
            break
        in_chunk = {m["id"]: {"content": m["content"] or "", "session_id": m["session_id"],
                              "date": str(m["created_at"] or "")[:10]}
                    for m in msgs if m["id"] in chunk["msg_ids"]}
        notes, sups, dropped = validate(out, in_chunk, sess_text, existing)
        st["dropped"] += len(dropped)
        st["reasons"] += dropped[:5]
        st["added"] += await _save(notes, sups)
        st["superseded"] += len(sups)
        st["chunks"] += 1
        st["messages"] += len(chunk["msg_ids"])
        await md._kv(WATERMARK_KEY, str(chunk["last_id"]))
    st["remaining"] = max(0, len(chunks) - st["chunks"])
    await _save_run(st)
    return st


async def _save_run(st: dict) -> None:
    from datetime import datetime, timedelta, timezone
    now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    await md._kv(LAST_RUN_KEY, json.dumps({**st, "at": now}, ensure_ascii=False))


async def last_run() -> dict | None:
    raw = await md._kv(LAST_RUN_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


TOOL = {
    "name": "get_stock_notes",
    "description": "读某只票的个股档案: 过往问答里整理过的主营与赚钱方式、产业链位置、关联关系、带日期的重大事件、"
                   "当时的分析结论、用户对它的看法与安排, 每条带当时日期与出处会话。问到【个股档案】索引里的票时先读它,"
                   " 再用当下工具补行情与最新事实; 引用档案里的结论时说明是哪天的判断。传代码或简称。",
    "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
}
