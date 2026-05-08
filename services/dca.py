"""定投 (DCA) 自动扣款.

每天检查 active + next_due <= today 的计划, 写一条 pending ADD action 进流水.

frequency:
  - daily_trading: 每个 A 股交易日 (周末 + 法定节假日跳过, 用 chinese_calendar)
  - weekly: 每周指定星期 (1=周一..7=周日)
  - monthly: 每月指定日 (1-31, 月末超出 clamp)

mode:
  - amount: 固定金额 ¥, 写 amount=value, shares=None (T+1 净值确认)
  - shares: 固定份数, 写 shares=value, amount=0 (确认时填实际成本)
"""
from __future__ import annotations
import calendar
from datetime import date, datetime, timedelta
from typing import Iterable

from database import (
    list_due_dca_schedules,
    update_dca_schedule,
    add_external_action,
)


def _is_a_share_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        import chinese_calendar as cc
        return cc.is_workday(d)
    except Exception:
        return True  # 库不可用时退到周末判断


def _next_a_share_trading_day(after: date) -> date:
    candidate = after + timedelta(days=1)
    for _ in range(60):  # 兜底, 不超过 60 天
        if _is_a_share_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    return candidate  # 极端情况兜底返回


def _next_monthly(cur: date, day_of_month: int) -> date:
    next_month = cur.month + 1
    next_year = cur.year
    if next_month > 12:
        next_month = 1
        next_year += 1
    last_day = calendar.monthrange(next_year, next_month)[1]
    target_day = min(int(day_of_month), last_day)
    return date(next_year, next_month, target_day)


def _next_weekly(cur: date, day_of_week: int) -> date:
    """day_of_week: 1=Mon..7=Sun. Returns next occurrence strictly after cur."""
    target = ((int(day_of_week) - 1) % 7) + 1  # 1..7 normalize
    diff = (target - cur.isoweekday()) % 7
    if diff == 0:
        diff = 7
    return cur + timedelta(days=diff)


def compute_next_due(current: str | date, frequency: str,
                     day_of_month: int | None = None,
                     day_of_week: int | None = None) -> str:
    """从 current 推到下一个触发日."""
    cur = datetime.fromisoformat(current[:10]).date() if isinstance(current, str) else current
    freq = (frequency or "monthly").lower()
    if freq == "daily_trading":
        return _next_a_share_trading_day(cur).isoformat()
    if freq == "weekly":
        if day_of_week is None:
            day_of_week = cur.isoweekday()
        return _next_weekly(cur, day_of_week).isoformat()
    # monthly
    if day_of_month is None:
        day_of_month = cur.day
    return _next_monthly(cur, day_of_month).isoformat()


def initial_next_due(frequency: str,
                     day_of_month: int | None = None,
                     day_of_week: int | None = None,
                     today: date | None = None) -> str:
    """新建计划时的首次触发日 (>= today)."""
    today = today or date.today()
    freq = (frequency or "monthly").lower()
    if freq == "daily_trading":
        if _is_a_share_trading_day(today):
            return today.isoformat()
        return _next_a_share_trading_day(today).isoformat()
    if freq == "weekly":
        if day_of_week is None:
            day_of_week = today.isoweekday()
        # 当周该星期 (>=今天) 否则下周
        target = ((int(day_of_week) - 1) % 7) + 1
        diff = (target - today.isoweekday()) % 7
        return (today + timedelta(days=diff)).isoformat()
    # monthly
    if day_of_month is None:
        day_of_month = today.day
    last_day = calendar.monthrange(today.year, today.month)[1]
    target_day = min(int(day_of_month), last_day)
    candidate = date(today.year, today.month, target_day)
    if candidate < today:
        return _next_monthly(candidate, day_of_month).isoformat()
    return candidate.isoformat()


async def fire_due_dcas(today: date | None = None) -> list[dict]:
    today = today or date.today()
    today_str = today.isoformat()
    schedules = await list_due_dca_schedules(today_str)
    fired: list[dict] = []
    for s in schedules:
        try:
            mode = (s.get("mode") or "amount").lower()
            value = float(s["value"])
            kwargs = {
                "asset_id": s["asset_id"],
                "action_type": "ADD",
                "trade_date": today_str,
                "note": f"DCA {today_str}",
                "status": "pending",
            }
            if mode == "shares":
                kwargs["shares"] = value
                kwargs["amount"] = 0
            else:
                kwargs["amount"] = value
            action_id = await add_external_action(**kwargs)
            next_due = compute_next_due(
                today_str,
                s.get("frequency") or "monthly",
                s.get("day_of_month"),
                s.get("day_of_week"),
            )
            await update_dca_schedule(
                s["id"], next_due=next_due, last_fired_at=today_str,
            )
            fired.append({
                "dca_id": s["id"],
                "asset_id": s["asset_id"],
                "action_id": action_id,
                "mode": mode,
                "value": value,
                "next_due": next_due,
            })
            print(f"[dca] fired #{s['id']} → action #{action_id}, next due {next_due}")
        except Exception as e:
            print(f"[dca] fire #{s.get('id')} failed: {e}")
    return fired
