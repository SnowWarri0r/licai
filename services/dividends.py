"""A股现金分红自动识别 → 摊薄成本(对齐券商口径)。

券商(招商等)显示的是摊薄成本: 持有期间每收到一次现金分红, 每股成本下调该次每股股息(含税)。
本模块拉东财分红送配数据, 算出某持仓"开仓日之后、已实施"的累计每股现金分红, 供成本计算扣减。

失败一律返回 0(不破成本计算): 数据源抖动时退回未摊薄成本, 不会让持仓盈亏算崩。
"""
from __future__ import annotations
import asyncio
import datetime as _dt
import time as _t

_cache: dict = {}          # code -> (rows, ts)
_TTL = 12 * 3600           # 分红方案很少变, 缓存 12h


def _today_cst() -> _dt.date:
    return (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).date()


def _fetch_dividends_sync(bare: str) -> list[dict]:
    """东财分红送配明细 → [{ex_date(date), per_share(每股现金,含税), progress}]。只留已实施的现金分红。"""
    import akshare as ak
    c = _cache.get(bare)
    if c and _t.time() - c[1] < _TTL:
        return c[0]
    out: list[dict] = []
    try:
        df = ak.stock_fhps_detail_em(symbol=bare)
        if df is not None and len(df):
            for _, r in df.iterrows():
                prog = str(r.get("方案进度") or "")
                if "实施" not in prog:                       # 只认已实施分配
                    continue
                ratio = r.get("现金分红-现金分红比例")          # 每 10 股派 X 元(含税)
                exd = str(r.get("除权除息日") or "")[:10]
                try:
                    per_share = float(ratio) / 10.0
                    exdate = _dt.date.fromisoformat(exd)
                except (TypeError, ValueError):
                    continue
                if per_share > 0:
                    out.append({"ex_date": exdate, "per_share": round(per_share, 4), "progress": prog})
    except Exception:
        return []                                            # 失败不缓存, 下次重试
    _cache[bare] = (out, _t.time())
    return out


async def cumulative_dividend_per_share(code: str, since_date: str | None) -> float:
    """开仓日 since_date 之后、且除权除息日已到(<=今天)的累计每股现金分红(含税)。
    用于摊薄成本: 摊薄成本 = 综合成本 - 本值。since_date 为空或解析失败 → 返 0。"""
    if not since_date:
        return 0.0
    if str(code or "").upper().startswith(("HK.", "US.")):
        return 0.0               # 这是 A 股分红数据源; 港美股分红走 DIVIDEND 流水(含预扣税), 识别见下方 scan_pending
    bare = (code or "").split(".")[-1].lstrip("shz").strip()
    if not bare.isdigit():
        return 0.0
    try:
        since = _dt.date.fromisoformat(str(since_date)[:10])
    except (TypeError, ValueError):
        return 0.0
    today = _today_cst()
    rows = await asyncio.to_thread(_fetch_dividends_sync, bare)
    # 买在除权除息日之前(ex_date > 开仓日)才吃得到这次分红; ex_date 已到才生效
    total = sum(r["per_share"] for r in rows if since < r["ex_date"] <= today)
    return round(total, 4)


async def dilute_state(code: str, st: dict) -> dict:
    """对 compute_position_state 结果做分红摊薄(对齐券商): 按当前持仓段开仓日之后累计每股现金分红下调 cost_price。
    原地改 st 并返回; 拿不到分红/无持仓时原样返回。"""
    if not st or float(st.get("shares") or 0) <= 0:
        return st
    lots = st.get("lots") or []
    open_date = min((l.get("trade_date") for l in lots if l.get("trade_date")), default=None)
    div_ps = await cumulative_dividend_per_share(code, open_date)
    if div_ps > 0:
        raw = st.get("cost_price_raw")
        base = raw if raw is not None else st.get("cost_price")
        if base is not None:
            st["cost_price_raw"] = round(base, 4)
            st["cost_price"] = round(max(0.0, base - div_ps), 4)
        fc = st.get("fifo_cost_price")
        if fc is not None:
            st["fifo_cost_price"] = round(max(0.0, fc - div_ps), 4)
        st["div_per_share"] = div_ps
    return st


