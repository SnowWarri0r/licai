"""个股档案: 核对规则、页面渲染、按代码/简称取页、整体整理一轮(模型打桩)。用假代码与假名称。"""
import asyncio
import json
import os
import tempfile

import pytest

from services import stock_notes as sn


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


MSGS = {
    1: {"content": "测试科技(900001) 怎么样", "session_id": 1, "date": "2026-10-01"},
    2: {"content": "测试科技(900001) 主营光模块, 八成营收来自海外数据中心客户。它同时参股测试材料。"
                   "今天收涨 3%, 量比 1.2。", "session_id": 1, "date": "2026-10-01"},
    3: {"content": "另一只票 测试能源(900002) 的主营是储能电池", "session_id": 2, "date": "2026-10-02"},
}
SESS = {1: MSGS[1]["content"] + MSGS[2]["content"], 2: MSGS[3]["content"]}


def test_validate_rules():
    out = {"notes": [
        {"code": "900001", "name": "测试科技", "kind": "主营", "text": "测试科技主营光模块, 八成营收来自海外数据中心客户",
         "msg": 2, "quote": "主营光模块, 八成营收来自海外数据中心客户"},
        {"code": "sh900001", "name": "测试科技", "kind": "关联", "text": "测试科技参股测试材料",
         "msg": 2, "quote": "它同时参股测试材料"},
        {"code": "900001", "name": "测试科技", "kind": "结论", "text": "测试科技今天收涨 3%", "msg": 2,
         "quote": "今天收涨 3%, 量比 1.2"},                                    # 行情数字
        {"code": "900001", "name": "测试科技", "kind": "主营", "text": "编的", "msg": 2, "quote": "原文没有这句话"},
        {"code": "900002", "name": "测试能源", "kind": "主营", "text": "测试能源主营储能电池", "msg": 2,
         "quote": "主营光模块, 八成营收"},                                      # 会话 1 没提到它
        {"code": "900001", "name": "测试科技", "kind": "心情", "text": "x", "msg": 2, "quote": "主营光模块"},
    ], "supersede": [{"id": 7, "msg": 3, "quote": "的主营是储能电池"}, {"id": 99, "msg": 3, "quote": "储能电池"}]}
    existing = {7: {"code": "900002", "text": "测试能源主营风电"}}
    notes, sups, dropped = sn.validate(out, MSGS, SESS, existing)
    assert [(n["code"], n["kind"]) for n in notes] == [("900001", "主营"), ("900001", "关联")]
    assert notes[0]["said_on"] == "2026-10-01" and notes[0]["session_id"] == 1
    assert [s["id"] for s in sups] == [7] and "2026-10-02" in sups[0]["reason"]
    assert len(dropped) == 5
    assert any("行情" in d for d in dropped) and any("没有提到" in d for d in dropped)


def test_render_page_and_index():
    rows = [{"id": 1, "code": "900001", "name": "测试科技", "kind": "关联", "text": "参股测试材料",
             "said_on": "2026-10-01", "session_id": 1},
            {"id": 2, "code": "900001", "name": "测试科技", "kind": "主营", "text": "主营光模块",
             "said_on": "2026-10-01", "session_id": 1}]
    page = sn.render_page("900001", rows)
    assert page.index("主营与赚钱方式") < page.index("关联关系") and "#2" in page
    idx = sn.render_index([("900001", "测试【科技】", 2)])
    assert "测试科技(900001)" in idx and "get_stock_notes" in idx
    assert sn.render_index([]) == ""


def _seed_messages(path):
    import sqlite3
    con = sqlite3.connect(path)
    con.execute("INSERT INTO ask_session (id, title) VALUES (1, '测试科技')")
    for mid, role in ((1, "user"), (2, "assistant")):
        con.execute("INSERT INTO ask_message (id, session_id, role, content, created_at) VALUES (?, 1, ?, ?, ?)",
                    (mid, role, MSGS[mid]["content"], "2026-10-01 09:00:00"))
    con.commit()
    con.close()


def test_run_lookup_delete(temp_db, monkeypatch):
    _seed_messages(temp_db)
    from services import llm_client

    def fake(prompt, system, model, max_tokens):
        assert "[msg 2 · 2026-10-01 · 助手]" in prompt        # 助手消息带编号、全文
        return json.dumps({"notes": [{"code": "900001", "name": "测试科技", "kind": "主营",
                                      "text": "测试科技主营光模块", "msg": 2, "quote": "主营光模块, 八成"}],
                           "supersede": []}, ensure_ascii=False)
    monkeypatch.setattr(llm_client, "call_claude", fake)
    st = asyncio.run(sn.run())
    assert st["added"] == 1 and st["chunks"] == 1 and st["remaining"] == 0
    assert asyncio.run(sn.run())["note"] == "没有新消息"
    assert sn.index_sync() == [("900001", "测试科技", 1)]
    r = asyncio.run(sn.lookup("测试科技"))
    assert r["code"] == "900001" and "测试科技主营光模块" in r["page"]
    assert asyncio.run(sn.lookup("sz900001"))["n"] == 1
    assert asyncio.run(sn.lookup("没有的票"))["page"] == ""
    eid = asyncio.run(sn.entries())[0]["id"]
    assert asyncio.run(sn.delete_entry(eid)) and sn.index_sync() == []


def test_per_stock_cap(temp_db, monkeypatch):
    monkeypatch.setattr(sn, "MAX_PER_STOCK", 2)
    notes = [{"code": "900001", "name": "测试科技", "kind": "事件", "text": f"事件{i}", "quote": "q",
              "msg_id": 1, "session_id": 1, "said_on": "2026-10-01"} for i in range(3)]
    asyncio.run(sn._save(notes, []))
    assert [e["text"] for e in asyncio.run(sn.entries())] == ["事件1", "事件2"]


def test_failed_chunk_keeps_watermark(temp_db, monkeypatch):
    _seed_messages(temp_db)
    from services import llm_client
    monkeypatch.setattr(llm_client, "call_claude", lambda *a, **k: "不是 JSON")
    st = asyncio.run(sn.run())
    assert st["failed"] == 1 and st["remaining"] == 1


def test_volatile_filter_keeps_structure_drops_positions():
    v = sn._VOLATILE.search
    assert v("用户持有测试科技600股, 成本35.4")
    assert v("用户以1.96清仓测试ETF全部10700份")
    assert not v("测试科技 5 年股本膨胀 68% 源于换股吸收合并")
    assert not v("用户认为测试指数比主题 ETF 稳, 计划回调后再接回")
