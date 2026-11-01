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

编译成检查(2026-10): 光写成提示词, 规则会被跳过 —— 你的纠正里一大类是"该查的没查",
而 prompt 里早有对应规则。TRACE(arXiv 2606.13174)的做法是把纠正编译成"出答案前必须
通过的检查", 同一批偏好的遵守率从 55%(全部规则文字)升到 70%。这里起草时顺带判断能否
写成机器判据(问题关键词 + 必调工具 / 答案正则), 能写就编译, 再拿原纠正那一轮回放:
原来那版错答案必须被它拦下, 否则检查作废只留文字。历史回答里会误触发几条也一并算出来,
批准时一起看。批准后由 answer_guard 的 R8 在出答案前执行。
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
    # 2026-10 补: 读错意图/该查没查的说法, 取自真实对话
    "我说的是", "我问的是", "明明", "你看了", "你看我的", "你查我", "你查一下", "不用管",
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


def _meta_tools(msg: dict):
    """assistant 消息落库时记下的工具序列; 早期消息没有 meta, 返回 None(区别于"一个没调")。"""
    try:
        t = json.loads(msg.get("meta") or "{}").get("tools_used")
        return list(t) if isinstance(t, list) else None
    except (ValueError, AttributeError):
        return None


def followups(rows: list[dict]) -> list[dict]:
    """会话里每条"紧跟在 assistant 之后"的 user 消息, 连同被它回应的那一轮。
    会话开头的第一句不算(没有可纠正的对象)。"""
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
            asked = ""
            for j in range(i - 2, -1, -1):
                if msgs[j].get("role") == "user":
                    asked = msgs[j].get("content") or ""
                    break
            out.append({
                "session_id": sid,
                "msg_id": m.get("id") or 0,
                "question": asked[:300],
                "answer": (prev.get("content") or "")[:1200],
                "correction": (m.get("content") or "")[:300],
                "full_answer": prev.get("content") or "",
                "tools": _meta_tools(prev),
            })
    return out


def mine_corrections(rows: list[dict], max_items: int = 20) -> list[dict]:
    """从会话消息里找出 (问题, 回答, 纠正) 三元组 —— 只按关键词判纠正的快速路径。"""
    return [it for it in followups(rows) if looks_like_correction(it["correction"])][-max_items:]


# 关键词只认得出一部分纠正: 实测真实跟进 121 条里关键词认出 14 条, 漏掉的有
# "我说的是我的总亏损""我有这只的持仓啊""你看我的流水啊""你看了分时没有"这类 ——
# 恰好是最该沉淀的"意图读错/该查没查"。所以关键词之外再让模型成批判一遍(TRACE 同样用
# 小模型逐条判纠正信号)。判过的消息记游标, 不重复花额度。
_CLASSIFY_SYSTEM = (
    "下面是一个理财分析助手的若干轮对话片段, 每条给出助手上一轮回答的开头与用户紧接着说的话。"
    "找出其中用户在纠正上一轮回答的条目: 指出回答有错、漏查了该查的数据、读错了用户意图或口径、"
    "对呈现方式提出修改要求。用户提出新问题、表达情绪、给出新指令、补充新信息而上一轮并无不当的, 不算。"
    "只输出单行 JSON: {\"corrections\": [编号, ...]}"
)
_CURSOR_KEY = "rule_forge_classified_upto"


async def classify_followups(items: list[dict], batch: int = 25) -> set:
    """让模型成批判哪些跟进是纠正。返回判为纠正的 msg_id 集合; 调用失败的那批当作没有。"""
    import asyncio as _aio
    import services.llm_client as llm
    hit: set = set()
    for k in range(0, len(items), batch):
        part = items[k:k + batch]
        body = "\n\n".join(f"[{n}] 助手上一轮: {it['answer'][:200]}\n    用户: {it['correction']}"
                            for n, it in enumerate(part))
        try:
            text = await _aio.to_thread(llm.call_claude, body, _CLASSIFY_SYSTEM, "balanced", 800)
        except Exception:
            continue
        d = _parse_any_json(text) or {}
        for n in d.get("corrections") or []:
            if isinstance(n, int) and 0 <= n < len(part):
                hit.add(part[n]["msg_id"])
    return hit


_JUDGED_KEY = "rule_forge_judged_ids"     # 模型判为纠正的消息 id: 一次最多起草 3 条, 剩下的下次接着起草


