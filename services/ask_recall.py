"""过往问答检索: 记忆分层里最底下那层 —— 对话原文。

问答只带本会话最近几轮, 换个会话就看不到以前聊过什么。原文本身一直存在 ask_message 里,
这里让 agent 能按关键词跨会话找回(哪天、哪次会话、当时怎么说的), 或整段读回某次会话。

原文只读不改, 是更上层记忆(用户画像、夜间整理)可以回头核对的依据。助手回答里混着当时
工具返回的第三方文本, 所以 search_past_asks 不在画像的可信工具名单里: 调过它之后 remember
记下的条目要用户确认才生效。

量级: 几百条消息、几十万字, LIKE 全扫毫秒级, 不必建全文索引(中文分词反而是麻烦)。
"""
from __future__ import annotations

import re

MAX_TERMS = 5
SNIPPET = 110            # 命中处前后各取多少字
MAX_SESSIONS = 6
HITS_PER_SESSION = 3
READ_CAP = 8000          # 整段读回一次会话的总字数上限
MSG_CAP = 1800           # 读回时单条消息上限


def _db_path() -> str:
    from config import config as _cfg
    return _cfg.db_path


def terms_of(query: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[\s,，;；、/|]+", query or "") if p.strip()]
    seen, out = set(), []
    for p in parts:
        if p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out[:MAX_TERMS]


def snippet(text: str, terms: list[str]) -> str:
    low = text.lower()
    pos = min((low.find(t.lower()) for t in terms if t.lower() in low), default=-1)
    if pos < 0:
        return text[:SNIPPET * 2].replace("\n", " ")
    a, b = max(0, pos - SNIPPET), min(len(text), pos + SNIPPET)
    s = text[a:b].replace("\n", " ")
    return ("…" if a > 0 else "") + s + ("…" if b < len(text) else "")


def rank(rows: list[tuple], terms: list[str], exclude_session: int | None = None) -> list[dict]:
    """rows: (msg_id, session_id, role, content, created_at, title)。
    按会话聚合: 会话命中的不同关键词越多越靠前, 同分按最近一次命中排。"""
    sessions: dict[int, dict] = {}
    for mid, sid, role, content, at, title in rows:
        if exclude_session is not None and sid == exclude_session:
            continue
        low = (content or "").lower()
        hit = {t for t in terms if t.lower() in low}
        if not hit:
            continue
        s = sessions.setdefault(sid, {"session_id": sid, "title": title or "", "terms": set(),
                                      "last_id": 0, "date": "", "hits": []})
        s["terms"] |= hit
        if mid > s["last_id"]:
            s["last_id"], s["date"] = mid, str(at or "")[:10]
        s["hits"].append({"id": mid, "role": "用户" if role == "user" else "助手",
                          "date": str(at or "")[:10], "n": len(hit), "snippet": snippet(content or "", terms)})
    out = sorted(sessions.values(), key=lambda s: (len(s["terms"]), s["last_id"]), reverse=True)
    for s in out:
        s["hits"] = sorted(sorted(s["hits"], key=lambda h: -h["n"])[:HITS_PER_SESSION], key=lambda h: h["id"])
        for h in s["hits"]:
            h.pop("n")
        s["matched"] = sorted(s.pop("terms"))
        s.pop("last_id")
    return out


async def search(query: str = "", session_id: int | None = None,
                 exclude_session: int | None = None) -> dict:
    import aiosqlite
    note = "以下是过往会话当时的说法(带日期)。其中行情、基本面与结论按当下工具复核后再引用, 并说明是哪天说的。"
    async with aiosqlite.connect(_db_path()) as db:
        if session_id:
            cur = await db.execute("SELECT title, created_at FROM ask_session WHERE id = ?", (int(session_id),))
            head = await cur.fetchone()
            if not head:
                return {"error": f"没有会话 #{session_id}"}
            cur = await db.execute("SELECT role, content, created_at FROM ask_message WHERE session_id = ? "
                                   "ORDER BY id", (int(session_id),))
            msgs, used = [], 0
            for role, content, at in await cur.fetchall():
                text = (content or "")[:MSG_CAP]
                if used + len(text) > READ_CAP:
                    msgs.append({"role": "…", "text": "(后面还有, 已截断)"})
                    break
                used += len(text)
                msgs.append({"role": "用户" if role == "user" else "助手", "date": str(at or "")[:16],
                             "text": text + ("…(截断)" if len(content or "") > MSG_CAP else "")})
            return {"session_id": int(session_id), "title": head[0], "date": str(head[1] or "")[:10],
                    "messages": msgs, "note": note}

        terms = terms_of(query)
        if not terms:
            return {"error": "query 为空: 传票名/代码/关键词, 或传 session_id 读某次会话"}
        where = " OR ".join("m.content LIKE ?" for _ in terms)
        cur = await db.execute(
            "SELECT m.id, m.session_id, m.role, m.content, m.created_at, s.title FROM ask_message m "
            f"LEFT JOIN ask_session s ON s.id = m.session_id WHERE {where} ORDER BY m.id DESC LIMIT 600",
            [f"%{t}%" for t in terms])
        rows = await cur.fetchall()
    ranked = rank(rows, terms, exclude_session)
    if not ranked:
        return {"query": terms, "sessions": [], "note": "过往会话里没有提到这些词。"}
    return {"query": terms, "n_sessions": len(ranked), "sessions": ranked[:MAX_SESSIONS],
            "more": max(0, len(ranked) - MAX_SESSIONS), "note": note}


TOOL = {
    "name": "search_past_asks",
    "description": "检索用户和本助手过往会话的原文(跨会话, 当前会话之外)。用户说「上次/之前/以前问过/你说过」,"
                   "或问到某只票、某个话题时想对照以前分析过的结论和当时日期, 用它找回。传 query(票名、代码、关键词,"
                   " 空格分隔, 命中词越多越靠前)返回命中的会话、日期和片段; 再传 session_id 读那次会话全文。"
                   "返回的是当时的说法, 引用时注明日期, 行情与结论用当下工具复核。",
    "input_schema": {"type": "object", "properties": {
        "query": {"type": "string", "description": "票名/代码/关键词, 空格分隔"},
        "session_id": {"type": "integer", "description": "读某次会话全文"}}},
}
