"""Anthropic Messages ⇄ OpenAI 两种协议(Responses / Chat Completions)的请求与响应互转。

agent 主循环、各调用方一律按 Anthropic Messages 的结构读写(content 块、tool_use / tool_result、
stop_reason、usage)。接 OpenAI 协议的端点(官方、各类中转站、one-api/new-api 网关)时, 在这里把请求
翻译出去, 把响应翻译回同样的形状, 上层代码不用感知协议差异。

只翻译两边都有的东西: 文本、图片(base64)、自定义函数工具、工具调用与结果、停止原因、用量。
Anthropic 独有的块(thinking、server_tool_use、web_search_tool_result、cache_control)在出站时丢弃 ——
它们只会出现在官方 Anthropic 端点的对话里, 换协议后的请求里不会再有。

全部是纯函数, 不碰网络, 便于单测。
"""
from __future__ import annotations

import json

ANTHROPIC = "anthropic"
OPENAI_RESPONSES = "openai-responses"
OPENAI_CHAT = "openai-chat"
FORMATS = (ANTHROPIC, OPENAI_RESPONSES, OPENAI_CHAT)

# 各协议相对 /v1 的路径
PATHS = {
    ANTHROPIC: "/messages",
    OPENAI_RESPONSES: "/responses",
    OPENAI_CHAT: "/chat/completions",
}


def normalize_format(fmt: str | None) -> str:
    """容忍常见写法: openai_responses / responses / chat / openai(=chat) 等。"""
    f = str(fmt or "").strip().lower().replace("_", "-")
    if f in ("", "anthropic", "claude", "messages"):
        return ANTHROPIC
    if f in ("openai-responses", "responses", "response"):
        return OPENAI_RESPONSES
    if f in ("openai-chat", "chat", "chat-completions", "openai", "openai-compatible"):
        return OPENAI_CHAT
    raise ValueError(f"不支持的 API 格式: {fmt!r}, 可选 {', '.join(FORMATS)}")


# ── 公共小工具 ─────────────────────────────────────────

def _system_text(system) -> str:
    """system 可能是字符串, 也可能是 Anthropic 的 text 块列表。"""
    if not system:
        return ""
    if isinstance(system, str):
        return system
    return "\n\n".join(b.get("text", "") for b in system if isinstance(b, dict) and b.get("text"))


def _data_uri(block: dict) -> str | None:
    src = block.get("source") or {}
    if src.get("type") == "base64" and src.get("data"):
        return f"data:{src.get('media_type') or 'image/png'};base64,{src['data']}"
    if src.get("type") == "url" and src.get("url"):
        return src["url"]
    return None


def _tool_result_parts(block: dict) -> tuple[str, list[str]]:
    """tool_result.content → (文本, 图片 data URI 列表)。content 可以是字符串或 text/image 块列表。"""
    c = block.get("content")
    if c is None:
        return "", []
    if isinstance(c, str):
        return c, []
    texts, images = [], []
    for b in c:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            texts.append(b.get("text", ""))
        elif b.get("type") == "image":
            uri = _data_uri(b)
            if uri:
                images.append(uri)
    return "\n".join(texts), images


def _function_tools(tools: list | None) -> list[dict]:
    """只保留自定义函数工具(有 input_schema 的); 服务端工具各家不通用。"""
    out = []
    for t in tools or []:
        if not isinstance(t, dict) or "input_schema" not in t or not t.get("name"):
            continue
        out.append({"name": t["name"], "description": t.get("description", ""),
                    "parameters": t.get("input_schema") or {"type": "object", "properties": {}}})
    return out


def _parse_args(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw or "{}")
        return v if isinstance(v, dict) else {"value": v}
    except (TypeError, ValueError):
        return {}


# ── 请求: Anthropic → OpenAI Responses ──────────────────

