"""答案边生成边推: SSE 拼回的响应要能原样回传, 初稿事件要在该撤回时撤回。"""
import asyncio
import json
from unittest import mock

import pytest

import services.llm_client as llm
import services.stock_agent as sa


class _Resp:
    def __init__(self, events):
        self._lines = []
        for ev in events:
            self._lines += [f"event: {ev['type']}".encode(), f"data: {json.dumps(ev)}".encode(), b""]
        self.closed = False

    def iter_lines(self, decode_unicode=False):
        yield from self._lines

    def close(self):
        self.closed = True


def _sse_tool_round():
    return [
        {"type": "message_start", "message": {"id": "m1", "role": "assistant", "content": [],
                                              "usage": {"input_tokens": 10, "cache_read_input_tokens": 5}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "想"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "先查"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "citations_delta",
                                                              "citation": {"url": "u", "cited_text": "c"}}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2,
         "content_block": {"type": "tool_use", "id": "t1", "name": "get_quote", "input": {}}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": '{"co'}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "input_json_delta", "partial_json": 'de":"600519"}'}},
        {"type": "content_block_stop", "index": 2},
        {"type": "content_block_start", "index": 3,
         "content_block": {"type": "tool_use", "id": "t2", "name": "get_holdings", "input": {}}},
        {"type": "content_block_stop", "index": 3},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 42}},
        {"type": "message_stop"},
    ]


def test_read_stream_rebuilds_full_response():
    seen = []
    r = _Resp(_sse_tool_round())
    d = llm._read_stream(r, seen.append)
    assert seen == ["先查"] and r.closed
    assert d["stop_reason"] == "tool_use"
    assert d["usage"] == {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 42}
    th, tx, tu, tu2 = d["content"]
    assert th == {"type": "thinking", "thinking": "想", "signature": "sig"}   # 回传要带签名
    assert tx["text"] == "先查" and tx["citations"] == [{"url": "u", "cited_text": "c"}]
    assert tu["input"] == {"code": "600519"}
    assert tu2["input"] == {}                     # 无参工具: 没有分片也要是 {}


def test_read_stream_overload_midway_is_retryable():
    evs = _sse_tool_round()[:7] + [{"type": "error", "error": {"type": "overloaded_error", "message": "x"}}]
    with pytest.raises(llm.LLMOverloaded):
        llm._read_stream(_Resp(evs), lambda t: None)


def _drain(gen):
    async def go():
        return [ev async for ev in gen]
    return asyncio.run(go())


def _fake_llm(rounds):
    """每次调用依次取一轮: (正文增量列表, 响应)。增量经 on_text 推出, 模拟流式。"""
    it = iter(rounds)

    def fake(messages, *a, on_text=None, **kw):
        deltas, resp = next(it)
        for t in deltas:
            if on_text:
                on_text(t)
        return resp
    return fake


@pytest.fixture
def quiet(monkeypatch):
    async def no_tools():
        return []
    monkeypatch.setattr(sa, "_active_tools", no_tools)
    monkeypatch.setattr(sa, "_system", lambda: "s")
    monkeypatch.setattr(sa, "_DRAFT_FLUSH_S", 0)
    monkeypatch.setattr(sa._guard, "inspect", lambda *a, **k: [])
    async def run_tool(tu):
        return {"price": 1}
    monkeypatch.setattr(sa, "_run_tool", run_tool)


LONG = "茅台今天收跌, 成交额较前一日放大, " * 4


def test_final_round_streams_draft_then_answer(quiet):
    resp = {"content": [{"type": "text", "text": LONG}], "stop_reason": "end_turn"}
    with mock.patch.object(sa._llm, "call_claude_messages", side_effect=_fake_llm([([LONG[:30], LONG[30:]], resp)])):
        evs = _drain(sa.ask_stock_stream("茅台怎么了"))
    kinds = [e["type"] for e in evs]
    drafts = "".join(e["text"] for e in evs if e["type"] == "draft")
    assert "draft" in kinds and kinds.index("draft") < kinds.index("answer")
    assert drafts == LONG and "draft_reset" not in kinds


def test_tool_round_text_is_withdrawn(quiet):
    tool = {"content": [{"type": "text", "text": LONG},
                        {"type": "tool_use", "id": "t", "name": "get_quote", "input": {"code": "600519"}}],
            "stop_reason": "tool_use"}
    final = {"content": [{"type": "text", "text": "结论"}], "stop_reason": "end_turn"}
    with mock.patch.object(sa._llm, "call_claude_messages",
                           side_effect=_fake_llm([([LONG], tool), (["结论"], final)])):
        evs = _drain(sa.ask_stock_stream("茅台怎么了"))
    kinds = [e["type"] for e in evs]
    assert kinds.index("draft") < kinds.index("draft_reset") < kinds.index("step")
    assert evs[kinds.index("answer")]["text"].endswith("结论")


def test_short_preamble_never_flashes(quiet):
    tool = {"content": [{"type": "text", "text": "我先查一下"},
                        {"type": "tool_use", "id": "t", "name": "get_quote", "input": {}}],
            "stop_reason": "tool_use"}
    final = {"content": [{"type": "text", "text": "好"}], "stop_reason": "end_turn"}
    with mock.patch.object(sa._llm, "call_claude_messages",
                           side_effect=_fake_llm([(["我先查一下"], tool), (["好"], final)])):
        kinds = [e["type"] for e in _drain(sa.ask_stock_stream("茅台怎么了"))]
    assert "draft" not in kinds and "draft_reset" not in kinds


def test_guard_repair_withdraws_draft(quiet, monkeypatch):
    calls = []
    monkeypatch.setattr(sa._guard, "inspect", lambda *a, **k: [] if calls.append(1) or len(calls) > 1 else ["缺数"])
    monkeypatch.setattr(sa._guard, "repair_message", lambda p: {"role": "user", "content": "补"})
    r1 = {"content": [{"type": "text", "text": LONG}], "stop_reason": "end_turn"}
    r2 = {"content": [{"type": "text", "text": LONG + "补齐"}], "stop_reason": "end_turn"}
    with mock.patch.object(sa._llm, "call_claude_messages",
                           side_effect=_fake_llm([([LONG], r1), ([LONG + "补齐"], r2)])):
        evs = _drain(sa.ask_stock_stream("茅台怎么了"))
    kinds = [e["type"] for e in evs]
    assert kinds.index("draft_reset") < kinds.index("step") < kinds.index("answer")
    assert evs[kinds.index("answer")]["text"].endswith("补齐")


def test_overload_retry_withdraws_partial_draft(quiet, monkeypatch):
    monkeypatch.setattr(sa, "_OVERLOAD_ROUND_WAIT_S", 0)
    n = []

    def fake(messages, *a, on_text=None, **kw):
        n.append(1)
        on_text(LONG)
        if len(n) == 1:
            raise llm.LLMOverloaded("流式中途断开", 0)
        return {"content": [{"type": "text", "text": LONG}], "stop_reason": "end_turn"}

    with mock.patch.object(sa._llm, "call_claude_messages", side_effect=fake):
        kinds = [e["type"] for e in _drain(sa.ask_stock_stream("茅台怎么了"))]
    assert kinds[:3] == ["draft", "draft_reset", "step"] and kinds[-2:] == ["answer", "done"]