async def _kv(key: str, value: str | None = None) -> str:
    db = await _conn()
    try:
        if value is not None:
            await db.execute("INSERT OR REPLACE INTO app_config (key, value) VALUES (?, ?)", (key, value))
            await db.commit()
            return value
        cur = await db.execute("SELECT value FROM app_config WHERE key = ?", (key,))
        row = await cur.fetchone()
        return row[0] if row else ""
    finally:
        await db.close()


_DRAFT_SYSTEM_BASE = (
    "你在维护一个理财分析 agent 的 system prompt。它的规则写成【标题】正文 的形式, "
    "一律用正向陈述(说该怎么做, 而不是列举不许做什么), 每条一句到两句, 带上判据。\n"
    "我给你一次真实的'用户纠正'记录。请判断它是否指向一条可以固化的通用规则:\n"
    "· 指向数据源缺失、代码 bug、环境问题(如进程没重启)的, 不是规则问题 —— 回 skip\n"
    "· 只针对这一只票/这一天的偶发事实, 不够通用 —— 回 skip\n"
    "· 已被现有规则覆盖、这次回答却没有遵守的 —— 回 enforce, title 填被违反的那条现有规则的标题, "
    "body 一句话说明这次怎么违反的, 并按下文给出 check(这类最需要机器检查: 文字规则已经在, 模型仍然跳过)\n"
    "· 已被现有规则覆盖、回答也遵守了的 —— 回 skip, 并在 why 里点出是哪一条\n"
    "· 确实是'表述方式/取数顺序/口径'这类下次还会犯的, 起草一条规则\n"
    "只输出 JSON, 单行, 不要前言: {\"verdict\":\"rule\"|\"enforce\"|\"skip\", \"title\":\"…\", "
    "\"body\":\"…\", \"why\":\"一句话说明为什么(或为什么 skip)\"}\n"
    "JSON 字符串内部需要引用时用「」, 不要出现英文双引号 —— 未转义会让整段解析失败。\n"
    "起草规则时, 再判断它能否写成出答案前的机器检查, 能写时在 JSON 里加 check 字段:\n"
    "{\"when\": {\"question_any\": [用户问题里出现其一即适用的关键词], \"tools_any\": [本轮调过其一即适用的工具], "
    "\"topics_any\": [读过其一即适用的作答规范]}, \"require_tools\": [[调过其中一个即满足的工具名], ...], "
    "\"answer_forbid\": [答案里出现即违规的正则], \"answer_require\": [答案里必须出现的正则], "
    "\"message\": \"一句话告诉 agent 怎么改\"}\n"
    "when 至少填一项, 让检查只在相关问题上生效; require_tools / answer_forbid / answer_require 至少填一项; "
    "question_any 选这条规则的场景特有、用户真实会说的短词(如「做T」「低吸」「量比」), 让检查只命中这类问题; "
    "answer_forbid 写原纠正里那种错误说法本身, 让符合规则的回答不会被误拦。检查的判据要能在原纠正那一轮上成立: 那一版回答应当被它判为违规。"
    "正则写成短语级: 用字面词与 (A|B) 多选一, 间隔用带上限的 .{0,12}, 每条 120 字以内。"
    "规则依赖语义判断(论证方式、口吻、框架选择)、写不成关键词与工具判据时, check 填 null。"
)


TOPICS = ("stock", "holdings", "market", "intraday", "chain", "allocation")


def tool_names() -> set:
    try:
        from services.stock_agent import _TOOLS
        return {t["name"] for t in _TOOLS if t.get("name")} | {"web_search"}
    except Exception:
        return set()


