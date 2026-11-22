"""跨会话检索(ask_recall)与夜间整理(memory_dream): 纯函数 + 临时库, 模型调用打桩。"""
import asyncio
import json
import os
import tempfile

import pytest

from services import ask_recall as ar
from services import memory_dream as md
from services import user_memory as um


@pytest.fixture
def temp_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)
    monkeypatch.delenv("AGENT_MEMORY_READONLY", raising=False)
    from database import init_db
    asyncio.run(init_db())
    yield path
    os.unlink(path)


def _seed(path, sessions):
    """sessions: [(title, [(role, content), ...]), ...] → 写入 ask_session/ask_message。"""
    import sqlite3
    con = sqlite3.connect(path)
    for i, (title, msgs) in enumerate(sessions, start=1):
        con.execute("INSERT INTO ask_session (id, title) VALUES (?, ?)", (i, title))
        for role, content in msgs:
            con.execute("INSERT INTO ask_message (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                        (i, role, content, f"2026-10-{10 + i:02d} 10:00:00"))
    con.commit()
    con.close()


SESSIONS = [
    ("看看测试甲", [("user", "测试甲 最近怎么样"), ("assistant", "测试甲 属于示例行业, 当时判断偏震荡。")]),
    ("定投说明", [("user", "我的场外基金每月扣款都是定投, 别当成主动加仓"),
                  ("assistant", "明白, 场外扣款按定投处理。")]),
    ("再看测试甲和测试乙", [("user", "测试甲 和 测试乙 对比一下"), ("assistant", "测试乙 估值更低。")]),
]


# ── ask_recall ────────────────────────────────────────

def test_terms_and_snippet():
    assert ar.terms_of("测试甲, 测试乙  测试甲") == ["测试甲", "测试乙"]
    s = ar.snippet("前" * 300 + "关键词" + "后" * 300, ["关键词"])
    assert "关键词" in s and s.startswith("…") and s.endswith("…")


def test_search_ranks_by_terms_and_excludes_current(temp_db):
    _seed(temp_db, SESSIONS)
    r = asyncio.run(ar.search("测试甲 测试乙"))
    assert [s["session_id"] for s in r["sessions"]] == [3, 1]       # 两个词都命中的排前
    assert r["sessions"][0]["matched"] == ["测试乙", "测试甲"]
    assert r["sessions"][0]["date"] == "2026-10-13"
    r = asyncio.run(ar.search("测试甲", exclude_session=3))
    assert [s["session_id"] for s in r["sessions"]] == [1]
    assert asyncio.run(ar.search("不存在的词"))["sessions"] == []
    assert "error" in asyncio.run(ar.search(""))


def test_read_session(temp_db):
    _seed(temp_db, SESSIONS)
    r = asyncio.run(ar.search(session_id=2))
    assert r["title"] == "定投说明" and [m["role"] for m in r["messages"]] == ["用户", "助手"]
    assert "error" in asyncio.run(ar.search(session_id=99))


# ── memory_dream: 纯函数 ──────────────────────────────

def _msgs():
    out, i = [], 0
    for sid, (_, ms) in enumerate(SESSIONS, start=1):
        for role, content in ms:
            i += 1
            out.append({"id": i, "session_id": sid, "role": role, "content": content,
                        "created_at": "2026-10-11 10:00:00"})
    return out


def test_build_chunks_keeps_sessions_whole(monkeypatch):
    monkeypatch.setattr(md, "CHUNK_CHARS", 120)
    chunks = md.build_chunks(_msgs(), {2: "定投说明"})
    assert len(chunks) >= 2
    assert all(c["text"].count("## 会话") >= 1 for c in chunks)
    assert chunks[-1]["last_id"] == 6
    assert {1, 3, 5} == set().union(*(c["user_ids"] for c in chunks))
    assert "[msg 3 · 2026-10-11 · 用户]" in "".join(c["text"] for c in chunks)


USER = {3: {"content": "我的场外基金每月扣款都是定投, 别当成主动加仓", "session_id": 2, "date": "2026-10-12"}}


def test_validate_requires_verbatim_user_quote():
    ops = [
        {"op": "add", "fact": "用户的场外基金扣款均为定投", "msg": 3, "quote": "每月扣款都是定投"},
        {"op": "add", "fact": "编出来的", "msg": 3, "quote": "原话里没有这句"},
        {"op": "add", "fact": "引用助手", "msg": 4, "quote": "按定投处理"},          # 4 是助手消息
        {"op": "update", "id": 9, "fact": "x", "msg": 3, "quote": "每月扣款都是定投"},  # 9 不存在
        {"op": "merge", "ids": [1, 2], "fact": "用户偏好简短回答"},
        {"op": "merge", "ids": [1], "fact": "只有一条"},
        {"op": "update", "id": 1, "fact": "", "msg": 3, "quote": "别当成主动加仓"},
    ]
    ok, dropped = md.validate(ops, USER, {1: "用户喜欢简短", 2: "用户要求回答简短"})
    assert [(o["fact"], o["replaces"], o["also"]) for o in ok] == [
        ("用户的场外基金扣款均为定投", None, []),
        ("用户偏好简短回答", 1, [2]),
        ("", 1, []),
    ]
    assert "每月扣款都是定投" in ok[0]["note"] and ok[2]["note"].startswith("建议删除")
    assert len(dropped) == 4


def test_parse_tolerates_prose():
    assert md.parse('好的 {"ops": [{"op": "add"}]} 完') == [{"op": "add"}]
    assert md.parse("没有 JSON") is None


# ── memory_dream: 整体跑一遍(模型打桩) ────────────────

def test_run_proposes_and_advances_watermark(temp_db, monkeypatch):
    _seed(temp_db, SESSIONS)
    calls = []

    def fake(prompt, system, model, max_tokens):
        calls.append(prompt)
        return json.dumps({"ops": [{"op": "add", "fact": "用户的场外基金扣款均为定投", "msg": 3,
                                    "quote": "扣款都是定投"},
                                   {"op": "add", "fact": "瞎编", "msg": 3, "quote": "不存在"}]},
                          ensure_ascii=False)
    from services import llm_client
    monkeypatch.setattr(llm_client, "call_claude", fake)
    st = asyncio.run(md.run())
    assert st["proposed"] == 1 and st["dropped"] == 1 and st["messages"] == 6
    items = asyncio.run(um.list_all())
    assert items[0]["status"] == "pending" and items[0]["origin"] == "dream"
    assert "会话 2" in items[0]["note"]
    # 再跑: 没有新消息, 不调模型
    st = asyncio.run(md.run())
    assert st.get("note") == "没有新的用户消息" and len(calls) == 1
    assert asyncio.run(md.last_run())["note"] == "没有新的用户消息"


def test_failed_chunk_keeps_watermark(temp_db, monkeypatch):
    _seed(temp_db, SESSIONS)
    from services import llm_client
    monkeypatch.setattr(llm_client, "call_claude", lambda *a, **k: "不是 JSON")
    st = asyncio.run(md.run())
    assert st["failed"] == 1 and st["chunks"] == 0
    assert asyncio.run(md._kv(md.WATERMARK_KEY)) == ""


def test_approve_merge_and_delete_proposals(temp_db):
    import sqlite3
    con = sqlite3.connect(temp_db)
    for f in ("用户喜欢简短", "用户要求回答简短", "用户只看 A 股"):
        con.execute("INSERT INTO user_memory (fact, status) VALUES (?, 'active')", (f,))
    con.commit()
    con.close()
    merge = asyncio.run(um.propose("用户偏好简短回答", replaces=1, also_replaces=[2], note="合并"))
    drop = asyncio.run(um.propose("", replaces=3, note="建议删除"))
    assert asyncio.run(um.propose("用户偏好简短回答")) is None        # 重复提案不存
    assert asyncio.run(um.approve(merge)) and asyncio.run(um.approve(drop))
    st = {m["id"]: m["status"] for m in asyncio.run(um.list_all())}
    assert st == {1: "replaced", 2: "replaced", 3: "replaced", merge: "active", drop: "deleted"}
    assert [m["fact"] for m in um.active_sync()] == ["用户偏好简短回答"]


def test_readonly_skips(temp_db, monkeypatch):
    monkeypatch.setenv("AGENT_MEMORY_READONLY", "1")
    assert asyncio.run(md.run())["note"].startswith("评测模式")


def test_validate_phase_and_confirm():
    meta = {1: {"as_of": "2026-09-01", "review_days": 30}, 2: {"as_of": "", "review_days": 0}}
    active = {1: "用户近期没有额外资金", 2: "用户要求回答简短"}
    ops = [
        {"op": "confirm", "id": 1, "msg": 3, "quote": "每月扣款都是定投"},
        {"op": "confirm", "id": 2, "msg": 3, "quote": "每月扣款都是定投"},   # 长期条目不收 confirm
        {"op": "confirm", "id": 1, "msg": 3, "quote": "编的"},
        {"op": "add", "fact": "用户这个月在等资金到账", "msg": 3, "quote": "别当成主动加仓", "phase": True},
        {"op": "update", "id": 1, "fact": "用户近期有一笔资金到账", "msg": 3, "quote": "别当成主动加仓"},
    ]
    ok, dropped = md.validate(ops, USER, active, meta)
    day = USER[3]["date"]
    assert ok[0] == {"confirm": 1, "as_of": day}
    assert ok[1]["phase"] and ok[1]["as_of"] == day
    assert ok[2]["phase"] and ok[2]["replaces"] == 1               # 改写阶段性条目, 仍是阶段性
    assert len(dropped) == 2


def test_run_applies_confirm_directly(temp_db, monkeypatch):
    _seed(temp_db, SESSIONS)
    import sqlite3
    con = sqlite3.connect(temp_db)
    con.execute("INSERT INTO user_memory (fact, status, as_of, review_days) "
                "VALUES ('用户场外扣款是定投', 'active', '2026-08-01', 30)")
    con.commit()
    con.close()
    from services import llm_client
    seen = []

    def fake(prompt, *a, **k):
        seen.append(prompt)
        return json.dumps({"ops": [{"op": "confirm", "id": 1, "msg": 3, "quote": "扣款都是定投"}]},
                          ensure_ascii=False)
    monkeypatch.setattr(llm_client, "call_claude", fake)
    st = asyncio.run(md.run())
    assert st["confirmed"] == 1 and st["proposed"] == 0
    assert "[阶段性, 截至 2026-08-01]" in seen[0]
    assert um.active_sync()[0]["as_of"] == "2026-10-12"
