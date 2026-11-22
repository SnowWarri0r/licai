// 预加载进 Claude Code(Bun 程序, BUN_OPTIONS=--preload)。把主对话请求(x-claude-code-request-class: main)
// 的流式回复复制一份写到 LICAI_CC_TAP 指向的文件, 每个 SSE 事件一行 JSON; Claude Code 自己读的那份原样不动。
// 请求、连接与 TLS 都是 Claude Code 自己的, 这里只 tee 回复体。
(() => {
  const file = process.env.LICAI_CC_TAP;
  if (!file) return;
  // 不传给 Claude Code 再起的子进程
  delete process.env.LICAI_CC_TAP;
  const opts = (process.env.BUN_OPTIONS || "").replace(/(^|\s)--preload(=|\s+)\S*cc_bridge\/tap\.js/, "").trim();
  if (opts) process.env.BUN_OPTIONS = opts;
  else delete process.env.BUN_OPTIONS;

  const fs = require("fs");
  let fd;
  try {
    fd = fs.openSync(file, "a");
  } catch {
    return;
  }
  const put = (v) => {
    try {
      fs.writeSync(fd, JSON.stringify(v) + "\n");
    } catch {}
  };
  const Real = globalThis.Bun && Bun.FetchSession;
  if (typeof Real !== "function") {
    put({ tap: "none" });
    return;
  }
  let n = 0;
  const read = async (s, body) => {
    const dec = new TextDecoder();
    let buf = "";
    const event = (text) => {
      const data = text
        .split("\n")
        .filter((l) => l.startsWith("data:"))
        .map((l) => l.slice(5).replace(/^ /, ""))
        .join("\n");
      if (!data) return;
      try {
        const e = JSON.parse(data);
        if (e && e.type !== "ping") put({ s, e });
      } catch {}
    };
    try {
      for await (const chunk of body) {
        buf = (buf + dec.decode(chunk, { stream: true })).replace(/\r\n?/g, "\n");
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          event(buf.slice(0, i));
          buf = buf.slice(i + 2);
        }
      }
      if (buf.trim()) event(buf);
      put({ s, end: "done" });
    } catch {
      put({ s, end: "cut" });
    }
  };
  class FetchSession extends Real {
    async fetch(input, init) {
      const res = await super.fetch(input, init);
      try {
        const url = String((input && input.url) || input);
        const headers = new Headers((init && init.headers) || (input && input.headers) || undefined);
        if (headers.get("x-claude-code-request-class") === "main" && /\/v1\/messages(\?|$)/.test(url)) {
          const type = res.headers.get("content-type") || "";
          if (res.body && /^text\/event-stream/.test(type)) {
            const [theirs, mine] = res.body.tee();
            Object.defineProperty(res, "body", { value: theirs, configurable: true });
            const s = ++n;
            put({ s, open: true, status: res.status });
            read(s, mine);
          } else if (!res.ok) {
            // 非流式的出错回复(限流、过载、鉴权): 只记状态码, Claude Code 自己会重试
            put({ http_error: res.status, retry_after: res.headers.get("retry-after") || "" });
          }
        }
      } catch {}
      return res;
    }
  }
  try {
    Bun.FetchSession = FetchSession;
  } catch {}
  put({ tap: Bun.FetchSession === FetchSession ? "on" : "none" });
})();
