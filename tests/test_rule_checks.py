"""纠正编译成检查(R8): 校验、执行、回放。

最要紧的性质:
  1. 没有适用条件的检查不收 —— 一条全局检查会打回所有回答
  2. 引用不存在的工具/写错的正则不收 —— 模型会编工具名
  3. 回放拦不下原纠正那一轮的检查作废 —— 它没抓住纠正的要点
"""
import json

from services import answer_guard as g
from services import rule_forge as rf

NAMES = {"get_intraday", "get_quote", "get_trades", "get_holdings", "load_guide"}

INTRADAY = {"when": {"question_any": ["冲高", "回落", "翻绿"]},
            "require_tools": [["get_intraday"]],
            "message": "盘中路径要看分时"}


def test_validate_accepts_and_normalises():
    chk, err = rf.validate_check(INTRADAY | {"answer_forbid": []}, NAMES)
    assert err == "" and chk["require_tools"] == [["get_intraday"]]
    assert "answer_forbid" not in chk          # 空字段规整掉


def test_validate_rejects_blanket_check():
    chk, err = rf.validate_check({"require_tools": [["get_intraday"]]}, NAMES)
    assert chk is None and "适用条件" in err


def test_validate_rejects_unknown_tool_and_bad_regex():
    chk, err = rf.validate_check({"when": {"question_any": ["x"]}, "require_tools": [["get_minute_bars"]]}, NAMES)
    assert chk is None and "不存在的工具" in err
    chk, err = rf.validate_check({"when": {"question_any": ["x"]}, "answer_forbid": ["(未闭合"]}, NAMES)
    assert chk is None and "正则" in err


def test_validate_rejects_no_criterion_and_unknown_topic():
    assert rf.validate_check({"when": {"question_any": ["x"]}}, NAMES)[0] is None
    chk, err = rf.validate_check({"when": {"topics_any": ["crypto"]}, "require_tools": [["get_quote"]]}, NAMES)
    assert chk is None and "作答规范" in err


def test_fires_only_when_applicable_and_violated():
    chk, _ = rf.validate_check(INTRADAY, NAMES)
    assert g.check_fires(chk, "早上冲高回落了吗", "翻绿了", ["get_quote"], [])
    assert not g.check_fires(chk, "早上冲高回落了吗", "翻绿了", ["get_quote", "get_intraday"], [])
    assert not g.check_fires(chk, "茅台估值高吗", "…", ["get_quote"], [])


def test_answer_regex_and_topics():
    chk, _ = rf.validate_check({"when": {"topics_any": ["holdings"]},
                                "answer_forbid": ["继续放量"], "answer_require": [r"\d+股"]}, NAMES)
    why = g.check_fires(chk, "", "今天继续放量", [], ["holdings"])
    assert "继续放量" in why and "缺少" in why
    assert not g.check_fires(chk, "", "今天继续放量", [], ["stock"])


def test_compiled_violations_message_names_rule():
    chk, _ = rf.validate_check(INTRADAY, NAMES)
    out = g.compiled_violations("冲高回落了吗", "回落了", ["get_quote"], {}, checks=[("日内看分时", chk)])
    assert out and "【日内看分时】" in out[0] and "get_intraday" in out[0]


def _msgs():
    meta = lambda tools: json.dumps({"tools_used": tools})
    return [
        {"session_id": 1, "role": "user", "content": "这只ETF冲高回落了吗", "meta": ""},
        {"session_id": 1, "role": "assistant", "content": "已经翻绿", "meta": meta(["get_quote"])},
        {"session_id": 1, "role": "user", "content": "你看了分时没有, 明明都在红盘", "meta": ""},
        {"session_id": 2, "role": "user", "content": "今天冲高了吗", "meta": ""},
        {"session_id": 2, "role": "assistant", "content": "冲高后回落", "meta": meta(["get_intraday"])},
        {"session_id": 3, "role": "user", "content": "回落到哪了", "meta": ""},
        {"session_id": 3, "role": "assistant", "content": "…", "meta": meta(["get_quote"])},
    ]


def test_mine_carries_origin_tools():
    items = rf.mine_corrections(_msgs())
    assert len(items) == 1 and items[0]["tools"] == ["get_quote"]