def to_responses_request(messages: list, system, model: str, max_tokens: int,
                         tools: list | None = None, stream: bool = False) -> dict:
    items: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            items.append({"role": role, "content": content})
            continue
        parts: list[dict] = []        # 当前这条消息里攒着的文本/图片
        late_images: list[str] = []   # 工具结果里的图: 跟在所有 function_call_output 之后单独发

        def flush():
            if not parts:
                return
            if role == "assistant":
                items.append({"role": "assistant",
                              "content": "".join(p.get("text", "") for p in parts)})
            else:
                items.append({"role": role, "content": list(parts)})
            parts.clear()

        for b in content or []:
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if kind == "text":
                parts.append({"type": "output_text" if role == "assistant" else "input_text",
                              "text": b.get("text", "")})
            elif kind == "image" and role != "assistant":
                uri = _data_uri(b)
                if uri:
                    parts.append({"type": "input_image", "image_url": uri})
            elif kind == "tool_use":
                flush()
                items.append({"type": "function_call", "call_id": b.get("id"), "name": b.get("name"),
                              "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)})
            elif kind == "tool_result":
                flush()
                text, imgs = _tool_result_parts(b)
                items.append({"type": "function_call_output", "call_id": b.get("tool_use_id"),
                              "output": text or ("(error)" if b.get("is_error") else "")})
                late_images.extend(imgs)
        flush()
        if late_images:
            items.append({"role": "user", "content": [{"type": "input_image", "image_url": u}
                                                      for u in late_images]})

    payload: dict = {"model": model, "input": items, "max_output_tokens": max_tokens, "store": False}
    sys_text = _system_text(system)
    if sys_text:
        payload["instructions"] = sys_text
    fts = _function_tools(tools)
    if fts:
        payload["tools"] = [{"type": "function", **t} for t in fts]
    if stream:
        payload["stream"] = True
    return payload


# ── 请求: Anthropic → OpenAI Chat Completions ───────────

def to_chat_request(messages: list, system, model: str, max_tokens: int,
                    tools: list | None = None, stream: bool = False) -> dict:
    out: list[dict] = []
    sys_text = _system_text(system)
    if sys_text:
        out.append({"role": "system", "content": sys_text})

    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text = "".join(b.get("text", "") for b in content or []
                           if isinstance(b, dict) and b.get("type") == "text")
            calls = [{"id": b.get("id"), "type": "function",
                      "function": {"name": b.get("name"),
                                   "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False)}}
                     for b in content or [] if isinstance(b, dict) and b.get("type") == "tool_use"]
            msg: dict = {"role": "assistant", "content": text or None}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
            continue
        # user: 工具结果各成一条 tool 消息(必须紧跟在 assistant 的 tool_calls 后面), 文本/图片随后
        parts: list[dict] = []
        late_images: list[str] = []
        for b in content or []:
            if not isinstance(b, dict):
                continue
            kind = b.get("type")
            if kind == "tool_result":
                text, imgs = _tool_result_parts(b)
                out.append({"role": "tool", "tool_call_id": b.get("tool_use_id"),
                            "content": text or ("(error)" if b.get("is_error") else "")})
                late_images.extend(imgs)
            elif kind == "text":
                parts.append({"type": "text", "text": b.get("text", "")})
            elif kind == "image":
                uri = _data_uri(b)
                if uri:
                    parts.append({"type": "image_url", "image_url": {"url": uri}})
        parts.extend({"type": "image_url", "image_url": {"url": u}} for u in late_images)
        if parts:
            # 纯文本时用字符串: 部分兼容实现不接受数组形式的 content
            if all(p["type"] == "text" for p in parts):
                out.append({"role": role, "content": "\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": role, "content": parts})

    payload: dict = {"model": model, "messages": out, "max_tokens": max_tokens}
    fts = _function_tools(tools)
    if fts:
        payload["tools"] = [{"type": "function", "function": t} for t in fts]
    if stream:
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
    return payload


# ── 响应: OpenAI → Anthropic 形状 ───────────────────────

def _usage(inp: int, out: int, cached: int) -> dict:
    """Anthropic 的 input_tokens 不含缓存读取部分, OpenAI 的含 —— 拆开对齐。"""
    cached = min(cached, inp)
    return {"input_tokens": inp - cached, "output_tokens": out,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": cached}


def from_responses(data: dict) -> dict:
    content: list[dict] = []
    for item in data.get("output") or []:
        kind = item.get("type")
        if kind == "message":
            for c in item.get("content") or []:
                if c.get("type") in ("output_text", "text") and c.get("text"):
                    content.append({"type": "text", "text": c["text"]})
                elif c.get("type") == "refusal" and c.get("refusal"):
                    content.append({"type": "text", "text": c["refusal"]})
        elif kind == "function_call":
            content.append({"type": "tool_use", "id": item.get("call_id") or item.get("id"),
                            "name": item.get("name"), "input": _parse_args(item.get("arguments"))})
    if not content and data.get("output_text"):     # 个别中转站只给汇总字段
        content.append({"type": "text", "text": data["output_text"]})

    if any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    elif data.get("status") == "incomplete" and \
            (data.get("incomplete_details") or {}).get("reason") == "max_output_tokens":
        stop = "max_tokens"
    else:
        stop = "end_turn"
    u = data.get("usage") or {}
    return {"id": data.get("id"), "type": "message", "role": "assistant", "model": data.get("model"),
            "content": content, "stop_reason": stop,
            "usage": _usage(int(u.get("input_tokens") or 0), int(u.get("output_tokens") or 0),
                            int((u.get("input_tokens_details") or {}).get("cached_tokens") or 0))}


_CHAT_STOP = {"tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens",
              "content_filter": "refusal"}


def from_chat(data: dict) -> dict:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    content: list[dict] = []
    text = msg.get("content")
    if isinstance(text, list):      # 少数实现把 content 也给成数组
        text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
    if text:
        content.append({"type": "text", "text": text})
    for call in msg.get("tool_calls") or []:
        fn = call.get("function") or {}
        content.append({"type": "tool_use", "id": call.get("id"), "name": fn.get("name"),
                        "input": _parse_args(fn.get("arguments"))})
    stop = _CHAT_STOP.get(choice.get("finish_reason"), "end_turn")
    if stop != "tool_use" and any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    u = data.get("usage") or {}
    return {"id": data.get("id"), "type": "message", "role": "assistant", "model": data.get("model"),
            "content": content, "stop_reason": stop,
            "usage": _usage(int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0),
                            int((u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0))}


# ── 流式: 逐行喂 SSE, 结束后得到与非流式一致的 dict ─────

class StreamError(RuntimeError):
    def __init__(self, message: str, overloaded: bool = False):
        super().__init__(message)
        self.overloaded = overloaded


def _is_overload(err: dict) -> bool:
    s = f"{err.get('type', '')} {err.get('code', '')}".lower()
    return any(k in s for k in ("rate_limit", "overload", "server_error", "529", "429"))


class ResponsesStream:
    """Responses SSE: 正文增量走 response.output_text.delta, 收尾的 response.completed
    (或 incomplete) 带完整 response 对象, 直接交给 from_responses。"""

    def __init__(self, on_text):
        self.on_text = on_text
        self.final: dict | None = None
        self.text: list[str] = []

    def feed(self, data: dict) -> None:
        kind = data.get("type", "")
        if kind == "response.output_text.delta":
            d = data.get("delta") or ""
            if d:
                self.text.append(d)
                self.on_text(d)
        elif kind in ("response.completed", "response.incomplete"):
            self.final = data.get("response") or {}
        elif kind in ("response.failed", "error"):
            err = (data.get("response") or {}).get("error") or data.get("error") or data
            raise StreamError(f"LLM 流式响应出错: {err.get('code') or err.get('type')}: "
                              f"{err.get('message', '')}", _is_overload(err))

    def result(self) -> dict:
        if self.final is None:
            raise StreamError("LLM 流式响应没有收到结束事件", overloaded=True)
        out = from_responses(self.final)
        if not out["content"] and self.text:     # 有的中转站 completed 里 output 为空
            out["content"] = [{"type": "text", "text": "".join(self.text)}]
        return out


class ChatStream:
    """Chat Completions SSE: 每个 chunk 的 choices[0].delta 带正文片段或按 index 分片的
    tool_calls(arguments 是分段 JSON), 最后一个 chunk 带 usage(需 stream_options.include_usage)。"""

    def __init__(self, on_text):
        self.on_text = on_text
        self.text: list[str] = []
        self.calls: dict[int, dict] = {}
        self.finish: str | None = None
        self.usage: dict = {}
        self.meta: dict = {}

    def feed(self, data: dict) -> None:
        if data.get("error"):
            err = data["error"] if isinstance(data["error"], dict) else {"message": str(data["error"])}
            raise StreamError(f"LLM 流式响应出错: {err.get('code') or err.get('type')}: "
                              f"{err.get('message', '')}", _is_overload(err))
        if not self.meta and data.get("id"):
            self.meta = {"id": data.get("id"), "model": data.get("model")}
        if data.get("usage"):
            self.usage = data["usage"]
        for ch in data.get("choices") or []:
            delta = ch.get("delta") or {}
            if delta.get("content"):
                self.text.append(delta["content"])
                self.on_text(delta["content"])
            for tc in delta.get("tool_calls") or []:
                slot = self.calls.setdefault(tc.get("index", len(self.calls)),
                                             {"id": None, "name": "", "arguments": ""})
                if tc.get("id"):
                    slot["id"] = tc["id"]
                fn = tc.get("function") or {}
                if fn.get("name"):
                    slot["name"] += fn["name"]
                if fn.get("arguments"):
                    slot["arguments"] += fn["arguments"]
            if ch.get("finish_reason"):
                self.finish = ch["finish_reason"]

    def result(self) -> dict:
        msg = {"content": "".join(self.text) or None,
               "tool_calls": [{"id": c["id"], "type": "function",
                               "function": {"name": c["name"], "arguments": c["arguments"]}}
                              for _, c in sorted(self.calls.items())]}
        return from_chat({**self.meta, "usage": self.usage,
                          "choices": [{"message": msg, "finish_reason": self.finish}]})
