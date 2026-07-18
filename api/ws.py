"""WebSocket endpoint for real-time price push and alerts."""
from __future__ import annotations
import asyncio
import json
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from database import get_all_holdings
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
                    # Push a one-line summary to feishu (signal 模型: 客观信息倾向, 非操作建议)
                    if feishu_notify.is_enabled() and results:
                        lines = [f"{today} 早盘简报"]
                        for b in results:
                            sig = b.get("signal", "中性")
                            lines.append(
                                f"【{b.get('stock_name')}】{sig} — {b.get('summary', '')}"
                            )
                        await feishu_notify.send_text("\n".join(lines))
                except Exception as e:
                    print(f"[briefing] Generation failed: {e}")

            await asyncio.sleep(60)
        except Exception as e:
            print(f"[briefing] Loop error: {e}")
            await asyncio.sleep(120)


# --- 收盘持仓小结(纯数据, 非 LLM) ---
_eod_done_date: str = ""

async def eod_summary_loop():
    """交易日 15:10~15:40 窗口推一次收盘小结: 持仓涨跌归因 + 事件(上榜/涨跌停/预告披露) + 大盘。"""
    global _eod_done_date
    from datetime import datetime, timezone, timedelta
    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            t = cst_now.hour * 60 + cst_now.minute
            if 910 <= t <= 940 and today != _eod_done_date:
                from services.market_data import _is_a_share_trading_day
                if _is_a_share_trading_day(cst_now.date()):
                    from services.eod_summary import push_eod_summary
                    r = await push_eod_summary()
                    print(f"[eod] 收盘小结 {today} pushed={r.get('pushed')}")
                    try:
                        from services.sector_share import archive_today
                        n = await archive_today()
                        print(f"[eod] 板块份额入档 {n} 行")
                    except Exception as e:
                        print(f"[eod] 板块份额入档失败: {e}")
                _eod_done_date = today
            await asyncio.sleep(120)
        except Exception as e:
            print(f"[eod] Loop error: {e}")
            await asyncio.sleep(300)


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
