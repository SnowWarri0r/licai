"""WebSocket endpoint for real-time price push and alerts."""
from __future__ import annotations
import asyncio
import json
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from database import get_all_holdings, get_custom_alerts, mark_alert_triggered
from services.market_data import get_realtime_quotes, is_market_hours, is_trading_day_active
from services import feishu_notify
from config import config

router = APIRouter()

_clients: set[WebSocket] = set()


async def broadcast(message: dict):
    dead = set()
    data = json.dumps(message, ensure_ascii=False, default=str)
    for ws in list(_clients):
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    for d in dead:
        _clients.discard(d)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _clients.add(ws)
    try:
        while True:
            try:
                data = await asyncio.wait_for(ws.receive_text(), timeout=30)
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
            except asyncio.TimeoutError:
                try:
                    await ws.send_text(json.dumps({"type": "heartbeat"}))
                except Exception:
                    break
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(ws)


async def price_monitor_loop():
    """Background task: push prices every cycle, recompute suggestions every 5min."""
    while True:
        try:
            if not _clients:
                await asyncio.sleep(5)
                continue

            interval = config.refresh_interval if is_market_hours() else config.idle_interval

            holdings = await get_all_holdings()
            if not holdings:
                await asyncio.sleep(interval)
                continue

            codes = [h["stock_code"] for h in holdings]
            quotes = await get_realtime_quotes(codes)
            if not quotes:
                await asyncio.sleep(interval)
                continue

            # Push price updates (lightweight)
            await broadcast({
                "type": "price_update",
                "data": quotes,
                "market_open": is_trading_day_active(),
            })

            # Only check alerts during active trading hours (9:30-11:30, 13:00-15:00)
            if not is_market_hours():
                await asyncio.sleep(interval)
                continue

            # Check unwind tranche triggers + fundamental changes
            try:
                await _check_unwind_alerts(holdings, quotes)
            except Exception as e:
                print(f"[unwind_alert] Error: {e}")

            # Check custom price alerts
            try:
                custom_alerts = await get_custom_alerts()
                for ca in custom_alerts:
                    q = quotes.get(ca["stock_code"])
                    if not q or q["price"] <= 0:
                        continue
                    price = q["price"]
                    triggered = False
                    if ca["alert_type"] == "price_below" and price <= ca["price"]:
                        triggered = True
                    elif ca["alert_type"] == "price_above" and price >= ca["price"]:
                        triggered = True
                    elif ca["alert_type"] == "stop_loss" and price <= ca["price"]:
                        triggered = True

                    if triggered:
                        msg = ca["message"] or f"{'跌破' if 'below' in ca['alert_type'] or 'stop' in ca['alert_type'] else '突破'} {ca['price']:.2f}"
                        alert_data = {
                            "stock_code": ca["stock_code"],
                            "stock_name": q.get("stock_name", ca["stock_code"]),
                            "alert_type": "CUSTOM_" + ca["alert_type"].upper(),
                            "price": price,
                            "message": msg,
                        }
                        await broadcast({"type": "alert", "data": alert_data})
                        if feishu_notify.is_enabled():
                            await feishu_notify.send_text(
                                f"⚠️ 自定义告警: {q.get('stock_name', '')}({ca['stock_code']}) {msg}，当前价 {price:.2f}"
                            )
                        await mark_alert_triggered(ca["id"])
            except Exception as e:
                print(f"[custom_alert] Error: {e}")

            await asyncio.sleep(interval)
        except Exception as e:
            print(f"[monitor] Error: {e}")
            await asyncio.sleep(10)


# --- Unwind tranche + fundamental health alerts ---
_tranche_alert_history: dict[tuple[int, str], float] = {}  # (tranche_id, type) -> last_fired_ts
_fundamental_level_cache: dict[str, str] = {}  # stock_code -> last known level
_TRANCHE_ALERT_COOLDOWN = 1800  # 30 min per tranche


