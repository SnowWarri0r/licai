"""一次 agent 运行(run): 跑在服务端后台, 不再挂在浏览器那根 fetch 上。

起因: 问一句然后切到别的页, 那一轮就没了 —— 切回来是空的, 历史里也翻不到。实测是两层原因,
两层都得改, 只改一层现象还在:

1) 执行权原来在前端。切页 → 组件卸载 → abort() → 客户端断开 → Starlette 取消响应任务,
   ask_stock_stream 在下一个 yield 处收到 CancelledError 就地死掉。拿一个最小探针实测过:
   客户端一走, 生成器当场 CANCELLED, 后面的 tick 一个都不再出现 —— 所以"后台接着跑"从来没发生。
2) 落库原来在最后一步(前端收到完整答案才 POST /api/ask/messages), 而会话是"存第一条消息"
   时才建的。一轮没答完 → 一行都不写 → 历史里连"我问过什么"都没有, 自然找不到。

现在: 开跑先建会话并把问题写进库(历史里立刻看得见), agent 在独立 task 里跑到底, 事件按序
进内存缓冲, 前端拿游标拉、断了从游标续拉。前端断开只是"不看了", 不影响它跑; 答完由服务端
落库 —— 所以切页 / 刷新 / 甚至关掉页面, 答案照样会出现在历史里。

内存里只留"正在跑 + 刚跑完"的那几条(供重挂和续拉), 原文归 DB。服务重启则内存里的 run 全没,
这时前端续拉会拿到一条明确的提示, 让人去历史里翻原文, 而不是空转。
"""
from __future__ import annotations
import asyncio
import json as _json
import time
import uuid
from typing import Optional

from database import (create_ask_session, add_ask_message, save_ask_run, update_ask_run,
                      get_ask_run, list_ask_runs, prune_ask_runs)
from services.stock_agent import ask_stock_stream

_KEEP_DONE_SEC = 30 * 60      # 跑完的 run 在内存留半小时: 够切回来把过程重看一遍
_MAX_RUNS = 30
_HARD_WALL_SEC = 20 * 60      # 单条 SSE 连接的兜底上限, 不让它无限挂着
# 事件落盘的合并窗口: 每条都写一次盘, 一轮下来是几十次开关连接; 攒一下再写。
# 收尾必定 flush, 所以最坏也就丢掉最后这半秒的中间步骤(答案在 ask_message 里, 不受影响)。
_FLUSH_SEC = 0.5
# 过程提示不是工具, 不进历史 meta(否则重开会话会多出假工具胶囊)
_NOT_TOOLS = ("llm_retry", "self_check")

_RUNS: "dict[str, Run]" = {}


class Run:
    """一问一答的一次执行。events 是只追加的事件流, 下标即游标 —— 前端断开重连靠它对齐。"""

    def __init__(self, question: str, session_id: int, scope: str, title: str):
        self.id = uuid.uuid4().hex[:12]
        self.question = question
        self.session_id = session_id
        self.scope = scope                 # market | stock:<code>, 前端按这个认领属于自己的那条
        self.title = title
        self.events: list[dict] = []
        self.done = False
        self.answered = False              # 有没有真答出来(区别于出错/取消)
        self.started_at = time.time()
        self.finished_at: Optional[float] = None
        self.task: Optional[asyncio.Task] = None
        self._flushed_at = 0.0             # 上次落盘时间(合并窗口用)

    def brief(self) -> dict:
        return {"run_id": self.id, "session_id": self.session_id, "question": self.question,
                "scope": self.scope, "done": self.done, "answered": self.answered,
                "n_events": len(self.events), "started_at": self.started_at}

    async def flush(self, force: bool = False) -> None:
        """把事件流写盘。进程没了内存就没了 —— 落盘的这份是重启后唯一还认得这一轮的东西。"""
        if not force and time.time() - self._flushed_at < _FLUSH_SEC:
            return
        self._flushed_at = time.time()
        status = ("done" if self.done else "running")
        try:
            await update_ask_run(self.id, _json.dumps(self.events, ensure_ascii=False),
                                 status, self.answered, self._flushed_at)
        except Exception:
            pass                            # 落盘失败不该影响正在跑的这一轮


def _gc() -> None:
    now = time.time()
    for rid, r in list(_RUNS.items()):
        if r.done and r.finished_at and now - r.finished_at > _KEEP_DONE_SEC:
            _RUNS.pop(rid, None)
    if len(_RUNS) > _MAX_RUNS:                     # 还超, 就按开始时间丢最老的已完成
        old = sorted((r for r in _RUNS.values() if r.done), key=lambda r: r.started_at)
        for r in old[:len(_RUNS) - _MAX_RUNS]:
            _RUNS.pop(r.id, None)


