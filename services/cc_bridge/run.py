"""在伪终端里驱动一个交互式 Claude Code 进程。

一个 Run 对应一段对话: 起进程时定下模型、系统提示和工具; 用户消息以括号粘贴写进输入框再回车;
回复从 tap.js 抄下的主请求流里读; 工具调用经 MCP 小助手回到本进程的回调服务, 挂起直到
调用方交回结果。会话记录(transcript)只用来确认消息已发出、发现 API 报错。
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import pty
import queue
import re
import secrets
import select
import shutil
import signal
import struct
import subprocess
import sys
import termios
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import convert

logger = logging.getLogger(__name__)

HERE = Path(__file__).resolve().parent
ROOT = Path(os.environ.get("LICAI_CC_HOME") or (Path.home() / ".cache" / "licai" / "cc-bridge"))
# 所有进程共用一个工作目录: Claude Code 只在第一次问是否信任它
WORK = ROOT / "work"
RUNS = ROOT / "runs"

COLS, ROWS = 200, 50
READY_WAIT = 45.0
SENT_WAIT = 3.0
TOOL_WAIT = 1800.0
# 一次回复里两条流之间、或流内事件之间最长的静默; 超过且进程还活着就按卡死处理
QUIET_WAIT = 300.0

_ANSI = re.compile(r"\x1b\[[0-9;?<>=]*[A-Za-z~@`]|\x1b\][^\x07\x1b]*(\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>78DEHM]|\x1b")


class CCError(RuntimeError):
    """Claude Code 没能按预期运行(没登录、没出提示符、进程退出等)。"""


class CCApiError(RuntimeError):
    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _child_env(tap_file: Path) -> dict:
    env = {}
    for k, v in os.environ.items():
        if k.startswith("CLAUDE") or k in ("TERM", "COLUMNS", "LINES", "BUN_OPTIONS", "ANTHROPIC_API_KEY",
                                           "ANTHROPIC_BASE_URL", "ANTHROPIC_AUTH_TOKEN"):
            continue
        env[k] = v
    env["TERM"] = "xterm-256color"
    env["LICAI_CC_TAP"] = str(tap_file)
    env["BUN_OPTIONS"] = f"--preload {HERE / 'tap.js'}"
    # licai 的工具全部直接列给模型, 不走 Claude Code 的按需加载
    env["ENABLE_TOOL_SEARCH"] = "false"
    env["DISABLE_AUTOUPDATER"] = "1"
    return env


def claude_binary() -> str | None:
    p = os.environ.get("LICAI_CLAUDE_BIN") or shutil.which("claude")
    if p:
        return p
    for c in (Path.home() / ".local" / "bin" / "claude", Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if c.exists():
            return str(c)
    return None


def project_dir() -> Path:
    """Claude Code 存这个工作目录会话记录的地方。"""
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))
    return config / "projects" / re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(WORK))


# ── 工具回调服务 ─────────────────────────────────────────

_runs_by_token: dict[str, "Run"] = {}
_server: ThreadingHTTPServer | None = None
_server_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        token = self.path.rsplit("/", 1)[-1]
        run = _runs_by_token.get(token)
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        except ValueError:
            body = {}
        if run is None:
            res = {"content": [{"type": "text", "text": "这次对话已结束"}], "is_error": True}
        else:
            res = run.tool_call(body.get("id") or "", body.get("name") or "", body.get("arguments") or {})
        data = json.dumps(res, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def _callback_base() -> str:
    global _server
    with _server_lock:
        if _server is None:
            _server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
            _server.daemon_threads = True
            threading.Thread(target=_server.serve_forever, name="cc-bridge-callback", daemon=True).start()
        return f"http://127.0.0.1:{_server.server_port}/cb/"


# ── 一个 Claude Code 进程 ────────────────────────────────

class Run:
    def __init__(self, model: str, system: str | None, tools: list | None, effort: str | None = None):
        self.model = model
        self.system = system or ""
        self.id = uuid.uuid4().hex[:12]
        self.sid = str(uuid.uuid4())
        self.dir = RUNS / self.id
        self.token = secrets.token_hex(16)
        self.view: list[str] = []          # 已交给这个进程的消息指纹(含它回的)
        self.turns: list[str] = []         # 各轮提问的身份(convert.question_key)
        self.ended = False                 # 上一次回复已答完(不是停在工具调用上)
        self.lock = threading.Lock()       # 同一时刻只跑一次调用
        self.last_used = time.time()
        self.closed = False
        self.events: queue.Queue = queue.Queue()
        self._cv = threading.Condition()
        self._results: dict[str, dict] = {}
        self._waiting: set[str] = set()
        self._screen = bytearray()
        self._user_rows = 0
        self._proc: subprocess.Popen | None = None
        self._fd = -1
        mtools, self.web = convert.mcp_tools(tools)
        self._start(mtools, effort)

    # 启动
    def _start(self, mtools: list, effort: str | None) -> None:
        exe = claude_binary()
        if not exe:
            raise CCError("找不到本机的 claude 命令; 先安装 Claude Code 并登录")
        WORK.mkdir(parents=True, exist_ok=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        system_file = self.dir / "system.md"
        system_file.write_text(self.system or "You are a helpful assistant.", encoding="utf-8")
        self.tap_file = self.dir / "tap.jsonl"
        self.tap_file.touch()
        self.listed = self.dir / "listed"
        args = [exe, "--model", self.model, "--session-id", self.sid,
                "--system-prompt-file", str(system_file), "--setting-sources", "",
                "--permission-mode", "default", "--no-chrome", "--strict-mcp-config"]
        own = "WebSearch" if self.web else ""
        args += ["--tools", own]
        allowed = ["WebSearch"] if self.web else []
        if mtools:
            (self.dir / "tools.json").write_text(json.dumps(mtools, ensure_ascii=False), encoding="utf-8")
            (self.dir / "callback").write_text(_callback_base() + self.token, encoding="utf-8")
            _runs_by_token[self.token] = self
            mcp = {"mcpServers": {"licai": {"command": sys.executable, "args": [
                "-I", str(HERE / "mcp_helper.py"), str(self.dir / "callback"), str(self.dir / "tools.json"),
                str(self.listed)]}}}
            allowed.append("mcp__licai")
        else:
            mcp = {"mcpServers": {}}
            self.listed.write_text("1")
        args += ["--mcp-config", json.dumps(mcp, ensure_ascii=False)]
        if allowed:
            args += ["--allowedTools", ",".join(allowed)]
        if effort:
            args += ["--effort", effort]
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLS, 0, 0))
        try:
            self._proc = subprocess.Popen(args, stdin=slave, stdout=slave, stderr=slave, cwd=str(WORK),
                                          env=_child_env(self.tap_file), start_new_session=True, close_fds=True)
        finally:
            os.close(slave)
        self._fd = master
        threading.Thread(target=self._read_pty, name=f"cc-pty-{self.id}", daemon=True).start()
        try:
            self._ready()
        except Exception:
            self.close()
            raise
        threading.Thread(target=self._tail_tap, name=f"cc-tap-{self.id}", daemon=True).start()
        threading.Thread(target=self._tail_transcript, name=f"cc-log-{self.id}", daemon=True).start()

    def _read_pty(self) -> None:
        while not self.closed:
            try:
                r, _, _ = select.select([self._fd], [], [], 0.2)
            except (OSError, ValueError):
                break
            if not r:
                if self._proc and self._proc.poll() is not None:
                    break
                continue
            try:
                data = os.read(self._fd, 65536)
            except OSError:
                break
            if not data:
                break
            # 终端查询: 光标位置、终端类型
            if b"\x1b[6n" in data:
                self._write(b"\x1b[1;1R")
            if b"\x1b[c" in data or b"\x1b[0c" in data:
                self._write(b"\x1b[?62;22c")
            with self._cv:
                self._screen += data
                if len(self._screen) > 256 * 1024:
                    del self._screen[:-128 * 1024]
        self.events.put(("exit", self._proc.poll() if self._proc else None))

    def _write(self, data: bytes) -> None:
        try:
            os.write(self._fd, data)
        except OSError:
            pass

    def screen(self, tail: int = 4000) -> str:
        with self._cv:
            raw = bytes(self._screen[-tail * 4:])
        text = _ANSI.sub(" ", raw.decode("utf-8", "replace"))
        return re.sub(r"\s+", " ", text)[-tail:]

    def _clear_screen(self) -> None:
        with self._cv:
            self._screen.clear()

    def _ready(self) -> None:
        deadline = time.time() + READY_WAIT
        trust_tries = 0
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise CCError(f"Claude Code 启动后退出了(rc={self._proc.returncode}): {self.screen(600)}")
            s = self.screen(3000)
            if "trust this folder" in s:
                if trust_tries >= 3:
                    raise CCError("Claude Code 一直在问是否信任工作目录: " + s[-600:])
                trust_tries += 1
                time.sleep(0.6)
                self._clear_screen()
                self._write(b"\x1b[B")
                time.sleep(0.4)
                self._write(b"\r")
                time.sleep(1.0)
                continue
            if re.search(r"Select login method|Please run /login|Not logged in|Log in to", s):
                raise CCError("本机 Claude Code 没有登录; 在终端里运行一次 claude 并登录")
            if "Bypass Permissions" in s and "accept" in s.lower():
                raise CCError("Claude Code 在问是否接受跳过权限模式, 需要在终端里手动处理一次")
            if "❯" in s and "for shortcuts" in s and self.listed.exists():
                return
            time.sleep(0.2)
        raise CCError("Claude Code 45 秒内没有出现输入提示符: " + self.screen(800))

    # tap 与会话记录
    def _tail_tap(self) -> None:
        pos = 0
        part = b""
        while not self.closed:
            try:
                with open(self.tap_file, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
            except OSError:
                chunk = b""
            if chunk:
                pos += len(chunk)
                part += chunk
                *lines, part = part.split(b"\n")
                for line in lines:
                    try:
                        v = json.loads(line)
                    except ValueError:
                        continue
                    if "tap" in v:
                        if v["tap"] != "on":
                            self.events.put(("fatal", "tap 没有挂上(这个版本的 Claude Code 不支持预加载)"))
                    elif "http_error" in v:
                        self.events.put(("http_error", v))
                    elif "e" in v:
                        self.events.put(("ev", v["e"]))
                    elif v.get("end"):
                        self.events.put(("end", v["end"]))
                    elif v.get("open"):
                        self.events.put(("open", v.get("status")))
                continue
            time.sleep(0.03)

    def _tail_transcript(self) -> None:
        path = project_dir() / f"{self.sid}.jsonl"
        pos = 0
        part = b""
        while not self.closed:
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
            except OSError:
                chunk = b""
            if not chunk:
                time.sleep(0.1)
                continue
            pos += len(chunk)
            part += chunk
            *lines, part = part.split(b"\n")
            for line in lines:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("type") == "user" and not row.get("isMeta"):
                    content = (row.get("message") or {}).get("content")
                    if isinstance(content, str) or not any(
                            isinstance(b, dict) and b.get("type") == "tool_result" for b in content or []):
                        with self._cv:
                            self._user_rows += 1
                            self._cv.notify_all()
                if row.get("type") == "assistant" and row.get("isApiErrorMessage"):
                    text = convert.user_text(row.get("message") or {})
                    self.events.put(("api_error", text or "Claude Code 报告 API 错误"))

    # 发消息
    def send(self, text: str) -> None:
        text = text.replace("\x1b", "").replace("\r\n", "\n").replace("\r", "\n")
        if not text.strip():
            text = "."
        if text[0] in "/!#&@":
            text = " " + text
        with self._cv:
            before = self._user_rows
        self._clear_screen()
        self._write(b"\x1b[200~" + text.encode("utf-8") + b"\x1b[201~")
        # 等粘贴显示出来: 长文本 Claude Code 会折成「[Pasted text …]」
        settle = min(3.0, 0.3 + len(text) / 20000)
        time.sleep(settle)
        for _ in range(4):
            self._write(b"\r")
            with self._cv:
                if self._cv.wait_for(lambda: self._user_rows > before, timeout=SENT_WAIT):
                    return
            if self._proc.poll() is not None:
                raise CCError(f"Claude Code 已退出: {self.screen(600)}")
        raise CCError("Claude Code 没有接收这条消息: " + self.screen(800))

    # 工具结果
    def give_results(self, results: dict[str, dict]) -> None:
        with self._cv:
            self._results.update(results)
            self._cv.notify_all()

    def tool_call(self, tool_use_id: str, name: str, arguments: dict) -> dict:
        """MCP 小助手转来的一次工具调用: 等调用方交回这个 tool_use 的结果。"""
        with self._cv:
            self._waiting.add(tool_use_id)
            ok = self._cv.wait_for(lambda: tool_use_id in self._results or self.closed, timeout=TOOL_WAIT)
            self._waiting.discard(tool_use_id)
            if not ok or self.closed:
                return {"content": [{"type": "text", "text": "调用方没有交回结果"}], "is_error": True}
            return self._results.pop(tool_use_id)

    # 等一次完整回复
    def reply(self, on_text=None, timeout: float = 1800.0) -> dict:
        r = convert.Reply(self.model)
        deadline = time.time() + timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise CCError("等 Claude Code 回复超时")
            try:
                kind, val = self.events.get(timeout=min(left, QUIET_WAIT))
            except queue.Empty:
                with self._cv:
                    busy_tools = bool(self._waiting)
                if busy_tools or self._proc.poll() is None and "esc to interrupt" in self.screen(1500).lower():
                    continue
                raise CCError("Claude Code 长时间没有动静: " + self.screen(600))
            if kind == "ev":
                r.event(val, on_text)
            elif kind == "end":
                if val == "cut":
                    r._blocks, r._json = {}, {}
                    continue
                if r.end_stream():
                    self.last_used = time.time()
                    return r.response()
            elif kind == "api_error":
                raise CCApiError(val, _status_of(val))
            elif kind == "http_error":
                status = int(val.get("http_error") or 0)
                # 429/529/5xx Claude Code 会自己重试, 这里只在它放弃(写进会话记录)时报错
                if status in (401, 403):
                    raise CCApiError(f"Claude Code 的请求被拒绝(HTTP {status}), 可能需要重新登录", status)
            elif kind == "fatal":
                raise CCError(val)
            elif kind == "exit":
                raise CCError(f"Claude Code 进程退出了: {self.screen(600)}")

    def interrupt(self) -> None:
        self._write(b"\x1b")

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        _runs_by_token.pop(self.token, None)
        with self._cv:
            self._cv.notify_all()
        if self._proc and self._proc.poll() is None:
            try:
                os.killpg(self._proc.pid, signal.SIGTERM)
                self._proc.wait(2)
            except Exception:
                try:
                    os.killpg(self._proc.pid, signal.SIGKILL)
                except Exception:
                    pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        # 排查时设 LICAI_CC_KEEP=1 保留运行目录(tap 抄下的流)与会话记录
        if os.environ.get("LICAI_CC_KEEP") != "1":
            shutil.rmtree(self.dir, ignore_errors=True)
            try:
                (project_dir() / f"{self.sid}.jsonl").unlink()
            except OSError:
                pass


def _status_of(text: str) -> int:
    m = re.search(r"\b(4\d\d|5\d\d)\b", text or "")
    if m:
        return int(m.group(1))
    if re.search(r"overload", text or "", re.I):
        return 529
    return 0


def sweep() -> None:
    """清掉上次服务退出时没收拾的运行目录和会话记录。"""
    shutil.rmtree(RUNS, ignore_errors=True)
    pd = project_dir()
    if pd.exists():
        cutoff = time.time() - 86400
        for p in pd.glob("*.jsonl"):
            try:
                if p.stat().st_mtime < cutoff:
                    p.unlink()
            except OSError:
                pass
