"""Anthropic ⇄ OpenAI(Responses / Chat Completions) 协议互转, 以及 llm_client 按格式分流。
不发真实请求: HTTP 层用假响应替换。"""
import json
from unittest import mock

import pytest

import services.llm_client as llm
import services.llm_formats as f

TOOLS = [
    {"name": "get_quote", "description": "查行情",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}}},
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 3},
]
IMG = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}

# 一次完整的工具往返: 提问 → 模型调工具 → 工具结果(带图) → 追问
CONVO = [
    {"role": "user", "content": "看看 TEST01"},
    {"role": "assistant", "content": [
        {"type": "thinking", "thinking": "想一下", "signature": "sig"},
        {"type": "text", "text": "我查一下。"},
        {"type": "tool_use", "id": "call_1", "name": "get_quote", "input": {"code": "TEST01"}},
    ]},
    {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "call_1",
         "content": [{"type": "text", "text": '{"price": 1.0}'}, IMG]},
        {"type": "text", "text": "顺便说说趋势"},
    ]},
]


def test_normalize_format():
    assert f.normalize_format("") == "anthropic"
    assert f.normalize_format("OpenAI_Responses") == "openai-responses"
    assert f.normalize_format("chat") == "openai-chat"
    with pytest.raises(ValueError):
        f.normalize_format("gemini")


def test_responses_request_shape():
    p = f.to_responses_request(CONVO, [{"type": "text", "text": "身份"}, {"type": "text", "text": "规则"}],
                               "m", 1000, TOOLS)
    assert p["instructions"] == "身份\n\n规则"
    assert p["max_output_tokens"] == 1000 and p["store"] is False
    assert p["tools"] == [{"type": "function", "name": "get_quote", "description": "查行情",
                           "parameters": TOOLS[0]["input_schema"]}]
    items = p["input"]
    assert items[0] == {"role": "user", "content": "看看 TEST01"}
    assert items[1] == {"role": "assistant", "content": "我查一下。"}       # thinking 丢弃
    assert items[2]["type"] == "function_call" and items[2]["call_id"] == "call_1"
    assert json.loads(items[2]["arguments"]) == {"code": "TEST01"}
    assert items[3] == {"type": "function_call_output", "call_id": "call_1", "output": '{"price": 1.0}'}
    assert items[4] == {"role": "user", "content": [{"type": "input_text", "text": "顺便说说趋势"}]}
    assert items[5]["content"][0] == {"type": "input_image", "image_url": "data:image/png;base64,QUJD"}
    assert "stream" not in p


def test_chat_request_shape():
    p = f.to_chat_request(CONVO, "规则", "m", 500, TOOLS, stream=True)
    msgs = p["messages"]
    assert msgs[0] == {"role": "system", "content": "规则"}
    assert msgs[2]["role"] == "assistant" and msgs[2]["content"] == "我查一下。"
    assert msgs[2]["tool_calls"][0]["function"]["name"] == "get_quote"
    assert msgs[3] == {"role": "tool", "tool_call_id": "call_1", "content": '{"price": 1.0}'}
    # 追问文本 + 工具结果里的图合成一条 user 消息, 排在 tool 消息之后
    assert msgs[4]["role"] == "user"
    assert msgs[4]["content"][0] == {"type": "text", "text": "顺便说说趋势"}
    assert msgs[4]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert [t["function"]["name"] for t in p["tools"]] == ["get_quote"]
    assert p["stream"] is True and p["stream_options"] == {"include_usage": True}


def test_from_responses_tool_call_and_usage():
    out = f.from_responses({
        "id": "resp_1", "status": "completed", "model": "m",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "好的"}]},
            {"type": "function_call", "id": "fc_1", "call_id": "call_9", "name": "get_quote",
             "arguments": '{"code":"TEST02"}'},
        ],
        "usage": {"input_tokens": 100, "output_tokens": 20, "input_tokens_details": {"cached_tokens": 60}},
    })
    assert out["content"] == [{"type": "text", "text": "好的"},
                              {"type": "tool_use", "id": "call_9", "name": "get_quote",
                               "input": {"code": "TEST02"}}]
    assert out["stop_reason"] == "tool_use"
    assert out["usage"]["input_tokens"] == 40 and out["usage"]["cache_read_input_tokens"] == 60


def test_from_responses_truncated():
    out = f.from_responses({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                            "output": []})
    assert out["stop_reason"] == "max_tokens" and out["content"] == []


def test_from_chat():
    out = f.from_chat({"choices": [{"finish_reason": "tool_calls", "message": {
        "content": None, "tool_calls": [{"id": "c1", "type": "function",
                                         "function": {"name": "get_quote", "arguments": "坏的json"}}]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3}})
    assert out["content"] == [{"type": "tool_use", "id": "c1", "name": "get_quote", "input": {}}]
    assert out["stop_reason"] == "tool_use"
    assert f.from_chat({"choices": [{"finish_reason": "length", "message": {"content": "半句"}}]}
                       )["stop_reason"] == "max_tokens"


def test_chat_stream_assembles_tool_calls():
    seen = []
    s = f.ChatStream(seen.append)
    for d in [
        {"id": "x", "choices": [{"delta": {"content": "先"}}]},
        {"choices": [{"delta": {"content": "查"}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "get_quote",
                                                                                    "arguments": '{"co'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'de":"T"}'}}]}}]},
        {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2}},
    ]:
        s.feed(d)
    out = s.result()
    assert seen == ["先", "查"]
    assert out["content"][0] == {"type": "text", "text": "先查"}
    assert out["content"][1]["input"] == {"code": "T"}
    assert out["stop_reason"] == "tool_use" and out["usage"]["output_tokens"] == 2


