"""从"用户纠正"里沉淀候选规则 —— 半自动, 人批准才生效。

背景: 近三个月 stock_agent.py 上带 fix/漏/错/凭 字样的提交 41 个, prompt 里 56 条
【】规则每条都是某次翻车留下的疤。这个循环一直在跑, 只是变异算子是人。这里把"提案"
自动化, 闸门留给人。

为什么坚持要闸门:
  · prompt 已有 56 条规则, 自动往里加最容易造成规则互撞
  · prompt 对单个词都敏感 —— 曾因中文 label 词表缺"主题/标题"两个词, 模型就把首句吞了
  · 一条坏规则会静默影响所有回答, 而回归评测只有 8 例, 兜不住

所以: 自动挖掘 + 自动起草 → 落进 prompt_rule 表 status=pending → 人过一眼 →
approve 才进 prompt。pending 的一律不进。
"""
from __future__ import annotations
import json
import re

# 用户在"纠正"时的说法。取自这个项目里真实出现过的纠正原话:
#   "现在不是涨, 是跌" / "怎么还是说大跌" / "怎么都没分析出来" / "海外的成交额真的拿不到吗"
#   "什么叫那天" / "全是乱码" / "点了一下保存选择就崩了"
_CORRECTION = (
    "不是", "不对", "错了", "搞错", "反了", "怎么还", "怎么又", "怎么都", "为什么还",
    "漏", "少了", "没分析", "分析不出", "拿不到", "取不到", "乱码", "崩了", "白屏",
    "什么叫", "看不清", "重合", "整坏", "没生效", "还是旧",
)
# 这些是正常追问/指令, 不是纠正 —— 不排除的话每次"推"都会被当成一次纠正
_NOT_CORRECTION = ("推", "发", "加", "做", "接着做", "继续", "行", "好", "可以", "嗯")


def looks_like_correction(text: str) -> bool:
    """这句用户消息是不是在纠正上一条回答。"""
    t = (text or "").strip()
    if not t or len(t) <= 4 and t in _NOT_CORRECTION:
        return False
    if t in _NOT_CORRECTION:
        return False
    return any(w in t for w in _CORRECTION)


def mine_corrections(rows: list[dict], max_items: int = 20) -> list[dict]:
    """从会话消息里找出 (问题, 回答, 纠正) 三元组。

    rows: 按 (session_id, id) 升序的 [{session_id, role, content}]。
    只取"紧跟在 assistant 之后、且带纠正措辞"的 user 消息 —— 会话开头的第一句不算
    (没有可纠正的对象)。
    """
    out: list[dict] = []
    by_sess: dict = {}
    for r in rows:
        by_sess.setdefault(r.get("session_id"), []).append(r)
    for sid, msgs in by_sess.items():
        for i, m in enumerate(msgs):
            if m.get("role") != "user" or i == 0:
                continue
            prev = msgs[i - 1]
            if prev.get("role") != "assistant":
                continue
            if not looks_like_correction(m.get("content")):
                continue
            asked = ""
            for j in range(i - 2, -1, -1):
                if msgs[j].get("role") == "user":
                    asked = msgs[j].get("content") or ""
                    break
            out.append({
                "session_id": sid,
                "question": asked[:300],
                "answer": (prev.get("content") or "")[:1200],
                "correction": (m.get("content") or "")[:300],
            })
    return out[-max_items:]


_DRAFT_SYSTEM_BASE = (
    "你在维护一个理财分析 agent 的 system prompt。它的规则写成【标题】正文 的形式, "
    "一律用正向陈述(说该怎么做, 而不是列举不许做什么), 每条一句到两句, 带上判据。\n"
    "我给你一次真实的'用户纠正'记录。请判断它是否指向一条可以固化的通用规则:\n"
    "· 指向数据源缺失、代码 bug、环境问题(如进程没重启)的, 不是规则问题 —— 回 skip\n"
    "· 只针对这一只票/这一天的偶发事实, 不够通用 —— 回 skip\n"
    "· 已被现有规则覆盖的 —— 回 skip, 并在 why 里点出是哪一条\n"
    "· 确实是'表述方式/取数顺序/口径'这类下次还会犯的, 起草一条规则\n"
    "只输出 JSON, 单行, 不要前言: {\"verdict\":\"rule\"|\"skip\", \"title\":\"…\", "
    "\"body\":\"…\", \"why\":\"一句话说明为什么(或为什么 skip)\"}\n"
    "JSON 字符串内部需要引用时用「」, 不要出现英文双引号 —— 未转义会让整段解析失败。"
)


def existing_titles() -> list[str]:
    """现有 system prompt 里的规则标题。起草时给模型看, 免得提重复的。

    实测不给会怎样: 它提了一条"判断涨停前先核实板块涨跌幅限制", 而 prompt 里早有
    【涨停跌停按该股真实幅度判】。
    """
    try:
        from services.stock_agent import _SYSTEM
        return sorted(set(re.findall(r"【([^】]{2,24})】", _SYSTEM)))
    except Exception:
        return []


