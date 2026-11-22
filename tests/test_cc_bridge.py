"""cc_bridge 的纯函数: 续接指纹、首条消息展开、工具与结果转换、流式事件拼装。"""
from services.cc_bridge import convert


def _ev_stream(blocks, stop):
    """按 Anthropic SSE 的事件序列造一条流。blocks: [("text", "…") | ("tool_use", name, id, json)]"""
    evs = [{"type": "message_start", "message": {"usage": {"input_tokens": 3, "cache_read_input_tokens": 100}}}]
    for i, b in enumerate(blocks):
        if b[0] == "text":
            evs.append({"type": "content_block_start", "index": i, "content_block": {"type": "text", "text": ""}})
            for piece in (b[1][:2], b[1][2:]):
                evs.append({"type": "content_block_delta", "index": i, "delta": {"type": "text_delta", "text": piece}})
        else:
            _, name, tid, raw = b
            evs.append({"type": "content_block_start", "index": i,
                        "content_block": {"type": "tool_use", "id": tid, "name": name, "input": {}}})
            evs.append({"type": "content_block_delta", "index": i,
                        "delta": {"type": "input_json_delta", "partial_json": raw}})
        evs.append({"type": "content_block_stop", "index": i})
    evs.append({"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 7}})
    evs.append({"type": "message_stop"})
    return evs


def test_reply_returns_licai_tool_calls_with_prefix_stripped():
    r = convert.Reply("m")
    seen = []
    for e in _ev_stream([("text", "先查价"), ("tool_use", "mcp__licai__get_price", "tu1", '{"code":"600001"}')], "tool_use"):
        r.event(e, seen.append)
    assert r.end_stream() is True
    resp = r.response()
    assert resp["stop_reason"] == "tool_use"
    assert resp["content"] == [{"type": "text", "text": "先查价"},
                               {"type": "tool_use", "id": "tu1", "name": "get_price", "input": {"code": "600001"}}]
    assert "".join(seen) == "先查价"
    assert resp["usage"]["cache_read_input_tokens"] == 100 and resp["usage"]["output_tokens"] == 7


def test_reply_spans_claude_code_own_web_search():
    r = convert.Reply("m")
    for e in _ev_stream([("text", "我搜一下"), ("tool_use", "WebSearch", "ws1", '{"query":"示例"}')], "tool_use"):
        r.event(e)
    # 只调了 Claude Code 自己的工具: 这次响应还没完, 等下一条流
    assert r.end_stream() is False
    for e in _ev_stream([("text", "结论如下")], "end_turn"):
        r.event(e)
    assert r.end_stream() is True
    resp = r.response()
    assert [b["type"] for b in resp["content"]] == ["text", "server_tool_use", "text"]
    assert resp["content"][1]["name"] == "web_search"
    assert resp["stop_reason"] == "end_turn"


def test_fingerprint_ignores_assistant_text_but_tracks_tool_ids():
    a1 = {"role": "assistant", "content": [{"type": "text", "text": "甲"}, {"type": "tool_use", "id": "x", "name": "n", "input": {}}]}
    a2 = {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "n", "input": {}}]}
    assert convert.fingerprint(a1) == convert.fingerprint(a2)
    u1 = {"role": "user", "content": "问题一"}
    u2 = {"role": "user", "content": [{"type": "text", "text": "问题一"}]}
    assert convert.fingerprint(u1) == convert.fingerprint(u2)
    assert convert.fingerprint(u1) != convert.fingerprint({"role": "user", "content": "问题二"})


def test_flatten_single_and_history():
    assert convert.flatten([{"role": "user", "content": "示例股份怎么样"}]) == "示例股份怎么样"
    msgs = [
        {"role": "user", "content": "示例股份怎么样"},
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "get_quote", "input": {"code": "600001"}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "x" * 7000}]},
    ]
    text = convert.flatten(msgs)
    assert "[调用工具 get_quote]" in text and "已截断" in text
    assert text.rstrip().endswith("请基于以上工具结果接着完成上面的回答。")


def test_mcp_conversions():
    tools, web = convert.mcp_tools([
        {"type": "web_search_20250305", "name": "web_search"},
        {"name": "calc", "description": "算", "input_schema": {"type": "object"}},
    ])
    assert web is True
    assert tools == [{"name": "calc", "description": "算", "inputSchema": {"type": "object"}}]
    res = convert.mcp_result({"type": "tool_result", "tool_use_id": "t", "is_error": True, "content": [
        {"type": "text", "text": "出错"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAA"}}]}, "用户补充: 再看看")
    assert res["is_error"] is True
    assert res["content"] == [{"type": "text", "text": "出错"},
                              {"type": "image", "data": "AAA", "mimeType": "image/png"},
                              {"type": "text", "text": "用户补充: 再看看"}]


def test_normalize_format_accepts_claude_code():
    from services import llm_formats
    assert llm_formats.normalize_format("claude_code") == llm_formats.CLAUDE_CODE
    assert llm_formats.normalize_format("cc") == llm_formats.CLAUDE_CODE


def test_history_questions_matches_followup_shape():
    seed = [{"role": "user", "content": [{"type": "text", "text": "状态块"}, {"type": "text", "text": "上证怎么样"}]}]
    follow = [{"role": "user", "content": "上证怎么样"}, {"role": "assistant", "content": "涨了"},
              {"role": "user", "content": [{"type": "text", "text": "状态块2"}, {"type": "text", "text": "那深证呢"}]}]
    assert convert.history_questions(follow) == [convert.question_key(seed[0])]
    # 工具往返中的历史不是这种形状
    assert convert.history_questions(seed) is None
