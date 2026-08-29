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
                    try:
                        # 榜单里没日线的票补一批: 概念线的资金曲线要靠它, 涨幅榜天天换新面孔
                        from services.concept_trend import warm_cache
                        w = await warm_cache()
                        print(f"[eod] 榜单日线补齐 {w.get('fetched')}/{w.get('missing')} 只")
                    except Exception as e:
                        print(f"[eod] 榜单日线补齐失败: {e}")
                _eod_done_date = today
            await asyncio.sleep(120)
        except Exception as e:
            print(f"[eod] Loop error: {e}")
            await asyncio.sleep(300)


_dca_done_date: str = ""
_dca_settle_date: str = ""      # 已跑过自动结算的日期
_dca_catchup_done: bool = False  # 本进程是否已补过隔夜积压

async def dca_loop():
    """每天最多跑一次定投扫描.

    策略: 当天还没跑过 (today != _dca_done_date) 就立即跑, 不再卡时间窗口
    避免漏触发 (server 中午才开机也能补)。fire_due_dcas 自身扫所有 next_due<=today
    所以多日漏跑也能一次补齐.

    触发之后还要结算: 定投份额靠 T+1 净值确认, 以前只有手动「一键确认」接口,
    出门几天回来就是一堆 pending, 成本口径一直飘。这里每晚 19:00 后自动跑一次,
    另外进程启动时若发现隔夜积压立即补一次(出差回来打开就是干净的)。
    settle_pending 扫的是全部 pending 而不只今天的, 所以漏几天也能一次追平。"""
    global _dca_done_date, _dca_settle_date, _dca_catchup_done
    from datetime import datetime, timezone, timedelta
    from services.dca import fire_due_dcas

    async def _settle(reason: str):
        from api.assets_routes import settle_pending_dca
        try:
            r = await settle_pending_dca()
            # 一笔没结算也要打, 否则"净值没出"和"结算逻辑挂了"在日志里长得一样
            print(f"[dca] {reason}: 结算 {r.get('settled', 0)} 笔, 净值未出跳过 {r.get('skipped', 0)} 笔")
            if r.get("settled"):
                if feishu_notify.is_enabled():
                    await feishu_notify.send_text(
                        f"{reason}: 自动确认定投 {r['settled']} 笔"
                        + (f", 净值未出 {r['skipped']} 笔待下次" if r.get("skipped") else ""))
            return True
        except Exception as e:
            print(f"[dca] settle_pending failed: {e}")
            return False

    async def _has_stale_pending(today: str) -> bool:
        """存在早于今天的 pending 流水 → 说明漏结算了(通常是 server 关着)。"""
        from database import list_external_assets, list_external_actions
        for a in await list_external_assets():
            if (a.get("asset_type") or "") != "FUND":
                continue
            for x in await list_external_actions(a["id"]):
                if ((x.get("status") or "confirmed") == "pending"
                        and (x.get("action_type") or "").upper() in ("ADD", "BUY", "REDEEM", "SELL")
                        and (x.get("trade_date") or "")[:10] < today):
                    return True
        return False

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

            # 启动补账: 有隔夜积压就立刻结算一次, 不等到晚上
            if not _dca_catchup_done:
                _dca_catchup_done = True
                try:
                    if await _has_stale_pending(today):
                        await _settle("隔夜积压补账")
                except Exception as e:
                    print(f"[dca] catchup check failed: {e}")

            # 每晚净值出了之后自动结算一次(海外基金当天没出的, 明晚这一轮补上)
            if today != _dca_settle_date and cst_now.hour >= 19:
                if await _settle(f"{today} 自动结算"):
                    _dca_settle_date = today

            await asyncio.sleep(60)
        except Exception as e:
            print(f"[dca] Loop error: {e}")
            await asyncio.sleep(120)


_rule_mine_date: str = ""


async def rule_mine_loop():
    """每周挖一次"用户纠正"→ 起草候选规则进待审队列(人批准才进 prompt)。

    周一早上 9:20 前后跑, 每次最多起草 3 条 —— 后台循环不该悄悄烧模型额度, 而且待审
    队列一次涌进十条也没人看得完。批准/否决在设置页里点。
    """
    global _rule_mine_date
    from datetime import datetime, timezone, timedelta
    while True:
        try:
            cst_now = datetime.now(timezone.utc) + timedelta(hours=8)
            today = cst_now.strftime("%Y-%m-%d")
            t = cst_now.hour * 60 + cst_now.minute
            if cst_now.weekday() == 0 and 560 <= t <= 600 and today != _rule_mine_date:
                _rule_mine_date = today          # 先占位: 失败也不在同一窗口内反复重试
                from services import rule_forge as rf
                st = await rf.mine_new(max_drafts=3)
                print(f"[rules] 本周挖掘: 新纠正 {st['corrections']} 条, "
                      f"新增候选 {st['added']}, 判为非规则 {st['skipped']}, "
                      f"重复 {st['dup']}, 失败 {st['failed']}")
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[rules] Loop error: {e}")
            await asyncio.sleep(1800)
