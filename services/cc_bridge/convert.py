"""cc_bridge 的纯函数: 消息指纹、首条消息展开、工具定义与结果的格式转换、流式事件拼装。不碰进程与网络, 便于单测。"""
from __future__ import annotations

import hashlib
import json

PREFIX = "mcp__licai__"
# Claude Code 自己执行的工具(联网搜索): 不交给 licai, 在返回里记作 server_tool_use 供统计
OWN_TOOLS = {"WebSearch": "web_search", "WebFetch": "web_fetch"}
RESULT_CAP = 6000


def _h(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:12]


def fingerprint(msg: dict) -> str:
    """一条消息在续接判断里的身份。

    助手消息只认其中的 tool_use id(正文与思考块调用方可能裁剪, 不参与比较); 用户消息认
    tool_result 对应的 id 和正文。同一会话的下一次调用, 前面各条的指纹应与上次一致。
    """
    role = msg.get("role", "")
    content = msg.get("content")
    if isinstance(content, str):
        return f"{role}|t:{_h(content)}" if role == "user" else f"{role}|"
    parts = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "tool_use":
            parts.append("u:" + str(b.get("id", "")))
        elif t == "tool_result":
            parts.append("r:" + str(b.get("tool_use_id", "")))
        elif t == "text" and role == "user":
            parts.append("t:" + _h(b.get("text", "")))
        elif t == "image" and role == "user":
            parts.append("i")
    return role + "|" + ",".join(parts)


def fingerprints(messages: list) -> list[str]:
    return [fingerprint(m) for m in messages]


def question_key(msg: dict) -> str:
    """一条用户消息里「提问本身」的身份: 最后一个正文块(前面可能附有每轮都变的状态块)。"""
    content = msg.get("content")
    if isinstance(content, str):
        return _h(content.strip())
    texts = [b.get("text", "") for b in content or [] if isinstance(b, dict) and b.get("type") == "text"]
    return _h(texts[-1].strip()) if texts else ""


def history_questions(messages: list) -> list[str] | None:
    """调用方传来的是「只含正文的多轮历史 + 当前问题」时, 返回历史里各轮提问的身份; 否则 None。

    下一轮问答的历史只带每轮的问与答(没有中间的工具往返), 与进程里实际的对话结构不同, 按前缀
    对不上; 只要历史里的提问依次就是某个进程答过的那几轮, 就能接着用它。
    """
    if len(messages) < 3 or messages[-1].get("role") != "user":
        return None
    hist = messages[:-1]
    if any(not isinstance(m.get("content"), str) for m in hist):
        return None
    qs = [question_key(m) for m in hist if m.get("role") == "user"]
    return qs or None


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for b in content or []:
        if isinstance(b, dict) and b.get("type") == "text":
            out.append(b.get("text", ""))
        elif isinstance(b, dict) and b.get("type") == "image":
            out.append("[图片]")
    return "\n".join(x for x in out if x)


def _clip(text: str, cap: int = RESULT_CAP) -> str:
    return text if len(text) <= cap else text[:cap] + f"\n…(已截断, 原文 {len(text)} 字)"


def user_text(msg: dict) -> str:
    """用户消息里要粘贴进输入框的正文。"""
    return _text_of(msg.get("content"))


def flatten(messages: list) -> str:
    """新起一个 Claude Code 时, 把调用方给的整段历史写成一条消息。

    只有一条用户消息时就是它的正文; 否则先列此前的对话(工具调用与结果一并写出, 结果过长截断),
    再写当前这一条。最后一条是工具结果时, 交代基于这些结果接着作答。
    """
    if len(messages) == 1 and messages[0].get("role") == "user":
        return user_text(messages[0])
    lines = ["以下是此前的对话记录, 包括调用过的工具和返回结果:", ""]
    for m in messages[:-1]:
        lines.extend(_render(m))
    last = messages[-1] if messages else {}
    has_results = any(isinstance(b, dict) and b.get("type") == "tool_result"
                      for b in (last.get("content") if isinstance(last.get("content"), list) else []))
    if has_results:
        lines.extend(_render(last))
        lines += ["---", "", "请基于以上工具结果接着完成上面的回答。"]
    else:
        lines += ["---", "", "当前的消息:", "", user_text(last)]
    return "\n".join(lines)


def _render(m: dict) -> list[str]:
    role = m.get("role")
    content = m.get("content")
    if isinstance(content, str):
        return [("[用户] " if role == "user" else "[助手] ") + content, ""]
    out = []
    for b in content or []:
        if not isinstance(b, dict):
            continue
        t = b.get("type")
        if t == "text" and b.get("text"):
            out.append(("[用户] " if role == "user" else "[助手] ") + b["text"])
        elif t == "tool_use":
            out.append(f"[调用工具 {b.get('name')}] {json.dumps(b.get('input') or {}, ensure_ascii=False)}")
        elif t == "server_tool_use":
            out.append(f"[联网 {b.get('name')}] {json.dumps(b.get('input') or {}, ensure_ascii=False)}")
        elif t == "tool_result":
            out.append("[工具结果] " + _clip(_text_of(b.get("content"))))
    out.append("")
    return out


