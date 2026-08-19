"""agent loop 撞上上游过载(529)时的行为。

背景: 一次问答要跑十几轮 LLM 调用, 每轮之间攒着工具结果。原来任何一轮抛异常都直接
中止并把错误甩给前端 —— 第 12 轮撞上 529 就等于前面 11 轮的取数全白干。
llm_client 内部已有一层退避重试, 这里测的是第二层: loop 层原样重发同一个 messages。
"""
import asyncio
from unittest import mock

import pytest

import services.llm_client as llm
import services.stock_agent as sa


@pytest.fixture(autouse=True)
def no_real_wait():
    """把 loop 层的等待压成 0, 否则一个用例要跑 20 秒。"""
    saved = sa._OVERLOAD_ROUND_WAIT_S
    sa._OVERLOAD_ROUND_WAIT_S = 0
    yield
    sa._OVERLOAD_ROUND_WAIT_S = saved


def _drain(gen):
    """把 async generator 收成 list。"""
    async def go():
        return [ev async for ev in gen]
    return asyncio.run(go())


def _resp(text="done"):
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


# ── _llm_round: 过载重发 ────────────────────────────────

def test_round_retries_on_overload_then_succeeds():
    """529 → 推一条等待提示 → 重发 → 拿到响应。messages 原样复用, 不重跑工具。"""
    calls = []

    def fake(messages, *a, **kw):
        calls.append(messages)
        if len(calls) < 3:
            raise llm.LLMOverloaded("overloaded", 529)
        return _resp("ok")

    msgs = [{"role": "user", "content": "为什么涨"}]
    with mock.patch.object(sa._llm, "call_claude_messages", side_effect=fake):
        out = _drain(sa._llm_round(msgs))

    kinds = [k for k, _ in out]
    assert kinds == ["wait", "wait", "resp"]
    assert out[-1][1] == _resp("ok")
    # 三次调用传的都是同一个 messages 对象 —— 攒下来的工具结果没丢
    assert all(m is msgs for m in calls)
    # 等待提示带的是可读文案, 不是异常字符串
    assert out[0][1]["tool"] == "llm_retry"
    assert "过载" in out[0][1]["label"]


def test_round_gives_up_after_limit_with_readable_error():
    """一直过载: 报错要说清是上游忙而不是用户配置错了。"""
    saved = sa._OVERLOAD_ROUND_RETRIES
    sa._OVERLOAD_ROUND_RETRIES = 2
    try:
        with mock.patch.object(sa._llm, "call_claude_messages",
                               side_effect=llm.LLMOverloaded("LLM 上游过载 (HTTP 529)", 529)) as m:
            out = _drain(sa._llm_round([{"role": "user", "content": "q"}]))
        assert [k for k, _ in out] == ["wait", "wait", "error"]
        assert m.call_count == 3                      # 1 + 2 次重发
        msg = out[-1][1]
        assert "上游" in msg and "配置" in msg
    finally:
        sa._OVERLOAD_ROUND_RETRIES = saved


def test_round_wait_grows_each_attempt():
    """等待时间递增(n×基数), 不是每次都等同样久。"""
    sa._OVERLOAD_ROUND_WAIT_S = 20
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    with mock.patch.object(sa._llm, "call_claude_messages",
                           side_effect=llm.LLMOverloaded("x", 529)), \
         mock.patch.object(sa.asyncio, "sleep", fake_sleep):
        _drain(sa._llm_round([{"role": "user", "content": "q"}]))
    assert slept == [20, 40, 60][:len(slept)]
    assert slept == sorted(slept) and len(set(slept)) == len(slept)


def test_round_does_not_retry_non_overload_errors():
    """401/400 这类重发多少次都一样, 立刻报错, 别让用户干等两分钟。"""
    with mock.patch.object(sa._llm, "call_claude_messages",
                           side_effect=RuntimeError("LLM API 401 鉴权失败")) as m:
        out = _drain(sa._llm_round([{"role": "user", "content": "q"}]))
    assert [k for k, _ in out] == ["error"]
    assert m.call_count == 1
    assert "401" in out[0][1]


# ── ask_stock_stream: 端到端 ────────────────────────────

def test_stream_survives_overload_mid_analysis():
    """第 2 轮过载不该让整场分析作废 —— 前面那轮的工具步骤要还在, 最后照样出答案。"""
    seq = [
        {"content": [{"type": "tool_use", "id": "t1", "name": "get_quote",
                      "input": {"code": "600519"}}], "stop_reason": "tool_use"},
        llm.LLMOverloaded("LLM 上游过载 (HTTP 529)", 529),
        {"content": [{"type": "text", "text": "结论"}], "stop_reason": "end_turn"},
    ]
    it = iter(seq)

    def fake(*a, **kw):
        nxt = next(it)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    async def fake_tool(tu):
        return {"code": "600519", "price": 1500}

    with mock.patch.object(sa._llm, "call_claude_messages", side_effect=fake), \
         mock.patch.object(sa, "_run_tool", fake_tool):
        evs = _drain(sa.ask_stock_stream("茅台怎么了"))

    types = [e["type"] for e in evs]
    assert types[-2:] == ["answer", "done"]
    assert [e["text"] for e in evs if e["type"] == "answer"] == ["结论"]
    # 过载提示以 step 形式推给前端(当 SSE 心跳用), 且工具步骤没被抹掉
    tools = [e.get("tool") for e in evs if e["type"] == "step"]
    assert "get_quote" in tools and "llm_retry" in tools


def test_stream_reports_error_when_overload_never_clears():
    saved = sa._OVERLOAD_ROUND_RETRIES
    sa._OVERLOAD_ROUND_RETRIES = 1
    try:
        with mock.patch.object(sa._llm, "call_claude_messages",
                               side_effect=llm.LLMOverloaded("LLM 上游过载 (HTTP 529)", 529)):
            evs = _drain(sa.ask_stock_stream("茅台怎么了"))
        assert evs[-1]["type"] == "error"
        assert "过载" in evs[-1]["error"]
    finally:
        sa._OVERLOAD_ROUND_RETRIES = saved


# ── ask_stock(非流式) ──────────────────────────────────

def test_ask_stock_retries_overload():
    """非流式版同样要重发, 只是没处推等待提示。"""
    it = iter([llm.LLMOverloaded("x", 529), _resp("结论")])

    def fake(*a, **kw):
        nxt = next(it)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    with mock.patch.object(sa._llm, "call_claude_messages", side_effect=fake):
        out = asyncio.run(sa.ask_stock("茅台怎么了"))
    assert out["answer"] == "结论"
    assert not out.get("error")
    assert "llm_retry" not in out.get("tools_used", [])   # 重试不算工具调用
