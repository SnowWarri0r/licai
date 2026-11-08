"""子任务: 主 agent 派出去的子 agent。

每个子任务是一个独立上下文的子 agent(同一条 ask_stock_stream、同一套数据工具), 只拿到主 agent
写好的任务说明, 跑完把结论交回。两种用法由主 agent 在 dispatch_tasks 里选:
  wait=true   并行拆解: 主 agent 等这批子任务都回来, 再汇总作答(逐只过一遍观察池这类)。
  wait=false  后台任务: 派出后主对话立即回复, 子任务跑完结果作为一条 role=task 的消息进会话(页面上是卡片)。

边界:
- 子 agent 不能再派子任务, 也不能写记忆、记交易计划(stock_agent._active_tools 按 agent_ctx.depth 去掉)。
- 同时最多跑 _MAX_PARALLEL 个(上游有并发限流), 单次最多派 _MAX_PER_CALL 个, 一轮最多 _MAX_PER_RUN 个。
- 主 agent 等结果时这一轮被取消(用户点停) → 这批等着汇总的子任务一并取消; 后台任务不受影响。
- 服务重启时还没跑完的子任务标成 interrupted(run.py), 不自动重跑。
"""
from __future__ import annotations

import asyncio
import json
import time

from database import add_ask_message, create_agent_task, get_agent_task, list_agent_tasks, update_agent_task
from services import agent_ctx

_MAX_PARALLEL = 3
_MAX_PER_CALL = 5
_MAX_PER_RUN = 10
_WAIT_CAP_S = 300
_NOT_TOOLS = ("llm_retry", "self_check")

_sem: asyncio.Semaphore | None = None
_LIVE: dict[int, dict] = {}          # task_id → {task, status, steps, last, done: Event}

TOOL_DISPATCH = {
    "name": "dispatch_tasks",
    "description": "把工作派给子 agent 执行。每个子 agent 有独立上下文和同一套数据工具, 跑完把结论交回。两种用法: "
                   "(1) wait=true 并行拆解: 需要对多只标的或几个互相独立的方面分别取数分析时(如逐只过一遍观察池、"
                   "分别梳理几条产业链), 每项派一个子任务, 等全部返回后由你汇总作答; "
                   "(2) wait=false 后台任务: 用户表示可以稍后看结果, 或这项工作需要很多步而用户还想接着聊别的时, "
                   "派出后立即回复用户任务已在后台运行, 结果完成后以卡片出现在对话中。"
                   "brief 写成独立完整的任务说明: 标的名称与代码、要查哪些方面、交回什么内容, 子 agent 看不到本对话。"
                   f"单次最多 {_MAX_PER_CALL} 个, 每个子任务对应一项真正独立的工作; 一两只标的的普通问题由你直接调用工具完成。",
    "input_schema": {
        "type": "object",
        "properties": {
            "tasks": {"type": "array", "maxItems": _MAX_PER_CALL, "items": {
                "type": "object",
                "properties": {"title": {"type": "string", "description": "十字以内的任务名, 显示在任务条上"},
                               "brief": {"type": "string", "description": "给子 agent 的完整任务说明"}},
                "required": ["title", "brief"]}},
            "wait": {"type": "boolean", "description": "true=等结果回来由你汇总; false=后台运行, 你先回复用户"},
        },
        "required": ["tasks", "wait"],
    },
}

TOOL_GET = {
    "name": "get_tasks",
    "description": "查本会话派出的子任务与后台任务的状态和结果。用户问起之前派出的后台任务(进度、结论)时用; "
                   "task_id 留空列出全部(结果截断), 给出 task_id 返回该任务完整结果。",
    "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}},
}


def brief_prompt(title: str, brief: str) -> str:
    return ("【子任务】这是主助手为当前对话拆出的一项独立工作, 你的回答会交回主助手汇总, 也会展示给用户。"
            "只完成下面这一项: 按说明调用工具取数, 回答聚焦任务要求的内容, 结论在前, 数字注明出处与日期。\n"
            f"【任务】{title}\n{brief}")


def _stream(question: str):
    from services.stock_agent import ask_stock_stream
    return ask_stock_stream(question)


async def spawn(title: str, brief: str, background: bool) -> int:
    ctx = agent_ctx.run.get() or {}
    sid = ctx.get("session_id")
    tid = await create_agent_task(sid, ctx.get("run_id") or "", title, brief, background)
    live = {"status": "queued", "steps": 0, "last": "", "done": asyncio.Event()}
    _LIVE[tid] = live
    live["task"] = asyncio.create_task(_run(tid, title, brief, sid, background, live))
    return tid


