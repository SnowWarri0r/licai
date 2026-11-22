"""经本机 Claude Code 调用模型: 在伪终端里驱动真正的交互式 Claude Code, 用它自己的登录与身份发请求。

对上层的形状与 llm_client.call_claude_messages 相同: 传完整 messages 历史和工具定义, 返回一次
Anthropic Messages 响应(content 里的 tool_use、stop_reason、usage)。工具由调用方自己的循环执行:

- 一段对话对应一个常驻的 Claude Code 进程。下一次调用的历史与上次交出去的前缀一致时,
  只把新增的部分交给同一个进程: 工具结果交回挂起的 MCP 调用, 新的用户消息粘贴进输入框。
  前缀对不上(历史被改写、进程已回收)就新起一个, 把整段历史写成一条消息。
- 进程空闲 IDLE_S 秒后回收; 同时最多 MAX_RUNS 个, 满了先回收最久没用的空闲进程。
"""
from __future__ import annotations

import atexit
import logging
import threading
import time

from . import convert
from .run import CCApiError, CCError, Run, claude_binary, sweep

logger = logging.getLogger(__name__)

IDLE_S = 600
MAX_RUNS = 6

_runs: list[Run] = []
_lock = threading.Lock()
_reaper_started = False
_swept = False

__all__ = ["call_messages", "call_text", "available", "CCError", "CCApiError", "shutdown", "stats"]


def available() -> bool:
    return claude_binary() is not None


def _start_reaper() -> None:
    global _reaper_started
    if _reaper_started:
        return
    _reaper_started = True
    # 服务退出时把后台的 Claude Code 一并收掉
    atexit.register(shutdown)

    def loop():
        while True:
            time.sleep(30)
            now = time.time()
            with _lock:
                stale = [r for r in _runs if not r.lock.locked() and now - r.last_used > IDLE_S]
                for r in stale:
                    _runs.remove(r)
            for r in stale:
                r.close()

    threading.Thread(target=loop, name="cc-bridge-reaper", daemon=True).start()


def _claim(messages: list, model: str, system: str | None, tools: list | None, effort: str | None):
    """找能续上这段历史的进程并锁住; 返回 (run, 新增的消息)。找不到返回 (None, None)。"""
    fps = convert.fingerprints(messages)
    names = sorted(t.get("name", "") for t in tools or [])
    with _lock:
        for r in sorted(_runs, key=lambda x: -len(x.view)):
            n = len(r.view)
            if r.closed or n == 0 or n >= len(fps) or fps[:n] != r.view or r.model != model:
                continue
            if getattr(r, "tool_names", None) != names:
                continue
            tail = messages[n:]
            if len(tail) != 1 or tail[0].get("role") != "user":
                continue
            if not r.lock.acquire(blocking=False):
                continue
            return r, tail[0]
        # 下一轮问答: 历史只剩每轮的问与答, 按各轮提问对上一个已答完的进程
        qs = convert.history_questions(messages)
        if qs:
            for r in sorted(_runs, key=lambda x: -x.last_used):
                if (r.closed or not r.ended or r.model != model or getattr(r, "tool_names", None) != names
                        or len(r.turns) < len(qs) or r.turns[-len(qs):] != qs):
                    continue
                if not r.lock.acquire(blocking=False):
                    continue
                return r, messages[-1]
    return None, None


def _new_run(model: str, system: str | None, tools: list | None, effort: str | None) -> Run:
    global _swept
    if not _swept:
        _swept = True
        sweep()
    _start_reaper()
    victims = []
    with _lock:
        while len(_runs) >= MAX_RUNS:
            idle = [r for r in _runs if not r.lock.locked()]
            if not idle:
                break
            v = min(idle, key=lambda x: x.last_used)
            _runs.remove(v)
            victims.append(v)
    for v in victims:
        v.close()
    r = Run(model, system, tools, effort)
    r.tool_names = sorted(t.get("name", "") for t in tools or [])
    r.lock.acquire()
    with _lock:
        _runs.append(r)
    return r


def _drop(r: Run) -> None:
    with _lock:
        if r in _runs:
            _runs.remove(r)
    r.close()


def call_messages(messages: list, system: str | None, model: str, tools: list | None = None,
                  effort: str | None = None, on_text=None, timeout: float = 1800.0) -> dict:
    if not messages:
        raise ValueError("messages 为空")
    run, tail = _claim(messages, model, system, tools, effort)
    fresh = run is None
    if fresh:
        run = _new_run(model, system, tools, effort)
    try:
        if fresh:
            run.send(convert.flatten(messages))
        else:
            _feed(run, tail)
        last = messages[-1]
        if fresh or not _has_results(last):
            run.turns.append(convert.question_key(last))
        run.ended = False
        resp = run.reply(on_text=on_text, timeout=timeout)
        run.view = convert.fingerprints(messages) + [convert.fingerprint(
            {"role": "assistant", "content": resp["content"]})]
        run.ended = resp.get("stop_reason") != "tool_use"
        return resp
    except BaseException:
        _drop(run)
        raise
    finally:
        if not run.closed:
            run.lock.release()


def _has_results(msg: dict) -> bool:
    c = msg.get("content")
    return isinstance(c, list) and any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)


def _feed(run: Run, msg: dict) -> None:
    content = msg.get("content")
    blocks = content if isinstance(content, list) else []
    results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
    if not results:
        run.send(convert.user_text(msg))
        return
    extra = "\n\n".join(b.get("text", "") for b in blocks
                        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"))
    out = {}
    for i, b in enumerate(results):
        out[b.get("tool_use_id", "")] = convert.mcp_result(b, extra if i == len(results) - 1 else "")
    run.give_results(out)


# 一问一答(资讯解读、夜间整理、盯盘判定等后台调用)同时最多几个, 免得一阵批量调用拉起一串进程
ONESHOT_MAX = 3
_oneshot = threading.BoundedSemaphore(ONESHOT_MAX)


def call_text(prompt: str, system: str | None, model: str, effort: str | None = None,
              timeout: float = 600.0) -> str:
    """一问一答: 起一个不带工具的进程, 答完即回收。"""
    with _oneshot:
        run = _new_run(model, system, None, effort)
        try:
            run.send(prompt)
            resp = run.reply(timeout=timeout)
            return "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
        finally:
            _drop(run)


def stats() -> dict:
    with _lock:
        return {"runs": len(_runs), "busy": sum(1 for r in _runs if r.lock.locked())}


def shutdown() -> None:
    with _lock:
        runs = list(_runs)
        _runs.clear()
    for r in runs:
        r.close()
