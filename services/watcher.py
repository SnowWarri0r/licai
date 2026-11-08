"""盯盘员: 主动层的统一出口。各检测器只产出「发现」, 这里去重后写进 watch_finding,
页面顶部列出当天的发现, 问答 agent 经 get_watch_findings 读取。不推飞书。

发现 = {key, detector, code, name, level(info|warn), title, body, evidence}。
key 是去重键(检测器 + 标的 + 状态), 同一 key 只记一次; 状态变了就换 key 记新的。

检测器:
  books_drift  券商可用金额与 licai 记的现金对不上 → 提醒有成交还没同步。
               读券商可用金额的命令由本机 config.py 的 watch_cash_cmd 给(输出一个数); 它会被执行,
               所以只放在本机配置文件里, 不进数据库(导入备份能写 app_config)。对照的现金资产 id 由
               app_config.watch_cash_asset_id 给; 两者缺一不跑。命令失败(客户端没开)静默跳过。
  risk_hits    A 股持仓的排雷命中数 ≥ 2 且比上一交易日多(插件 risk_flags, 收盘后的当日文件出来才跑)。
  fresh_buy    成交流水里新出现的 A 股买入 → 你历史上同类买入之后的结果(entry_review.mirror) + 排雷命中。
               水位记在 app_config.watch_last_action_id; 第一次运行只记水位不产出, 免得把旧流水全报一遍。
  tail_pull    A 股持仓尾盘 30 分钟(14:30 → 收盘)涨幅 ≥ 阈值且收盘未涨停/跌停。
  sealed_close A 股持仓收盘封住涨停(区分一字板)。这两个收盘后跑, 分时走 TDX(没启用就跳过),
               阈值与历史同类文案由插件 watch_events 给; 没装插件只给事实不给历史。
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
    from config import config
    from database import get_config, get_external_asset
    cmd, aid = getattr(config, "watch_cash_cmd", ""), await get_config("watch_cash_asset_id")
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


# ---------------- 排雷命中 ----------------
def _count_line(cat: dict, k: int) -> str:
    st = ((cat.get("by_count") or {}).get("post") or {}).get(str(min(k, 3)))
    if not st:
        return ""
    return (f"历史同类(2019 起, 命中 {min(k, 3)} 项): 之后 20 日相对全池 {st['ex20'] * 100:+.1f}%, "
            f"跌超同期 15% 的占 {st['bad'] * 100:.0f}%")


def _labels(cat: dict, flags: list[str]) -> str:
    names = (cat.get("flags") or {})
    return " · ".join((names.get(f) or {}).get("label", f) for f in flags)


def _lhb_note(rf: dict, date: str) -> str:
    t = rf.get("lhb_through")
    return f"(龙虎榜截至 {t[5:]})" if t and t < date else ""


def risk_findings(held: dict, rf: dict, date: str) -> list[dict]:
    """held: {6位代码: 名称}; rf: 插件 risk_flags 的返回。命中 ≥ 2 且比上一份多 → 发现。"""
    if rf.get("stale") or rf.get("date") != date:
        return []
    cat, out = rf.get("catalog") or {}, []
    for code, name in held.items():
        now, prev = rf["flags"].get(code) or [], rf.get("prev_flags", {}).get(code) or []
        if len(now) < 2 or len(now) <= len(prev):
            continue
        body = [f"命中: {_labels(cat, now)}", _count_line(cat, len(now)), f"数据截至 {date[5:]} 收盘" + _lhb_note(rf, date)]
        out.append({"key": f"risk_hits::{code}::{date}", "detector": "risk_hits", "code": code, "name": name, "level": "warn",
                    "title": f"{name} 排雷命中 {len(now)} 项" + (f"(前一交易日 {len(prev)} 项)" if rf.get("prev_date") else ""),
                    "body": "\n".join(x for x in body if x), "evidence": {"hits": now, "prev_hits": prev, "data_date": date}})
    return out


async def _held_a_shares() -> dict:
    from api.portfolio_routes import list_holdings
    from services.market_data import split_stock_code
    return {h.stock_code: h.stock_name or h.stock_code for h in await list_holdings()
            if h.shares and split_stock_code(h.stock_code)[0] == "A"}


async def check_risk_hits() -> list[dict]:
    from services import analyzers
    held = await _held_a_shares()
    if not held:
        return []
    date = today()
    return await record(risk_findings(held, analyzers.risk_flags_all(list(held)), date), date)


# ---------------- 新买入对照 ----------------
_MIRROR_SKIP = ("买入价相对前一日收盘",)       # mirror 这一项用查询时的实时价, 同步晚于成交时不代表这笔买入


def fresh_buy_finding(acts: list[dict], name: str, mirror: dict, rf: dict) -> dict:
    """acts: 同一只票同一天的买入流水(分几单买的合成一条); mirror: entry_review.mirror 的返回; rf: risk_flags 的返回。"""
    act = acts[0]
    code = act["stock_code"]
    lines = [f"{act['trade_date'][5:]} 买入 " + " / ".join(f"{a['shares']:g} 股 @{a['price']:g}" for a in acts)]
    ms = [m for m in (mirror.get("matches") or []) if m.get("dim") not in _MIRROR_SKIP and m.get("n_done")]
    if ms:
        lines.append("你历史上同类买入之后 20 个交易日(只看自己的记录):")
        for m in ms:
            rel, win = ("相对中证1000", "跑赢") if "超额" in (m.get("metric") or "") else ("平均", "赚钱")
            lines.append(f"· {m['dim']} {m['label']}: {m['n_done']} 次, {rel} {m['mean20_pct']:+.1f}%, {win} {m['win20'] * 100:.0f}%")
    hits = [] if rf.get("stale") else (rf.get("flags") or {}).get(code) or []
    cat = rf.get("catalog") or {}
    if not rf.get("stale") and (rf.get("in_pool") or {}).get(code):
        lines.append(f"排雷命中 {len(hits)} 项" + (f": {_labels(cat, hits)}" if hits else "")
                     + f"(数据截至 {rf['date'][5:]} 收盘{_lhb_note(rf, rf['date']).replace('(', ', ').rstrip(')')})")
        if hits:
            lines.append(_count_line(cat, len(hits)))
    return {"key": f"fresh_buy::{code}::{act['trade_date']}::{','.join(str(a['id']) for a in acts)}",
            "detector": "fresh_buy", "code": code, "name": name,
            "level": "warn" if len(hits) >= 2 else "info", "title": f"新买入 {name}",
            "body": "\n".join(x for x in lines if x),
            "evidence": {"action_ids": [a["id"] for a in acts], "hits": hits, "mirror_total": mirror.get("total")}}


async def check_fresh_buys() -> list[dict]:
    from database import get_config, set_config
    from services import analyzers
    from services.entry_review import mirror
    from services.market_data import split_stock_code
    db = await get_db()
    try:
        last = await get_config("watch_last_action_id")
        cur = await db.execute("SELECT COALESCE(MAX(id), 0) FROM position_actions")
        top = (await cur.fetchone())[0]
        if last is None:
            await set_config("watch_last_action_id", str(top))
            return []
        cur = await db.execute("SELECT id, stock_code, action_type, shares, price, trade_date FROM position_actions "
                               "WHERE id > ? AND action_type IN ('BUY', 'ADD') ORDER BY id", (int(last),))
        acts = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    acts = [a for a in acts if split_stock_code(a["stock_code"])[0] == "A" and (a.get("trade_date") or "") >= _days_ago(7)]
    out = []
    if acts:
        held = await _held_a_shares()
        rf = analyzers.risk_flags_all([a["stock_code"] for a in acts])
        groups: dict = {}
        for a in acts:
            groups.setdefault((a["stock_code"], a["trade_date"]), []).append(a)
        for (code, day), g in groups.items():
            g = await _day_buys(code, day) or g          # 当天这只票的全部买入, 不只水位之后的
            try:
                m = await mirror(code)
            except Exception as e:  # noqa: BLE001
                print(f"[watcher] mirror {code} 失败: {e}")
                m = {}
            f = fresh_buy_finding(g, held.get(code) or code, m, rf)
            await _supersede_buy(code, day, f["key"])
            out.append(f)
    fresh = await record(out) if out else []
    await set_config("watch_last_action_id", str(top))
    return fresh


# ---------------- 收盘事件: 尾盘拉升 / 收盘封板 ----------------
def close_findings(code: str, name: str, pts: list[dict], q: dict, lim_pct: float, ev: dict, date: str) -> list[dict]:
    """pts: 当日分时 [{time: "HH:MM", price}]; q: 实时行情(收盘后 = 收盘价) {price, prev_close, open, low}。"""
    pc, close = q.get("prev_close"), q.get("price")
    if not pc or not close:
        return []
    up = round(pc * (1 + lim_pct / 100) + 1e-9, 2)
    dn = round(pc * (1 - lim_pct / 100) + 1e-9, 2)
    sealed, floored = close >= up - 0.005, close <= dn + 0.005
    out = []
    if sealed:
        yizi = (q.get("open") or 0) >= up - 0.005 and (q.get("low") or 0) >= up - 0.005
        lines = ((ev.get("sealed_close") or {}).get("yizi" if yizi else "normal")) or []
        out.append({"key": f"sealed_close::{code}::{date}", "detector": "sealed_close", "code": code, "name": name,
                    "level": "info", "title": f"{name} 收盘封住涨停" + ("(一字板)" if yizi else ""),
                    "body": "\n".join([f"收盘 {close:g}, 涨停价 {up:g}"] + lines),
                    "evidence": {"close": close, "limit": up, "yizi": yizi}})
        return out
    before = [p for p in pts if p.get("price") and p.get("time", "") <= "14:30"]
    if floored or not before:
        return out
    p1430 = before[-1]["price"]
    tail = close / p1430 - 1
    thr = (ev.get("tail_pull") or {}).get("min_tail30", 0.02)
    if tail >= thr:
        out.append({"key": f"tail_pull::{code}::{date}", "detector": "tail_pull", "code": code, "name": name,
                    "level": "warn", "title": f"{name} 尾盘 30 分钟 {tail * 100:+.1f}%",
                    "body": "\n".join([f"14:30 {p1430:g} → 收盘 {close:g}"] + ((ev.get("tail_pull") or {}).get("lines") or [])),
                    "evidence": {"p1430": p1430, "close": close, "tail30": round(tail, 4)}})
    return out


async def check_close_events() -> list[dict]:
    import services.tdx_client as tdx
    from services import analyzers
    from services.market_data import get_realtime_quotes
    from services.market_review import _limit_pct
    if not tdx.is_enabled():
        return []
    held = await _held_a_shares()
    if not held:
        return []
    date = today()
    quotes = await get_realtime_quotes(list(held))
    ev = analyzers.watch_events_all()
    out = []
    for code, name in held.items():
        m = await tdx.minute(code)
        if not m or str(m.get("date") or "").replace("-", "") != date.replace("-", ""):
            continue
        out += close_findings(code, name, m.get("points") or [], quotes.get(code) or {}, _limit_pct(code, name), ev, date)
    return await record(out, date) if out else []


async def _day_buys(code: str, day: str) -> list[dict]:
    db = await get_db()
    try:
        cur = await db.execute("SELECT id, stock_code, action_type, shares, price, trade_date FROM position_actions "
                               "WHERE stock_code = ? AND trade_date = ? AND action_type IN ('BUY', 'ADD') ORDER BY id", (code, day))
        return [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()


async def _supersede_buy(code: str, day: str, keep_key: str):
    """同一只票同一天后来又买了: 早先那条新买入提醒收起, 由合并后的新一条替代。"""
    db = await get_db()
    try:
        await db.execute("UPDATE watch_finding SET dismissed = 1 WHERE detector = 'fresh_buy' AND code = ? "
                         "AND key LIKE ? AND key <> ? AND dismissed = 0", (code, f"fresh_buy::{code}::{day}::%", keep_key))
        await db.commit()
    finally:
        await db.close()


def _days_ago(n: int) -> str:
    from datetime import timedelta
    return (now_cst() - timedelta(days=n)).strftime("%Y-%m-%d")


async def watcher_loop():
    """交易日每 10 分钟: 9:00~20:00 看新买入; 15:05~20:00 看尾盘拉升/收盘封板; 15:20~20:00 核现金;
    16:45~20:00 看排雷命中(私仓收盘任务写完当日标记后)。"""
    from services.market_data import _is_a_share_trading_day
    while True:
        try:
            n = now_cst()
            t = n.hour * 60 + n.minute
            if _is_a_share_trading_day(n.date()):
                for lo, fn in ((540, check_fresh_buys), (905, check_close_events), (920, check_books_drift), (1005, check_risk_hits)):
                    if lo <= t <= 1200:
                        try:
                            await fn()
                        except Exception as e:
                            print(f"[watcher] {fn.__name__} 出错: {e}")
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[watcher] Loop error: {e}")
            await asyncio.sleep(600)