async def _run(tid: int, title: str, brief: str, session_id, background: bool, live: dict) -> None:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_MAX_PARALLEL)
    agent_ctx.depth.set(1)
    agent_ctx.run.set(None)                 # 子 agent 不碰主对话的收件箱
    answer, used, sources, meta, status = "", [], [], {}, "failed"
    try:
        async with _sem:
            live["status"] = "running"
            await update_agent_task(tid, status="running", started_at=time.time())
            async for ev in _stream(brief_prompt(title, brief)):
                t = ev.get("type")
                if t == "step" and ev.get("tool") not in _NOT_TOOLS:
                    used.append(ev.get("tool"))
                    live["steps"] += 1
                    live["last"] = ev.get("label") or ev.get("tool") or ""
                elif t == "sources":
                    sources.extend(ev.get("sources") or [])
                elif t == "answer":
                    answer = ev.get("text") or ""
                elif t == "error":
                    meta["error"] = ev.get("error")
        status = "done" if answer else "failed"
    except asyncio.CancelledError:
        status = "cancelled"
    except Exception as e:  # noqa: BLE001
        meta["error"] = str(e)
    meta.update(tools_used=used, sources=sources[:20])
    try:
        await update_agent_task(tid, status=status, result=answer, meta=json.dumps(meta, ensure_ascii=False),
                                finished_at=time.time())
        if answer and (background or live.get("background")) and session_id:
            await add_ask_message(session_id, "task", answer, json.dumps(
                {"task_id": tid, "title": title, "tools_used": used, "sources": sources[:20]}, ensure_ascii=False))
    finally:
        live["status"] = status
        live["done"].set()


def _view(row: dict, full: bool = True) -> dict:
    live = _LIVE.get(row["id"]) or {}
    status = live.get("status") if live and row["status"] in ("queued", "running") else row["status"]
    try:
        meta = json.loads(row.get("meta") or "{}")
    except ValueError:
        meta = {}
    res = row.get("result") or ""
    return {"task_id": row["id"], "title": row["title"], "status": status, "background": bool(row.get("background")),
            "steps": live.get("steps", len(meta.get("tools_used") or [])), "last_step": live.get("last", ""),
            "result": res if full else res[:600], "error": meta.get("error"), "sources": meta.get("sources") or [],
            "run_id": row.get("run_id") or "", "created_at": row.get("created_at"), "finished_at": row.get("finished_at")}


async def view(task_id: int) -> dict | None:
    row = await get_agent_task(task_id)
    return _view(row) if row else None


async def list_session(session_id: int) -> list[dict]:
    return [_view(r) for r in await list_agent_tasks(session_id)]


async def wait(ids: list[int], timeout: float) -> None:
    futs = [asyncio.ensure_future(_LIVE[i]["done"].wait()) for i in ids if i in _LIVE]
    if not futs:
        return
    try:
        await asyncio.wait(futs, timeout=timeout)
    finally:
        for f in futs:
            f.cancel()


def cancel(task_id: int) -> bool:
    live = _LIVE.get(task_id)
    t = (live or {}).get("task")
    if not t or t.done():
        return False
    t.cancel()
    return True


async def dispatch(tasks: list, wait_for: bool) -> dict:
    """dispatch_tasks 工具。"""
    ctx = agent_ctx.run.get()
    if ctx is None or agent_ctx.depth.get() > 0:
        return {"error": "当前环境不能派子任务"}
    tasks = [t for t in (tasks or []) if isinstance(t, dict) and (t.get("brief") or "").strip()]
    if not tasks:
        return {"error": "tasks 为空: 每项要有 title 与 brief"}
    room = _MAX_PER_RUN - ctx.get("spawned", 0)
    if room <= 0:
        return {"error": f"这一轮已派出 {_MAX_PER_RUN} 个子任务, 到上限了; 用已有结果作答"}
    dropped = max(0, len(tasks) - min(room, _MAX_PER_CALL))
    tasks = tasks[:min(room, _MAX_PER_CALL)]
    ctx["spawned"] = ctx.get("spawned", 0) + len(tasks)
    ids = [await spawn((t.get("title") or "子任务").strip()[:20], t["brief"].strip(), not wait_for) for t in tasks]
    note = f"另有 {dropped} 项超出上限未派出。" if dropped else ""
    if not wait_for:
        return {"tasks": [{"task_id": i, "title": (t.get("title") or "")[:20], "status": "queued"} for i, t in zip(ids, tasks)],
                "note": "已在后台运行, 完成后结果以卡片出现在对话中。回复用户时说明派出了哪些任务。" + note}
    try:
        await wait(ids, _WAIT_CAP_S)
    except asyncio.CancelledError:          # 主 agent 这一轮被停了: 等着汇总的子任务没人要了
        for i in ids:
            cancel(i)
        raise
    out = [await view(i) for i in ids]
    slow = [v["title"] for v in out if v and v["status"] in ("queued", "running")]
    if slow:
        note += f"{'、'.join(slow)} 超过 {_WAIT_CAP_S // 60} 分钟还没跑完, 转为后台继续, 完成后可用 get_tasks 取结果。"
        for v in out:
            if v and v["status"] in ("queued", "running"):        # 转后台: 跑完照后台任务出卡片
                _LIVE[v["task_id"]]["background"] = True
                await update_agent_task(v["task_id"], background=1)
    return {"tasks": [{k: v[k] for k in ("task_id", "title", "status", "result", "error")} for v in out if v],
            **({"note": note} if note else {})}


async def get(task_id: int | None) -> dict:
    """get_tasks 工具。"""
    ctx = agent_ctx.run.get() or {}
    if task_id:
        v = await view(int(task_id))
        return v or {"error": f"没有子任务 {task_id}"}
    sid = ctx.get("session_id")
    if not sid:
        return {"tasks": []}
    return {"tasks": [{k: v[k] for k in ("task_id", "title", "status", "background", "result")}
                      for v in (_view(r, full=False) for r in await list_agent_tasks(sid))]}
