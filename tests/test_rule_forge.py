"""纠正挖掘 + 候选规则渲染。

最要紧的两条性质:
  1. "推""接着做"这类正常指令不能被当成纠正 —— 否则每次都提案, 队列全是噪音
  2. pending 的规则一律不进 prompt —— 闸门失效比不做这个功能更糟
"""
from services import rule_forge as rf


# ── 纠正识别 ────────────────────────────────────────────

def test_real_corrections_recognised():
    """全部取自这个项目里真实出现过的纠正原话。"""
    for t in ("迈威尔现在不是涨，是跌",
              "怎么还是说大跌，盘前不是涨了吗",
              "我问迈威尔为什么涨，怎么都没分析出来",
              "海外的成交额真的拿不到吗",
              "什么叫那天",
              "全是乱码",
              "点了一下保存选择就崩了",
              "分时图上三个B在一起重合了，看不清",
              "榜单的成交额比例尺好像拖一下就会给整坏掉"):
        assert rf.looks_like_correction(t), t


def test_normal_instructions_not_treated_as_corrections():
    """这些是正常指令/追问。不排除的话每次"推"都会被记成一次纠正。"""
    for t in ("推", "发", "加", "接着做", "继续", "行", "好", "可以", "嗯",
              "那明天呢", "帮我看看茅台", "先继续改动吧", ""):
        assert not rf.looks_like_correction(t), t


# ── 三元组挖掘 ──────────────────────────────────────────

ROWS = [
    {"session_id": 1, "role": "user", "content": "迈威尔为什么涨"},
    {"session_id": 1, "role": "assistant", "content": "现在不是涨，是大跌 7.82%"},
    {"session_id": 1, "role": "user", "content": "怎么还是说大跌，盘前不是涨了吗"},
    {"session_id": 1, "role": "assistant", "content": "抱歉，盘前 240.56"},
    {"session_id": 2, "role": "user", "content": "帮我看看茅台"},
    {"session_id": 2, "role": "assistant", "content": "茅台 1307"},
    {"session_id": 2, "role": "user", "content": "推"},
]


def test_mine_pairs_question_answer_correction():
    got = rf.mine_corrections(ROWS)
    assert len(got) == 1
    it = got[0]
    assert it["session_id"] == 1
    assert it["question"] == "迈威尔为什么涨"
    assert "大跌" in it["answer"]
    assert "盘前不是涨了吗" in it["correction"]


def test_first_message_never_counts():
    """会话开头那句没有可纠正的对象, 哪怕带"不是"也不算。"""
    rows = [{"session_id": 9, "role": "user", "content": "这不是涨吗"}]
    assert rf.mine_corrections(rows) == []


def test_correction_must_follow_an_assistant_turn():
    rows = [{"session_id": 9, "role": "user", "content": "问题"},
            {"session_id": 9, "role": "user", "content": "怎么还没答"}]
    assert rf.mine_corrections(rows) == []


# ── 起草输出解析 ────────────────────────────────────────

def test_parse_draft_tolerates_prose_around_json():
    """thinking 类模型会在 JSON 前后夹带散文, 按花括号配对抠。"""
    text = ('我先分析一下这次纠正的性质。{"verdict": "rule", "title": "美股报价按时段表述", '
            '"body": "带 ext_hours 时分开写两个时点。", "why": "会复发"} 以上。')
    d = rf.parse_draft(text)
    assert d["verdict"] == "rule" and d["title"] == "美股报价按时段表述"


def test_parse_draft_skips_non_json_and_bad_shape():
    assert rf.parse_draft("这次是数据源问题, 不是规则问题") is None
    assert rf.parse_draft("") is None
    assert rf.parse_draft('{"foo": 1}') is None          # 没有 verdict 不算


def test_parse_draft_picks_the_object_with_verdict():
    text = '{"note": {"a": 1}} 然后 {"verdict": "skip", "why": "偶发"}'
    assert rf.parse_draft(text)["verdict"] == "skip"


