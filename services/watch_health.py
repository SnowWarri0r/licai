"""盯盘员自检(watchdog): 盯盘员各检测器是在正常跑, 还是在静默地失败。

检测器没产出发现有三种可能: 确实没事、因故跳过(客户端没开 / 当日数据没生成)、出错。三者在页面上
看起来一样(都是什么也没有), 所以每次运行都记一笔(watch_health 表, 每个检测器一行):
  ok       跑成了。可带说明(note), 也可带 alert(跑成了但数据有异常, 如龙虎榜落后)。
  skipped  跳过, 原因写进 note。expected=1 是配置决定的常态(没配置 / 没有持仓 / TDX 没启用), 不算异常。
  error    出错, fail_streak 记连续出错次数。

检测器里用 Skip(原因, expected) 表示跳过, 用 note()/alert() 附说明。

status() 按 watcher.SCHEDULE 的运行时段判断异常:
  该跑没跑       在时段内超过 STALE_MIN 分钟没有运行记录(循环卡住或挂了)。
  连续出错       fail_streak ≥ 2。
  一直没跑成     非常态的跳过持续 SKIP_GRACE_MIN 分钟以上且今天没有一次跑成; 时段结束后还没跑成也算。
  数据异常       今天的运行带 alert。
另外: 跑了 TASK_SLOW_MIN 分钟以上的子任务、超过 RUN_SLOW_MIN 分钟还在跑的问答。
"""
from __future__ import annotations

import time
import traceback
from contextvars import ContextVar
from datetime import datetime

from database import get_db

STALE_MIN, SKIP_GRACE_MIN, TASK_SLOW_MIN, RUN_SLOW_MIN = 25, 60, 10, 20

_notes: ContextVar[dict | None] = ContextVar("watch_notes", default=None)


class Skip(Exception):
    def __init__(self, reason: str, expected: bool = False):
        super().__init__(reason)
        self.reason, self.expected = reason, expected


def note(msg: str) -> None:
    n = _notes.get()
    if n is not None:
        n["note"].append(msg)


def alert(msg: str) -> None:
    n = _notes.get()
    if n is not None:
        n["alert"].append(msg)


async def run_tracked(det: str, fn, now: datetime | None = None):
    """跑一个检测器并记一笔。返回检测器的返回值; 跳过或出错返回 None。"""
    from services.watcher import now_cst
    token = _notes.set({"note": [], "alert": []})
    res, outcome, msg, expected = None, "ok", "", False
    try:
        res = await fn()
    except Skip as s:
        outcome, msg, expected = "skipped", s.reason, s.expected
    except Exception as e:  # noqa: BLE001
        outcome, msg = "error", f"{type(e).__name__}: {e}"[:300]
        print(f"[watcher] {det} 出错: {e}\n{traceback.format_exc(limit=3)}")
    finally:
        n = _notes.get()
        _notes.reset(token)
    if outcome == "ok":
        msg = "; ".join(n["note"])
    n_new = len(res) if isinstance(res, list) else int(bool(res))
    at = (now or now_cst()).isoformat(timespec="seconds")
    db = await get_db()
    try:
        await db.execute(
            "INSERT INTO watch_health (detector, last_at, outcome, n_new, note, alert, expected, fail_streak, last_ok_at) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(detector) DO UPDATE SET last_at = excluded.last_at, "
            "outcome = excluded.outcome, n_new = excluded.n_new, note = excluded.note, alert = excluded.alert, "
            "expected = excluded.expected, "
            "fail_streak = CASE WHEN excluded.outcome = 'error' THEN watch_health.fail_streak + 1 ELSE 0 END, "
            "last_ok_at = COALESCE(excluded.last_ok_at, watch_health.last_ok_at)",
            (det, at, outcome, n_new, msg, "; ".join(n["alert"]), int(expected), int(outcome == "error"),
             at if outcome == "ok" else None))
        await db.commit()
    finally:
        await db.close()
    return res


def _hm(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def judge(row: dict | None, lo: int, hi: int, active: bool, now: datetime) -> str:
    """一个检测器当前的异常描述; 正常返回 ''。row = watch_health 那一行(没有就是 None)。"""
    if not active:
        return ""
    t, day = now.hour * 60 + now.minute, now.strftime("%Y-%m-%d")
    if t < lo:
        return ""
    last = datetime.fromisoformat(row["last_at"]) if row and row.get("last_at") else None
    age = (now - last).total_seconds() / 60 if last else None
    in_window = t <= hi
    if in_window and t - lo >= STALE_MIN and (age is None or age > STALE_MIN):
        return f"该跑没跑: {_hm(lo)} 起每 10 分钟一次, " + (f"上次是 {last:%m-%d %H:%M}" if last else "还没有运行记录")
    if not row or not last or last.strftime("%Y-%m-%d") != day:
        return ""
    if row["outcome"] == "error" and row["fail_streak"] >= 2:
        return f"连续 {row['fail_streak']} 次出错: {row['note']}"
    ok_today = (row.get("last_ok_at") or "")[:10] == day
    if row["outcome"] == "skipped" and not row["expected"] and not ok_today and (not in_window or t - lo >= SKIP_GRACE_MIN):
        return ("今天一直没跑成: " if not in_window else f"{_hm(lo)} 起一直没跑成: ") + row["note"]
    if row.get("alert"):
        return row["alert"]
    return ""


async def status(now: datetime | None = None) -> dict:
    from services.market_data import _is_a_share_trading_day
    from services.watcher import SCHEDULE, now_cst
    now = now or now_cst()
    trading = _is_a_share_trading_day(now.date())
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM watch_health")
        rows = {r["detector"]: dict(r) for r in await cur.fetchall()}
        cur = await db.execute("SELECT id, title, started_at FROM agent_task WHERE status = 'running' AND started_at < ?",
                               (time.time() - TASK_SLOW_MIN * 60,))
        slow_tasks = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute("SELECT run_id, question, started_at FROM ask_run WHERE status = 'running' AND started_at < ?",
                               (time.time() - RUN_SLOW_MIN * 60,))
        slow_runs = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    dets, problems = [], []
    for det, label, lo, hi, any_day, _fn in SCHEDULE:
        r = rows.get(det)
        active = trading or any_day
        p = judge(r, lo, hi, active, now)
        if p:
            problems.append({"who": label, "text": p})
        dets.append({"detector": det, "label": label, "window": f"{_hm(lo)}~{_hm(hi)}" + ("" if any_day else " 交易日"),
                     "active_today": active, "last_at": (r or {}).get("last_at"), "outcome": (r or {}).get("outcome"),
                     "n_new": (r or {}).get("n_new"), "note": (r or {}).get("note") or "", "alert": (r or {}).get("alert") or "",
                     "last_ok_at": (r or {}).get("last_ok_at"), "problem": p})
    for t in slow_tasks:
        problems.append({"who": "子任务", "text": f"#{t['id']} {t['title']} 已跑 {int((time.time() - t['started_at']) / 60)} 分钟还没结束"})
    for r in slow_runs:
        problems.append({"who": "问答", "text": f"「{(r['question'] or '')[:20]}」已跑 {int((time.time() - r['started_at']) / 60)} 分钟还没结束"})
    return {"checked_at": now.isoformat(timespec="seconds"), "problems": problems, "detectors": dets}