def mcp_tools(tools: list | None) -> tuple[list, bool]:
    """Anthropic 工具定义 → MCP tools/list 的条目; 第二项是是否带了服务端联网搜索。"""
    out, web = [], False
    for t in tools or []:
        if t.get("type", "").startswith("web_search"):
            web = True
            continue
        if "input_schema" not in t:
            continue
        out.append({"name": t["name"], "description": t.get("description", ""), "inputSchema": t["input_schema"]})
    return out, web


def mcp_result(block: dict, extra_text: str = "") -> dict:
    """tool_result 块 → MCP tools/call 的结果。extra_text 是同一条用户消息里附带的正文(回答期间的补充)。"""
    content = block.get("content")
    items = []
    if isinstance(content, str):
        items.append({"type": "text", "text": content})
    else:
        for c in content or []:
            if not isinstance(c, dict):
                continue
            if c.get("type") == "text":
                items.append({"type": "text", "text": c.get("text", "")})
            elif c.get("type") == "image":
                src = c.get("source") or {}
                if src.get("type") == "base64":
                    items.append({"type": "image", "data": src.get("data", ""),
                                  "mimeType": src.get("media_type", "image/png")})
    if extra_text:
        items.append({"type": "text", "text": extra_text})
    if not items:
        items.append({"type": "text", "text": ""})
    return {"content": items, "is_error": bool(block.get("is_error"))}


class Reply:
    """把 tap 抄下来的若干条流(一条流 = 一次 API 回复)拼成调用方要的一次响应。

    Claude Code 自己执行的工具(联网搜索)会让一次回答跨好几条流: 这些流的正文接着累加, 其中的
    WebSearch 调用记成 server_tool_use; 直到某条流以 end_turn 收尾, 或要调 licai 的工具, 才算一次响应。
    """

    def __init__(self, model: str):
        self.model = model
        self.content: list = []
        self.usage = {"input_tokens": 0, "output_tokens": 0,
                      "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        self.stop_reason = None
        self._blocks: dict = {}
        self._json: dict = {}

    def event(self, e: dict, on_text=None) -> None:
        t = e.get("type")
        if t == "message_start":
            self._blocks, self._json = {}, {}
            self.stop_reason = None
            self._add_usage((e.get("message") or {}).get("usage") or {})
        elif t == "content_block_start":
            self._blocks[e.get("index")] = dict(e.get("content_block") or {})
        elif t == "content_block_delta":
            i, d = e.get("index"), e.get("delta") or {}
            b = self._blocks.get(i)
            if b is None:
                return
            dt = d.get("type")
            if dt == "text_delta":
                b["text"] = b.get("text", "") + d.get("text", "")
                if on_text and d.get("text"):
                    on_text(d["text"])
            elif dt == "input_json_delta":
                self._json[i] = self._json.get(i, "") + d.get("partial_json", "")
        elif t == "message_delta":
            self.stop_reason = (e.get("delta") or {}).get("stop_reason") or self.stop_reason
            u = e.get("usage") or {}
            if "output_tokens" in u:
                self.usage["output_tokens"] += u.get("output_tokens") or 0

    def _add_usage(self, u: dict) -> None:
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            self.usage[k] += u.get(k) or 0

    def end_stream(self) -> bool:
        """一条流结束。返回 True 表示这次响应完整了(该交给调用方), False 表示 Claude Code 还会接着请求。"""
        licai_calls = False
        for i in sorted(k for k in self._blocks if k is not None):
            b = self._blocks[i]
            t = b.get("type")
            if t in ("tool_use", "server_tool_use"):
                raw = self._json.get(i)
                if raw:
                    try:
                        b["input"] = json.loads(raw)
                    except ValueError:
                        b["input"] = {}
                b.setdefault("input", {})
            if t == "text" and b.get("text"):
                self.content.append({"type": "text", "text": b["text"]})
            elif t == "tool_use" and b.get("name", "").startswith(PREFIX):
                self.content.append({"type": "tool_use", "id": b.get("id"), "name": b["name"][len(PREFIX):],
                                     "input": b.get("input") or {}})
                licai_calls = True
            elif t == "tool_use" and b.get("name") in OWN_TOOLS:
                self.content.append({"type": "server_tool_use", "id": b.get("id"),
                                     "name": OWN_TOOLS[b["name"]], "input": b.get("input") or {}})
        self._blocks, self._json = {}, {}
        if self.stop_reason == "tool_use" and not licai_calls:
            return False
        return self.stop_reason is not None

    def response(self) -> dict:
        return {"type": "message", "role": "assistant", "model": self.model, "content": self.content,
                "stop_reason": self.stop_reason or "end_turn", "usage": dict(self.usage)}
