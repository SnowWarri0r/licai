"""盯盘员: 主动层的统一出口。各检测器只产出「发现」, 这里去重后写进 watch_finding,
页面顶部列出当天的发现, 问答 agent 经 get_watch_findings 读取。不推飞书。

发现 = {key, detector, code, name, level(info|warn), title, body, evidence}。
key 是去重键(检测器 + 标的 + 状态), 同一 key 只记一次; 状态变了就换 key 记新的。

检测器:
  books_drift  券商可用金额与 licai 记的现金对不上 → 提醒有成交还没同步。
               读券商可用金额的命令由 app_config.watch_cash_cmd 给(输出一个数), 对照的现金资产 id
               由 app_config.watch_cash_asset_id 给; 两者缺一不跑。命令失败(客户端没开)静默跳过。
"""
from __future__ import annotations

import asyncio
import json
import shlex
from datetime import datetime
from zoneinfo import ZoneInfo

from database import get_db

_CST = ZoneInfo("Asia/Shanghai")
CASH_TOL = 1.0          # 元; 券商与账本的现金差在这以内算对上

TOOL_GET = {
    "name": "get_watch_findings",
    "description": "取盯盘员记下的发现: 持仓风险、账本与券商对不上等, 每条带事实、历史同类统计与数据截至日。"
                   "用户问'我持仓有什么要注意的/盯盘员说了什么/账对上了吗'时用。date 留空=今天。",
    "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}}},
}


def now_cst() -> datetime:
    return datetime.now(_CST)


def today() -> str:
    return now_cst().strftime("%Y-%m-%d")


async def record(findings: list[dict], date: str | None = None) -> list[dict]:
    """写入发现, 已有的 key 跳过。返回新写入的。"""
    date = date or today()
    fresh = []
    db = await get_db()
    try:
        for f in findings:
            cur = await db.execute(
                "INSERT OR IGNORE INTO watch_finding (key, date, detector, code, name, level, title, body, evidence, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (f["key"], date, f["detector"], f.get("code") or "", f.get("name") or "", f.get("level") or "info",
                 f["title"], f.get("body") or "", json.dumps(f.get("evidence") or {}, ensure_ascii=False),
                 now_cst().isoformat(timespec="seconds")))
            if cur.rowcount:
                fresh.append(f)
        await db.commit()
    finally:
        await db.close()
    return fresh


async def resolve(detector: str, date: str, keep_key: str = "") -> int:
    """把某检测器当天的发现标为已收起(状态被新的取代, 或问题已消失)。keep_key 那条保留。"""
    db = await get_db()
    try:
        cur = await db.execute("UPDATE watch_finding SET dismissed = 1 WHERE detector = ? AND date = ? AND key <> ? AND dismissed = 0",
                               (detector, date, keep_key))
        await db.commit()
        return cur.rowcount
    finally:
        await db.close()


async def dismiss(finding_id: int) -> bool:
    db = await get_db()
    try:
        cur = await db.execute("UPDATE watch_finding SET dismissed = 1 WHERE id = ?", (finding_id,))
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


async def list_findings(date: str | None = None, include_dismissed: bool = False) -> dict:
    date = date or today()
    db = await get_db()
    try:
        sql = "SELECT * FROM watch_finding WHERE date = ?" + ("" if include_dismissed else " AND dismissed = 0") + " ORDER BY id"
        cur = await db.execute(sql, (date,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    for r in rows:
        r["evidence"] = json.loads(r.get("evidence") or "{}")
        r["dismissed"] = bool(r.get("dismissed"))
    return {"date": date, "items": rows}


# ---------------- 检测器 ----------------
async def _broker_cash(cmd: str) -> float | None:
    """跑配置的命令读券商可用金额; 命令失败、超时或输出不是数都返回 None。"""
    try:
        p = await asyncio.create_subprocess_exec(*shlex.split(cmd), stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.DEVNULL)
        out, _ = await asyncio.wait_for(p.communicate(), timeout=30)
        if p.returncode != 0:
            return None
        return float(out.decode().strip().replace(",", ""))
    except (asyncio.TimeoutError, ValueError, OSError):
        return None


def drift_finding(broker: float, book: float, asset_name: str, date: str) -> dict | None:
    """券商可用金额 vs 账本现金 → 发现; 对得上返回 None。key 带券商金额, 金额再变就是新状态。"""
    diff = broker - book
    if abs(diff) < CASH_TOL:
        return None
    return {"key": f"books_drift::{date}::{broker:.2f}", "detector": "books_drift", "level": "warn",
            "title": f"{asset_name}和券商对不上",
            "body": f"券商可用金额 {broker:,.2f} / licai 记的 {book:,.2f}, 相差 {diff:+,.2f}, 可能有成交还没同步",
            "evidence": {"broker": broker, "book": book, "diff": round(diff, 2)}}


async def check_books_drift() -> dict | None:
    from database import get_config, get_external_asset
    cmd, aid = await get_config("watch_cash_cmd"), await get_config("watch_cash_asset_id")
    if not cmd or not aid:
        return None
    asset = await get_external_asset(int(aid))
    if not asset:
        return None
    broker = await _broker_cash(cmd)
    if broker is None:
        return None
    date = today()
    f = drift_finding(broker, float(asset.get("manual_value") or 0), asset.get("name") or "现金", date)
    await resolve("books_drift", date, keep_key=f["key"] if f else "")
    if f:
        await record([f], date)
    return f


async def watcher_loop():
    """交易日 15:20~20:00 每 10 分钟核一次现金(收盘后同步成交的时段)。"""
    from services.market_data import _is_a_share_trading_day
    while True:
        try:
            n = now_cst()
            t = n.hour * 60 + n.minute
            if 920 <= t <= 1200 and _is_a_share_trading_day(n.date()):
                await check_books_drift()
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[watcher] Loop error: {e}")
            await asyncio.sleep(600)