# ── 港美股现金分红识别 ─────────────────────────────────────────────────────────
# A 股分红是"算出来"的(只摊薄展示成本, 不落流水); 港美股分红按 DIVIDEND 流水入已实现收益, 金额/税/币种
# 以券商结单为准(预扣税因公司而异、宣派币种≠到账币种、可选以股代息), 所以这里只给"待确认"建议,
# 用户一键记入(可改金额/税)或忽略, 不自动写流水。
#
# 数据源(都免费, 实测本机可通):
#   港股: 东财港股 F10 分红派息 RPT_HKF10_MAIN_DIVBASIC —— 除净日 / 发放日 / 方案原文(含"相当于港币 X 元")
#   美股: Nasdaq quote/{sym}/dividends —— 不复权金额 + 除息日 + 派息日, 只覆盖 Nasdaq 上市票;
#         其余(NYSE 等)回退 Yahoo chart events=div,splits —— 金额是拆股复权后的, 用同一响应里的拆股事件还原,
#         没有派息日。
import json as _json
import re as _re

_fcache: dict = {}            # code -> (events, ts)
_F_TTL = 12 * 3600
DISMISS_KEY = "dividend_dismissed"   # app_config: ["HK.00700|2026-05-15", ...] 用户忽略的建议

_HK_CCY = {"港币": "HKD", "港元": "HKD", "美元": "USD", "人民币": "CNY", "欧元": "EUR", "英镑": "GBP",
           "日元": "JPY", "新加坡元": "SGD", "加元": "CAD", "澳元": "AUD"}
_HK_DECL = _re.compile(r"每股派\s*(港币|港元|美元|人民币|欧元|英镑|日元|新加坡元|加元|澳元)?\s*([\d.]+)\s*元")
_HK_EQ = _re.compile(r"相当于\s*港[币元]\s*([\d.]+)\s*元\s*([(（]计算值[)）])?")


def _d10(v) -> str | None:
    s = str(v or "")[:10].replace("/", "-")      # 东财这张表日期是 2026/05/15
    return s if _re.fullmatch(r"\d{4}-\d{2}-\d{2}", s) else None


def parse_hk_plan(plan: str) -> dict | None:
    """东财港股分红方案原文 → {per_share(港币, 无等值则 None), declared_ccy, declared_ps, estimated, scrip}。
    非现金派息(只送股/不分配)返回 None。"""
    plan = str(plan or "")
    m = _HK_DECL.search(plan)
    if not m:
        return None
    ccy = _HK_CCY.get(m.group(1) or "港币", "HKD")
    try:
        dps = float(m.group(2))
    except ValueError:
        return None
    eq = _HK_EQ.search(plan)
    if ccy == "HKD":
        hkd, est = dps, False
    elif eq:
        hkd, est = float(eq.group(1)), bool(eq.group(2))
    else:
        hkd, est = None, False
    return {"per_share": hkd, "declared_ccy": ccy, "declared_ps": dps, "estimated": est,
            "scrip": "以股代息" in plan}