async def _check_unwind_alerts(holdings, quotes):
    """Check for tranche triggers and fundamental level transitions.

    Fires Feishu + WS alerts when:
    1. Price reaches a pending tranche's trigger_price AND health check passes
    2. Fundamental health degrades (🟢 → 🟡 or any → 🔴)
    """
    from database import get_tranches
    from services.fundamental_score import fetch_health_snapshot

    now = time.time()

    for h in holdings:
        code = h["stock_code"]
        q = quotes.get(code)
        if not q or q["price"] <= 0:
            continue
        price = q["price"]
        name = q.get("stock_name") or h.get("stock_name") or code

        # Fetch current health
        try:
            fund = await fetch_health_snapshot(code, name)
        except Exception:
            continue
        level = fund["level"]

        # --- Health degradation alert ---
        prev_level = _fundamental_level_cache.get(code)
        if prev_level and prev_level != level:
            is_degrade = (prev_level == "green" and level in ("yellow", "red")) \
                or (prev_level == "yellow" and level == "red")
            if is_degrade:
                emoji_prev = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[prev_level]
                emoji_now = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[level]
                msg = f"⚠️ {name}({code}) 基本面恶化 {emoji_prev} → {emoji_now}\n" \
                      f"当前价 {price:.2f} | 评分 {fund['score']:+.2f}\n" \
                      f"建议：{'暂停所有档位加仓' if level == 'red' else '仅执行浅档'}"
                if feishu_notify.is_enabled():
                    await feishu_notify.send_text(msg)
                await broadcast({
                    "type": "alert",
                    "data": {
                        "stock_code": code, "stock_name": name,
                        "alert_type": "HEALTH_DEGRADED", "price": price,
                        "message": f"基本面 {emoji_prev}→{emoji_now}",
                    },
                })
        _fundamental_level_cache[code] = level

        # --- Tranche trigger check ---
        tranches = await get_tranches(code)
        for t in tranches:
            if t["status"] != "pending":
                continue
            trig = t["trigger_price"]
            # Within 0.3% of trigger = triggered
            if price > trig * 1.003:
                continue

            # Health gate
            req = t["requires_health"]
            health_ok = req == "any" \
                or (req == "yellow" and level != "red") \
                or (req == "green" and level == "green")

            key = (t["id"], "triggered" if health_ok else "locked")
            last_fired = _tranche_alert_history.get(key, 0)
            if now - last_fired < _TRANCHE_ALERT_COOLDOWN:
                continue
            _tranche_alert_history[key] = now

            health_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[level]
            if health_ok:
                msg = (
                    f"📍 {name}({code}) 档位{t['idx']} 触发\n"
                    f"现价 {price:.2f} ≈ 触发价 {trig:.2f}\n"
                    f"健康度 {health_emoji} | 建议加仓 +{t['shares']}股"
                )
                alert_type = "TRANCHE_TRIGGERED"
            else:
                msg = (
                    f"🔒 {name}({code}) 档位{t['idx']} 触及但锁定\n"
                    f"现价 {price:.2f} ≈ 触发价 {trig:.2f}\n"
                    f"健康度 {health_emoji} (需 {req}) | 暂不建议加仓"
                )
                alert_type = "TRANCHE_LOCKED"

            if feishu_notify.is_enabled():
                await feishu_notify.send_text(msg)
            await broadcast({
                "type": "alert",
                "data": {
                    "stock_code": code, "stock_name": name,
                    "alert_type": alert_type, "price": price,
                    "message": f"档{t['idx']} {trig:.2f} {'可执行' if health_ok else '锁定'}",
                },
            })


# --- Pre-market Feishu push ---
_premarket_sent_date: str = ""