def validate_check(raw, names: set | None = None):
    """校验并规整模型给的 check。返回 (check, 错误说明); 不合格时 check=None。

    挡三类: 引用了不存在的工具(模型会编工具名)、正则写不对、没有适用条件的全局检查
    (一条坏检查会打回所有回答, 所以只收带 when 的)。"""
    if not isinstance(raw, dict):
        return None, "没有检查"
    names = names if names is not None else tool_names()

    def strs(v, cap=12):
        return [str(x).strip() for x in (v or [])[:cap] if str(x).strip()] if isinstance(v, list) else []

    w = raw.get("when") if isinstance(raw.get("when"), dict) else {}
    when = {k: strs(w.get(k)) for k in ("question_any", "tools_any", "topics_any")}
    when = {k: v for k, v in when.items() if v}
    if not when:
        return None, "没有适用条件(when), 会对所有回答生效"
    groups = [strs(g) for g in raw.get("require_tools") or [] if isinstance(g, list)]
    groups = [g for g in groups if g]
    chk = {"when": when, "require_tools": groups,
           "answer_forbid": strs(raw.get("answer_forbid"), 6),
           "answer_require": strs(raw.get("answer_require"), 6),
           "message": str(raw.get("message") or "").strip()[:200]}
    if not (groups or chk["answer_forbid"] or chk["answer_require"]):
        return None, "没有判据"
    used = set(when.get("tools_any", [])) | {t for g in groups for t in g}
    if names and used - names:
        return None, f"引用了不存在的工具: {sorted(used - names)}"
    if set(when.get("topics_any", [])) - set(TOPICS):
        return None, f"未知的作答规范: {sorted(set(when['topics_any']) - set(TOPICS))}"
    for rx in chk["answer_forbid"] + chk["answer_require"]:
        try:
            re.compile(rx)
        except re.error as e:
            return None, f"正则写错了 {rx!r}: {e}"
        bad = _rx_risk(rx)
        if bad:
            return None, f"正则 {rx!r} {bad}"
    return {k: v for k, v in chk.items() if v}, ""


RX_MAX_LEN, RX_MAX_REPEAT = 120, 50


def _rx_risk(rx: str) -> str:
    """模型起草的正则启用后会对每个回答跑一遍, 回溯爆炸会卡住服务。判据是短语级的, 只收:
    长度 ≤ RX_MAX_LEN、量词不嵌套、不用反向引用; 无上限量词(+ * {n,})只许加在单个字符上且全条至多 1 个
    (一个时最坏是答案长度的平方), 其余量词上限 ≤ RX_MAX_REPEAT。合格返回空串。"""
    import sre_constants as C
    import sre_parse
    if len(rx) > RX_MAX_LEN:
        return f"超过 {RX_MAX_LEN} 字"
    unbounded = [0]
    single = {C.LITERAL, C.NOT_LITERAL, C.ANY, C.IN, C.CATEGORY}

    def walk(items, in_repeat):
        for op, av in items:
            if op in (C.GROUPREF, C.GROUPREF_EXISTS):
                return "用了反向引用"
            if op in (C.MAX_REPEAT, C.MIN_REPEAT):
                lo, hi, sub = av
                if in_repeat:
                    return "量词嵌套"
                if hi == C.MAXREPEAT:
                    unbounded[0] += 1
                    if len(sub) != 1 or sub[0][0] not in single:
                        return "给分组加了无上限量词(用 {0,n} 写)"
                    if unbounded[0] > 1:
                        return "无上限量词超过 1 个(用 {0,n} 写)"
                elif hi > RX_MAX_REPEAT:
                    return f"量词上限超过 {RX_MAX_REPEAT}"
                r = walk(sub, True)
            elif op == C.SUBPATTERN:
                r = walk(av[-1], in_repeat)
            elif op == C.BRANCH:
                r = next((x for b in av[1] for x in [walk(b, in_repeat)] if x), "")
            elif op in (C.ASSERT, C.ASSERT_NOT):
                r = walk(av[1], in_repeat)
            else:
                r = ""
            if r:
                return r
        return ""
    return walk(sre_parse.parse(rx), False)


def _turns(rows: list[dict]):
    """历史里每一轮 (问题, 回答, 工具): 只取落库时带了工具记录的回答。"""
    by_sess: dict = {}
    for r in rows:
        by_sess.setdefault(r.get("session_id"), []).append(r)
    for msgs in by_sess.values():
        for i, m in enumerate(msgs):
            if m.get("role") != "assistant" or i == 0 or msgs[i - 1].get("role") != "user":
                continue
            tools = _meta_tools(m)
            if tools is not None:
                yield msgs[i - 1].get("content") or "", m.get("content") or "", tools


MAX_FIRE_RATE = 0.15


