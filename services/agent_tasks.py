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

预约任务(schedule_task): 用户在对话里约「以后某个时间再查」。存成 status=scheduled 的子任务(run_at = 北京时间),
scheduler_loop 每 30 秒把到点的派出去, 跑法同后台任务(结果卡片进原会话), 另在盯盘员面板记一条(detector=scheduled),
不在那个对话里也看得到。服务重启不丢; 错过 _LATE_LIMIT_H 小时以上的标成 failed 不补跑。同时排着的最多 _MAX_SCHEDULED 个。
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
_MAX_SCHEDULED, _MAX_AHEAD_DAYS, _LATE_LIMIT_H = 10, 30, 12
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

TOOL_SCHEDULE = {
    "name": "schedule_task",
    "description": "预约一项以后执行的分析: 用户要你在某个时间再查、再看、再核对时用(例: 明天收盘后看看某只票有没有放量回落; "
                   "下周一开盘前看隔夜美股对持仓的影响)。到点由子 agent 执行, 结果以卡片进入本对话, 并在盯盘员面板记一条。"
                   "run_at 写北京时间 YYYY-MM-DD HH:MM, 按系统提示里的今天日期换算; 「收盘后」取 15:10, 「开盘前」取 09:00, "
                   "落在非交易日时顺延到下一个交易日。brief 写成独立完整的任务说明: 标的名称与代码、要核对的条件、交回什么, "
                   f"执行时子 agent 看不到本对话。最多提前 {_MAX_AHEAD_DAYS} 天, 同时排着的最多 {_MAX_SCHEDULED} 个。"
                   "预约后回复用户预约的时间与内容。",
    "input_schema": {"type": "object", "properties": {
        "title": {"type": "string", "description": "十字以内的任务名"},
        "brief": {"type": "string", "description": "给子 agent 的完整任务说明"},
        "run_at": {"type": "string", "description": "北京时间 YYYY-MM-DD HH:MM"}},
        "required": ["title", "brief", "run_at"]},
}

TOOL_CANCEL = {
    "name": "cancel_scheduled",
    "description": "取消一个还没执行的预约任务。task_id 取自 get_tasks 或本会话子任务列表。",
    "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}, "required": ["task_id"]},
}

TOOL_GET = {
    "name": "get_tasks",
    "description": "查本会话派出的子任务与后台任务的状态和结果。用户问起之前派出的后台任务(进度、结论)时用; "
                   "task_id 留空列出全部(结果截断), 给出 task_id 返回该任务完整结果。",
    "input_schema": {"type": "object", "properties": {"task_id": {"type": "integer"}}},
}


def brief_prompt(title: str, brief: str, run_at: str = "") -> str:
    return ((f"【预约任务】用户预约在 {run_at} 执行这项分析, 现在到点了; 以工具返回的最新数据为准。\n" if run_at else "") +
            "【子任务】这是主助手为当前对话拆出的一项独立工作, 你的回答会交回主助手汇总, 也会展示给用户。"
            "只完成下面这一项: 按说明调用工具取数, 回答聚焦任务要求的内容, 结论在前, 数字注明出处与日期。\n"
            f"【任务】{title}\n{brief}")


def _stream(question: str):
    from services.stock_agent import ask_stock_stream
    return ask_stock_stream(question)


async def spawn(title: str, brief: str, background: bool, untrusted: bool = False) -> int:
    """untrusted: brief 里带着第三方原文 → 子 agent 只给公开数据工具(agent_ctx.untrusted)。"""
    ctx = agent_ctx.run.get() or {}
    sid = ctx.get("session_id")
    tid = await create_agent_task(sid, ctx.get("run_id") or "", title, brief, background)
    _launch(tid, title, brief, sid, background, untrusted=untrusted)
    return tid


def _launch(tid: int, title: str, brief: str, session_id, background: bool, run_at: str = "", untrusted: bool = False) -> dict:
    live = {"status": "queued", "steps": 0, "last": "", "done": asyncio.Event(), "run_at": run_at, "untrusted": untrusted}
    _LIVE[tid] = live
    live["task"] = asyncio.create_task(_run(tid, title, brief, session_id, background, live))
    return live


async def _run(tid: int, title: str, brief: str, session_id, background: bool, live: dict) -> None:
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_MAX_PARALLEL)
    agent_ctx.depth.set(1)
    agent_ctx.run.set(None)                 # 子 agent 不碰主对话的收件箱
    agent_ctx.untrusted.set(bool(live.get("untrusted")))
    answer, used, sources, meta, status = "", [], [], {}, "failed"
    try:
        async with _sem:
            live["status"] = "running"
            await update_agent_task(tid, status="running", started_at=time.time())
            async for ev in _stream(brief_prompt(title, brief, live.get("run_at") or "")):
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
        if live.get("run_at"):
            await _note_scheduled(tid, title, live["run_at"], status, answer, meta.get("error"))
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
            "run_at": row.get("run_at") or "",
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


async def cancel_any(task_id: int) -> bool:
    """在跑的取消执行; 还没到点的预约直接作废。"""
    if cancel(task_id):
        return True
    row = await get_agent_task(task_id)
    if row and row["status"] == "scheduled":
        await update_agent_task(task_id, status="cancelled", finished_at=time.time())
        return True
    return False


# ---------------- 预约 ----------------
def _now():
    from services.watcher import now_cst
    return now_cst()