def draft_system() -> str:
    titles = existing_titles()
    if not titles:
        return _DRAFT_SYSTEM_BASE
    return (_DRAFT_SYSTEM_BASE + "\n现有规则标题(共 " + str(len(titles)) + " 条), "
            "语义已被覆盖的一律 skip:\n" + " / ".join(titles))


def draft_prompt(item: dict) -> str:
    return (f"用户当时问: {item['question']}\n\n"
            f"agent 的回答(截断): {item['answer']}\n\n"
            f"用户的纠正原话: {item['correction']}")


def parse_draft(text: str) -> dict | None:
    """从模型输出里抠出裁定。

    两层: 先按花括号配对抠 JSON(thinking 类模型会在 JSON 前后夹带散文); 严格 JSON 解不出
    时再按字段名正则兜底 —— 实测模型会在中文正文里写未转义的英文双引号(如 是"放量"还),
    这种 json.loads 一定失败, 而内容其实是好的。靠 prompt 约束挡不干净, 得能兜住。
    """
    if not text:
        return None
    strict = _parse_json(text)
    if strict:
        return strict
    return _parse_loose(text)


def _parse_loose(text: str) -> dict | None:
    v = re.search(r'"verdict"\s*:\s*"(rule|skip)"', text)
    if not v:
        return None
    out = {"verdict": v.group(1)}
    for key in ("title", "body", "why"):
        # 取到下一个 ", "<字段名>": 之前, 或到对象结束 —— 不要求内部引号合规
        m = re.search(rf'"{key}"\s*:\s*"(.*?)"\s*(?:,\s*"(?:verdict|title|body|why)"|\}})',
                      text, re.S)
        if m:
            out[key] = m.group(1).strip()
    return out


def _parse_json(text: str) -> dict | None:
    start = text.find("{")
    while start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        d = json.loads(text[start:i + 1])
                        if isinstance(d, dict) and "verdict" in d:
                            return d
                    except json.JSONDecodeError:
                        pass
                    break
        start = text.find("{", start + 1)
    return None


def render_rules(rules: list[dict]) -> str:
    """已批准的规则渲染成 prompt 尾部的一段。空列表返回空串(不留痕)。"""
    live = [r for r in rules if (r.get("title") or "").strip() and (r.get("body") or "").strip()]
    if not live:
        return ""
    lines = "".join(f"【{r['title'].strip('【】')}】{r['body'].strip()}\n" for r in live)
    return "\n【以下规则由历次纠正沉淀而来】\n" + lines


# ── DB ──────────────────────────────────────────────────

async def _conn():
    import aiosqlite
    from config import config as _cfg
    return await aiosqlite.connect(_cfg.db_path)


async def fetch_messages() -> list[dict]:
    db = await _conn()
    try:
        cur = await db.execute(
            "SELECT session_id, role, content FROM ask_message ORDER BY session_id, id")
        return [{"session_id": r[0], "role": r[1], "content": r[2]} for r in await cur.fetchall()]
    finally:
        await db.close()


async def add_candidate(title: str, body: str, evidence: str, session_id=None) -> bool:
    """插入候选。同标题已存在(任何状态)就不重复插 —— 免得同一类纠正反复提案。"""
    db = await _conn()
    try:
        cur = await db.execute("SELECT 1 FROM prompt_rule WHERE title = ?", (title,))
        if await cur.fetchone():
            return False
        await db.execute(
            "INSERT INTO prompt_rule (title, body, evidence, session_id) VALUES (?, ?, ?, ?)",
            (title, body, evidence[:500], session_id))
        await db.commit()
        return True
    finally:
        await db.close()


async def list_rules(status: str = "") -> list[dict]:
    db = await _conn()
    try:
        sql = ("SELECT id, title, body, status, evidence, created_at FROM prompt_rule"
               + (" WHERE status = ?" if status else "") + " ORDER BY id")
        cur = await db.execute(sql, (status,) if status else ())
        cols = ("id", "title", "body", "status", "evidence", "created_at")
        return [dict(zip(cols, r)) for r in await cur.fetchall()]
    finally:
        await db.close()


async def decide(rule_id: int, status: str) -> bool:
    db = await _conn()
    try:
        cur = await db.execute(
            "UPDATE prompt_rule SET status = ?, decided_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status, rule_id))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


def active_rules_sync() -> list[dict]:
    """给 _system() 用的同步读取。prompt 每轮都要拼, 不能在这儿开 async。

    读不到就返回空 —— 表还没建、库被占用都不该让整个 agent 起不来。
    """
    try:
        import sqlite3
        from config import config as _cfg
        con = sqlite3.connect(_cfg.db_path, timeout=1.0)
        try:
            rows = con.execute("SELECT title, body FROM prompt_rule "
                               "WHERE status = 'active' ORDER BY id").fetchall()
        finally:
            con.close()
        return [{"title": t, "body": b} for t, b in rows]
    except Exception:
        return []
