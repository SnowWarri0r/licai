"""WebSocket endpoint for real-time price push and alerts."""
from __future__ import annotations
import asyncio
import json
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from database import get_all_holdings, get_custom_alerts, mark_alert_triggered
from services.market_data import get_realtime_quotes, is_market_hours, is_trading_day_active, is_a_share
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


_dca_done_date: str = ""

async def dca_loop():
    """每天最多跑一次定投扫描.

    策略: 当天还没跑过 (today != _dca_done_date) 就立即跑, 不再卡时间窗口
    避免漏触发 (server 中午才开机也能补)。fire_due_dcas 自身扫所有 next_due<=today
    所以多日漏跑也能一次补齐."""
    global _dca_done_date
    from datetime import datetime, timezone, timedelta
    from services.dca import fire_due_dcas

    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")

            if today != _dca_done_date:
                try:
                    fired = await fire_due_dcas()
                    _dca_done_date = today
                    if fired:
                        print(f"[dca] Fired {len(fired)} schedules on {today}")
                        if feishu_notify.is_enabled():
                            lines = [f"💸 {today} 定投触发 {len(fired)} 笔"]
                            for f in fired:
                                v = f["value"]
                                unit = "¥" if f["mode"] == "amount" else "份"
                                lines.append(f"  asset#{f['asset_id']} {unit}{v} → action #{f['action_id']} (pending)")
                            await feishu_notify.send_text("\n".join(lines))
                except Exception as e:
                    print(f"[dca] fire_due_dcas failed: {e}")

            await asyncio.sleep(60)
        except Exception as e:
            print(f"[dca] Loop error: {e}")
            await asyncio.sleep(120)
