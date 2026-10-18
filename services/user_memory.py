"""用户画像记忆: 用户亲口说过、跨会话都成立的事实, 每次问答都带上。

为什么要: 历史只带本会话最近 8 条, 换个会话就忘。实测同一件事要反复交代 ——
用户在一次会话里说明"场外扣款都是定投"之后, 新会话里仍把定投扣款当主动操作分析。

和 rule_forge 的分工: rule_forge 沉淀的是"agent 该怎么答"的行为规则, 由模型起草, 人批准才
生效; 这里记的是"用户是什么情况"的事实, 来源是用户原话, 所以直接生效 —— 但必须看得见、
删得掉(设置页 /api/settings/memory)。条数封顶, 防止越记越长挤占上下文。
"""
from __future__ import annotations
import os

MAX_ACTIVE = 40
MAX_FACT_CHARS = 160

TOOL = {
    "name": "remember",
    "description": "记下用户亲口说过、跨会话长期成立的个人事实或偏好(账户结构、哪些是定投、关注的打法、"
                   "对呈现方式的要求), 之后每次问答都会带上。fact 写成一句第三人称陈述"
                   "(如'用户的场外基金扣款均为定投, 不属于主动操作')。与【用户画像】里已有条目冲突或需更新时,"
                   " 传 replace_id 覆盖那一条; fact 传空且带 replace_id 表示删除该条。",
    "input_schema": {"type": "object", "properties": {
        "fact": {"type": "string"}, "replace_id": {"type": "integer"}}, "required": ["fact"]},
}


def _readonly() -> bool:
    # 评测时开: 不然每跑一次都往演示账本里写记忆, 下一次评测的 prompt 就变了
    return os.environ.get("AGENT_MEMORY_READONLY", "").lower() in ("1", "true", "yes")


def _db_path() -> str:
    from config import config as _cfg
    return _cfg.db_path


def active_sync() -> list[dict]:
    """给 _system() 用的同步读取, 读不到返回空(表没建/库被占用不该让 agent 起不来)。"""
    try:
        import sqlite3
        con = sqlite3.connect(_db_path(), timeout=1.0)
        try:
            rows = con.execute("SELECT id, fact FROM user_memory WHERE status = 'active' "
                               "ORDER BY id").fetchall()
        finally:
            con.close()
        return [{"id": i, "fact": f} for i, f in rows]
    except Exception:
        return []


def render(items: list[dict]) -> str:
    if not items:
        return ""
    lines = "".join(f"- [{m['id']}] {m['fact']}\n" for m in items)
    return "\n【用户画像·用户亲口说过的长期事实, 作答时直接采用】\n" + lines


async def remember(args: dict, session_id: int | None = None) -> dict:
    import aiosqlite
    fact = (args.get("fact") or "").strip()[:MAX_FACT_CHARS]
    rid = args.get("replace_id")
    if not fact and not rid:
        return {"error": "fact 为空"}
    if _readonly():
        return {"ok": True, "note": "评测模式, 未落库"}
    async with aiosqlite.connect(_db_path()) as db:
        if rid:
            cur = await db.execute("UPDATE user_memory SET status = 'replaced' "
                                   "WHERE id = ? AND status = 'active'", (int(rid),))
            if cur.rowcount == 0:
                return {"error": f"没有生效中的 #{rid}"}
        if not fact:
            await db.commit()
            return {"ok": True, "deleted": int(rid)}
        cur = await db.execute("SELECT COUNT(*) FROM user_memory WHERE status = 'active'")
        if (await cur.fetchone())[0] >= MAX_ACTIVE:
            await db.rollback()
            return {"error": f"画像已满 {MAX_ACTIVE} 条, 先用 replace_id 合并或删除旧条目"}
        cur = await db.execute("INSERT INTO user_memory (fact, session_id, replaces) VALUES (?, ?, ?)",
                               (fact, session_id, int(rid) if rid else None))
        await db.commit()
        return {"ok": True, "id": cur.lastrowid}


async def list_all() -> list[dict]:
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM user_memory ORDER BY id DESC")
        return [dict(r) for r in await cur.fetchall()]


async def delete(mid: int) -> bool:
    import aiosqlite
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute("UPDATE user_memory SET status = 'deleted' "
                               "WHERE id = ? AND status = 'active'", (mid,))
        await db.commit()
        return cur.rowcount > 0
