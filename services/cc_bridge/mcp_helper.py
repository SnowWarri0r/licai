"""Claude Code 拉起的 stdio MCP 小助手: 把 licai 的工具列给 Claude Code, 工具调用转交回 licai 进程执行。

只用标准库, 以 `python -I` 运行。参数: 回调地址所在文件、工具清单文件、列出工具后写的标记文件。
tools/call 阻塞在对 licai 回调的 HTTP 请求上, 直到 licai 的 agent 循环交回这次调用的结果。
"""
import json
import sys
import urllib.request


def main(argv):
    cb_path, tools_path, listed_path = argv[1], argv[2], argv[3]
    with open(cb_path) as f:
        callback = f.read().strip()

    def out(obj):
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()

    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        mid, method = req.get("id"), req.get("method")
        params = req.get("params") or {}
        if method == "initialize":
            out({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "licai", "version": "1"}}})
        elif method == "tools/list":
            with open(tools_path) as f:
                tools = json.load(f)
            out({"jsonrpc": "2.0", "id": mid, "result": {"tools": tools}})
            with open(listed_path, "w") as f:
                f.write("1")
        elif method == "tools/call":
            meta = params.get("_meta") or {}
            body = json.dumps({"id": meta.get("claudecode/toolUseId", ""), "name": params.get("name", ""),
                               "arguments": params.get("arguments") or {}}, ensure_ascii=False).encode()
            try:
                r = urllib.request.urlopen(urllib.request.Request(
                    callback, body, {"Content-Type": "application/json"}), timeout=3600)
                res = json.loads(r.read())
            except Exception as e:  # licai 那边没接上: 作为工具错误交回, 不让 Claude Code 卡住
                res = {"content": [{"type": "text", "text": f"工具调用未送达 licai: {e}"}], "is_error": True}
            out({"jsonrpc": "2.0", "id": mid, "result": {
                "content": res.get("content") or [{"type": "text", "text": ""}],
                "isError": bool(res.get("is_error"))}})
        elif mid is not None:
            out({"jsonrpc": "2.0", "id": mid, "result": {}})


if __name__ == "__main__":
    main(sys.argv)
