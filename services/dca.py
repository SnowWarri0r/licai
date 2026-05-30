"""定投 (DCA) 自动扣款.

每天检查 active + next_due <= today 的计划, 写一条 pending ADD action 进流水.

frequency:
  - daily_trading: 每个对应市场交易日触发. 市场由基金名推断:
      CN (A 股 / 黄金 ETF): chinese_calendar
      US (QDII 纳指/标普/全球): NYSE 日历
      HK (港股 / 恒生): HKEX 日历
      JP (日经): JPX 日历
  - weekly: 每周指定星期 (1=周一..7=周日)
  - monthly: 每月指定日 (1-31, 月末超出 clamp)

mode:
  - amount: 固定金额 ¥, 写 amount=value, shares=None (T+1 净值确认)
  - shares: 固定份数, 写 shares=value, amount=0 (确认时填实际成本)
"""
from __future__ import annotations
import calendar
import re
from datetime import date, datetime, timedelta
from typing import Iterable

from database import (
    list_due_dca_schedules,
    update_dca_schedule,
    add_external_action,
    get_external_asset,
)


def _market_of_asset_name(name: str | None) -> str:
    """按基金名推断底层市场代码: CN / US / HK / JP."""
    n = str(name or "")
    if re.search(r"港股|恒生|H\s*股|中概", n):
        return "HK"
    if re.search(r"QDII|纳斯达克|纳指|标普|美股|全球(?!财)|海外(?!债)", n):
        return "US"
    if re.search(r"日经|东证|TOPIX", n):
        return "JP"
    # 默认 A 股 (含黄金 / 商品 / A 股 ETF / 行业基)
    return "CN"


_EC_CAL_BY_MARKET = {"US": "XNYS", "HK": "XHKG", "JP": "JPX"}
_ec_cal_cache: dict[str, object] = {}


def _get_ec_calendar(market: str):
    if market in _ec_cal_cache:
        return _ec_cal_cache[market]
    try:
        import exchange_calendars as ec
        cal_id = _EC_CAL_BY_MARKET.get(market)
        if not cal_id:
            return None
        cal = ec.get_calendar(cal_id)
        _ec_cal_cache[market] = cal
        return cal
    except Exception as e:
        print(f"[dca] exchange_calendars unavailable for {market}: {e}")
        return None


def _is_a_share_trading_day(d: date) -> bool:
    if d.weekday() >= 5:
        return False
    try:
        import chinese_calendar as cc
        return cc.is_workday(d)
    except Exception:
        return True  # 库不可用时退到周末判断


def _is_market_trading_day(d: date, market: str) -> bool:
    if market == "CN":
        return _is_a_share_trading_day(d)
    cal = _get_ec_calendar(market)
    if cal is None:
        # exchange_calendars 不可用 → 兜底用 A 股日历, 至少不写错单
        return _is_a_share_trading_day(d)
    try:
        return bool(cal.is_session(d.isoformat()))
    except Exception:
        return _is_a_share_trading_day(d)


def _next_market_trading_day(after: date, market: str) -> date:
    candidate = after + timedelta(days=1)
    for _ in range(60):
        if _is_market_trading_day(candidate, market):
            return candidate
        candidate += timedelta(days=1)
    return candidate


def _next_a_share_trading_day(after: date) -> date:
    return _next_market_trading_day(after, "CN")


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
                     day_of_week: int | None = None,
                     market: str = "CN") -> str:
    """从 current 推到下一个触发日."""
    cur = datetime.fromisoformat(current[:10]).date() if isinstance(current, str) else current
    freq = (frequency or "monthly").lower()
    if freq == "daily_trading":
        return _next_market_trading_day(cur, market).isoformat()
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
                     today: date | None = None,
                     market: str = "CN") -> str:
    """新建计划时的首次触发日.

    设计选择: 严格 > today (不在创建当天扣款), 避免跟用户当天手动录入冲突.
    用户想立即扣可以手动调用 /api/dca/fire-due, 或把 next_due 改成今天.
    """
    today = today or date.today()
    freq = (frequency or "monthly").lower()
    if freq == "daily_trading":
        # 跳今天, 找下个交易日 (按市场)
        return _next_market_trading_day(today, market).isoformat()
    if freq == "weekly":
        if day_of_week is None:
            day_of_week = today.isoweekday()
        target = ((int(day_of_week) - 1) % 7) + 1
        diff = (target - today.isoweekday()) % 7
        if diff == 0:
            diff = 7  # 同星期算下周
        return (today + timedelta(days=diff)).isoformat()
    # monthly
    if day_of_month is None:
        day_of_month = today.day
    last_day = calendar.monthrange(today.year, today.month)[1]
    target_day = min(int(day_of_month), last_day)
    candidate = date(today.year, today.month, target_day)
    if candidate <= today:
        return _next_monthly(candidate, day_of_month).isoformat()
    return candidate.isoformat()


async def fire_due_dcas(today: date | None = None) -> list[dict]:
    today = today or date.today()
    today_str = today.isoformat()
    schedules = await list_due_dca_schedules(today_str)
    fired: list[dict] = []
    for s in schedules:
        try:
            freq = (s.get("frequency") or "monthly").lower()
            # daily_trading 模式: 按底层市场日历判断, 海外休市日跳过 fire,
            # next_due 推到下一个对应市场交易日 (保持 active, 不写脏 pending 流水).
            asset = await get_external_asset(s["asset_id"])
            market = _market_of_asset_name((asset or {}).get("name", ""))
            if freq == "daily_trading" and not _is_market_trading_day(today, market):
                next_due = _next_market_trading_day(today, market).isoformat()
                await update_dca_schedule(s["id"], next_due=next_due)
                print(f"[dca] skip #{s['id']} ({market} 休市) → next due {next_due}")
                continue

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
                freq,
                s.get("day_of_month"),
                s.get("day_of_week"),
                market=market,
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
                "market": market,
            })
            print(f"[dca] fired #{s['id']} ({market}) → action #{action_id}, next due {next_due}")
        except Exception as e:
            print(f"[dca] fire #{s.get('id')} failed: {e}")
    return fired
