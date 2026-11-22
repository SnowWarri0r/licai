"""夜间整理(dreaming): 回看新会话的原文, 维护用户画像。

画像(user_memory)每次问答都注入, 是记忆分层里最贵、也最怕错的一层 —— 一条错的画像会被带进
之后每一次会话。对话中靠 agent 当场调用 remember 来记, 实际很少触发; 整理放到收盘后离线做,
有完整原文可以回头核对, 也不占问答时的注意力。

一次整理做三件事, 全部产出为待确认提案(设置页确认后才生效):
  add     新会话里用户交代的长期事实
  update  用户的新说法更新或推翻了某条已有画像(给出新表述); fact 为空即建议删除
  merge   已有画像里说同一件事的几条合成一条
  confirm 用户重申某条阶段性状态仍成立: 把它的日期推到那条消息的日期(只改日期不改内容, 直接生效)

阶段性状态(资金是否充裕、近期仓位安排)带日期记录, 超过 user_memory.REVIEW_DAYS 天没再确认就标为待复核。

依据用代码核对, 不靠模型自觉: add/update 引用的原话必须逐字出现在所给编号的那条**用户**消息里
(助手回答里混着第三方工具文本, 不能作为画像依据); 引用的已有画像编号必须是生效中的。
核对不过的提案直接丢弃并计数。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timedelta, timezone

from services import user_memory as um

logger = logging.getLogger(__name__)

WATERMARK_KEY = "memory_dream_last_id"
LAST_RUN_KEY = "memory_dream_last_run"
CHUNK_CHARS = 30000       # 一次模型调用喂多少原文
MAX_CHUNKS = 3            # 一次整理最多调几次模型, 剩下的下次接着
MAX_OPS = 8               # 一次调用最多收几条提案: 待确认队列一次涌进太多没人看
ASSISTANT_HEAD = 220      # 助手回答只截开头作上下文
USER_CAP = 600
QUOTE_MIN, QUOTE_MAX = 4, 80
MODEL = "smart"

SYSTEM = (
    "你在为一个理财分析助手整理「用户画像」。画像会注入之后的每一次问答, 因此只收录同时满足两点的内容: "
    "下一次会话很可能还会用到; 工具当场查不到。符合的典型内容: 账户与资金结构(哪些是定投、哪些是长期仓、资金来源与用途)、"
    "投资习惯与约束、对回答呈现方式的明确要求、用户对自身情况的说明。"
    "行情、持仓数量与成本、某天对某只票的看法属于会变化或工具能查到的内容, 由工具在作答时获取。\n"
    "每收录一条, 都是在预测它以后会被用到; 错误的一条会影响之后所有回答, 因此宁缺毋滥, 没有合格内容时返回空列表。\n\n"
    "任务:\n"
    "1. add: 从【新会话】的用户消息中提取符合标准的事实。\n"
    "2. update: 用户的新说法更新或推翻了【已有画像】中的某一条时, 给出该条的新表述; 用户明确表示某条已不成立时, fact 填空字符串表示删除。\n"
    "3. merge: 【已有画像】中表达同一件事的多条, 合成一条。\n"
    "依据: add 与 update 必须给出用户消息编号 msg, 以及从该条用户消息中逐字复制的原话 quote(4 到 80 字)。"
    "merge 的依据就是被合并的那几条画像。\n"
    "4. confirm: 用户在新会话里重申【已有画像】中某条标了「阶段性」的状态仍然成立时, 给出该条编号与依据, 系统把它的日期更新到那条消息的日期。\n"
    "fact 写成一句第三人称陈述, 以「用户」开头, 160 字以内, 只陈述事实。"
    "会随时间变化的阶段性状态(资金是否充裕、近期的仓位安排)在 add/update 中加 \"phase\": true, 系统按引用消息的日期记录时点并到期提醒复核。\n"
    "只输出一行 JSON: {\"ops\": [{\"op\": \"add\", \"fact\": \"…\", \"msg\": 编号, \"quote\": \"…\", \"phase\": false}, "
    "{\"op\": \"update\", \"id\": 画像编号, \"fact\": \"…\", \"msg\": 编号, \"quote\": \"…\"}, "
    "{\"op\": \"merge\", \"ids\": [画像编号, …], \"fact\": \"…\"}, "
    "{\"op\": \"confirm\", \"id\": 画像编号, \"msg\": 编号, \"quote\": \"…\"}]}。"
    "JSON 字符串内部需要引号时用「」。"
)


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "")


# ── 纯函数部分(可单测) ──────────────────────────────

def build_chunks(msgs: list[dict], titles: dict[int, str] | None = None, *,
                 chunk_chars: int | None = None, assistant_cap: int = ASSISTANT_HEAD,
                 assistant_ids: bool = False) -> list[dict]:
    """msgs: [{id, session_id, role, content, created_at}] 按 id 升序。按会话成段, 再按字数切块。
    返回 [{"text", "last_id", "user_ids", "msg_ids"}]; 一个会话不跨块(除非它自己就超长)。
    assistant_ids=True 时助手消息也带编号(个股档案要引用助手当时的结论)。"""
    titles = titles or {}
    limit = chunk_chars or CHUNK_CHARS
    by_sess: dict[int, list[dict]] = {}
    for m in msgs:
        by_sess.setdefault(m["session_id"], []).append(m)
    chunks, cur, size, last, uids, mids = [], [], 0, 0, set(), set()
    for sid in sorted(by_sess, key=lambda s: by_sess[s][0]["id"]):
        lines = [f"## 会话 {sid} {titles.get(sid, '')}".rstrip()]
        for m in by_sess[sid]:
            day = str(m.get("created_at") or "")[:10]
            text = re.sub(r"\s+", " ", m.get("content") or "").strip()
            if m["role"] == "user":
                lines.append(f"[msg {m['id']} · {day} · 用户] {text[:USER_CAP]}")
            elif m["role"] == "assistant":
                tag = f"[msg {m['id']} · {day} · 助手]" if assistant_ids else "[助手]"
                lines.append(f"{tag} {text[:assistant_cap]}{'…' if len(text) > assistant_cap else ''}")
        block = "\n".join(lines) + "\n"
        if cur and size + len(block) > limit:
            chunks.append({"text": "".join(cur), "last_id": last, "user_ids": uids, "msg_ids": mids})
            cur, size, uids, mids = [], 0, set(), set()
        cur.append(block)
        size += len(block)
        last = max(last, max(m["id"] for m in by_sess[sid]))
        uids |= {m["id"] for m in by_sess[sid] if m["role"] == "user"}
        mids |= {m["id"] for m in by_sess[sid]}
    if cur:
        chunks.append({"text": "".join(cur), "last_id": last, "user_ids": uids, "msg_ids": mids})
    return chunks


def render_memory(items: list[dict]) -> str:
    if not items:
        return "(暂无)"
    def tag(m):
        if not int(m.get("review_days") or 0):
            return ""
        return f" [阶段性, 截至 {m.get('as_of') or '?'}]"
    return "\n".join(f"#{m['id']} {m['fact']}{tag(m)}" for m in items)


def validate(ops, user_msgs: dict[int, dict], active: dict[int, str],
             meta: dict[int, dict] | None = None) -> tuple[list[dict], list[str]]:
    """核对模型提出的每条操作。user_msgs: {msg_id: {"content", "session_id", "date"}} —— 只含本块的用户消息;
    active: {画像id: fact}; meta: {画像id: {"as_of", "review_days"}}。返回 (通过的操作, 丢弃原因)。
    confirm 通过的形如 {"confirm": id, "as_of": 日期}, 其余是待确认提案。"""
    meta = meta or {}
    is_phase = lambda i: bool(int((meta.get(i) or {}).get("review_days") or 0))
    ok, dropped = [], []
    if not isinstance(ops, list):
        return ok, ["ops 不是列表"]
    for op in ops[:MAX_OPS * 2]:
        if not isinstance(op, dict):
            dropped.append("格式不对")
            continue
        kind = op.get("op")
        fact = um._clean(str(op.get("fact") or ""))
        if kind in ("add", "update", "confirm"):
            try:
                mid = int(op.get("msg"))
            except (TypeError, ValueError):
                dropped.append(f"{kind}: 缺 msg")
                continue
            msg = user_msgs.get(mid)
            quote = str(op.get("quote") or "").strip()
            if not msg:
                dropped.append(f"{kind}: msg {mid} 不是本批的用户消息")
                continue
            if not (QUOTE_MIN <= len(_norm(quote)) <= QUOTE_MAX) or _norm(quote) not in _norm(msg["content"]):
                dropped.append(f"{kind}: 引文不在 msg {mid} 原话里")
                continue
            note = f"{msg['date']} 会话 {msg['session_id']} 用户原话: 「{quote}」"
            if kind == "confirm":
                try:
                    rid = int(op.get("id"))
                except (TypeError, ValueError):
                    dropped.append("confirm: 缺 id")
                    continue
                if rid not in active or not is_phase(rid):
                    dropped.append(f"confirm: #{rid} 不是生效中的阶段性画像")
                    continue
                ok.append({"confirm": rid, "as_of": msg["date"]})
                continue
            if kind == "add":
                if not fact:
                    dropped.append("add: fact 为空")
                    continue
                if any(_norm(fact) == _norm(f) for f in active.values()):
                    dropped.append("add: 与已有画像相同")
                    continue
                ok.append({"fact": fact, "replaces": None, "also": [], "note": note,
                           "session_id": msg["session_id"], "phase": bool(op.get("phase")),
                           "as_of": msg["date"]})
            else:
                try:
                    rid = int(op.get("id"))
                except (TypeError, ValueError):
                    dropped.append("update: 缺 id")
                    continue
                if rid not in active:
                    dropped.append(f"update: #{rid} 不是生效中的画像")
                    continue
                if fact and _norm(fact) == _norm(active[rid]):
                    dropped.append(f"update: #{rid} 没有变化")
                    continue
                ok.append({"fact": fact, "replaces": rid, "also": [], "session_id": msg["session_id"],
                           "note": ("建议删除。" if not fact else "建议改写。") + note,
                           "phase": bool(op.get("phase")) or is_phase(rid), "as_of": msg["date"]})
        elif kind == "merge":
            try:
                ids = sorted({int(i) for i in op.get("ids") or []})
            except (TypeError, ValueError):
                dropped.append("merge: ids 格式不对")
                continue
            if len(ids) < 2 or any(i not in active for i in ids) or not fact:
                dropped.append("merge: 至少两条生效中的画像且给出合并后表述")
                continue
            phased = [i for i in ids if is_phase(i)]
            ok.append({"fact": fact, "replaces": ids[0], "also": ids[1:], "session_id": None,
                       "note": "合并 " + "、".join(f"#{i}" for i in ids) + " 为一条。",
                       "phase": bool(phased),     # 合并后的日期取几条里最早的, 宁可早到期
                       "as_of": min((str(meta[i].get("as_of") or "") for i in phased), default="")})
        else:
            dropped.append(f"未知操作 {kind!r}")
    return ok[:MAX_OPS], dropped


def parse(text: str):
    from services.rule_forge import _parse_any_json
    d = _parse_any_json(text or "")
    return (d or {}).get("ops") if isinstance(d, dict) else None


# ── 读写库 ──────────────────────────────────────────

async def _kv(key: str, value: str | None = None) -> str:
    from services.rule_forge import _kv as kv
    return await kv(key, value)


async def _load(after_id: int) -> tuple[list[dict], dict[int, str]]:
    import aiosqlite
    async with aiosqlite.connect(um._db_path()) as db:
        cur = await db.execute("SELECT id, session_id, role, content, created_at FROM ask_message "
                               "WHERE id > ? AND role IN ('user', 'assistant') ORDER BY id", (after_id,))
        msgs = [{"id": r[0], "session_id": r[1], "role": r[2], "content": r[3], "created_at": r[4]}
                for r in await cur.fetchall()]
        sids = sorted({m["session_id"] for m in msgs})
        titles = {}
        if sids:
            cur = await db.execute(f"SELECT id, title FROM ask_session WHERE id IN ({','.join('?' * len(sids))})",
                                   sids)
            titles = {r[0]: (r[1] or "")[:40] for r in await cur.fetchall()}
    return msgs, titles


async def run(max_chunks: int = MAX_CHUNKS) -> dict:
    """整理一次。从上次的水位接着读新消息; 每块成功后才推进水位, 失败的块下次重读。"""
    from services import llm_client
    st = {"chunks": 0, "messages": 0, "proposed": 0, "confirmed": 0, "dropped": 0, "dup": 0, "failed": 0,
          "reasons": []}
    if um._readonly():
        st["note"] = "评测模式, 不整理"
        return st
    last = int((await _kv(WATERMARK_KEY)) or 0)
    msgs, titles = await _load(last)
    if not any(m["role"] == "user" for m in msgs):
        st["note"] = "没有新的用户消息"
        await _save_run(st)
        return st
    for chunk in build_chunks(msgs, titles)[:max_chunks]:
        items = await um.list_all()
        live = sorted((m for m in items if m["status"] == "active"), key=lambda m: m["id"])
        active = {m["id"]: m["fact"] for m in live}
        meta = {m["id"]: m for m in live}
        pending = [m for m in items if m["status"] == "pending"]
        prompt = ("【已有画像】(生效中)\n" + render_memory(live)
                  + "\n\n【待确认中的提案】(已提过, 不必重复)\n"
                  + render_memory([m for m in pending if m["fact"]])
                  + "\n\n【新会话】\n" + chunk["text"])
        try:
            text = await asyncio.to_thread(llm_client.call_claude, prompt, SYSTEM, MODEL, 3000)
            ops = parse(text)
            if ops is None:
                raise ValueError("模型输出不是 JSON")
        except Exception as e:
            logger.warning("memory dream: 第 %d 块失败: %s", st["chunks"] + 1, e)
            st["failed"] += 1
            break
        user_msgs = {m["id"]: {"content": m["content"], "session_id": m["session_id"],
                               "date": str(m["created_at"] or "")[:10]}
                     for m in msgs if m["id"] in chunk["user_ids"]}
        good, dropped = validate(ops, user_msgs, active, meta)
        st["dropped"] += len(dropped)
        st["reasons"] += dropped[:5]
        for g in good:
            if "confirm" in g:
                st["confirmed"] += int(await um.confirm(g["confirm"], g["as_of"]))
                continue
            rid = await um.propose(g["fact"], replaces=g["replaces"], also_replaces=g["also"],
                                   note=g["note"], session_id=g["session_id"],
                                   phase=g["phase"], as_of=g["as_of"])
            if rid:
                st["proposed"] += 1
            else:
                st["dup"] += 1
        st["chunks"] += 1
        st["messages"] += sum(1 for m in msgs if last < m["id"] <= chunk["last_id"])
        last = chunk["last_id"]
        await _kv(WATERMARK_KEY, str(last))
    await _save_run(st)
    return st


async def _save_run(st: dict) -> None:
    now = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    await _kv(LAST_RUN_KEY, json.dumps({**st, "at": now}, ensure_ascii=False))


async def last_run() -> dict | None:
    raw = await _kv(LAST_RUN_KEY)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None