def _hk_events_sync(sym: str) -> list[dict]:
    import requests
    r = requests.get("https://datacenter.eastmoney.com/securities/api/data/v1/get", params={
        "reportName": "RPT_HKF10_MAIN_DIVBASIC",
        "columns": "SECURITY_CODE,UPDATE_DATE,REPORT_TYPE,EX_DIVIDEND_DATE,DIVIDEND_DATE,YEAR,PLAN_EXPLAIN,IS_BFP",
        "filter": f'(SECURITY_CODE="{sym}")(IS_BFP="0")', "pageNumber": "1", "pageSize": "200",
        "sortTypes": "-1,-1", "sortColumns": "NOTICE_DATE,EX_DIVIDEND_DATE", "source": "F10", "client": "PC",
    }, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    data = ((r.json() or {}).get("result") or {}).get("data") or []
    out = []
    for row in data:
        p = parse_hk_plan(row.get("PLAN_EXPLAIN"))
        ex = _d10(row.get("EX_DIVIDEND_DATE"))
        if not p or not ex:                       # 只送股/不分配, 或除净日未定
            continue
        out.append({"ex_date": ex, "pay_date": _d10(row.get("DIVIDEND_DATE")), "currency": "HKD",
                    "declared": str(row.get("PLAN_EXPLAIN") or ""), "kind": str(row.get("REPORT_TYPE") or ""),
                    "source": "eastmoney", **p})
    return out


def _us_nasdaq_sync(sym: str) -> list[dict] | None:
    """Nasdaq 分红历史(不复权, 带派息日)。非 Nasdaq 上市票接口返回 rows=null → None 交给回退源。"""
    import requests
    r = requests.get(f"https://api.nasdaq.com/api/quote/{sym}/dividends", params={"assetclass": "stocks"},
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"}, timeout=10)
    rows = ((((r.json() or {}).get("data") or {}).get("dividends") or {}).get("rows"))
    if rows is None:
        return None

    def _md(s):
        try:
            return _dt.datetime.strptime(str(s), "%m/%d/%Y").date().isoformat()
        except ValueError:
            return None
    out = []
    for x in rows:
        if str(x.get("type") or "").lower() != "cash":
            continue
        ex = _md(x.get("exOrEffDate"))
        try:
            amt = float(str(x.get("amount") or "").replace("$", "").replace(",", ""))
        except ValueError:
            continue
        if ex and amt > 0:
            out.append({"ex_date": ex, "pay_date": _md(x.get("paymentDate")), "per_share": amt,
                        "currency": str(x.get("currency") or "USD"), "declared": f"${amt}", "estimated": False,
                        "source": "nasdaq"})
    return out


def yahoo_unadjust(divs: dict, splits: dict | None, gmtoffset: int = 0) -> list[dict]:
    """Yahoo events → 事件列表。Yahoo 的分红金额按之后的拆股复权过: 原始 = 复权值 × 除息日之后各次拆股比例之积。"""
    sp = [(int(s.get("date") or k), float(s.get("numerator") or 0) / float(s.get("denominator") or 1))
          for k, s in (splits or {}).items() if float(s.get("numerator") or 0) > 0]
    out = []
    for k, v in (divs or {}).items():
        ts = int(v.get("date") or k)
        f = 1.0
        for st, ratio in sp:
            if st > ts:
                f *= ratio
        amt = float(v.get("amount") or 0) * f
        if amt > 0:
            ex = _dt.datetime.utcfromtimestamp(ts + gmtoffset).date().isoformat()
            out.append({"ex_date": ex, "pay_date": None, "per_share": round(amt, 6), "currency": "USD",
                        "declared": f"${round(amt, 6)}", "estimated": False, "source": "yahoo"})
    return sorted(out, key=lambda e: e["ex_date"])


def _us_yahoo_sync(sym: str) -> list[dict]:
    import requests
    r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}",
                     params={"range": "10y", "interval": "1mo", "events": "div,splits"},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    res = ((r.json() or {}).get("chart") or {}).get("result") or []
    if not res:
        return []
    ev = res[0].get("events") or {}
    return yahoo_unadjust(ev.get("dividends"), ev.get("splits"), int((res[0].get("meta") or {}).get("gmtoffset") or 0))


def foreign_events_sync(code: str) -> list[dict] | None:
    """港美股某票的现金分红事件(按除净日升序)。拉不到返回 None(不缓存, 下次重试)。"""
    code = str(code or "").upper()
    c = _fcache.get(code)
    if c and _t.time() - c[1] < _F_TTL:
        return c[0]
    try:
        if code.startswith("HK."):
            ev = _hk_events_sync(code[3:].zfill(5))
        elif code.startswith("US."):
            sym = code[3:]
            try:
                ev = _us_nasdaq_sync(sym)
            except Exception:
                ev = None
            if ev is None:
                ev = _us_yahoo_sync(sym.replace(".", "-"))     # Yahoo 写 BRK-B
        else:
            return None
    except Exception:
        return None
    ev = sorted(ev, key=lambda e: e["ex_date"])
    _fcache[code] = (ev, _t.time())
    return ev


def market_today(code: str) -> str:
    """交易所当地今天: 美股按美东、港股按香港时间。"""
    from zoneinfo import ZoneInfo
    tz = "America/New_York" if str(code).upper().startswith("US.") else "Asia/Hong_Kong"
    return _dt.datetime.now(ZoneInfo(tz)).date().isoformat()


def shares_before(actions: list, day: str, fractional: bool = False) -> float:
    """除净日前一个交易日收盘时的持股: 回放 trade_date < day 的买卖/送股/拆合股流水。
    fractional=True(美股碎股)时保留 4 位小数, 合股零头也留着; 否则取整(零头折现金)。"""
    from services.position_ledger import ACQUIRE, RELEASE, SPLIT, split_ratio
    sh = 0.0
    for a in sorted(actions, key=lambda a: (str(a.get("trade_date") or a.get("created_at") or "")[:10], a.get("id") or 0)):
        d = str(a.get("trade_date") or a.get("created_at") or "")[:10]
        if not d or d >= day:
            continue
        t = (a.get("action_type") or "").upper()
        if t in ACQUIRE:
            sh += float(a.get("shares") or 0)
        elif t in RELEASE:
            sh = max(0.0, sh - float(a.get("shares") or 0))
        elif t == SPLIT:
            sh = sh * (split_ratio(a) or 1)
            if not fractional:
                sh = float(int(sh + 1e-6))
    return round(sh, 4) if fractional else int(sh + 1e-6)


def _merge_same_day(events: list[dict]) -> list[dict]:
    """同一除净日的多项派息(如年度 + 特别)合成一笔: 券商流水和用户手记通常按日一笔。"""
    by: dict = {}
    for e in events:
        k = e["ex_date"]
        if k not in by:
            by[k] = dict(e, parts=[e])
            continue
        m = by[k]
        m["parts"].append(e)
        if m.get("per_share") is not None and e.get("per_share") is not None:
            m["per_share"] = round(m["per_share"] + e["per_share"], 6)
        else:
            m["per_share"] = None
        if e.get("declared_ps") is not None and m.get("declared_ccy") == e.get("declared_ccy"):
            m["declared_ps"] = round((m.get("declared_ps") or 0) + e["declared_ps"], 6)
        m["declared"] = " + ".join(p["declared"] for p in m["parts"])
        m["kind"] = " + ".join(p.get("kind") or "" for p in m["parts"]).strip(" +")
        m["estimated"] = m.get("estimated") or e.get("estimated")
        m["scrip"] = m.get("scrip") or e.get("scrip")
        m["pay_date"] = max(filter(None, [m.get("pay_date"), e.get("pay_date")]), default=None)
    return [by[k] for k in sorted(by)]


def _recorded_ex_dates(events: list[dict], actions: list) -> set:
    """已手记过的分红: 每条 DIVIDEND 流水认领日期最近的一个事件(窗口: 除净日前 5 天 ~ 派息日后 45 天)。"""
    taken = set()
    for a in actions:
        if (a.get("action_type") or "").upper() != "DIVIDEND":
            continue
        d = str(a.get("trade_date") or a.get("created_at") or "")[:10]
        try:
            dd = _dt.date.fromisoformat(d)
        except ValueError:
            continue
        best, gap = None, None
        for e in events:
            ex = _dt.date.fromisoformat(e["ex_date"])
            anchor = _dt.date.fromisoformat(e["pay_date"]) if e.get("pay_date") else ex
            if not (ex - _dt.timedelta(days=5) <= dd <= anchor + _dt.timedelta(days=45)):
                continue
            g = abs((dd - anchor).days)
            if gap is None or g < gap:
                best, gap = e["ex_date"], g
        if best:
            taken.add(best)
    return taken


def pending_for_code(code: str, actions: list, events: list[dict], today: str,
                     dismissed: set | None = None, broker: str | None = None) -> list[dict]:
    """某票待确认的分红建议(纯函数, 方便测试)。只出: 除净日已到、除净前一日收盘有持股、没手记过、没被忽略。"""
    from services.market_fees import dividend_tax_rate
    code = str(code).upper()
    mkt = "HK" if code.startswith("HK.") else "US"
    dismissed = dismissed or set()
    evs = _merge_same_day(events)
    recorded = _recorded_ex_dates(evs, actions)
    rate = dividend_tax_rate(mkt, broker)
    out = []
    for e in evs:
        ex = e["ex_date"]
        if ex > today or ex in recorded or f"{code}|{ex}" in dismissed:
            continue
        sh = shares_before(actions, ex, fractional=(mkt == "US"))
        if sh <= 1e-9:
            continue
        ps, pay = e.get("per_share"), e.get("pay_date")
        warn = []
        if mkt == "HK" and e.get("declared_ccy") and e["declared_ccy"] != "HKD":
            ccy = {"USD": "美元", "CNY": "人民币"}.get(e["declared_ccy"], e["declared_ccy"])
            if ps is None:
                warn.append(f"公告以{ccy}宣派且未给港币等值, 请按结单填每股港币金额")
            else:
                warn.append(f"公告以{ccy}宣派, 按港币等值记; 实收港币以结单为准")
            if e["declared_ccy"] == "CNY":
                warn.append("人民币宣派的多为内地注册公司, 通常预扣 10% 所得税, 以结单为准填税")
        if e.get("estimated"):
            warn.append("港币金额是按汇率折算的估算值")
        if e.get("scrip"):
            warn.append("可选以股代息; 若选了收股票请改记送股")
        if not pay:
            warn.append("数据源没有派息日, 按除净日记汇率; 可改成结单上的到账日")
        status = "needs_input" if ps is None else ("upcoming" if pay and pay > today else "payable")
        gross = round(sh * ps, 2) if ps is not None else None
        tax = round(gross * rate, 2) if gross is not None else None
        out.append({
            "stock_code": code, "market": mkt, "ex_date": ex, "pay_date": pay, "trade_date": pay or ex,
            "per_share": ps, "currency": e.get("currency") or ("HKD" if mkt == "HK" else "USD"),
            "declared": e.get("declared"), "kind": e.get("kind") or "", "shares": sh,
            "gross": gross, "tax_rate": rate, "tax": tax,
            "net": round(gross - tax, 2) if gross is not None else None,
            "status": status, "warnings": warn, "source": e.get("source"),
        })
    return out


async def dismissed_set() -> set:
    from database import get_config
    try:
        return set(_json.loads(await get_config(DISMISS_KEY) or "[]"))
    except (ValueError, TypeError):
        return set()


async def dismiss(code: str, ex_date: str) -> None:
    from database import set_config
    s = await dismissed_set()
    s.add(f"{str(code).upper()}|{str(ex_date)[:10]}")
    await set_config(DISMISS_KEY, _json.dumps(sorted(s)))


async def scan_pending(only_code: str | None = None) -> dict:
    """全部港美股持仓(含已清仓, 持有期内漏记的也算)的待确认分红。拉数据失败的票列进 errors。"""
    from database import get_all_holdings, get_position_actions
    dismissed = await dismissed_set()
    codes = [h for h in await get_all_holdings()
             if str(h.get("stock_code") or "").upper().startswith(("HK.", "US."))]
    if only_code:
        codes = [h for h in codes if h["stock_code"].upper() == only_code.upper()]
    items, errors = [], []

    async def one(h):
        code = h["stock_code"]
        actions = await get_position_actions(code, limit=500)
        if not actions:
            return
        ev = await asyncio.to_thread(foreign_events_sync, code)
        if ev is None:
            errors.append(code)
            return
        for it in pending_for_code(code, actions, ev, market_today(code), dismissed, h.get("broker")):
            it["stock_name"] = h.get("stock_name") or ""
            items.append(it)
    await asyncio.gather(*(one(h) for h in codes))
    items.sort(key=lambda x: (x["ex_date"], x["stock_code"]), reverse=True)
    return {"items": items, "errors": sorted(errors)}