def replay(chk: dict, origin: dict, rows: list[dict]):
    """编译出的检查拿历史回放。返回 (检查是否保留, 说明)。

    原纠正那一轮必须被判违规 —— 拦不下原来的错, 这条检查就没抓住纠正的要点, 作废。
    原对话没有工具记录(早期消息)时没法回放, 保留并注明。误触发数只报告, 由人决定。"""
    from services.answer_guard import check_fires

    def topics(tools):      # 落库只记了工具名没记读了哪个规范; 读过规范就按都读过算(宁可多触发, 回放从严)
        return list(TOPICS) if "load_guide" in (tools or []) else []
    notes = []
    if origin.get("tools") is None:
        notes.append("原对话没有工具记录, 未能回放")
    elif not check_fires(chk, origin.get("question", ""), origin.get("full_answer") or origin.get("answer", ""),
                         origin["tools"], topics(origin["tools"])):
        return False, "回放原纠正那一轮没有拦下, 检查作废, 只保留文字规则"
    else:
        notes.append("回放原纠正那一轮: 拦下了")
    turns = list(_turns(rows))
    fired = sum(1 for q, a, t in turns if check_fires(chk, q, a, t, topics(t)) and q != origin.get("question"))
    notes.append(f"历史 {len(turns)} 轮回答里会触发 {fired} 轮")
    # 适用范围太宽的检查会让大量正常回答多绕一圈补查(实测见过"调了日线就要求查分时",
    # 历史两成回答都会触发)。原纠正是一次具体的错, 合格的检查只该命中少数同类问题。
    if fired > max(2, MAX_FIRE_RATE * len(turns)):        # 历史太少时不按比例判
        return False, "; ".join(notes) + f", 超过 {MAX_FIRE_RATE:.0%} 适用范围过宽, 检查作废"
    return True, "; ".join(notes)



def existing_titles() -> list[str]:
    """现有 system prompt 里的规则标题。起草时给模型看, 免得提重复的。

    实测不给会怎样: 它提了一条"判断涨停前先核实板块涨跌幅限制", 而 prompt 里早有
    【涨停跌停按该股真实幅度判】。
    """
    try:
        from services.stock_agent import _PROMPT_ALL as _SYSTEM
        return sorted(set(re.findall(r"【([^】]{2,24})】", _SYSTEM)))
    except Exception:
        return []


def draft_system() -> str:
    base = (_DRAFT_SYSTEM_BASE + "\n可用工具: " + ", ".join(sorted(tool_names()))
            + "\n作答规范: " + ", ".join(TOPICS))
    titles = existing_titles()
    if not titles:
        return base
    return (base + "\n现有规则标题(共 " + str(len(titles)) + " 条), "
            "语义已被覆盖的按上面 enforce / skip 处理:\n" + " / ".join(titles))


def draft_prompt(item: dict) -> str:
    tools = item.get("tools")
    return (f"用户当时问: {item['question']}\n\n"
            f"agent 的回答(截断): {item['answer']}\n\n"
            + (f"agent 当时调过的工具: {', '.join(tools) or '(没调工具)'}\n\n" if tools is not None else "")
            + f"用户的纠正原话: {item['correction']}")


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
    v = re.search(r'"verdict"\s*:\s*"(rule|enforce|skip)"', text)
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


ENFORCE_PREFIX = "[检查] "      # 只给现有规则加检查的条目: 文字已在 prompt 里, 不再重复追加


def render_rules(rules: list[dict]) -> str:
    """已批准的规则渲染成 prompt 尾部的一段。空列表返回空串(不留痕)。"""
    live = [r for r in rules if (r.get("title") or "").strip() and (r.get("body") or "").strip()
            and not r["title"].startswith(ENFORCE_PREFIX)]
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
            "SELECT session_id, role, content, meta, id FROM ask_message ORDER BY session_id, id")
        return [{"session_id": r[0], "role": r[1], "content": r[2], "meta": r[3], "id": r[4]}
                for r in await cur.fetchall()]
    finally:
        await db.close()


async def add_candidate(title: str, body: str, evidence: str, session_id=None,
                        check: dict | None = None, note: str = "") -> bool:
    """插入候选。同标题已存在(任何状态)就不重复插 —— 免得同一类纠正反复提案。"""
    db = await _conn()
    try:
        cur = await db.execute("SELECT 1 FROM prompt_rule WHERE title = ?", (title,))
        if await cur.fetchone():
            return False
        await db.execute(
            "INSERT INTO prompt_rule (title, body, evidence, session_id, check_json, check_note) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (title, body, evidence[:500], session_id,
             json.dumps(check, ensure_ascii=False) if check else "", note))
        await db.commit()
        return True
    finally:
        await db.close()


async def list_rules(status: str = "") -> list[dict]:
    db = await _conn()
    try:
        sql = ("SELECT id, title, body, status, evidence, created_at, check_json, check_note, check_on FROM prompt_rule"
               + (" WHERE status = ?" if status else "") + " ORDER BY id")
        cur = await db.execute(sql, (status,) if status else ())
        cols = ("id", "title", "body", "status", "evidence", "created_at", "check", "check_note", "check_on")
        out = [dict(zip(cols, r)) for r in await cur.fetchall()]
        for r in out:
            r["check"] = json.loads(r["check"]) if r["check"] else None
        return out
    finally:
        await db.close()