async def premarket_push_loop():
    """Send today's unwind plan summary to Feishu around 9:15 on trading days."""
    global _premarket_sent_date
    from datetime import datetime, timezone, timedelta

    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            cst_now = utc_now + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            t = cst_now.hour * 60 + cst_now.minute

            # Send between 9:10~9:20 on weekdays, once per day
            if (cst_now.weekday() < 5 and 550 <= t <= 560
                    and today != _premarket_sent_date
                    and feishu_notify.is_enabled()):

                holdings = await get_all_holdings()
                if holdings:
                    codes = [h["stock_code"] for h in holdings]
                    quotes = await get_realtime_quotes(codes)

                    lines = [f"📊 今日解套重点 ({today})\n"]
                    from database import get_unwind_plan, get_tranches
                    from services.fundamental_score import fetch_health_snapshot

                    for h in holdings:
                        code = h["stock_code"]
                        q = quotes.get(code)
                        if not q or q["price"] <= 0:
                            continue

                        plan = await get_unwind_plan(code)
                        tranches = await get_tranches(code) if plan else []
                        fund = await fetch_health_snapshot(code, h.get("stock_name", ""))
                        health_emoji = {"green": "🟢", "yellow": "🟡", "red": "🔴"}[fund["level"]]
                        cost_gap_pct = (h["cost_price"] - q["price"]) / h["cost_price"] * 100 if h["cost_price"] > 0 else 0

                        name = h.get("stock_name") or code
                        lines.append(f"【{name}】距回本: {cost_gap_pct:.1f}% | 健康度 {health_emoji}")

                        if tranches:
                            pending = [t for t in tranches if t["status"] == "pending"]
                            nearby = [t for t in pending if abs(t["trigger_price"] - q["price"]) / q["price"] < 0.05]
                            if fund["level"] == "red":
                                lines.append(f"  ⏸ 所有档位暂停 (基本面红灯)")
                            elif nearby:
                                for t in nearby[:2]:
                                    dist = (t["trigger_price"] - q["price"]) / q["price"] * 100
                                    lines.append(f"  📌 档{t['idx']} {t['trigger_price']:.2f} ({dist:+.1f}%) +{t['shares']}股")
                            else:
                                lines.append(f"  无档位接近触发,观望")
                        else:
                            lines.append(f"  ⚠️ 尚未生成解套计划")
                        lines.append("")

                    if len(lines) > 1:
                        await feishu_notify.send_text("\n".join(lines))
                        _premarket_sent_date = today
                        print(f"[premarket] Pushed plan for {today}")

            await asyncio.sleep(30)
        except Exception as e:
            print(f"[premarket] Error: {e}")
            await asyncio.sleep(60)


# --- Daily database backup ---
_backup_done_date: str = ""

async def backup_loop():
    """Backup portfolio.db daily at 20:00 CST."""
    global _backup_done_date
    import shutil
    from datetime import datetime, timezone, timedelta
    from pathlib import Path

    backup_dir = Path(config.db_path).parent / "backups"
    backup_dir.mkdir(exist_ok=True)

    while True:
        try:
            utc_now = datetime.now(timezone.utc)
            cst_now = utc_now + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            hour = cst_now.hour

            if hour == 20 and today != _backup_done_date:
                src = Path(config.db_path)
                if src.exists():
                    dst = backup_dir / f"portfolio_{today}.db"
                    shutil.copy2(str(src), str(dst))
                    _backup_done_date = today
                    print(f"[backup] Database backed up to {dst}")

                    # Keep only last 30 backups
                    backups = sorted(backup_dir.glob("portfolio_*.db"))
                    for old in backups[:-30]:
                        old.unlink()

            await asyncio.sleep(300)  # check every 5 minutes
        except Exception as e:
            print(f"[backup] Error: {e}")
            await asyncio.sleep(300)


# --- Morning briefing daily loop ---
_briefing_done_date: str = ""

async def briefing_loop():
    """Generate LLM briefing for each holding around 9:00 CST on weekdays.

    Once per day. Runs asynchronously while market opens at 9:30 so user
    sees it before placing orders.
    """
    global _briefing_done_date
    from datetime import datetime, timezone, timedelta
    from services.morning_briefing import generate_all_briefings

    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            t = cst_now.hour * 60 + cst_now.minute

            # Window: weekdays 8:55 ~ 9:10 CST, once per day
            if (cst_now.weekday() < 5 and 535 <= t <= 550
                    and today != _briefing_done_date):
                print(f"[briefing] Generating morning briefings for {today}")
                try:
                    results = await generate_all_briefings()
                    _briefing_done_date = today
                    print(f"[briefing] Done: {len(results)} briefings saved")
                    # Push a one-line summary to feishu
                    if feishu_notify.is_enabled() and results:
                        lines = [f"📋 {today} 早盘简报"]
                        for b in results:
                            v = b.get("verdict", "hold")
                            tag = {
                                "lock_all": "🔒 锁档",
                                "hold": "⏸ 观望",
                                "raise": "↗ 上调",
                                "lower": "↘ 下调",
                                "add_now": "✅ 加仓",
                            }.get(v, v)
                            lines.append(
                                f"【{b.get('stock_name')}】{tag} — {b.get('summary', '')}"
                            )
                        await feishu_notify.send_text("\n".join(lines))
                except Exception as e:
                    print(f"[briefing] Generation failed: {e}")

            await asyncio.sleep(60)
        except Exception as e:
            print(f"[briefing] Loop error: {e}")
            await asyncio.sleep(120)