def test_replay_keeps_check_that_catches_origin():
    rows = _msgs()
    origin = rf.mine_corrections(rows)[0]
    chk, _ = rf.validate_check(INTRADAY, NAMES)
    keep, note = rf.replay(chk, origin, rows)
    assert keep and "拦下了" in note and "会触发 1 轮" in note      # 第 3 个会话那一轮


def test_replay_drops_check_that_misses_origin():
    rows = _msgs()
    origin = rf.mine_corrections(rows)[0]
    chk, _ = rf.validate_check({"when": {"question_any": ["冲高"]}, "require_tools": [["get_quote"]]}, NAMES)
    keep, note = rf.replay(chk, origin, rows)
    assert not keep and "作废" in note


def test_replay_without_tool_record_keeps_with_note():
    chk, _ = rf.validate_check(INTRADAY, NAMES)
    keep, note = rf.replay(chk, {"question": "q", "answer": "a", "tools": None}, [])
    assert keep and "未能回放" in note


def test_only_enabled_checks_of_active_rules_run(tmp_path, monkeypatch):
    """给已生效规则补编译的检查要单独启用; 待审规则的检查不执行。"""
    import sqlite3
    from config import config as cfg
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE prompt_rule (id INTEGER PRIMARY KEY, title TEXT, body TEXT, status TEXT, "
                "check_json TEXT DEFAULT '', check_on INTEGER DEFAULT 0)")
    js = json.dumps(INTRADAY)
    con.executemany("INSERT INTO prompt_rule (title, body, status, check_json, check_on) VALUES (?,?,?,?,?)", [
        ("已启用", "b", "active", js, 1),
        ("刚补编译", "b", "active", js, 0),
        ("待审", "b", "pending", js, 1),
        ("纯文字", "b", "active", "", 1),
    ])
    con.commit()
    con.close()
    monkeypatch.setattr(cfg, "db_path", str(db))
    assert [t for t, _ in rf.active_checks_sync()] == ["已启用"]


def test_enforce_entries_add_check_without_duplicating_text():
    """只给现有规则加检查的条目, 文字不再追加进 prompt。"""
    out = rf.render_rules([{"title": rf.ENFORCE_PREFIX + "盘中路径以分时为据", "body": "这次没看分时"},
                           {"title": "量能看环比", "body": "b"}])
    assert "量能看环比" in out and "盘中路径" not in out


def test_parse_enforce_verdict():
    d = rf.parse_draft('{"verdict":"enforce","title":"盘中路径以分时为据","body":"x","why":"y",'
                       '"check":{"when":{"question_any":["冲高"]},"require_tools":[["get_intraday"]]}}')
    assert d["verdict"] == "enforce" and d["check"]["require_tools"] == [["get_intraday"]]


def test_replay_drops_overbroad_check():
    """历史回答里大面积触发的检查作废: 合格的检查只该命中少数同类问题。"""
    meta = json.dumps({"tools_used": ["get_quote"]})
    rows = []
    for sid in range(10):
        rows += [{"session_id": sid, "role": "user", "content": "今天冲高了吗", "meta": "", "id": sid * 2},
                 {"session_id": sid, "role": "assistant", "content": "…", "meta": meta, "id": sid * 2 + 1}]
    chk, _ = rf.validate_check(INTRADAY, NAMES)
    keep, note = rf.replay(chk, {"question": "q", "answer": "a", "tools": None}, rows)
    assert not keep and "过宽" in note


def test_validate_rejects_backtracking_regex():
    """启用后的检查对每个回答都跑一遍, 会回溯爆炸的正则不收; 短语级写法照收。"""
    for rx, why in (("(a+)+$", "分组"), (".*涨.*停", "超过 1 个"), ("(涨停)+", "分组"), ("(\\w{0,9}){0,9}", "嵌套"), ("(涨|跌)\\1", "不支持"), ("(a|aa){0,50}$", "多选一"), (".{0,9}a.{0,9}b.{0,9}c.{0,9}d", "超过 3 个"), ("x" * 130, "字")):
        chk, err = rf.validate_check(INTRADAY | {"answer_forbid": [rx]}, NAMES)
        assert chk is None and why in err, (rx, err)
    chk, err = rf.validate_check(INTRADAY | {"answer_forbid": ["告诉我.{0,8}(哪个标的|代码)"]}, NAMES)
    assert err == "" and chk["answer_forbid"]