async def decide(rule_id: int, status: str) -> bool:
    """批准时连同它的检查一起启用(批准界面上检查就列在规则下面); 否决/撤掉时检查一并停用。"""
    db = await _conn()
    try:
        cur = await db.execute(
            "UPDATE prompt_rule SET status = ?, decided_at = CURRENT_TIMESTAMP, "
            "check_on = CASE WHEN ? = 'active' AND COALESCE(check_json, '') <> '' THEN 1 ELSE 0 END WHERE id = ?",
            (status, status, rule_id))
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


def active_checks_sync() -> list[tuple]:
    """已批准规则里编译过检查的那些: [(标题, check)]。answer_guard 出答案前调用。
    读库失败或某条坏了就跳过 —— 护栏不该因为它让问答起不来。"""
    try:
        import sqlite3
        from config import config as _cfg
        con = sqlite3.connect(_cfg.db_path, timeout=1.0)
        try:
            rows = con.execute("SELECT title, check_json FROM prompt_rule WHERE status = 'active' "
                               "AND check_on = 1 AND COALESCE(check_json, '') <> '' ORDER BY id").fetchall()
        finally:
            con.close()
    except Exception:
        return []
    out = []
    for title, raw in rows:
        try:
            chk, _ = validate_check(json.loads(raw), names=set())
        except ValueError:
            chk = None
        if chk:
            out.append((title, chk))
    return out


async def known_evidence() -> set:
    """已经起草过的纠正原话(任何状态)。

    每周自动挖时用它跳过老对话: 一是省额度(一条起草要调一次模型), 二是**被否决过的不能
    再提** —— 去重要对着"见过的", 不是对着"批准的", 否则否掉的规则每周原样回来。
    """
    db = await _conn()
    try:
        cur = await db.execute("SELECT evidence FROM prompt_rule WHERE evidence <> ''")
        return {(r[0] or "").strip() for r in await cur.fetchall()}
    finally:
        await db.close()


async def mine_new(max_drafts: int = 3) -> dict:
    """挖新纠正 → 起草 → 落进待审队列。返回统计。

    只处理没起草过的纠正, 每次最多 max_drafts 条 —— 这是个后台循环, 不该悄悄烧额度。
    起草失败/解析不出/判为非规则都只是跳过, 不影响主流程。
    """
    import services.llm_client as llm
    rows = await fetch_messages()
    seen = await known_evidence()
    fresh = [it for it in followups(rows) if (it.get("correction") or "").strip() not in seen]
    upto = int(await _kv(_CURSOR_KEY) or 0)
    judged = set(json.loads(await _kv(_JUDGED_KEY) or "[]"))
    todo = [it for it in fresh if it["msg_id"] > upto and not looks_like_correction(it["correction"])]
    if todo:
        judged |= await classify_followups(todo)
        await _kv(_JUDGED_KEY, json.dumps(sorted(judged)))
        await _kv(_CURSOR_KEY, str(max(it["msg_id"] for it in todo)))
    items = [it for it in fresh if looks_like_correction(it["correction"]) or it["msg_id"] in judged][-20:]
    stat = {"corrections": len(items), "added": 0, "skipped": 0, "dup": 0, "failed": 0, "compiled": 0}
    import asyncio as _aio
    for it in items[:max_drafts]:
        try:
            text = await _aio.to_thread(
                llm.call_claude, draft_prompt(it), draft_system(), "balanced", 3000)
        except Exception:
            stat["failed"] += 1
            continue
        d = parse_draft(text)
        if not d:
            stat["failed"] += 1
            continue
        if d.get("verdict") == "enforce" and d.get("title"):
            chk, note = compile_for(d.get("check"), it, rows)
            if chk:
                stat["compiled"] += 1
                title = ENFORCE_PREFIX + d["title"].strip("【】")
                if await add_candidate(title, d.get("body") or d.get("why") or "", it["correction"],
                                       it.get("session_id"), chk, note):
                    stat["added"] += 1
                else:
                    stat["dup"] += 1
                continue
            d = {"verdict": "skip", "why": f"现有规则【{d['title']}】已覆盖; 检查没编译成: {note or '无'}"}
        if d.get("verdict") != "rule" or not d.get("title") or not d.get("body"):
            # 判为"不是规则问题"(数据源/偶发)也要记一条 evidence, 否则下周再挖到它、
            # 再花一次额度得出同一个结论
            await add_candidate(f"[非规则] {it['correction'][:24]}", d.get("why", "")[:200],
                                it["correction"], it.get("session_id"))
            await decide_by_evidence(it["correction"], "rejected")
            stat["skipped"] += 1
            continue
        chk, note = compile_for(d.get("check"), it, rows)
        if chk:
            stat["compiled"] += 1
        if await add_candidate(d["title"], d["body"], it["correction"], it.get("session_id"), chk, note):
            stat["added"] += 1
        else:
            stat["dup"] += 1
    return stat