async def _drive(run: Run, agent_question: str, history: list, images: Optional[list]) -> None:
    """把 agent 跑完, 事件塞进缓冲, 收尾落库。这里不碰任何请求对象 —— 前端在不在都一样跑。"""
    answer = None
    sources: list = []
    steps: list = []
    charts: list = []
    cancelled = False
    try:
        async for ev in ask_stock_stream(agent_question, history, images):
            run.events.append(ev)
            await run.flush()               # 合并窗口内不重复写
            t = ev.get("type")
            if t == "answer":
                answer = ev.get("text")
            elif t == "sources":
                sources.extend(ev.get("sources") or [])
            elif t == "step":
                steps.append(ev.get("tool"))
            elif t == "chart":
                charts.append(ev.get("url"))
    except asyncio.CancelledError:
        cancelled = True
        run.events.append({"type": "error", "error": "已取消"})
    except Exception as e:
        run.events.append({"type": "error", "error": str(e)})
    # 落库放在这儿而不是前端: 答出来就存, 哪怕早就没人在看了 —— 这一步就是"切走也不丢"
    if answer and not cancelled:
        meta = {"tools_used": [s for s in steps if s and s not in _NOT_TOOLS],
                "sources": sources, "charts": charts}
        try:
            await add_ask_message(run.session_id, "assistant", answer,
                                  _json.dumps(meta, ensure_ascii=False))
            run.answered = True
        except Exception as e:
            run.events.append({"type": "error", "error": f"答案没能落库: {e}"})
    run.done = True
    run.finished_at = time.time()
    run.events.append({"type": "done"})
    await run.flush(force=True)             # 收尾这次必写: 内存里的会被 GC, 盘上的留着


async def start(question: str, *, agent_question: Optional[str] = None,
                history: Optional[list] = None, images: Optional[list] = None,
                session_id: Optional[int] = None, title: Optional[str] = None,
                scope: str = "market", user_meta: Optional[dict] = None) -> Run:
    """建会话(如需) → 先把问题落库 → 起后台 task。返回后这一轮的命运已经跟前端无关了。

    question 是给人看的原话; agent_question 是真正喂给 agent 的(抽屉会前缀上"名称(代码): ")。
    """
    _gc()
    if not session_id:
        session_id = await create_ask_session((title or question)[:80])
    await add_ask_message(session_id, "user", question,
                          _json.dumps(user_meta, ensure_ascii=False) if user_meta else "")
    run = Run(question, session_id, scope, title or question)
    _RUNS[run.id] = run
    try:                                    # 先建流水行: 之后每次 flush 都是 UPDATE
        await save_ask_run(run.id, session_id, scope, question, run.started_at)
        await prune_ask_runs()
    except Exception:
        pass
    run.task = asyncio.create_task(_drive(run, agent_question or question, history or [], images))
    return run


async def follow(run_id: str, cursor: int = 0):
    """从 cursor 起把事件吐出去, 追平了就等新的。每条带回 cursor, 断线后照它续拉。

    内存里没有就回盘上找: 重启之后那一轮的过程还在(状态已标 interrupted), 至少能看到它
    走到哪一步, 而不是一句"不在内存里了"。
    """
    run = _RUNS.get(run_id)
    if not run:
        async for ev in _follow_from_db(run_id, cursor):
            yield ev
        return
    if cursor < 0:
        cursor = 0
    deadline = time.time() + _HARD_WALL_SEC
    while True:
        if cursor < len(run.events):
            ev = run.events[cursor]
            cursor += 1
            yield {**ev, "cursor": cursor}
            if ev.get("type") == "done":
                return
            continue
        if run.done:                      # done 事件已经发过了(或缓冲被清), 别干等
            return
        if time.time() > deadline:
            return
        await asyncio.sleep(0.12)


async def _follow_from_db(run_id: str, cursor: int):
    """重放盘上那一轮(它一定已经结束了 —— 进程都换了, 原来的 task 不可能还在)。"""
    try:
        row = await get_ask_run(run_id)
    except Exception:
        row = None
    if not row:
        yield {"type": "error", "error": "这一轮找不着了(流水已清理), 原文去历史里翻", "gone": True}
        return
    try:
        evs = _json.loads(row.get("events") or "[]")
    except Exception:
        evs = []
    for i, ev in enumerate(evs):
        if i >= max(0, cursor):
            yield {**ev, "cursor": i + 1}
    if row.get("status") == "interrupted":
        yield {"type": "error", "error": "这一轮跑到一半服务重启了, 没跑完", "cursor": len(evs) + 1}
    if not evs or evs[-1].get("type") != "done":
        yield {"type": "done", "cursor": len(evs) + 2}


def get(run_id: str) -> Optional[Run]:
    return _RUNS.get(run_id)


async def live(scope: Optional[str] = None) -> list[dict]:
    """能重挂的 run: 内存里在跑/刚跑完的, 再补上盘里最近几条(重启前的, 都已收尾)。

    盘上那些一律 done=true —— 它们不可能再产出新事件了, 前端不该去"接着等"。
    """
    _gc()
    out = [r.brief() for r in _RUNS.values() if not scope or r.scope == scope]
    seen = {x["run_id"] for x in out}
    try:
        for row in await list_ask_runs(limit=20):
            if row["run_id"] in seen or (scope and row.get("scope") != scope):
                continue
            out.append({"run_id": row["run_id"], "session_id": row.get("session_id"),
                        "question": row.get("question") or "", "scope": row.get("scope") or "",
                        "done": True, "answered": bool(row.get("answered")),
                        "interrupted": row.get("status") == "interrupted",
                        "n_events": 0, "started_at": row.get("started_at") or 0})
    except Exception:
        pass
    out.sort(key=lambda x: x["started_at"])
    return out


def cancel(run_id: str) -> bool:
    """真取消(点"新对话"/明确停掉时用)。跟"前端走开"区分开: 走开不取消。"""
    run = _RUNS.get(run_id)
    if not run or run.done or not run.task:
        return False
    run.task.cancel()
    return True
