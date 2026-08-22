"""回答护栏。

同一套判定既做离线评测又做在线兜底。这里测两层:
  1. 规则本身 —— 拿真实错答验它抓不抓得住
  2. 接进 agent loop 之后 —— 不违规时**不能**多花一次调用(那是常态, 成本要为零),
     违规时只补一轮就收手(不能来回拉锯)
"""
import asyncio
from unittest import mock

import services.answer_guard as G
import services.stock_agent as sa


# ── 规则 ────────────────────────────────────────────────

QUOTE_WITH_EXT = {"code": "US.MRVL", "price": 216.0, "change_pct": -7.82,
                  "prev_close": 234.33, "session": "pre",
                  "ext_hours": {"label": "盘前", "price": 240.56, "change_pct": 11.37,
                                "as_of": "Aug 19 08:43AM EDT"}}

# 2026-08-19 界面上的实际输出
BAD = "迈威尔现在不是涨，是大跌——当前价 216.00 美元，跌 7.82%（前收 234.32）。"


def test_r1_missing_session_label():
    bad = G.check_against_tools(BAD, ["get_quote"], {"get_quote": [QUOTE_WITH_EXT]})
    assert len(bad) == 1
    assert "没在答案里点明时点" in bad[0] and "240.56" in bad[0]


def test_r1_passes_when_both_timepoints_stated():
    ok = "8/18 收盘 216.00 跌 7.82%；8/19 盘前 240.56 涨 11.37%。"
    assert G.check_against_tools(ok, ["get_quote"], {"get_quote": [QUOTE_WITH_EXT]}) == []


def test_r1_silent_when_no_ext_hours():
    """盘中时段没有 ext_hours, 这条不该多话。"""
    q = {"code": "US.MRVL", "price": 216.0, "session": "regular"}
    assert G.check_against_tools("现在 216.00", ["get_quote"], {"get_quote": [q]}) == []


def test_r2_stale_news_without_websearch():
    news = {"news": [{"title": "x"}] * 10, "latest_time": "2026-01-02 10:00:00"}
    bad = G.check_against_tools("涨是因为板块回暖。", ["get_news"], {"get_news": [news]})
    assert any("没有 web_search" in b for b in bad)


def test_r2_ok_when_searched():
    news = {"news": [{"title": "x"}], "latest_time": "2026-01-02 10:00:00"}
    assert G.check_against_tools("催化是…", ["get_news", "web_search"],
                                 {"get_news": [news]}) == []


def test_r3_fabricated_index_amount():
    """工具明说美股指数只有成交量, 答案里出现它的成交额就是编的。"""
    g = {"us_index": [{"name": "纳斯达克", "volume": 6.4e9},
                      {"name": "上证指数", "amount": 1.2e12}]}
    bad = G.check_against_tools("纳斯达克成交额 22.8 万亿。", ["get_global_indices"],
                                {"get_global_indices": [g]})
    assert any("纳斯达克" in b and "却出现了" in b for b in bad)
    # 有 amount 的那个照常说没问题
    assert G.check_against_tools("上证指数成交额 1.22 万亿。", ["get_global_indices"],
                                 {"get_global_indices": [g]}) == []


def test_global_rules():
    assert any("HTML" in b for b in G.global_checks("<mark>高亮</mark>", []))
    assert G.global_checks("这个位置建议买入。", [])
    assert G.global_checks("**要点**：放量。", []) == []


def test_unknown_tag_also_counts():
    """露出来的那次不是 <mark>, 是 <augment_code_snippet path=... mode=...> —— 联网读回来的
    正文里夹带的。白名单枚举永远追不上, 所以按"标签形状"判。"""
    for t in ('<augment_code_snippet path="a.py" mode="EXCERPT">正文',
              '</augment_code_snippet>', '<think>内心戏</think>', '<Foo-Bar attr=1 />'):
        assert any("标签" in b for b in G.global_checks(t, [])), t


def test_math_comparisons_are_not_tags():
    """不能把不等号当标签: 这类写法在答案里很常见, 误判会触发一轮无意义的重答。"""
    for t in ("占比 <0.5% 的小票", "若 A<B 则跑输", "市值 3<2 万亿", "PE <20 倍算便宜",
              "成交额 1.8 万亿, 涨停 63 家"):
        assert G.global_checks(t, []) == [], t


def test_inspect_combines_both_layers():
    bad = G.inspect("<b>现在大跌</b>", ["get_quote"], {"get_quote": [QUOTE_WITH_EXT]})
    assert len(bad) == 2      # 裸标签 + 缺时点


def test_repair_message_shape():
    m = G.repair_message(["问题一", "问题二"])
    assert m["role"] == "user"
    assert "问题一" in m["content"] and "问题二" in m["content"]
    assert "直接给最终版" in m["content"]


# ── 接进 loop 之后 ──────────────────────────────────────

def _resp(text=None, tool=None):
    if tool:
        return {"content": [{"type": "tool_use", "id": "t1", "name": tool,
                             "input": {"code": "MRVL"}}], "stop_reason": "tool_use"}
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def _run(seq, tool_out):
    it = iter(seq)

    async def fake_tool(tu):
        return tool_out

    with mock.patch.object(sa._llm, "call_claude_messages", side_effect=lambda *a, **k: next(it)) as m, \
         mock.patch.object(sa, "_run_tool", fake_tool):
        out = asyncio.run(sa.ask_stock("迈威尔现在涨还是跌"))
    return out, m.call_count


def test_clean_answer_costs_no_extra_call():
    """常态: 回答没问题时护栏一次调用都不额外花。"""
    good = "8/18 收盘 216.00 跌 7.82%；8/19 盘前 240.56 涨 11.37%。"
    out, calls = _run([_resp(tool="get_quote"), _resp(good)], QUOTE_WITH_EXT)
    assert out["answer"] == good
    assert calls == 2, f"多花了调用: {calls}"
    assert out.get("guard_repaired") is False


def test_violating_answer_triggers_one_repair():
    fixed = "8/18 收盘 216.00 跌 7.82%；8/19 盘前 240.56 涨 11.37%。"
    out, calls = _run([_resp(tool="get_quote"), _resp(BAD), _resp(fixed)], QUOTE_WITH_EXT)
    assert out["answer"] == fixed
    assert calls == 3                      # 多一次, 就一次
    assert out.get("guard_repaired") is True


def test_repair_runs_at_most_once():
    """模型改完还是不合规时不能来回拉锯 —— 第二版照原样返回, 不再补。"""
    out, calls = _run([_resp(tool="get_quote"), _resp(BAD), _resp(BAD)], QUOTE_WITH_EXT)
    assert out["answer"] == BAD
    assert calls == 3


def test_repair_prompt_carries_the_problem():
    """补的那一轮必须把具体问题带给模型, 不是笼统说'重写'。"""
    seen = []

    def cap(messages, *a, **k):
        seen.append([m for m in messages])
        return _resp(BAD) if len(seen) == 2 else (
            _resp(tool="get_quote") if len(seen) == 1 else _resp("已修正: 盘前 240.56"))

    async def fake_tool(tu):
        return QUOTE_WITH_EXT

    with mock.patch.object(sa._llm, "call_claude_messages", side_effect=cap), \
         mock.patch.object(sa, "_run_tool", fake_tool):
        asyncio.run(sa.ask_stock("迈威尔现在涨还是跌"))

    last_user = [m for m in seen[-1] if m["role"] == "user"][-1]
    assert "点明时点" in str(last_user["content"])