# ── 渲染进 prompt ───────────────────────────────────────

def test_render_active_rules():
    out = rf.render_rules([{"title": "甲", "body": "正文甲。"},
                           {"title": "【乙】", "body": "正文乙。"}])
    assert "【以下规则由历次纠正沉淀而来】" in out
    assert "【甲】正文甲。" in out
    assert "【乙】正文乙。" in out          # 标题自带书名号也不重复套


def test_render_nothing_when_no_active_rules():
    """没有已生效规则时不能往 prompt 里留任何痕迹 —— 那会白白动到缓存前缀。"""
    assert rf.render_rules([]) == ""
    assert rf.render_rules([{"title": "", "body": "x"}]) == ""
    assert rf.render_rules([{"title": "x", "body": "  "}]) == ""


def test_system_prompt_unaffected_when_nothing_active(monkeypatch):
    """闸门的核心性质: pending 的规则不能出现在 prompt 里。"""
    import services.stock_agent as sa
    monkeypatch.setattr(rf, "active_rules_sync",
                        lambda: [])          # 只有 active 才会被 render
    base = sa._system()
    assert "沉淀而来" not in base

    monkeypatch.setattr(rf, "active_rules_sync",
                        lambda: [{"title": "试验规则", "body": "测试正文。"}])
    after = sa._system()
    assert "【试验规则】测试正文。" in after
    # 日期段仍在最末尾: 缓存断点打在 system 末尾, 稳定的部分要留在前面
    assert after.index("试验规则") < after.index("【今天】")


def test_active_rules_sync_survives_missing_table(monkeypatch):
    """表还没建/库被占用时返回空, 不能让整个 agent 起不来。"""
    import config
    monkeypatch.setattr(config.config, "db_path", "/nonexistent/nope.db")
    assert rf.active_rules_sync() == []


def test_parse_draft_tolerates_unescaped_inner_quotes():
    """实测: 模型在中文正文里写未转义的英文双引号(是"放量"还…), json.loads 必失败,
    但内容是好的。靠 prompt 约束挡不干净, 解析要能兜住。"""
    text = ('{"verdict":"rule","title":"量能判断需明确比较基准",'
            '"body":"判断某日是"放量"还是"缩量"时, 一并给出对照基准。",'
            '"why":"会复发"}')
    assert rf._parse_json(text) is None          # 严格解析确实失败
    d = rf.parse_draft(text)                     # 兜底能救回来
    assert d["verdict"] == "rule"
    assert d["title"] == "量能判断需明确比较基准"
    assert "比较基准" in d["body"] or "对照基准" in d["body"]


def test_parse_draft_loose_keeps_skip_verdict():
    text = '前言。{"verdict":"skip","why":"已被现有规则"涨停跌停"覆盖"}'
    d = rf.parse_draft(text)
    assert d["verdict"] == "skip"


def test_draft_system_includes_existing_titles():
    """不给现有标题会怎样: 实测提了一条与【涨停跌停按该股真实幅度判】重复的规则。"""
    sysp = rf.draft_system()
    titles = rf.existing_titles()
    assert len(titles) > 30
    assert titles[0] in sysp
    assert "已被现有规则覆盖的" in sysp


# ── 每周自动挖掘(后台循环用) ─────────────────────────────

import asyncio
import os
import tempfile

import pytest


@pytest.fixture
def wf_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setattr("config.config.db_path", path)   # 单例, 不是 Config 类
    from database import init_db
    asyncio.run(init_db())
    yield path
    os.unlink(path)


def _seed_messages(path, rows):
    import sqlite3
    con = sqlite3.connect(path)
    try:
        con.execute("INSERT OR IGNORE INTO ask_session (id, title) VALUES (1, 't')")
        for role, content in rows:
            con.execute("INSERT INTO ask_message (session_id, role, content) VALUES (1,?,?)",
                        (role, content))
        con.commit()
    finally:
        con.close()