def test_responses_stream_and_errors():
    seen = []
    s = f.ResponsesStream(seen.append)
    s.feed({"type": "response.output_text.delta", "delta": "你好"})
    s.feed({"type": "response.completed", "response": {
        "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "你好"}]}]}})
    assert seen == ["你好"] and s.result()["content"][0]["text"] == "你好"

    s = f.ResponsesStream(lambda _: None)
    with pytest.raises(f.StreamError) as e:
        s.feed({"type": "error", "code": "rate_limit_exceeded", "message": "slow down"})
    assert e.value.overloaded
    with pytest.raises(f.StreamError):
        f.ResponsesStream(lambda _: None).result()     # 没收到结束事件


# ── llm_client 按格式分流 ────────────────────────────

@pytest.fixture
def openai_cfg():
    saved = (llm._api_format, llm._base_url, llm._api_key, llm._api_key_header, llm._api_key_prefix)
    llm._api_key = "sk-test-0000"
    llm._api_key_header = llm._DEFAULT_API_KEY_HEADER
    llm._api_key_prefix = ""
    yield
    llm._api_format, llm._base_url, llm._api_key, llm._api_key_header, llm._api_key_prefix = saved


class _Resp:
    def __init__(self, body=None, lines=None, status=200):
        self.status_code, self._body, self._lines = status, body, lines or []
        self.ok, self.headers, self.text = status < 400, {}, json.dumps(body or {})

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=False):
        return iter(x.encode() for x in self._lines)

    def close(self):
        pass


@pytest.mark.parametrize("fmt,base,url", [
    ("openai-responses", "https://relay.example/v1", "https://relay.example/v1/responses"),
    ("openai-chat", "https://relay.example", "https://relay.example/v1/chat/completions"),
    ("openai-chat", "https://relay.example/api/v3/", "https://relay.example/api/v3/chat/completions"),
    ("anthropic", "https://relay.example/v1", "https://relay.example/v1/messages"),
])
def test_url_per_format(openai_cfg, fmt, base, url):
    llm._api_format, llm._base_url = fmt, base
    assert llm._build_api_url() == url


def test_openai_headers_and_web_search_off(openai_cfg):
    llm._api_format, llm._base_url = "openai-responses", "https://api.anthropic.com"
    h = llm._build_headers("sk-test-0000", False)
    assert h["Authorization"] == "Bearer sk-test-0000" and "anthropic-version" not in h
    assert not llm._is_anthropic_official()      # OpenAI 协议下不发 web_search / cache_control
    llm._api_key_header = "api-key"
    assert llm._build_headers("k", False) == {"Content-Type": "application/json", "api-key": "k"}


def test_openai_requires_key(openai_cfg):
    llm._api_format, llm._api_key = "openai-chat", ""
    with pytest.raises(RuntimeError, match="API key"):
        llm._resolve_auth()


def test_call_messages_via_chat(openai_cfg):
    llm._api_format, llm._base_url = "openai-chat", "https://relay.example/v1"
    body = {"choices": [{"finish_reason": "stop", "message": {"content": "结论"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1}}
    with mock.patch.object(llm._llm_session, "post", return_value=_Resp(body)) as post:
        out = llm.call_claude_messages(CONVO, "规则", "fast", 300, tools=TOOLS)
        assert llm.call_claude("问", "规则", "fast", 300) == "结论"
    url = post.call_args_list[0].args[0]
    sent = post.call_args_list[0].kwargs["json"]
    assert url == "https://relay.example/v1/chat/completions"
    assert sent["messages"][0]["role"] == "system" and "cache_control" not in json.dumps(sent)
    assert out["content"] == [{"type": "text", "text": "结论"}] and out["stop_reason"] == "end_turn"


def test_call_messages_streaming_responses(openai_cfg):
    llm._api_format, llm._base_url = "openai-responses", "https://relay.example/v1"
    final = {"status": "completed", "output": [
        {"type": "function_call", "call_id": "c7", "name": "get_quote", "arguments": "{}"}]}
    lines = ["event: response.output_text.delta",
             "data: " + json.dumps({"type": "response.output_text.delta", "delta": "嗯"}),
             "", "data: " + json.dumps({"type": "response.completed", "response": final})]
    got = []
    with mock.patch.object(llm._llm_session, "post", return_value=_Resp(lines=lines)) as post:
        out = llm.call_claude_messages(CONVO, None, "smart", 300, tools=TOOLS, on_text=got.append)
    assert post.call_args.kwargs["json"]["stream"] is True and post.call_args.kwargs["stream"] is True
    assert got == ["嗯"] and out["stop_reason"] == "tool_use" and out["content"][0]["id"] == "c7"


def test_stream_overload_maps_to_llm_overloaded(openai_cfg):
    llm._api_format, llm._base_url = "openai-chat", "https://relay.example/v1"
    lines = ["data: " + json.dumps({"error": {"type": "server_error", "message": "busy"}})]
    with mock.patch.object(llm._llm_session, "post", return_value=_Resp(lines=lines)):
        with pytest.raises(llm.LLMOverloaded):
            llm.call_claude_messages(CONVO, None, "smart", 300, on_text=lambda _: None)
