"""龙虎榜席位明细: 某股某上榜日的买卖前五营业部(名称/金额/占成交比) + 席位画像标签。

数据 = 交易所披露(东财转发, akshare stock_lhb_stock_detail_em)。
席位标签是近似画像: 机构专用/沪深股通是官方口径; "常见量化通道"来自公开常识的知名席位
小表(会漂移, 仅供参考); 其余席位不贴标签。纯客观数据, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import time

_cache: dict = {}
_TTL = 6 * 3600

# 公开常识里高频出现的量化/机构通道席位(子串匹配, 保守小表, 宁缺勿滥)
_QUANT_SEATS = (
    "华鑫证券有限责任公司上海分公司",
    "中信证券股份有限公司上海分公司",
    "瑞银证券有限责任公司上海花园石桥路",
    "高盛(中国)证券有限责任公司上海浦东新区世纪大道",
    "摩根士丹利证券(中国)有限公司上海世纪大道",
    "中国国际金融股份有限公司上海分公司",
)


_seat_names: dict | None = None


def _load_seat_names() -> dict:
    """data/seat_names.json: 席位子串 → 江湖名号(公开资料整理, 用户可自行增删)。"""
    global _seat_names
    if _seat_names is None:
        import json
        import os
        p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "seat_names.json")
        try:
            with open(p) as f:
                _seat_names = {k: v for k, v in json.load(f).items() if not k.startswith("_")}
        except Exception:
            _seat_names = {}
    return _seat_names


def seat_tag(name: str) -> str:
    n = (name or "").strip()
    if n == "机构专用":
        return "机构"
    if "沪股通" in n or "深股通" in n:
        return "北向"
    for sub, nick in _load_seat_names().items():
        if sub[:12] in n:
            return nick
    for q in _QUANT_SEATS:
        if q[:10] in n:
            return "常见量化通道"
    return ""


def _pick_latest_day(records: list) -> tuple:
    """全榜原始行(可能一股多条上榜原因) → (最新披露日, 按代码去重合并后的行)。

    同股多条: 金额字段取绝对值最大的那条(不同榜单口径重叠, 相加会重复计),
    上榜原因合并展示。纯函数, 便于测试。
    """
    if not records:
        return "", []
    day = max(str(r.get("上榜日") or "") for r in records)
    by_code: dict = {}
    for r in records:
        if str(r.get("上榜日") or "") != day:
            continue
        code = str(r.get("代码") or "").strip()
        if not code:
            continue

        def _f(k):
            try:
                v = float(r.get(k))
                return v if v == v else None
            except (TypeError, ValueError):
                return None

        net = _f("龙虎榜净买额")
        row = {
            "code": code, "name": str(r.get("名称") or "").strip(),
            "涨跌幅": round(_f("涨跌幅") or 0, 2),
            "收盘价": _f("收盘价"),
            "净买额亿": round((net or 0) / 1e8, 2),
            "换手率": round(_f("换手率") or 0, 2),
            "解读": str(r.get("解读") or "").strip(),
            "上榜原因": str(r.get("上榜原因") or "").strip(),
        }
        old = by_code.get(code)
        if old is None:
            by_code[code] = row
        else:
            if abs(row["净买额亿"]) > abs(old["净买额亿"]):
                row["上榜原因"] = old["上榜原因"] + " / " + row["上榜原因"]
                by_code[code] = row
            elif row["上榜原因"] and row["上榜原因"] not in old["上榜原因"]:
                old["上榜原因"] += " / " + row["上榜原因"]
    rows = sorted(by_code.values(), key=lambda x: x["净买额亿"], reverse=True)
    return day, rows


def _daily_sync() -> dict:
    import datetime
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    end = datetime.date.today()
    start = end - datetime.timedelta(days=10)
    df = ak.stock_lhb_detail_em(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    if df is None or not len(df):
        return {"date": "", "rows": [], "note": "近10天无龙虎榜披露数据(东财源)。"}
    day, rows = _pick_latest_day(df.to_dict("records"))
    return {"date": day, "rows": rows,
            "note": ("交易所当日全部上榜个股(涨跌幅偏离/换手/振幅等触发披露)。"
                     "净买额=龙虎榜披露席位合计, 同股多榜单口径取金额最大一条。"
                     "点个股直接看该日买卖前五席位。纯客观数据, 不构成任何买卖建议。")}


_daily_cache: dict = {}


async def lhb_daily() -> dict:
    """最新一个披露日的龙虎榜全榜单(按净买额排序)。当日盘后约17点起逐步披露,
    未出时自动落到上一披露日。缓存30分钟。"""
    c = _daily_cache.get("d")
    if c and time.time() - c[1] < 1800:
        return c[0]
    r = await asyncio.to_thread(_daily_sync)
    if r["rows"]:
        _daily_cache["d"] = (r, time.time())
    return r


def _dates_sync(code: str) -> list:
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_lhb_stock_detail_date_em(symbol=code)
    if df is None or not len(df):
        return []
    return sorted((str(x)[:10] for x in df["交易日"].tolist()), reverse=True)[:8]


_dates_cache: dict = {}


async def stock_lhb_dates(code: str) -> list:
    """某股历史上榜日(最近8个, 新→旧)。缓存6h。"""
    c = _dates_cache.get(code)
    if c and time.time() - c[1] < _TTL:
        return c[0]
    try:
        r = await asyncio.to_thread(_dates_sync, code)
    except Exception:
        return []
    _dates_cache[code] = (r, time.time())
    return r


_EM_DATA_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _em_data_get(report: str, flt: str, page_size: int = 100,
                 sort_cols: str = "TRADE_DATE,SECURITY_CODE", sort_types: str = "-1,1") -> list:
    import requests
    s = requests.Session()
    s.trust_env = False
    for attempt in range(3):
        try:
            r = s.get(_EM_DATA_URL, params={
                "sortColumns": sort_cols, "sortTypes": sort_types,
                "pageSize": str(page_size), "pageNumber": "1",
                "reportName": report, "columns": "ALL",
                "source": "WEB", "client": "WEB", "filter": flt,
            }, timeout=10).json()
            return (r.get("result") or {}).get("data") or []
        except Exception:
            time.sleep(0.5 * (attempt + 1))
    return []


_active_cache: dict = {}


def _active_seats_sync(days: int = 90) -> list:
    """近 N 天活跃营业部 [(代码, 全名)](RPT_OPERATEDEPT_ACTIVE 一次拉全)。缓存12h。"""
    c = _active_cache.get("a")
    if c and time.time() - c[1] < 12 * 3600:
        return c[0]
    import datetime
    start = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    rows = _em_data_get("RPT_OPERATEDEPT_ACTIVE", f"(ONLIST_DATE>='{start}')",
                        page_size=5000, sort_cols="TOTAL_NETAMT,ONLIST_DATE,OPERATEDEPT_CODE",
                        sort_types="-1,-1,1")
    seen, out = set(), []
    for x in rows:
        code, name = str(x.get("OPERATEDEPT_CODE") or ""), str(x.get("OPERATEDEPT_NAME") or "")
        if code and name and code not in seen:
            seen.add(code)
            out.append((code, name))
    if out:
        _active_cache["a"] = (out, time.time())
    return out


def _seat_rows_sync(flt: str) -> list:
    """某席位最近100条上榜明细(按日期倒序), 带上榜后 1/5/10 日涨跌。"""
    out = []
    for x in _em_data_get("RPT_OPERATEDEPT_TRADE_DETAILSNEW", flt):
        def _f(k):
            v = x.get(k)
            try:
                return round(float(v), 2) if v is not None else None
            except (TypeError, ValueError):
                return None

        out.append({
            "日期": str(x.get("TRADE_DATE") or "")[:10],
            "code": str(x.get("SECURITY_CODE") or ""),
            "name": str(x.get("SECURITY_NAME_ABBR") or ""),
            "当日涨跌%": _f("CHANGE_RATE"),
            "买入万": round((_f("ACT_BUY") or 0) / 1e4, 1),
            "卖出万": round((_f("ACT_SELL") or 0) / 1e4, 1),
            "净额万": round((_f("NET_AMT") or 0) / 1e4, 1),
            "上榜原因": str(x.get("EXPLANATION") or "")[:30],
            "后1日%": _f("D1_CLOSE_ADJCHRATE"),
            "后5日%": _f("D5_CLOSE_ADJCHRATE"),
            "后10日%": _f("D10_CLOSE_ADJCHRATE"),
            "席位": str(x.get("ORG_NAME_ABBR") or x.get("OPERATEDEPT_NAME") or ""),
        })
    return out


_seat_hist_cache: dict = {}


async def seat_history(query: str, days: int = 90) -> dict:
    """席位近期上榜记录+客观统计。query=名录名号(章盟主)/席位名子串/营业部全名。

    交易所只披露营业部, 名号映射来自公开名录(会漂移); 统计为纯历史描述,
    不构成任何买卖建议。缓存6h。"""
    q = (query or "").strip()
    if not q:
        return {"error": "缺席位名(名号或营业部名)"}
    ck = f"{q}_{days}"
    c = _seat_hist_cache.get(ck)
    if c and time.time() - c[1] < _TTL:
        return c[0]

    # 名号/子串 → 名录前缀 → 活跃营业部反查代码; 全名(含"营业部")直接精确过滤
    prefixes = [p for p, nick in _load_seat_names().items() if q in nick or q in p]
    seats: list = []          # (filter, 显示名)
    matched_via = ""
    if prefixes:
        active = await asyncio.to_thread(_active_seats_sync)
        for p in prefixes:
            seats += [(f'(OPERATEDEPT_CODE="{code}")', name)
                      for code, name in active if p[:12] in name]
        matched_via = "公开名录"
    elif "营业部" in q or ("公司" in q and len(q) >= 12):
        seats = [(f'(OPERATEDEPT_NAME="{q}")', q)]
        matched_via = "营业部全名"
    else:
        active = await asyncio.to_thread(_active_seats_sync)
        seats = [(f'(OPERATEDEPT_CODE="{code}")', name)
                 for code, name in active if q in name][:3]
        matched_via = "活跃营业部名称匹配"
    if not seats:
        r = {"query": q, "rows": [], "note": f"没匹配到席位: 名录无此名号, 且近90天活跃营业部里搜不到'{q}'。"
                                            "名号表 data/seat_names.json 可自行增补。"}
        return r

    import datetime
    cutoff = (datetime.date.today() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    rows: list = []
    for flt, _name in seats[:4]:
        rows += await asyncio.to_thread(_seat_rows_sync, flt)
    rows = sorted((r for r in rows if r["日期"] >= cutoff), key=lambda x: x["日期"], reverse=True)[:80]

    buys = [r for r in rows if r["净额万"] > 0]
    d1 = [r["后1日%"] for r in buys if r["后1日%"] is not None]
    d5 = [r["后5日%"] for r in buys if r["后5日%"] is not None]
    stats = {
        "上榜次数": len(rows), "净买入次数": len(buys),
        "净买入后1日红盘率%": round(sum(1 for v in d1 if v > 0) / len(d1) * 100) if d1 else None,
        "净买入后1日平均%": round(sum(d1) / len(d1), 2) if d1 else None,
        "净买入后5日红盘率%": round(sum(1 for v in d5 if v > 0) / len(d5) * 100) if d5 else None,
    }
    r = {"query": q, "匹配方式": matched_via,
         "席位": sorted({x["席位"] for x in rows}) or [n for _, n in seats],
         "窗口天数": days, "stats": stats, "rows": rows,
         "note": "交易所披露的营业部上榜明细。名号→营业部映射来自公开名录, 席位会易主、名录会过时;"
                 "红盘率为纯历史统计, 不构成任何买卖建议。"}
    if rows:
        _seat_hist_cache[ck] = (r, time.time())
    return r


def _fetch_sync(code: str, date: str) -> dict:
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    d8 = str(date).replace("-", "")
    out = {"code": code, "date": f"{d8[:4]}-{d8[4:6]}-{d8[6:]}", "买入": [], "卖出": [], "上榜原因": ""}
    for flag, key, amt_col in (("买入", "买入", "买入金额"), ("卖出", "卖出", "卖出金额")):
        df = None
        for attempt in range(3):
            try:
                df = ak.stock_lhb_stock_detail_em(symbol=code, date=d8, flag=flag)
                break
            except Exception:
                time.sleep(0.5 * (attempt + 1))
        if df is None or not len(df):
            continue
        for _, r in df.iterrows():
            nm = str(r.get("交易营业部名称") or "").strip()
            try:
                amt = float(r.get(amt_col) or 0) / 1e4          # 万元
                pct = float(r.get(f"{amt_col}-占总成交比例") or 0)
            except (TypeError, ValueError):
                continue
            if not nm or amt <= 0:
                continue
            out[key].append({"席位": nm, "金额万": round(amt, 1),
                             "占成交%": round(pct * 100, 2) if pct < 1 else round(pct, 2),
                             "标签": seat_tag(nm)})
            if not out["上榜原因"]:
                out["上榜原因"] = str(r.get("类型") or "")[:40]
    out["买入总计万"] = round(sum(x["金额万"] for x in out["买入"]), 1)
    out["卖出总计万"] = round(sum(x["金额万"] for x in out["卖出"]), 1)
    return out


async def lhb_seat_detail(code: str, date: str) -> dict:
    """某股某日席位明细。当日未上榜 → 买卖为空 + note。缓存6h。"""
    ck = f"{code}_{str(date).replace('-', '')}"
    c = _cache.get(ck)
    if c and time.time() - c[1] < _TTL:
        return c[0]
    r = await asyncio.to_thread(_fetch_sync, code, date)
    if not r["买入"] and not r["卖出"]:
        r["note"] = "该日未上龙虎榜(涨跌幅/换手未触发披露条件), 无席位数据。"
        r["最近上榜日"] = await stock_lhb_dates(code)
        # 空结果也缓存(短TTL): 未上榜的日子占绝大多数, 每次点都重跑重试链太慢
        _cache[ck] = (r, time.time() - _TTL + 1800)
    else:
        r["note"] = ("交易所披露的买卖前五席位。'常见量化通道'为公开常识近似画像(会漂移, 仅供参考);"
                     "无标签≠游资。纯客观数据, 不构成任何买卖建议。")
        _cache[ck] = (r, time.time())
    return r
