"""极简 MCP streamable-HTTP 客户端 —— 只为「调远端一个只读工具」这件事写。

不引 mcp SDK: 我们只用 tools/list + tools/call 两个方法, 不需要 sampling/roots/prompts
那套完整实现, 少一个依赖少一处升级负担。

协议细节(对着 https://mcp.zsxq.com 实测):
  · 请求是标准 JSON-RPC 2.0 POST; Accept 必须同时带 application/json 和 text/event-stream,
    否则服务端可能拒绝
  · 响应体是 SSE 帧("event: message\\ndata: {...}"), 也可能是裸 JSON —— 两种都要能吃
  · 工具结果在 result.content[] 里, 类型 text 的 text 字段通常是**JSON 字符串**, 要二次解析
  · 失败时 result.isError=true, 错误内容同样在 content 里
    (实测无凭证调用: {"error":"Authentication failed","success":false})
"""
from __future__ import annotations
import json
import requests

_TIMEOUT = 20.0
_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def _parse_body(text: str) -> dict | None:
    """SSE 帧或裸 JSON → dict。SSE 有多帧时取最后一个带 result/error 的。"""
    text = (text or "").strip()
    if not text:
        return None
    if not text.startswith("event:") and not text.startswith("data:"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None
    out = None
    for line in text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            d = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and ("result" in d or "error" in d):
            out = d
    return out


def _unwrap(result: dict) -> dict:
    """result.content[] → 业务数据。text 块里是 JSON 字符串就解析, 否则原样返回文本。"""
    blocks = result.get("content") or []
    texts = [b.get("text") or "" for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
    joined = "\n".join(t for t in texts if t)
    if not joined:
        return {"ok": not result.get("isError"), "data": result.get("structuredContent") or {}}
    try:
        payload = json.loads(joined)
    except json.JSONDecodeError:
        payload = {"text": joined}
    if result.get("isError"):
        msg = payload.get("error") if isinstance(payload, dict) else str(payload)
        return {"ok": False, "error": {"type": "tool", "message": str(msg)[:300]}}
    # 服务端自己也带 success 标记, 失败时 isError 可能没置位, 一并认
    if isinstance(payload, dict) and payload.get("success") is False:
        return {"ok": False, "error": {"type": "tool",
                                       "message": str(payload.get("error") or payload)[:300]}}
    return {"ok": True, "data": payload}


def _rpc(url: str, method: str, params: dict | None, timeout: float, rpc_id: int = 1) -> dict:
    body = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        body["params"] = params
    try:
        s = requests.Session()
        s.trust_env = False                    # 走直连, 不吃系统代理(端点在境内)
        r = s.post(url, json=body, headers=_HEADERS, timeout=timeout)
    except requests.Timeout:
        return {"ok": False, "error": {"type": "timeout", "message": "MCP 端点超时"}}
    except requests.RequestException as e:
        return {"ok": False, "error": {"type": "network", "message": str(e)[:200]}}
    d = _parse_body(r.text)
    if d is None:
        return {"ok": False, "error": {"type": "parse",
                                       "message": f"HTTP {r.status_code}: {(r.text or '')[:160]}"}}
    if "error" in d:                           # JSON-RPC 层错误(方法不存在/参数非法)
        err = d["error"] or {}
        return {"ok": False, "error": {"type": "rpc",
                                       "message": str(err.get("message") or err)[:300]}}
    return {"ok": True, "raw": d.get("result") or {}}


def list_tools(url: str, timeout: float = _TIMEOUT) -> dict:
    r = _rpc(url, "tools/list", None, timeout)
    if not r.get("ok"):
        return r
    return {"ok": True, "tools": (r["raw"] or {}).get("tools") or []}


def call_tool(url: str, name: str, arguments: dict | None = None,
              timeout: float = _TIMEOUT) -> dict:
    """调一个远端工具, 返回 {"ok":True,"data":...} 或 {"ok":False,"error":{type,message}}。"""
    r = _rpc(url, "tools/call", {"name": name, "arguments": arguments or {}}, timeout, rpc_id=2)
    if not r.get("ok"):
        return r
    return _unwrap(r["raw"] or {})
