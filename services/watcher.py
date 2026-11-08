"""盯盘员: 主动层的统一出口。各检测器只产出「发现」, 这里去重后写进 watch_finding,
页面顶部列出当天的发现, 问答 agent 经 get_watch_findings 读取。不推飞书。

发现 = {key, detector, code, name, level(info|warn), title, body, evidence}。
key 是去重键(检测器 + 标的 + 状态), 同一 key 只记一次; 状态变了就换 key 记新的。

检测器:
  books_drift  券商可用金额与 licai 记的现金对不上 → 提醒有成交还没同步。
               读券商可用金额的命令由本机 config.py 的 watch_cash_cmd 给(输出一个数); 它会被执行,
               所以只放在本机配置文件里, 不进数据库(导入备份能写 app_config)。对照的现金资产 id 由
               app_config.watch_cash_asset_id 给; 两者缺一不跑。命令失败(客户端没开)静默跳过。
  risk_hits    A 股持仓与观察池的排雷命中数 ≥ 2 且比上一交易日多(插件 risk_flags, 收盘后的当日文件出来才跑)。
  fresh_buy    成交流水里新出现的 A 股买入 → 你历史上同类买入之后的结果(entry_review.mirror) + 排雷命中。
               水位记在 app_config.watch_last_action_id; 第一次运行只记水位不产出, 免得把旧流水全报一遍。
  tail_pull    A 股持仓与观察池尾盘 30 分钟(14:30 → 收盘)涨幅 ≥ 阈值且收盘未涨停/跌停。
  sealed_close A 股持仓与观察池收盘封住涨停(区分一字板)。这两个收盘后跑, 分时走 TDX(没启用就跳过),
               阈值与历史同类文案由插件 watch_events 给; 没装插件只给事实不给历史。
  unlock_soon  A 股持仓与观察池 5 个交易日内有限售解禁, 且占流通市值 ≥ 5%(event_calendar 的东财解禁表)。
  fund_est_miss 在持场外基金: 每个待公布净值日存最后一次回归估值(fund_est_log), 净值公布后与实际日涨幅比,
               偏差 ≥ max(3 倍该基金回归的历史日均误差, 0.3%) 记一条; 同一净值日的合成一条。
  fund_drop    在持基金(含场内 ETF): 最新净值日的跌幅落在这只基金近两年日涨幅最差 1%, 或近 5 个净值日累计跌幅
               落在近两年 5 日累计最差 2%; 同一净值日的合成一条。
观察池里的票(已持有的算持仓)标题前加「观察池」, 级别统一 info, 页面上排在持仓之后。
基金两项每天(含周末, QDII 的净值和美股收盘都会落在周末) 9:00~23:00 跑, 其余只在 A 股交易日跑。
新记下的发现交给 watch_dig 判断要不要派子 agent 查原因, 查到补充挂在那条发现下(dig_status / dig_result)。
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
    "description": "取盯盘员记下的发现: 持仓与观察池的排雷命中、收盘事件、临近解禁, 基金估值与净值偏差大、基金净值跌幅超出常见范围, "
                   "账本与券商对不上等, 每条带事实、历史同类统计与数据截至日; "
                   "观察池的发现标题以「观察池」开头; dig_result 非空的是盯盘员事后派子 agent 查到的成因。用户问'我持仓/自选有什么要注意的/盯盘员说了什么/账对上了吗'时用。date 留空=今天。",
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
    from services.watch_dig import DIGGABLE
    for r in rows:
        r["evidence"] = json.loads(r.get("evidence") or "{}")
        r["dismissed"] = bool(r.get("dismissed"))
        r["diggable"] = r["detector"] in DIGGABLE
        if r.get("dig_status") != "done":       # 只有查到补充的才给正文; 没补充的不出声
            r["dig_result"] = ""
    rows.sort(key=lambda r: r["evidence"].get("scope") == "watchlist")      # 持仓的在前, 各自按写入顺序
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


async def _watched_a_shares(held: dict) -> dict:
    """观察池里的 A 股, 去掉已持有的: {6位代码: 名称}。"""
    from database import list_watchlist
    from services.market_data import split_stock_code
    return {w["code"]: w.get("name") or w["code"] for w in await list_watchlist()
            if w["code"] not in held and split_stock_code(w["code"])[0] == "A"}


def as_watchlist(findings: list[dict]) -> list[dict]:
    """观察池的发现: 标题前加「观察池」, 级别降为 info(不是自己的仓位)。"""
    return [{**f, "title": f"观察池 {f['title']}", "level": "info",
             "evidence": {**(f.get("evidence") or {}), "scope": "watchlist"}} for f in findings]


async def check_risk_hits() -> list[dict]:
    from services import analyzers
    held = await _held_a_shares()
    watched = await _watched_a_shares(held)
    if not held and not watched:
        return []
    date = today()
    rf = analyzers.risk_flags_all(list(held) + list(watched))
    return await record(risk_findings(held, rf, date) + as_watchlist(risk_findings(watched, rf, date)), date)


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
    watched = await _watched_a_shares(held)
    if not held and not watched:
        return []
    date = today()
    quotes = await get_realtime_quotes(list(held) + list(watched))
    ev = analyzers.watch_events_all()
    out = []
    for group, wrap in ((held, list), (watched, as_watchlist)):
        for code, name in group.items():
            m = await tdx.minute(code)
            if not m or str(m.get("date") or "").replace("-", "") != date.replace("-", ""):
                continue
            out += wrap(close_findings(code, name, m.get("points") or [], quotes.get(code) or {}, _limit_pct(code, name), ev, date))
    return await record(out, date) if out else []


# ---------------- 临近解禁 ----------------
UNLOCK_DAYS, UNLOCK_MIN_PCT = 5, 5.0


def _trading_days_until(day: str, today_: str) -> int:
    """today_ 之后到 day(含)有几个 A 股交易日; day 就是今天 → 0。"""
    from datetime import date, timedelta
    from services.market_data import _is_a_share_trading_day
    d, end, n = date.fromisoformat(today_), date.fromisoformat(day), 0
    while d < end:
        d += timedelta(days=1)
        n += _is_a_share_trading_day(d)
    return n


def unlock_findings(code: str, name: str, events: list[dict], today_: str) -> list[dict]:
    """events: event_calendar._stock_events_sync 的返回。"""
    out = []
    for e in events:
        pct = e.get("pct")
        if e.get("type") != "解禁" or pct is None or pct < UNLOCK_MIN_PCT or e["date"] < today_:
            continue
        n = _trading_days_until(e["date"], today_)
        if n > UNLOCK_DAYS:
            continue
        out.append({"key": f"unlock_soon::{code}::{e['date']}", "detector": "unlock_soon", "code": code, "name": name,
                    "level": "warn", "title": f"{name} {e['date'][5:]} 限售解禁, 占流通市值 {pct:.1f}%",
                    "body": f"{e.get('detail') or '限售股'}; " + ("就在今天" if n == 0 else f"还有 {n} 个交易日")
                            + "\n数据: 东财限售解禁表, 以公司公告为准",
                    "evidence": {"date": e["date"], "pct": pct, "trading_days": n}})
    return out


async def check_unlocks() -> list[dict]:
    from services.event_calendar import _stock_events_sync
    held = await _held_a_shares()
    watched = await _watched_a_shares(held)
    date, out = today(), []
    for group, wrap in ((held, list), (watched, as_watchlist)):
        for code, name in group.items():
            evs = await asyncio.to_thread(_stock_events_sync, code)          # 12 小时缓存
            out += wrap(unlock_findings(code, name, evs, date))
    return await record(out, date) if out else []


# ---------------- 基金: 估值偏差 / 净值异常下跌 ----------------
EST_MISS_K, EST_MISS_FLOOR = 3.0, 0.3
DROP_Q1, DROP_Q5, DROP_MIN_N, DROP_YEARS_N = 0.01, 0.02, 250, 500
_nav_cache: dict = {}            # code → (取数时刻, {日期: 日涨幅%})


async def _held_funds() -> list[dict]:
    """在持的基金, 行情与估值和页面同一条链路: [{code, name, quote}]。"""
    from api.assets_routes import _enrich
    from database import list_external_assets
    rows = [r for r in await list_external_assets() if (r.get("asset_type") or "").upper() == "FUND" and r.get("code")
            and (float(r.get("shares") or 0) > 0 or float(r.get("pending_amount") or 0) > 0)]
    return [{"code": a["code"], "name": a.get("name") or a["code"], "quote": a.get("quote") or {}}
            for a in await asyncio.gather(*[_enrich(r) for r in rows]) if float(a.get("shares") or 0) > 0]


async def log_fund_estimates(funds: list[dict]) -> None:
    """只差一个净值日的估值存下来(同一天以最后一次为准, 公布后不再改); 已公布的填上实际涨跌。"""
    db = await get_db()
    try:
        for f in funds:
            q, est = f["quote"], f["quote"].get("model_est") or {}
            if est.get("days") == 1 and est.get("through"):
                await db.execute(
                    "INSERT INTO fund_est_log (code, nav_date, name, est_pct, mae, top10_pct, logged_at) VALUES (?,?,?,?,?,?,?) "
                    "ON CONFLICT(code, nav_date) DO UPDATE SET name = excluded.name, est_pct = excluded.est_pct, mae = excluded.mae, "
                    "top10_pct = excluded.top10_pct, logged_at = excluded.logged_at WHERE fund_est_log.actual_pct IS NULL",
                    (f["code"], est["through"], f["name"], float(est["pct"]), est.get("mae"), q.get("proxy_top10_pct"),
                     now_cst().isoformat(timespec="seconds")))
            nd, chg = str(q.get("nav_date") or "")[:10], q.get("change_pct")
            if nd and chg is not None:
                await db.execute("UPDATE fund_est_log SET actual_pct = ? WHERE code = ? AND nav_date = ? AND actual_pct IS NULL",
                                 (float(chg), f["code"], nd))
        await db.commit()
    finally:
        await db.close()


def est_miss_findings(rows: list[dict]) -> list[dict]:
    """rows: fund_est_log 里已公布的记录。偏差大的按净值日合成一条。"""
    by: dict = {}
    for r in rows:
        err = r["actual_pct"] - r["est_pct"]
        if abs(err) >= max(EST_MISS_K * (r.get("mae") or 0), EST_MISS_FLOOR):
            by.setdefault(r["nav_date"], []).append((r, err))
    out = []
    for nd, xs in sorted(by.items()):
        xs.sort(key=lambda x: x[0]["code"])
        lines = [f"{r['name'] or r['code']}: 净值 {r['actual_pct']:+.2f}%, 估值 {r['est_pct']:+.2f}%"
                 + (f"(前十大加权 {r['top10_pct']:+.2f}%)" if r.get("top10_pct") is not None else "")
                 + f", 差 {err:+.2f}%, 这只平时日均误差约 {r.get('mae') or 0:.2f}%" for r, err in xs]
        out.append({"key": f"fund_est_miss::{nd}::{','.join(r['code'] for r, _ in xs)}", "detector": "fund_est_miss",
                    "level": "info", "title": f"{nd[5:]} 基金估值偏差大: {len(xs)} 只",
                    "body": "\n".join(lines + [f"偏差大 = 超过平时日均误差的 {EST_MISS_K:g} 倍且不小于 {EST_MISS_FLOOR:g}%"]),
                    "evidence": {"nav_date": nd, "errs": {r["code"]: round(err, 2) for r, err in xs}}})
    return out


def _nav_changes_sync(code: str) -> dict:
    from services.event_calendar import _no_proxy
    _no_proxy()
    import akshare as ak
    df = ak.fund_open_fund_info_em(symbol=code, indicator="单位净值走势")
    if df is None or df.empty:
        return {}
    return {str(r["净值日期"])[:10]: float(r["日增长率"]) for _, r in df.iterrows()
            if r.get("日增长率") is not None and str(r.get("日增长率")) not in ("", "nan")}


async def _nav_changes(code: str, published: str) -> dict:
    """{日期: 日涨幅%}(东财, 已按分红调整)。3 小时缓存; 页面行情显示有更新的净值日就提前重取。"""
    import time
    c = _nav_cache.get(code)
    if c and time.time() - c[0] < 3 * 3600 and (not published or max(c[1], default="") >= published):
        return c[1]
    try:
        h = await asyncio.to_thread(_nav_changes_sync, code)
    except Exception as e:  # noqa: BLE001
        print(f"[watcher] 净值历史 {code} 失败: {e}")
        return c[1] if c else {}
    if h:
        _nav_cache[code] = (time.time(), h)
    return h


def drop_hits(chg: dict) -> dict | None:
    """chg: {日期: 日涨幅%}。最新净值日的跌幅 / 近 5 个净值日累计跌幅落在这只基金近两年的最差 1% / 2% → 命中。"""
    import numpy as np
    ds = sorted(chg)[-(DROP_YEARS_N + 1):]
    if len(ds) < DROP_MIN_N + 1:
        return None
    r = np.array([chg[d] for d in ds]) / 100
    hit = {}
    q1 = float(np.quantile(r[:-1], DROP_Q1))
    if r[-1] < 0 and r[-1] <= q1:
        hit["day"] = {"pct": round(float(r[-1]) * 100, 2), "line": round(q1 * 100, 2)}
    c5 = np.expm1(np.convolve(np.log1p(r), np.ones(5), "valid"))
    q5 = float(np.quantile(c5[:-5], DROP_Q5))                # 历史窗口不含与最新一天重叠的
    if c5[-1] < 0 and c5[-1] <= q5:
        hit["5d"] = {"pct": round(float(c5[-1]) * 100, 2), "line": round(q5 * 100, 2)}
    return {"date": ds[-1], **hit} if hit else None


def drop_findings(hits: list[tuple[str, str, dict]]) -> list[dict]:
    """hits: [(代码, 名称, drop_hits 的返回)]。按净值日合成一条。"""
    by: dict = {}
    for code, name, h in hits:
        by.setdefault(h["date"], []).append((code, name, h))
    out = []
    for nd, xs in sorted(by.items()):
        xs.sort()
        lines = []
        for code, name, h in xs:
            parts = []
            if "day" in h:
                parts.append(f"当日 {h['day']['pct']:+.2f}%(近两年最差 1% 的线 {h['day']['line']:+.2f}%)")
            if "5d" in h:
                parts.append(f"近 5 个净值日 {h['5d']['pct']:+.2f}%(近两年最差 2% 的线 {h['5d']['line']:+.2f}%)")
            lines.append(f"{name}: " + ", ".join(parts))
        out.append({"key": f"fund_drop::{nd}::{','.join(c for c, _, _ in xs)}", "detector": "fund_drop", "level": "warn",
                    "title": f"{nd[5:]} 基金净值跌幅超出常见范围: {len(xs)} 只",
                    "body": "\n".join(lines + ["常见范围按每只基金自己近两年的净值涨跌算"]),
                    "evidence": {"nav_date": nd, "hits": {c: h for c, _, h in xs}}})
    return out


async def _record_grouped(findings: list[dict], detector: str) -> list[dict]:
    """按净值日合成的发现: 同一净值日后来又多了几只, 新的一条替代旧的。"""
    fresh = await record(findings)
    for f in fresh:
        nd = f["evidence"]["nav_date"]
        db = await get_db()
        try:
            await db.execute("UPDATE watch_finding SET dismissed = 1 WHERE detector = ? AND key LIKE ? AND key <> ? AND dismissed = 0",
                             (detector, f"{detector}::{nd}::%", f["key"]))
            await db.commit()
        finally:
            await db.close()
    return fresh


async def check_funds() -> list[dict]:
    from datetime import timedelta
    funds = await _held_funds()
    if not funds:
        return []
    await log_fund_estimates(funds)
    since = (now_cst() - timedelta(days=7)).strftime("%Y-%m-%d")
    db = await get_db()
    try:
        cur = await db.execute("SELECT * FROM fund_est_log WHERE actual_pct IS NOT NULL AND nav_date >= ?", (since,))
        rows = [dict(r) for r in await cur.fetchall()]
    finally:
        await db.close()
    fresh = await _record_grouped(est_miss_findings(rows), "fund_est_miss")
    hits = []
    for f in funds:
        h = drop_hits(await _nav_changes(f["code"], str(f["quote"].get("nav_date") or "")[:10]))
        if h and h["date"] >= since:
            hits.append((f["code"], f["name"], h))
    return fresh + await _record_grouped(drop_findings(hits), "fund_drop")


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


async def _dig(fresh: list[dict]) -> None:
    from services import watch_dig
    try:
        await watch_dig.consider(fresh)
    except Exception as e:  # noqa: BLE001
        print(f"[watcher] 深挖判定出错: {e}")


async def watcher_loop():
    """每 10 分钟。A 股交易日: 9:00~20:00 看新买入与临近解禁; 15:05~20:00 看持仓与观察池的尾盘拉升/收盘封板;
    15:20~20:00 核现金; 16:45~20:00 看持仓与观察池的排雷命中(私仓收盘任务写完当日标记后)。
    每天 9:00~23:00 看基金估值偏差与净值跌幅(净值傍晚到夜里陆续公布)。"""
    from services.market_data import _is_a_share_trading_day
    from services import watch_dig
    try:
        n = await watch_dig.interrupt_stale()
        if n:
            print(f"[watcher] 上次没查完的深挖 {n} 条, 已标记为中断")
    except Exception as e:  # noqa: BLE001
        print(f"[watcher] 标记中断深挖失败: {e}")
    while True:
        try:
            n = now_cst()
            t = n.hour * 60 + n.minute
            trading = _is_a_share_trading_day(n.date())
            for lo, hi, fn, any_day in ((540, 1200, check_fresh_buys, False), (540, 1200, check_unlocks, False),
                                        (905, 1200, check_close_events, False), (920, 1200, check_books_drift, False),
                                        (1005, 1200, check_risk_hits, False), (540, 1380, check_funds, True)):
                if lo <= t <= hi and (trading or any_day):
                    try:
                        fresh = await fn()
                    except Exception as e:
                        print(f"[watcher] {fn.__name__} 出错: {e}")
                        continue
                    if isinstance(fresh, list) and fresh:
                        asyncio.create_task(_dig(fresh))
            await asyncio.sleep(600)
        except Exception as e:
            print(f"[watcher] Loop error: {e}")
            await asyncio.sleep(600)