def compile_for(raw, origin: dict, rows: list[dict]):
    """校验 + 回放。返回 (check 或 None, 说明)。"""
    chk, err = validate_check(raw)
    if not chk:
        return None, ("" if err == "没有检查" else f"检查没编译成: {err}")
    keep, note = replay(chk, origin, rows)
    return (chk if keep else None), note


_COMPILE_SYSTEM = (
    "你在给一个理财分析 agent 的已有行为规则补一条出答案前的机器检查。输入是规则原文和触发它的那次"
    "用户纠正(含当时的问题、agent 回答与调过的工具)。只输出单行 JSON: {\"check\": {...} 或 null}。\n"
)


async def compile_rule(rule_id: int) -> dict:
    """给已有规则补编译检查(老规则是在有这套之前批准的)。按 evidence 找回原纠正那一轮来回放。"""
    import asyncio as _aio
    import services.llm_client as llm
    rules = {r["id"]: r for r in await list_rules()}
    r = rules.get(rule_id)
    if not r:
        return {"ok": False, "error": f"没有 #{rule_id}"}
    rows = await fetch_messages()
    origin = next((it for it in mine_corrections(rows, max_items=10 ** 6)
                   if it["correction"].strip() == (r.get("evidence") or "").strip()[:300]), None)
    origin = origin or {"question": "", "answer": "", "tools": None}
    spec = _DRAFT_SYSTEM_BASE.split("起草规则时, 再判断它能否写成出答案前的机器检查, ")[-1]
    prompt = (f"规则:【{r['title']}】{r['body']}\n\n" + draft_prompt(origin | {"correction": r.get("evidence") or ""})
              + f"\n\n当时调过的工具: {origin.get('tools')}\n\n可用工具: {', '.join(sorted(tool_names()))}"
              + f"\n作答规范: {', '.join(TOPICS)}")
    text = await _aio.to_thread(llm.call_claude, prompt, _COMPILE_SYSTEM + spec, "balanced", 2000)
    d = _parse_any_json(text) or {}
    chk, note = compile_for(d.get("check"), origin, rows)
    db = await _conn()
    try:
        await db.execute("UPDATE prompt_rule SET check_json = ?, check_note = ?, check_on = 0 WHERE id = ?",
                         (json.dumps(chk, ensure_ascii=False) if chk else "", note or "规则依赖语义判断, 未编译检查", rule_id))
        await db.commit()
    finally:
        await db.close()
    return {"ok": True, "id": rule_id, "check": chk, "note": note}


async def set_check(rule_id: int, on: bool) -> bool:
    """单独启用/停用某条规则的检查(给已生效规则补编译的检查, 要人看过回放结果再启用)。"""
    db = await _conn()
    try:
        cur = await db.execute("UPDATE prompt_rule SET check_on = ? WHERE id = ? AND COALESCE(check_json, '') <> ''",
                               (1 if on else 0, rule_id))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


def _parse_any_json(text: str) -> dict | None:
    start = (text or "").find("{")
    while start >= 0:
        try:
            d, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(d, dict):
                return d
        except json.JSONDecodeError:
            pass
        start = text.find("{", start + 1)
    return None


async def decide_by_evidence(evidence: str, status: str) -> None:
    db = await _conn()
    try:
        await db.execute(
            "UPDATE prompt_rule SET status = ?, decided_at = CURRENT_TIMESTAMP "
            "WHERE evidence = ? AND status = 'pending'", (status, (evidence or "")[:500]))
        await db.commit()
    finally:
        await db.close()


async def pending_count() -> int:
    db = await _conn()
    try:
        cur = await db.execute("SELECT COUNT(*) FROM prompt_rule WHERE status = 'pending'")
        return (await cur.fetchone())[0]
    finally:
        await db.close()