_TALK = [("user", "迈威尔为什么涨"), ("assistant", "现在是大跌 7.82%"),
         ("user", "怎么还是说大跌，盘前不是涨了吗"), ("assistant", "抱歉, 盘前 240.56")]


def test_mine_new_adds_candidate(wf_db, monkeypatch):
    _seed_messages(wf_db, _TALK)
    draft = ('{"verdict":"rule","title":"美股报价按时段表述",'
             '"body":"带盘前盘后时分开写两个时点。","why":"会复发"}')
    import services.llm_client as llm
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: draft)
    st = asyncio.run(rf.mine_new(max_drafts=3))
    assert st["corrections"] == 1 and st["added"] == 1
    rules = asyncio.run(rf.list_rules())
    assert [r["title"] for r in rules] == ["美股报价按时段表述"]
    assert rules[0]["status"] == "pending"              # 闸门: 挖出来不自动生效


def test_mine_new_skips_already_drafted(wf_db, monkeypatch):
    """第二周再跑不能把同一句纠正再起草一遍 —— 一条起草要调一次模型。"""
    _seed_messages(wf_db, _TALK)
    calls = []
    import services.llm_client as llm
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: (
        calls.append(1) or '{"verdict":"rule","title":"甲","body":"正文。","why":"x"}'))
    asyncio.run(rf.mine_new())
    asyncio.run(rf.mine_new())
    assert len(calls) == 1, f"重复起草了 {len(calls)} 次"


def test_rejected_never_comes_back(wf_db, monkeypatch):
    """去重要对着"起草过的", 不是"批准的" —— 否则否掉的规则每周原样回来。

    两层都要拦住: 连模型都不该再调(按 evidence 跳过), 万一调了也不该插进去(标题已存在)。
    """
    _seed_messages(wf_db, _TALK)
    calls = []
    import services.llm_client as llm
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: (
        calls.append(1) or '{"verdict":"rule","title":"甲","body":"正文。","why":"x"}'))
    asyncio.run(rf.mine_new())
    rid = asyncio.run(rf.list_rules())[0]["id"]
    asyncio.run(rf.decide(rid, "rejected"))
    asyncio.run(rf.mine_new())
    rules = asyncio.run(rf.list_rules())
    assert len(rules) == 1 and rules[0]["status"] == "rejected"
    assert len(calls) == 1, "被否决过的纠正又花了一次模型额度"


def test_non_rule_verdict_is_remembered(wf_db, monkeypatch):
    """判为"不是规则问题"也要留痕, 否则下周再挖到它、再花一次额度得同一个结论。"""
    _seed_messages(wf_db, _TALK)
    calls = []
    import services.llm_client as llm
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: (
        calls.append(1) or '{"verdict":"skip","why":"数据源抖动, 偶发"}'))
    st = asyncio.run(rf.mine_new())
    assert st["skipped"] == 1 and st["added"] == 0
    asyncio.run(rf.mine_new())
    assert len(calls) == 1
    # 留痕的那条不能进 prompt
    assert rf.active_rules_sync() == []


def test_mine_new_caps_drafts(wf_db, monkeypatch):
    """后台循环不该悄悄烧额度: 一次最多起草 max_drafts 条。"""
    rows = []
    for i in range(5):
        rows += [("user", f"问题{i}"), ("assistant", f"答{i}"), ("user", f"不对，这里错了{i}")]
    _seed_messages(wf_db, rows)
    calls = []
    import services.llm_client as llm
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: (
        calls.append(1) or f'{{"verdict":"rule","title":"甲{len(calls)}","body":"正文。","why":"x"}}'))
    st = asyncio.run(rf.mine_new(max_drafts=2))
    assert st["corrections"] == 5 and len(calls) == 2 and st["added"] == 2


def test_pending_count(wf_db):
    assert asyncio.run(rf.pending_count()) == 0
    asyncio.run(rf.add_candidate("甲", "正文。", "不对"))
    assert asyncio.run(rf.pending_count()) == 1