async def _count_scheduled() -> int:
    from database import get_db
    db = await get_db()
    try:
        cur = await db.execute("SELECT COUNT(*) FROM agent_task WHERE status = 'scheduled'")
        return (await cur.fetchone())[0]
    finally:
        await db.close()


def parse_run_at(s: str, now) -> str | None:
    """'YYYY-MM-DD HH:MM'(北京时间) → 规范串; 格式不对返回 None。"""
    from datetime import datetime
    try:
        t = datetime.strptime((s or "").strip()[:16].replace("T", " "), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return t.strftime("%Y-%m-%d %H:%M")


async def schedule(title: str, brief: str, run_at: str) -> dict:
    """schedule_task 工具。"""
    from datetime import datetime, timedelta
    ctx = agent_ctx.run.get()
    if ctx is None or agent_ctx.depth.get() > 0:
        return {"error": "当前环境不能预约任务"}
    if not (brief or "").strip():
        return {"error": "brief 为空"}
    now = _now()
    at = parse_run_at(run_at, now)
    if not at:
        return {"error": f"run_at 格式应为 YYYY-MM-DD HH:MM(北京时间), 收到 {run_at!r}"}
    t = datetime.strptime(at, "%Y-%m-%d %H:%M")
    n = now.replace(tzinfo=None)
    if t < n + timedelta(minutes=1):
        return {"error": f"{at} 已经过去了(现在 {n:%Y-%m-%d %H:%M}); 立即执行用 dispatch_tasks"}
    if t > n + timedelta(days=_MAX_AHEAD_DAYS):
        return {"error": f"最多提前 {_MAX_AHEAD_DAYS} 天预约"}
    if await _count_scheduled() >= _MAX_SCHEDULED:
        return {"error": f"已经排着 {_MAX_SCHEDULED} 个预约任务了, 先取消不需要的(cancel_scheduled)"}
    title = (title or "预约任务").strip()[:20]
    tid = await create_agent_task(ctx.get("session_id"), ctx.get("run_id") or "", title, brief.strip(), True, run_at=at)
    return {"task_id": tid, "title": title, "run_at": at, "status": "scheduled",
            "note": "已预约; 到点后结果以卡片出现在本对话, 并在盯盘员面板记一条。回复用户时说明预约的时间与要查的内容。"}


async def cancel_scheduled(task_id) -> dict:
    """cancel_scheduled 工具。"""
    try:
        tid = int(task_id)
    except (TypeError, ValueError):
        return {"error": "task_id 应为整数"}
    row = await get_agent_task(tid)
    if not row or row["status"] != "scheduled":
        return {"error": f"#{tid} 不是还没执行的预约任务"}
    await cancel_any(tid)
    return {"task_id": tid, "status": "cancelled"}


async def run_due(now=None) -> list[int]:
    """把到点的预约派出去; 错过太久的标 failed。返回派出的 id。"""
    from datetime import datetime, timedelta
    from database import get_db
    now = (now or _now()).replace(tzinfo=None)
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM agent_task WHERE status = 'scheduled' AND run_at <> '' AND run_at <= ? ORDER BY run_at",
                               (now.strftime("%Y-%m-%d %H:%M"),))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    out = []
    for r in rows:
        if datetime.strptime(r["run_at"], "%Y-%m-%d %H:%M") < now - timedelta(hours=_LATE_LIMIT_H):
            await update_agent_task(r["id"], status="failed", finished_at=time.time(),
                                    meta=json.dumps({"error": f"错过预约时间 {r['run_at']} 超过 {_LATE_LIMIT_H} 小时(服务没在运行), 没有补跑"},
                                                    ensure_ascii=False))
            await _note_scheduled(r["id"], r["title"], r["run_at"], "failed", "", f"错过预约时间 {r['run_at']}, 没有补跑")
            continue
        await update_agent_task(r["id"], status="queued")
        _launch(r["id"], r["title"], r["brief"], r["session_id"], True, run_at=r["run_at"])
        out.append(r["id"])
    return out


async def _note_scheduled(tid: int, title: str, run_at: str, status: str, answer: str, error) -> None:
    """预约任务跑完在盯盘员面板记一条(不在原对话里也看得到)。"""
    import re
    from services import watcher
    first = next((ln for ln in (answer or "").splitlines() if ln.strip() and not ln.strip().startswith("#")), "")
    first = re.sub(r"\*\*|__|`|\[(实测|联网)[^\]]*\]", "", first).strip()
    ok = status == "done" and answer
    f = {"key": f"scheduled::{tid}", "detector": "scheduled", "level": "info",
         "title": f"预约任务{'完成' if ok else '没跑成'}: {title}",
         "summary": (first[:120] if ok else (error or "执行失败")),
         "body": (answer if ok else "") + f"\n\n预约时间 {run_at} · 任务 #{tid}",
         "evidence": {"task_id": tid, "run_at": run_at, "status": status}}
    try:
        await watcher.record([f])
    except Exception as e:  # noqa: BLE001
        print(f"[agent-tasks] 预约 #{tid} 记盯盘员失败: {e}")


async def scheduler_loop():
    while True:
        try:
            await run_due()
        except Exception as e:  # noqa: BLE001
            print(f"[agent-tasks] 预约循环出错: {e}")
        await asyncio.sleep(30)


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
    return {"tasks": [{k: v[k] for k in ("task_id", "title", "status", "background", "run_at", "result")}
                      for v in (_view(r, full=False) for r in await list_agent_tasks(sid))]}
