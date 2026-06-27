"""问股票为什么涨/跌 — 挂工具的小 agent。

LLM 自己决定调哪些工具(查行情/走势/新闻/持仓/大盘情绪)拿数据, 再总结涨跌原因。
自由问答。硬规则: 只做客观解读, 严禁任何买卖/操作建议。
"""
from __future__ import annotations
import asyncio
import json as _json
import re as _re
import time as _time

import services.llm_client as _llm

_MODEL = "claude-opus-4-8"
_MAX_ROUNDS = 8

# A 股 代码↔名称 表 (akshare, 缓存 12h, 供按名字解析)
_code_name_cache: tuple[dict, dict, float] | None = None


def _load_a_code_name_sync():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_info_a_code_name()
    name2code, code2name = {}, {}
    for _, r in df.iterrows():
        code = str(r.get("code") or "").strip()
        name = str(r.get("name") or "").strip()
        if code and name:
            name2code[name] = code
            code2name[code] = name
    return name2code, code2name


async def _code_name_maps():
    global _code_name_cache
    if _code_name_cache and _time.time() - _code_name_cache[2] < 43200:
        return _code_name_cache[0], _code_name_cache[1]
    try:
        n2c, c2n = await asyncio.to_thread(_load_a_code_name_sync)
        _code_name_cache = (n2c, c2n, _time.time())
        return n2c, c2n
    except Exception:
        return ({}, {}) if not _code_name_cache else (_code_name_cache[0], _code_name_cache[1])


# ---------------------------------------------------------------------------
# 工具执行器 (都返回可 JSON 序列化的 dict/list)
# ---------------------------------------------------------------------------

def _norm_code(code: str) -> str:
    """LLM 常给 A 股带 sh/sz 前缀(如 sh600667), 但行情接口要裸 6 位代码; 这里剥掉前缀。
    HK./US. 这类保持原样。"""
    c = (code or "").strip()
    m = _re.match(r"^(?:sh|sz|SH|SZ)(\d{6})$", c)
    return m.group(1) if m else c

async def _active_holdings() -> list[dict]:
    """真实在持的持仓: 按 ledger(综合成本法)现算 shares, 只留 shares>0。
    holdings 表的 shares 列对已清仓的票可能是陈旧非0值, 不能直接信; 这里跟 /api/portfolio 口径一致。"""
    from database import get_all_holdings, get_position_actions
    from services.position_ledger import compute_position_state
    from api.portfolio_routes import _broker_stock_fee
    out = []
    for h in await get_all_holdings():
        code = h.get("stock_code")
        shares = float(h.get("shares") or 0)
        try:
            acts = await get_position_actions(code, limit=500)
            if acts:
                rate, mn = await _broker_stock_fee(h.get("broker"))
                st = compute_position_state(acts, stock_code=code, commission_rate=rate, commission_min=mn)
                shares = float(st.get("shares") or 0)
        except Exception:
            pass  # ledger 算不出就退回表里的 shares
        if shares > 0:
            out.append({**h, "shares": shares})
    return out


async def _tool_resolve_stock(query: str) -> dict:
    """名字或代码 → 标准代码 + 名称。先查在持持仓(已清仓不算在持), 再查持仓表全部, 最后 A 股全表。"""
    from database import get_all_holdings
    q = (query or "").strip()
    if not q:
        return {"error": "空查询"}
    # 1) 在持持仓里匹配 (名字/代码 子串) → in_holdings=True
    try:
        for h in await _active_holdings():
            nm = h.get("stock_name") or ""
            cd = h.get("stock_code") or ""
            if q == nm or q == cd or (q in nm) or (q in cd):
                return {"code": cd, "name": nm, "in_holdings": True}
    except Exception:
        pass
    # 1b) 持仓表里有但已清仓 → 能解析出代码, 但标记 in_holdings=False
    try:
        for h in await get_all_holdings():
            nm = h.get("stock_name") or ""
            cd = h.get("stock_code") or ""
            if q == nm or q == cd or (q in nm) or (q in cd):
                return {"code": cd, "name": nm, "in_holdings": False, "note": "已清仓, 不在当前持仓"}
    except Exception:
        pass
    # 2) A 股全表
    n2c, c2n = await _code_name_maps()
    if q in c2n:
        return {"code": q, "name": c2n[q], "in_holdings": False}
    if q in n2c:
        return {"code": n2c[q], "name": q, "in_holdings": False}
    hits = [(nm, cd) for nm, cd in n2c.items() if q in nm][:5]
    if hits:
        return {"candidates": [{"name": nm, "code": cd} for nm, cd in hits]}
    return {"error": f"找不到 {q}"}


async def _tool_get_quote(code: str) -> dict:
    from services.market_data import get_realtime_quotes, normalize_stock_code, get_stock_name
    code = normalize_stock_code(_norm_code(code))
    q = (await get_realtime_quotes([code])).get(code)
    if not q:
        return {"error": f"{code} 无行情"}
    name = ""
    try:
        name = await get_stock_name(code)
    except Exception:
        pass
    return {
        "code": code, "name": name,
        "price": q.get("price"), "change_pct": q.get("change_pct"),
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "prev_close": q.get("prev_close"), "amount": q.get("amount"),
        "turnover_rate": q.get("turnover_rate"),
    }


def _us_daily_k_sync(symbol: str, datalen: int) -> list:
    """美股个股日K(新浪 US_MinKService, 裸 symbol 如 AAPL; 返回升序, 取末尾 N 条)。"""
    import requests as _rq
    url = (f"http://stock.finance.sina.com.cn/usstock/api/jsonp.php/var%20t=/"
           f"US_MinKService.getDailyK?symbol={symbol.upper()}&num={max(datalen + 5, 30)}")
    txt = _rq.get(url, headers={"Referer": "https://finance.sina.com.cn/stock/usstock/"}, timeout=6).text
    s = txt.find("=(")
    if s < 0:
        return []
    try:
        end = txt.rfind(");")
        arr = _json.loads(txt[s + 2:end if end > 0 else None])
    except Exception:
        return []
    return [float(d["c"]) for d in arr[-(datalen + 1):] if d.get("c")] if isinstance(arr, list) else []


async def _tool_get_trend(code: str, days: int = 20) -> dict:
    """近 N 日走势: 每日涨跌幅 + 累计。A 股走新浪历史; 港股走腾讯日K; 美股走新浪 US 日K。"""
    from services.market_data import (get_historical_data, normalize_stock_code, is_a_share,
                                       split_stock_code, _kline_tencent_hk)
    raw = normalize_stock_code(_norm_code(code))
    days = max(5, min(int(days or 20), 60))
    market, symbol = split_stock_code(raw)
    closes: list = []
    if is_a_share(raw):
        df = await get_historical_data(raw, days + 5)
        if df is None or df.empty:
            return {"error": "无历史数据"}
        closes = [float(x) for x in df["收盘"].tolist()][-(days + 1):]
    elif market == "HK":
        rows = await asyncio.to_thread(_kline_tencent_hk, f"hk{symbol.zfill(5)}", days + 5)
        closes = [float(r["close"]) for r in (rows or []) if r.get("close")][-(days + 1):]
    elif market == "US":
        closes = await asyncio.to_thread(_us_daily_k_sync, symbol, days)
    else:
        return {"error": "走势暂不支持该市场"}
    if len(closes) < 2:
        return {"error": "无历史数据"}
    code = raw
    daily = [round((closes[i] / closes[i - 1] - 1) * 100, 2) for i in range(1, len(closes))]
    cum = round((closes[-1] / closes[0] - 1) * 100, 2)
    up = sum(1 for d in daily if d > 0)
    return {
        "code": code, "days": len(daily),
        "cum_pct": cum, "up_days": up, "down_days": len(daily) - up,
        "last_close": round(closes[-1], 3),
        "daily_pct": daily[-min(10, len(daily)):],  # 最近 10 日逐日涨跌
    }


async def _tool_get_news(code: str) -> dict:
    """个股最近新闻 (akshare 东财, A 股)。"""
    from api.news_routes import _fetch_stock_news_em_sync
    from services.market_data import normalize_stock_code
    raw = normalize_stock_code(_norm_code(code))
    bare = raw.split(".")[-1] if "." in raw else raw
    items = await asyncio.to_thread(_fetch_stock_news_em_sync, bare)
    if not items:
        return {"news": [], "note": "暂无个股新闻"}
    return {"news": [{"title": it["title"], "summary": it["content"][:140],
                      "time": it["time"], "source": it["source"]} for it in items[:10]]}


def _em_secid(code: str) -> str:
    """A 股 6 位代码 → 东财 secid。沪(6/9/5)=1., 深(0/2/3/1)=0.。"""
    code = _norm_code(code)
    return ("1." if code[:1] in ("6", "9", "5") else "0.") + code


_fflow_cache: dict = {}


def _fetch_fund_flow_sync(code: str) -> dict:
    """个股主力资金流(东财 fflow/kline 日线): 近几日主力净流入趋势 + 今日各单类拆解。
    klines 每行: 日期,主力净,小单净,中单净,大单净,超大单净 (单位元)。直连 push2his/push2delay, 死分片 79.push2 不走。"""
    import requests as _rq
    import time as _t
    ck = f"ff_{code}"
    c = _fflow_cache.get(ck)
    if c and _t.time() - c[1] < 300:
        return c[0]
    secid = _em_secid(code)
    params = {"lmt": "8", "klt": "101", "secid": secid,
              "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56"}
    hosts = ["push2his.eastmoney.com", "push2.eastmoney.com", "push2delay.eastmoney.com"]
    for i in range(9):
        host = hosts[i % len(hosts)]
        try:
            r = _rq.get(f"https://{host}/api/qt/stock/fflow/kline/get", params=params, timeout=7,
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"})
            kl = (r.json().get("data") or {}).get("klines")
            if kl:
                rows = []
                for ln in kl[-6:]:
                    p = ln.split(",")
                    if len(p) < 6:
                        continue
                    rows.append({"date": p[0], "main": round(float(p[1]) / 1e8, 2),
                                 "small": round(float(p[2]) / 1e8, 2), "mid": round(float(p[3]) / 1e8, 2),
                                 "big": round(float(p[4]) / 1e8, 2), "xlarge": round(float(p[5]) / 1e8, 2)})
                if rows:
                    out = {"unit": "亿元", "today": rows[-1],
                           "main_net_series": [{"date": r["date"], "主力净流入亿": r["main"]} for r in rows]}
                    _fflow_cache[ck] = (out, _t.time())
                    return out
        except Exception:
            _t.sleep(0.3)
    return {"error": "资金流暂不可达(东财源抖动)"}


async def _tool_fund_flow(code: str) -> dict:
    """个股主力资金流: 今日主力/超大单/大单/中单/小单净额 + 近几日主力净流入趋势(判断谁在买/在卖)。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "资金流仅支持 A 股"}
    out = await asyncio.to_thread(_fetch_fund_flow_sync, raw)
    if "error" not in out:
        t = out["today"]
        out["note"] = (f"今日主力净{'流入' if t['main'] >= 0 else '流出'}{abs(t['main'])}亿"
                       f"(超大单{t['xlarge']}/大单{t['big']}/中单{t['mid']}/小单{t['small']}亿); "
                       "主力=超大单+大单, 正=资金净买入。")
    return out


_lhb_cache: dict = {}


def _fetch_lhb_sync(code: str = "", days: int = 12) -> dict:
    """龙虎榜(东财, akshare stock_lhb_detail_em): 近 N 日上榜明细。
    code 给定→该股上榜记录; 否则→最近交易日净买额排序(游资/机构在打哪些票)。"""
    import time as _t
    import datetime as _dt
    ck = f"lhb_{code}_{days}"
    c = _lhb_cache.get(ck)
    if c and _t.time() - c[1] < 1800:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    end = _dt.date.today()
    start = end - _dt.timedelta(days=days)
    df = ak.stock_lhb_detail_em(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    if df is None or df.empty:
        return {"error": "近期无龙虎榜数据"}
    bare = _norm_code(code)
    if bare:
        df = df[df["代码"].astype(str) == bare]
        if df.empty:
            return {"code": bare, "on_list": False, "note": f"{bare} 近{days}日未上龙虎榜"}
    else:
        # 市场视图: 取最近上榜日, 按净买额绝对值排序的活跃票
        df = df.sort_values("上榜日", ascending=False)
        latest = df["上榜日"].iloc[0]
        df = df[df["上榜日"] == latest].copy()
        df["_abs"] = df["龙虎榜净买额"].abs()
        df = df.sort_values("_abs", ascending=False).head(15)

    def row(r):
        return {"code": str(r["代码"]), "name": r["名称"], "date": str(r["上榜日"]),
                "change_pct": round(float(r["涨跌幅"]), 2) if r["涨跌幅"] == r["涨跌幅"] else None,
                "净买额亿": round(float(r["龙虎榜净买额"]) / 1e8, 2) if r["龙虎榜净买额"] == r["龙虎榜净买额"] else None,
                "解读": r.get("解读"), "上榜原因": r.get("上榜原因")}
    recs = [row(r) for _, r in df.iterrows()]
    out = ({"code": bare, "on_list": True, "records": recs} if bare
           else {"latest_date": recs[0]["date"] if recs else None, "top_by_net_buy": recs,
                 "note": "净买额正=游资/机构净买入(资金做多), 负=净卖出; 看解读/上榜原因辨别机构席位还是游资。"})
    _lhb_cache[ck] = (out, _t.time())
    return out


async def _tool_lhb(code: str = "") -> dict:
    """龙虎榜: code 给定→该股近期上榜(谁买谁卖/机构还是游资); 不给→最近交易日资金净买额榜(主力在打哪些票)。仅 A 股。"""
    if code:
        from services.market_data import normalize_stock_code, is_a_share
        if not is_a_share(normalize_stock_code(_norm_code(code))):
            return {"error": "龙虎榜仅支持 A 股"}
    try:
        return await asyncio.to_thread(_fetch_lhb_sync, code or "", 12)
    except Exception as e:
        return {"error": f"龙虎榜获取失败: {e}"}


# ssbk 里混了一堆"市场状态/指数成分"标签, 不是真正的行业/概念, 过滤掉
_SSBK_NOISE = {
    "题材股", "趋势股", "融资融券", "沪股通", "深股通", "标准普尔", "富时罗素", "机构重仓",
    "小盘成长", "小盘股", "中盘股", "大盘股", "白马股", "绩优股", "预盈预增", "MSCI中国",
    "上证180", "上证380", "上证50", "沪深300", "中证500", "创业板综", "深证成指",
    "东方财富热股", "央企改革", "国企改革", "央国企改革",
}
_SSBK_NOISE_KW = ["板块", "新高", "新低", "涨停", "跌停", "首板", "多板", "振幅", "换手", "昨日", "今日", "近期", "连板"]
_concepts_cache: dict = {}


def _fetch_stock_concepts_sync(code: str) -> dict:
    """个股所属板块/概念 + 核心题材(东财 F10 CoreConception)。code=裸6位 → SZ/SH 前缀。"""
    import requests as _rq
    import time as _t
    bare = _norm_code(code)
    ck = f"cc_{bare}"
    c = _concepts_cache.get(ck)
    if c and _t.time() - c[1] < 86400:
        return c[0]
    em_code = ("SH" if bare[:1] in ("6", "9", "5") else "SZ") + bare
    try:
        j = _rq.get("https://emweb.securities.eastmoney.com/PC_HSF10/CoreConception/PageAjax",
                    params={"code": em_code}, timeout=8, headers={"User-Agent": "Mozilla/5.0"}).json()
    except Exception:
        return {"error": "所属概念暂不可达"}
    boards = []
    for b in (j.get("ssbk") or []):
        nm = (b.get("BOARD_NAME") or "").strip()
        if not nm or nm in _SSBK_NOISE or any(k in nm for k in _SSBK_NOISE_KW):
            continue
        boards.append(nm)
    themes = []
    for t in (j.get("hxtc") or []):
        kw = (t.get("KEYWORD") or "").strip()
        if kw and kw != "经营范围" and kw not in themes:
            themes.append(kw)
    out = {"code": bare, "boards": boards[:20], "core_themes": themes[:8],
           "note": "boards=所属行业/概念板块(已滤掉指数成分等噪声标签); core_themes=核心题材/主营。可与热门概念榜交叉看是不是踩在资金主线上。"}
    _concepts_cache[ck] = (out, _t.time())
    return out


async def _tool_stock_concepts(code: str) -> dict:
    """个股所属行业/概念板块 + 核心题材。回答'这只票属于哪个概念、是不是踩在资金主线上'时用。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    if not is_a_share(normalize_stock_code(_norm_code(code))):
        return {"error": "所属概念仅支持 A 股"}
    return await asyncio.to_thread(_fetch_stock_concepts_sync, code)


_val_cache: dict = {}


def _fetch_valuation_sync(code: str) -> dict:
    """估值快照(东财 stock/get): PE(TTM)/PB/总市值/流通市值/行业。f162=PE×100, f167=PB×100, f116=总市值元。"""
    import requests as _rq
    import time as _t
    ck = f"val_{code}"
    c = _val_cache.get(ck)
    if c and _t.time() - c[1] < 600:
        return c[0]
    secid = _em_secid(code)
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]
    for i in range(9):
        host = hosts[i % len(hosts)]
        try:
            d = _rq.get(f"https://{host}/api/qt/stock/get", timeout=7,
                        params={"secid": secid, "fields": "f58,f43,f162,f167,f116,f117,f127"},
                        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}).json().get("data")
            if d and d.get("f58"):
                pe = d.get("f162"); pb = d.get("f167"); mcap = d.get("f116")
                out = {"行业": d.get("f127"),
                       "PE_TTM": round(pe / 100, 2) if isinstance(pe, (int, float)) and pe not in (0, None) else None,
                       "PB": round(pb / 100, 2) if isinstance(pb, (int, float)) and pb not in (0, None) else None,
                       "总市值亿": round(mcap / 1e8, 1) if isinstance(mcap, (int, float)) and mcap else None,
                       "流通市值亿": round(d.get("f117") / 1e8, 1) if isinstance(d.get("f117"), (int, float)) and d.get("f117") else None}
                _val_cache[ck] = (out, _t.time())
                return out
        except Exception:
            _t.sleep(0.3)
    return {}


_fin_cache: dict = {}


def _fetch_financials_sync(code: str) -> dict:
    """财务摘要(东财, akshare stock_financial_abstract): 取最新报告期的关键指标。"""
    import time as _t
    ck = f"fin_{code}"
    c = _fin_cache.get(ck)
    if c and _t.time() - c[1] < 3600:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_financial_abstract(symbol=code)
    if df is None or df.empty:
        return {}
    date_cols = [c for c in df.columns if str(c).isdigit() and len(str(c)) == 8]
    date_cols.sort(reverse=True)  # 最新在前
    by_ind = {}
    for _, r in df.iterrows():
        by_ind[str(r["指标"]).strip()] = r

    def latest(ind):
        row = by_ind.get(ind)
        if row is None:
            return None, None
        for dc in date_cols:
            v = row.get(dc)
            if v is not None and v == v and str(v) != "":  # 非 NaN 非空
                try:
                    return float(v), dc
                except (ValueError, TypeError):
                    return None, None
        return None, None

    def yi(ind):  # 元 → 亿
        v, dt = latest(ind)
        return (round(v / 1e8, 2) if v is not None else None), dt

    rev, rdt = yi("营业总收入")
    profit, _ = yi("归母净利润")
    rev_g, _ = latest("营业总收入增长率")
    profit_g, _ = latest("归属母公司净利润增长率")
    roe, _ = latest("净资产收益率(ROE)")
    gross, _ = latest("毛利率")
    netm, _ = latest("销售净利率")
    debt, _ = latest("资产负债率")
    eps, _ = latest("基本每股收益")
    bvps, _ = latest("每股净资产")
    out = {"报告期": rdt,
           "营业总收入亿": rev, "营收同比增长%": round(rev_g, 1) if rev_g is not None else None,
           "归母净利润亿": profit, "净利同比增长%": round(profit_g, 1) if profit_g is not None else None,
           "ROE%": round(roe, 2) if roe is not None else None,
           "毛利率%": round(gross, 1) if gross is not None else None,
           "净利率%": round(netm, 1) if netm is not None else None,
           "资产负债率%": round(debt, 1) if debt is not None else None,
           "每股收益元": round(eps, 3) if eps is not None else None,
           "每股净资产元": round(bvps, 2) if bvps is not None else None}
    _fin_cache[ck] = (out, _t.time())
    return out


def _fetch_hkus_fundamentals_sync(market: str, symbol: str) -> dict:
    """港股/美股基本面(东财, akshare em 指标): 营收/净利及同比、毛利率/净利率/ROE/负债率/EPS。金额→亿(原币种)。"""
    import time as _t
    ck = f"fin_{market}_{symbol}"
    c = _fin_cache.get(ck)
    if c and _t.time() - c[1] < 3600:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    try:
        if market == "HK":
            df = ak.stock_financial_hk_analysis_indicator_em(symbol=symbol.zfill(5), indicator="报告期")
            profit_key, profit_yoy = "HOLDER_PROFIT", "HOLDER_PROFIT_YOY"
            roe_key = "ROE_YEARLY"
        else:
            df = ak.stock_financial_us_analysis_indicator_em(symbol=symbol.upper(), indicator="年报")
            profit_key, profit_yoy = "PARENT_HOLDER_NETPROFIT", "PARENT_HOLDER_NETPROFIT_YOY"
            roe_key = "ROE_AVG"
    except Exception as e:
        return {"error": f"港美股财务源不可达: {e}"}
    if df is None or df.empty:
        return {}
    r = df.iloc[0].to_dict()

    def f(k, nd=2):
        v = r.get(k)
        try:
            return round(float(v), nd)
        except (ValueError, TypeError):
            return None

    def yi(k):
        v = r.get(k)
        try:
            return round(float(v) / 1e8, 2)
        except (ValueError, TypeError):
            return None
    cur = r.get("CURRENCY") or r.get("CURRENCY_ABBR") or ("HKD" if market == "HK" else "USD")
    out = {"valuation": {"币种": cur, "EPS": f("BASIC_EPS", 3), "每股净资产BPS": f("BPS", 2),
                         "note": "港美股 PE/PB 未直出, 如需用 get_quote 现价 / EPS、/ BPS 估算"},
           "financials": {"报告期": str(r.get("REPORT_DATE") or "")[:10],
                          "营业收入亿": yi("OPERATE_INCOME"), "营收同比增长%": f("OPERATE_INCOME_YOY", 1),
                          "归母净利润亿": yi(profit_key), "净利同比增长%": f(profit_yoy, 1),
                          "毛利率%": f("GROSS_PROFIT_RATIO", 1), "净利率%": f("NET_PROFIT_RATIO", 1),
                          "ROE%": f(roe_key, 2), "资产负债率%": f("DEBT_ASSET_RATIO", 1)},
           "note": f"金额单位亿{cur}; 港股取最近报告期, 美股取最近年报; 同比为 YoY。"}
    _fin_cache[ck] = (out, _t.time())
    return out


async def _tool_fundamentals(code: str) -> dict:
    """个股基本面+估值: 营收/净利及同比、ROE/毛利率/净利率、资产负债率、EPS, A股另含 PE/PB/总市值/行业。支持 A/港/美股。"""
    from services.market_data import normalize_stock_code, is_a_share, split_stock_code
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        market, symbol = split_stock_code(raw)
        if market in ("HK", "US"):
            out = await asyncio.to_thread(_fetch_hkus_fundamentals_sync, market, symbol)
            return out if out else {"error": "港美股基本面暂不可达"}
        return {"error": "基本面暂不支持该市场"}
    bare = _norm_code(code)
    val, fin = await asyncio.gather(
        asyncio.to_thread(_fetch_valuation_sync, bare),
        asyncio.to_thread(_fetch_financials_sync, bare),
        return_exceptions=True,
    )
    val = val if isinstance(val, dict) else {}
    fin = fin if isinstance(fin, dict) else {}
    if not val and not fin:
        return {"error": "基本面暂不可达(数据源抖动)"}
    return {"valuation": val, "financials": fin,
            "note": "营收/净利为报告期累计值, 同比增长是 YoY; PE_TTM/PB 为当前估值, 已是真实倍数(非百分比)。"}


_COMMODITY_CN = {"沪金": "黄金", "沪银": "白银", "沪铜": "铜", "沪铝": "铝", "沪锌": "锌",
                 "沪铅": "铅", "沪镍": "镍", "沪锡": "锡"}


async def _tool_commodity(code: str) -> dict:
    """个股关联的大宗商品期货价(有色: 铜/铝/金/锌/镍/锡 等走上期所连续合约)。看商品价能否解释有色股涨跌。"""
    from services.market_data import normalize_stock_code, is_a_share, get_commodity_for_stock
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "商品价仅支持 A 股"}
    try:
        c = await get_commodity_for_stock(raw)
    except Exception as e:
        return {"error": str(e)}
    if not c:
        return {"mapped": False, "note": "该票无对应交易所期货(钨/锑/稀土/锂等小金属无连续合约), 商品价不可得; 看现货价请结合新闻/板块。"}
    return {"mapped": True, "commodity": c.get("label"), "price": c.get("price"),
            "change_pct": c.get("change_pct"), "prev": c.get("prev"),
            "note": f"{c.get('label')}期货(上期所连续合约)现价; 有色股价常与对应金属价同步, 可佐证涨跌驱动。"}


_peers_cache: dict = {}


def _fetch_peers_sync(code: str) -> dict:
    """同行横向对比(东财): 先取个股主行业板块(f198=BKxxxx), 再拉该板块成分股的涨幅/PE/PB/主力净流入。"""
    import requests as _rq
    import time as _t
    ck = f"peers_{code}"
    c = _peers_cache.get(ck)
    if c and _t.time() - c[1] < 300:
        return c[0]
    secid = _em_secid(code)
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"]
    bk = ind = None
    for i in range(6):
        try:
            d = _rq.get(f"https://{hosts[i % len(hosts)]}/api/qt/stock/get", timeout=7, headers=hdr,
                        params={"secid": secid, "fields": "f127,f198"}).json().get("data")
            if d and d.get("f198"):
                bk = d.get("f198"); ind = d.get("f127"); break
        except Exception:
            _t.sleep(0.3)
    if not bk:
        return {"error": "拿不到所属行业板块"}
    params = {"pn": "1", "pz": "30", "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": "f62",
              "fs": f"b:{bk}", "fields": "f12,f14,f3,f9,f23,f62"}
    for i in range(9):
        try:
            diff = (_rq.get(f"https://{hosts[i % len(hosts)]}/api/qt/clist/get", params=params, timeout=7,
                            headers=hdr).json().get("data") or {}).get("diff")
            if diff:
                rows = []
                for x in diff:
                    try:
                        rows.append({"code": x.get("f12"), "name": x.get("f14"),
                                     "涨跌幅": x.get("f3"),
                                     "PE": x.get("f9") if isinstance(x.get("f9"), (int, float)) and x.get("f9") not in (0, "-") else None,
                                     "PB": x.get("f23") if isinstance(x.get("f23"), (int, float)) and x.get("f23") not in (0, "-") else None,
                                     "主力净流入亿": round(float(x.get("f62") or 0) / 1e8, 2)})
                    except (ValueError, TypeError):
                        continue
                # 按主力净流入排序, 取前12, 但确保目标票在内
                rows.sort(key=lambda r: (r["主力净流入亿"] or -999), reverse=True)
                top = rows[:12]
                if code not in [r["code"] for r in top]:
                    me = [r for r in rows if r["code"] == code]
                    top = (top[:11] + me) if me else top
                out = {"行业": ind, "板块": bk, "peers": top,
                       "note": f"同属【{ind}】板块, 按今日主力净流入排序; PE/PB 横向比可看谁贵谁便宜, 涨跌幅看谁领涨。"}
                _peers_cache[ck] = (out, _t.time())
                return out
        except Exception:
            _t.sleep(0.3)
    return {"error": "板块成分暂不可达"}


async def _tool_peers(code: str) -> dict:
    """同行横向对比: 同行业板块成分股的涨跌幅/PE/PB/主力净流入对照, 看目标票在同业里贵不贵、强不强、资金偏好谁。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    if not is_a_share(normalize_stock_code(_norm_code(code))):
        return {"error": "同行对比仅支持 A 股"}
    return await asyncio.to_thread(_fetch_peers_sync, _norm_code(code))


_holders_cache: dict = {}


def _fetch_shareholders_sync(code: str) -> dict:
    """十大流通股东(akshare, 最近报告期) + 北向(香港中央结算)持股变动 + 未来解禁。"""
    import time as _t
    import datetime as _dt
    ck = f"hold_{code}"
    c = _holders_cache.get(ck)
    if c and _t.time() - c[1] < 86400:
        return c[0]
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    sym = ("sh" if code[:1] in ("6", "9", "5") else "sz") + code
    # 找最近一个有数据的报告期(往回试 5 个季度末)
    today = _dt.date.today()
    cands = []
    y = today.year
    for yy in (y, y - 1):
        for md in ("1231", "0930", "0630", "0331"):
            d = f"{yy}{md}"
            if d <= today.strftime("%Y%m%d"):
                cands.append(d)
    cands = sorted(set(cands), reverse=True)[:5]
    holders, rdate, north = [], None, None
    for d in cands:
        try:
            df = ak.stock_gdfx_free_top_10_em(symbol=sym, date=d)
            if df is not None and not df.empty:
                rdate = d
                for _, r in df.iterrows():
                    nm = str(r.get("股东名称") or "")
                    rec = {"name": nm, "type": r.get("股东性质"),
                           "占流通股%": round(float(r.get("占总流通股本持股比例") or 0), 2),
                           "增减": r.get("增减"), "变动比率%": round(float(r.get("变动比率")), 2) if r.get("变动比率") == r.get("变动比率") else None}
                    holders.append(rec)
                    if "香港中央结算" in nm:
                        north = rec
                break
        except Exception:
            continue
    # 未来解禁
    unlock = []
    try:
        dfq = ak.stock_restricted_release_queue_em(symbol=code)
        if dfq is not None and not dfq.empty:
            for _, r in dfq.iterrows():
                dt = r.get("解禁时间")
                if dt and hasattr(dt, "strftime") and dt >= today:
                    unlock.append({"date": dt.strftime("%Y-%m-%d"),
                                   "类型": r.get("限售股类型"),
                                   "占流通市值%": round(float(r.get("占流通市值比例") or 0) * 100, 2),
                                   "解禁数量万股": round(float(r.get("实际解禁数量") or 0) / 1e4, 1)})
            unlock.sort(key=lambda x: x["date"])
    except Exception:
        pass
    out = {"报告期": rdate, "top10_circulating": holders[:10],
           "north_bound": north or {"note": "前十大流通股东无北向(香港中央结算)身影"},
           "upcoming_unlock": unlock[:5] if unlock else {"note": "未来无限售解禁(基本全流通)"},
           "note": "增减看主要股东在加仓还是减持; 北向=香港中央结算; 解禁占流通市值比越大、时间越近, 潜在抛压越大。"}
    _holders_cache[ck] = (out, _t.time())
    return out


async def _tool_shareholders(code: str) -> dict:
    """筹码面: 十大流通股东及增减、北向(香港中央结算)持股变动、未来限售解禁(抛压)。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    if not is_a_share(normalize_stock_code(_norm_code(code))):
        return {"error": "股东/解禁仅支持 A 股"}
    try:
        return await asyncio.to_thread(_fetch_shareholders_sync, _norm_code(code))
    except Exception as e:
        return {"error": f"股东数据获取失败: {e}"}


_ann_cache: dict = {}
# 值得重点标注的公告类型(资金/事件驱动)
_ANN_KEY_TYPES = ["分红", "回购", "增持", "减持", "业绩", "预增", "预减", "重组", "收购", "中标",
                  "股权激励", "定增", "并购", "重大资产", "股份转让", "实控人", "破产", "退市", "问询", "立案"]


def _fetch_announcements_sync(code: str, limit: int = 12) -> dict:
    """个股公告(东财 np-anotice-stock): 标题/日期/类型。结构化, 比新闻更权威(分红回购/业绩/重组/股权激励等)。"""
    import requests as _rq
    import time as _t
    ck = f"ann_{code}"
    c = _ann_cache.get(ck)
    if c and _t.time() - c[1] < 1800:
        return c[0]
    try:
        j = _rq.get("https://np-anotice-stock.eastmoney.com/api/security/ann",
                    params={"sr": "-1", "page_size": str(max(limit, 15)), "page_index": "1",
                            "ann_type": "A", "client_source": "web", "stock_list": code},
                    timeout=8, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}).json()
    except Exception:
        return {"error": "公告源不可达"}
    rows = (j.get("data") or {}).get("list") or []
    if not rows:
        return {"announcements": [], "note": "近期无公告"}
    out = []
    for a in rows[:limit]:
        types = [c.get("column_name") for c in (a.get("columns") or []) if c.get("column_name")]
        title = a.get("title") or ""
        key = any(any(kt in (t or "") for kt in _ANN_KEY_TYPES) for t in types) or any(kt in title for kt in _ANN_KEY_TYPES)
        out.append({"date": str(a.get("notice_date") or "")[:10], "title": title,
                    "类型": types, "key": key})
    res = {"announcements": out,
           "note": "结构化公告; key=true 是分红/回购/增减持/业绩/重组/股权激励等资金或事件驱动型, 优先看。"}
    _ann_cache[ck] = (res, _t.time())
    return res


async def _tool_announcements(code: str) -> dict:
    """个股公告(分红/回购/增减持/业绩/重组/股权激励/关联交易等), 比新闻权威。看公司层面有没有实质事件。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    if not is_a_share(normalize_stock_code(_norm_code(code))):
        return {"error": "公告仅支持 A 股(港美股可用 get_news 看回购等动态)"}
    return await asyncio.to_thread(_fetch_announcements_sync, _norm_code(code))


async def _tool_get_holdings() -> dict:
    try:
        hs = await _active_holdings()
        return {"holdings": [{"code": h.get("stock_code"), "name": h.get("stock_name"),
                              "shares": h.get("shares")} for h in hs],
                "note": "仅当前在持(已清仓的票不在此列, 按综合成本法现算 shares>0)。"}
    except Exception as e:
        return {"error": str(e)}


async def _tool_sector_momentum(days: int = 10) -> dict:
    """板块趋势矩阵: 各行业近 N 日累计涨跌/连涨动能/净流入 → 看动量是否延续(动量风格) 还是冲高回落(退潮/反转)。"""
    try:
        from services.sector_matrix import get_sector_matrix
        m = await get_sector_matrix(days=int(days or 10))
        rows = m.get("rows") or []
        if not rows:
            return {"error": "板块矩阵暂无数据"}
        def brief(r):
            return {"板块": r["name"], "今日": r.get("today_pct"), f"近{m.get('days')}日累计": r.get("cum_pct"),
                    "连涨天": r.get("streak"), "净流入亿": r.get("net_inflow")}
        return {"days": m.get("days"), "intraday": m.get("intraday"),
                "走强top": [brief(r) for r in rows[:8]],
                "退潮bottom": [brief(r) for r in rows[-5:]]}
    except Exception as e:
        return {"error": str(e)}


_concept_cache: dict = {}


def _fetch_hot_concepts_sync(top: int = 15) -> list[dict]:
    """今日东财概念板块涨幅榜(带主力净流入)。这是 量化/游资正在冲的'概念'粒度
    (如 CPO/HBM/先进封装/玻璃基板…), 比行业级更细。
    akshare 走死分片 79.push2 被墙 → 直连可达 host(push2delay 优先)+ 重试轮换。"""
    import requests as _rq
    import time as _t
    ck = f"concepts_{top}"
    c = _concept_cache.get(ck)
    if c and _t.time() - c[1] < 300:
        return c[0]
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com",
             "1.push2.eastmoney.com", "50.push2.eastmoney.com"]
    params = {"pn": "1", "pz": str(max(top, 30)), "po": "1", "np": "1", "fltt": "2",
              "invt": "2", "fid": "f3", "fs": "m:90 t:3",
              "fields": "f12,f14,f3,f62,f104,f105"}
    for i in range(12):
        host = hosts[i % len(hosts)]
        try:
            r = _rq.get(f"https://{host}/api/qt/clist/get", params=params, timeout=7)
            diff = (r.json().get("data") or {}).get("diff")
            if diff:
                out = []
                for x in diff[:top]:
                    try:
                        out.append({"概念": x.get("f14"), "涨跌幅": float(x.get("f3")),
                                    "主力净流入亿": round(float(x.get("f62") or 0) / 1e8, 2),
                                    "涨家": x.get("f104"), "跌家": x.get("f105")})
                    except (ValueError, TypeError):
                        continue
                if out:
                    _concept_cache[ck] = (out, _t.time())
                    return out
        except Exception:
            _t.sleep(0.3)
    return []


async def _tool_hot_concepts(top: int = 15) -> dict:
    """今日热门概念榜(概念粒度, 比行业细): 涨幅 + 主力净流入。看量化/资金在冲哪个具体概念。"""
    out = await asyncio.to_thread(_fetch_hot_concepts_sync, int(top or 15))
    if not out:
        return {"error": "概念榜暂不可达(东财源抖动), 请改用行业级 get_sector_momentum"}
    return {"top_concepts": out, "note": "按今日涨幅排序; 主力净流入正=资金流入"}


async def _tool_hot_rank() -> dict:
    """资金人气榜(东财): 资金/散户关注度最高的个股, 标出哪些在用户持仓里。看资金主线/抱团方向。"""
    try:
        from api.market_routes import hot_rank
        r = await hot_rank(top=20)
        items = [{"name": x.get("name"), "code": x.get("code"), "rank": x.get("rank"),
                  "mine": x.get("mine")} for x in (r.get("items") or [])]
        return {"top": items, "mine": [x.get("name") for x in (r.get("mine") or [])]}
    except Exception as e:
        return {"error": str(e)}


_POLICY_KW = [
    "央行", "降准", "降息", "逆回购", "MLF", "LPR", "国债", "专项债", "财政", "货币政策",
    "证监会", "银保监", "金融监管", "国常会", "国务院", "政治局", "发改委", "工信部", "部委",
    "政策", "监管", "调控", "刺激", "新政", "出口管制", "关税", "制裁", "实体清单",
    "反垄断", "反内卷", "供给侧", "去产能", "收储", "汇率", "稳增长", "会议", "规划", "意见",
]


async def _tool_market_news(limit: int = 40) -> dict:
    """全市场财经快讯(东财+财联社+同花顺), 含政策面/国家调控。用来把宏观政策、监管动向、
    产业政策、央行财政、重要会议等市场背景因素纳入分析。policy_news 是按关键词筛出的政策相关条目。"""
    try:
        from api.news_routes import market_news
        mn = await market_news()
        items = (mn.get("items") or [])[:max(limit, 40)]
        def is_pol(t):
            return any(k in (t or "") for k in _POLICY_KW)
        heads = [{"title": it.get("title"), "time": it.get("time"), "source": it.get("source")} for it in items]
        policy = [h for h in heads if is_pol(h["title"])]
        return {"policy_news": policy[:18], "headlines": heads[:limit], "note": "policy_news=政策/调控相关筛选; headlines=全部要闻(时间倒序)"}
    except Exception as e:
        return {"error": str(e)}


async def _tool_market_sentiment() -> dict:
    try:
        from api.market_routes import market_sentiment
        s = await market_sentiment()
        return {"mood": s.get("mood"), "mood_desc": s.get("mood_desc"),
                "n_zt": s.get("n_zt"), "n_dt": s.get("n_dt"), "zbl_rate": s.get("zbl_rate"),
                "max_lianban": s.get("max_lianban"), "money_effect": s.get("money_effect"),
                "hot_sectors": [h.get("name") for h in (s.get("hot_sectors") or [])[:6]]}
    except Exception as e:
        return {"error": str(e)}


_TOOLS = [
    {"name": "resolve_stock", "description": "把股票名字或代码解析成标准代码+名称。用户报名字(如'中钨高新')时先调它拿代码。",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "股票名字或代码"}}, "required": ["query"]}},
    {"name": "get_quote", "description": "查个股实时行情: 现价/当日涨跌幅/开高低/成交额/换手。code 直接用 resolve_stock 返回的 code 原样传(A股是裸6位如 600667 / 000657; 港美股 HK.00700 / US.AAPL), 不要自己加 sh/sz 前缀。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_trend", "description": "查个股近 N 个交易日走势: 累计涨跌/逐日涨跌/上涨天数。支持 A 股/港股/美股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "days": {"type": "integer", "description": "默认20"}}, "required": ["code"]}},
    {"name": "get_news", "description": "查个股最近新闻(标题+摘要+时间), 用来找涨跌的消息面原因。支持 A股/港股/美股(东财)。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_announcements", "description": "查个股公告(分红/回购/增减持/业绩预告/重组/股权激励/关联交易等), 结构化且比新闻权威。看公司层面有没有实质事件驱动。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_fund_flow", "description": "查个股主力资金流(谁在买/卖): 今日主力/超大单/大单/中单/小单净额(亿) + 近几日主力净流入趋势。回答'为什么涨/跌、是不是主力在拉、资金进还是出'的关键。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_lhb", "description": "龙虎榜: 传 code→该股近期是否上榜及净买额/机构还是游资席位/上榜原因(看是谁在拉); 不传 code→最近交易日资金净买额榜(主力/游资当天在打哪些票, 看资金主线)。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选; 留空看全市场榜"}}}},
    {"name": "get_stock_concepts", "description": "查个股所属行业/概念板块 + 核心题材。判断'这只票属于哪个概念、有没有踩在当下资金主线/热门概念上'时用; 可与 get_hot_concepts 交叉印证。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_fundamentals", "description": "查个股基本面+估值: 营收/净利及同比增速、ROE/毛利率/净利率、资产负债率、每股收益, 以及 PE(TTM)/PB/总市值/行业。回答'这票贵不贵、业绩好不好、盈利质地、有没有业绩拐点'时用。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_commodity", "description": "查个股关联的大宗商品期货价(有色金属股: 铜/铝/金/锌/镍/锡 走上期所连续合约)。判断有色股涨跌是不是金属价驱动时用; 钨/锑/稀土/锂等小金属无交易所合约会返回不可得。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_peers", "description": "同行横向对比: 同行业板块成分股的涨跌幅/PE/PB/主力净流入对照表。回答'同业里它贵不贵、谁领涨、资金更偏好谁、龙头是谁'时用; 可配合 get_fundamentals 看相对估值。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_shareholders", "description": "筹码面: 十大流通股东及增减持、北向(香港中央结算)持股变动、未来限售解禁(抛压)。回答'谁在持股、控股股东/国家队/北向在加还是减、有没有解禁压力'时用。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_holdings", "description": "查用户当前持仓列表(代码/名称/股数), 用于回答跟用户持仓的关系。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_market_sentiment", "description": "查大盘打板情绪(涨停数/连板高度/炸板率/赚钱效应/热点板块), 判断是个股原因还是大盘普涨普跌; 也用于判断市场风格(打板赚钱效应高=追涨/动量有效; 炸板率高+亏钱效应=高位分歧/反转)。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_sector_momentum", "description": "板块趋势矩阵: 各行业近N日累计涨跌/连涨动能/净流入。看哪些板块在持续走强(动量延续)、哪些冲高回落(退潮), 判断市场是动量风格还是高低切/轮动, 资金主线在哪。days 默认10。",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "get_hot_rank", "description": "资金人气榜(东财): 关注度最高的个股, 标出哪些在用户持仓。看资金主线/抱团方向。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_hot_concepts", "description": "今日热门概念板块榜(概念粒度, 比行业更细, 如 CPO/HBM/先进封装/玻璃基板/固态电池等): 涨幅+主力净流入。回答'量化/资金这几天在冲哪个具体概念、概念怎么切'时用它。",
     "input_schema": {"type": "object", "properties": {"top": {"type": "integer", "description": "默认15"}}}},
    {"name": "get_market_news", "description": "全市场财经快讯(含政策面/国家调控: 货币财政、央行、证监会/部委监管、产业政策、行业调控、出口管制/关税、国常会/政治局等重要会议)。分析市场背景、判断政策驱动/调控影响时必看; policy_news 是政策相关筛选。",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "默认40"}}}},
    # Anthropic 服务端联网搜索: 碰到本地工具查不到/可能过期的事实(海外公司是否上市/IPO/代码/政策/最新消息)用它核实, 不要凭记忆嘴硬。
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 4},
]

_EXECUTORS = {
    "resolve_stock": lambda a: _tool_resolve_stock(a.get("query", "")),
    "get_quote": lambda a: _tool_get_quote(a.get("code", "")),
    "get_trend": lambda a: _tool_get_trend(a.get("code", ""), a.get("days", 20)),
    "get_news": lambda a: _tool_get_news(a.get("code", "")),
    "get_announcements": lambda a: _tool_announcements(a.get("code", "")),
    "get_fund_flow": lambda a: _tool_fund_flow(a.get("code", "")),
    "get_lhb": lambda a: _tool_lhb(a.get("code", "")),
    "get_stock_concepts": lambda a: _tool_stock_concepts(a.get("code", "")),
    "get_fundamentals": lambda a: _tool_fundamentals(a.get("code", "")),
    "get_commodity": lambda a: _tool_commodity(a.get("code", "")),
    "get_peers": lambda a: _tool_peers(a.get("code", "")),
    "get_shareholders": lambda a: _tool_shareholders(a.get("code", "")),
    "get_holdings": lambda a: _tool_get_holdings(),
    "get_market_sentiment": lambda a: _tool_market_sentiment(),
    "get_sector_momentum": lambda a: _tool_sector_momentum(a.get("days", 10)),
    "get_hot_rank": lambda a: _tool_hot_rank(),
    "get_hot_concepts": lambda a: _tool_hot_concepts(a.get("top", 15)),
    "get_market_news": lambda a: _tool_market_news(a.get("limit", 40)),
}


def _active_tools() -> list:
    """web_search 是 Anthropic 服务端工具, 只有官方端点支持; 若切到 DeepSeek/硅基流动等
    非 Anthropic 厂商, 必须去掉它, 否则请求会被对方拒绝。其余自定义工具各厂商通用。"""
    try:
        if _llm._is_anthropic_official():
            return _TOOLS
    except Exception:
        pass
    return [t for t in _TOOLS if t.get("type") != "web_search_20250305"]

_SYSTEM = (
    "你是市场&个股解读助手。用户自由提问: 个股为什么涨跌/消息面/跟持仓关系, 以及【市场风格】类问题"
    "(这周市场在奖励什么打法、是动量追涨还是低吸反转、是题材轮动还是抱团、高低切迹象、资金主线在哪、情绪处在什么周期)。\n"
    "工具: resolve_stock(名字转代码)、get_quote(个股实时行情)、get_trend(个股近N日走势)、get_news(个股新闻)、"
    "get_fund_flow(个股主力资金流:谁在买卖)、get_lhb(龙虎榜:游资/机构席位)、get_stock_concepts(个股所属概念板块)、"
    "get_fundamentals(基本面+估值:营收净利/ROE/PE/PB)、get_commodity(关联金属期货价)、"
    "get_holdings(用户持仓)、get_market_sentiment(大盘打板情绪)、get_sector_momentum(板块趋势矩阵:动量/退潮/资金流)、"
    "get_hot_concepts(热门概念榜)、get_hot_rank(资金人气榜)、get_market_news(政策面)。\n"
    "【个股问题】先 resolve_stock 拿代码, 再 get_quote+get_trend; 找涨跌原因务必看 get_fund_flow(主力资金是进是出、谁在拉)"
    "+get_news(消息面)+get_announcements(公司公告: 分红回购/业绩预告/重组/股权激励等实质事件, 比新闻权威), 异动明显时 get_lhb(有没有上龙虎榜、游资还是机构在打); 用 get_stock_concepts 看它属于哪个概念, "
    "再与 get_hot_concepts/get_sector_momentum 交叉看是不是踩在当下资金主线上; 需要时 get_market_sentiment 判断个股事件还是大盘普涨跌; "
    "若该票/所属板块对政策敏感(有色/小金属/地产/半导体/医药/军工/新能源/平台经济等), 还要调 get_market_news 看有没有政策催化或调控压制。\n"
    "【基本面/估值】问'贵不贵、业绩好不好、盈利质地、有没有业绩拐点'时调 get_fundamentals"
    "(营收/净利及同比、ROE/毛利率/净利率、资产负债率、PE/PB/总市值); 即便只问涨跌, 涉及'涨这么多还能不能撑、估值高不高'也该看一眼基本面对照位置。"
    "有色/资源股涨跌还可调 get_commodity 看对应金属期货价(铜铝金锌镍锡)是不是同步驱动。\n"
    "【同行对比】问'同业里贵不贵、谁是龙头、资金更偏好谁、相对估值'时调 get_peers(同行业 PE/PB/涨幅/主力净流入对照), 配合 get_fundamentals 判断相对位置。\n"
    "【筹码面】问'谁在持股、控股股东/国家队/北向在加减仓、有没有解禁抛压'时调 get_shareholders(十大流通股东增减+北向变动+未来解禁)。\n"
    "(港美股可用 get_quote+get_trend+get_news+get_fundamentals; 资金流/龙虎榜/概念/同行/筹码/商品/公告 这些仅 A 股, 港美股查不到就如实说。)\n"
    "【'能不能进/明天怎么样/还能拿吗'这类问题】不要直接拒绝了事。照样把客观分析做全"
    "(为什么涨跌、消息面、政策面、走势位置、跟持仓关系、双向风险都摆出来), 只是【不给买卖结论】——"
    "结尾一句'方向性的进出/仓位得你自己定, 我只给客观信息'。决策依据给足, 但不替用户拍板。\n"
    "【多轮追问】对话可能有上文(前面聊过某只票/某个板块)。用户说'它/这只/明天呢'这类指代时, 顺着上文的标的继续, 别重新问是哪只。\n"
    "【市场风格问题】用 get_market_sentiment(打板赚钱效应高=追涨/动量有效; 炸板率高+亏钱效应=高位分歧/反转占优) + "
    "get_sector_momentum(连涨板块多=动量延续; 普遍冲高回落=退潮/高低切) + get_hot_concepts(概念主攻) + get_hot_rank(资金主线/抱团) 综合判断, "
    "用具体数字描述'市场这周在奖励什么行为、惩罚什么行为、资金往哪走'。这是客观的市场逻辑分析, 不是策略推荐。\n"
    "【政策面/国家调控——市场背景必看】分析市场背景、或个股/板块异动疑似政策驱动时, 必须调 get_market_news 看政策面"
    "(货币/财政: 降准降息/LPR/逆回购/专项债; 监管: 证监会/部委/反垄断/平台经济; 产业政策与行业调控: 收储/去产能/反内卷/限价/补贴; "
    "地缘: 出口管制/关税/制裁/实体清单; 重要会议: 国常会/政治局/发改委部署), 必要时再 web_search 补最新政策细节。"
    "要点出'这波行情/这个板块背后有没有政策催化或调控压制'(如 收储拉动有色、AI/算力产业政策、地产/化债/反内卷、关税扰动出口链), 用快讯标题/日期举证。\n"
    "【分析框架·一线打板资金视角】(客观套用, 不点名出处, 不据此给操作建议):\n"
    "  · 量化/游资以【板块/概念】为维度运作, 不是单票。判断市场=判断资金这几天在冲哪个板块概念、节奏多快"
    "(概念可能一两天就切, 如从 A 概念直接换到 B 概念)。要找出资金主线板块 + 有没有概念轮动切换。\n"
    "  · 概念粒度优先用 get_hot_concepts(能拿到 CPO/HBM/先进封装/玻璃基板 这种具体概念名 + 主力净流入), "
    "它比 get_sector_momentum 的行业级更细, 正是判断'量化在冲哪个概念'的关键; 两个结合看(概念找主攻方向, 行业动量看延续性)。\n"
    "  · 个股位置分层看'看逻辑 vs 纯资金博弈': 短线打板股 3板以下看逻辑(题材/催化/空间扎不扎实)、3板以上逻辑让位转纯资金接力; "
    "趋势股 涨幅1倍(100%)以内看逻辑、超1倍转纯资金博弈。即低位看逻辑、高位看资金, 点出领涨标的当前在哪一段。\n"
    "  · 据此描述: 资金的板块主线、概念切换的轮动节奏、领涨票在'看逻辑'还是'资金博弈'区。\n"
    "  · 数据粒度: get_hot_concepts 给到概念级(今日榜), get_sector_momentum 给行业级近N日动量, 配合用。"
    "概念榜是当日快照, '这几天怎么切'的多日轨迹要结合行业动量推断; 概念榜偶发不可达(东财抖动)时就退回行业级, 并说明。绝不硬编榜上没有的概念名。\n"
    "每个结论都要有工具数据支撑。\n"
    "【硬规则】只做客观解读与市场逻辑分析(市场在奖励什么/为什么动/什么消息), 严禁任何面向用户的操作建议: "
    "不许出现 你该买/该卖/该用XX策略去操作/加仓/减仓/能不能追/还能不能拿/目标价/止损/现在适合。"
    "描述'市场在奖励动量'可以, 但不许说'所以你该追涨'。料不足就直说不确定, 绝不编造新闻或数字。\n"
    "【知识边界·先搜再答, 别嘴硬】你的训练知识有截止日、可能已过期(尤其海外公司是否上市/最新IPO/重组/政策/某公司近况)。"
    "碰到本地工具(行情/板块/概念)查不到、或时效性强、或你不确定的事实, 不许凭记忆下肯定结论——先用 web_search 联网核实; "
    "搜到结果就以搜到的为准(并可在文中标明来源/日期), 若搜到该标的有代码就再用 get_quote 查实时行情。"
    "只有 web_search 也查不到时, 才说'查不到/无法确认, 建议你自行核实'。宁可去搜或说不知道, 绝不编一个确定的答案。\n"
    "回答用简体中文, 简洁直给, 分点列证据(数字), 该下的客观结论就下——但只对工具数据支撑的结论自信。"
)


_TOOL_CN = {
    "resolve_stock": "解析代码", "get_quote": "查行情", "get_trend": "查走势",
    "get_news": "查新闻", "get_announcements": "查公告", "get_fund_flow": "查资金流", "get_lhb": "查龙虎榜",
    "get_stock_concepts": "查所属概念", "get_fundamentals": "查基本面", "get_commodity": "查商品价",
    "get_peers": "同行对比", "get_shareholders": "查股东解禁",
    "get_holdings": "看持仓", "get_market_sentiment": "看大盘情绪",
    "get_sector_momentum": "看板块动量", "get_hot_rank": "看资金热度",
    "get_hot_concepts": "看热门概念", "get_market_news": "看政策快讯", "web_search": "联网搜索",
}


def _seed_messages(question: str, history: list | None) -> list:
    """把前端传来的多轮历史(只含 role+text 的简化对话)接到当前问题前面, 让 agent 有上下文。"""
    msgs = []
    for h in (history or [])[-8:]:           # 最多带最近 8 条, 控制 token
        role = h.get("role")
        content = (h.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            msgs.append({"role": role, "content": content[:4000]})
    msgs.append({"role": "user", "content": question})
    return msgs


async def ask_stock_stream(question: str, history: list | None = None):
    """流式版: 边跑边 yield 事件 (step/answer/done/error), 供 SSE 推给前端。
    每轮 LLM 调用之间 yield 工具步骤, 步骤实时出现; 末轮文本作为答案。
    history: 前端传的多轮对话历史 [{role, content}], 让 agent 有上下文(支持追问)。"""
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "error": "空问题"}
        return
    messages = _seed_messages(question, history)
    for rnd in range(_MAX_ROUNDS):
        try:
            resp = await asyncio.to_thread(
                _llm.call_claude_messages, messages, _SYSTEM, _MODEL, 2048, _active_tools())
        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        # 服务端联网搜索(web_search)已由 API 执行完, 这里只把"联网搜索"作为步骤推给前端
        for b in content:
            if b.get("type") == "server_tool_use" and b.get("name") == "web_search":
                yield {"type": "step", "tool": "web_search", "label": "联网搜索",
                       "arg": (b.get("input") or {}).get("query", "")}
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            yield {"type": "answer", "text": text.strip()}
            yield {"type": "done"}
            return
        # 先把这一轮模型的简短思考文本(若有)推出去当“正在做什么”的旁白
        think = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if think:
            yield {"type": "thought", "text": think[:120]}
        for tu in tus:
            yield {"type": "step", "tool": tu.get("name"),
                   "label": _TOOL_CN.get(tu.get("name"), tu.get("name")),
                   "arg": (tu.get("input") or {}).get("query") or (tu.get("input") or {}).get("code") or ""}
        results = []
        for tu in tus:
            try:
                fn = _EXECUTORS.get(tu.get("name"))
                out = await fn(tu.get("input") or {}) if fn else {"error": "未知工具"}
            except Exception as e:
                out = {"error": str(e)}
            results.append({"type": "tool_result", "tool_use_id": tu.get("id"),
                            "content": _json.dumps(out, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})
    yield {"type": "answer", "text": "（分析步数超限, 请换个问法或更具体）"}
    yield {"type": "done"}


async def ask_stock(question: str, history: list | None = None) -> dict:
    """跑 agent loop, 返回 {answer, tools_used, rounds}。"""
    question = (question or "").strip()
    if not question:
        return {"answer": "", "error": "空问题"}
    messages = _seed_messages(question, history)
    tools_used: list[str] = []
    for rnd in range(_MAX_ROUNDS):
        try:
            resp = await asyncio.to_thread(
                _llm.call_claude_messages, messages, _SYSTEM, _MODEL, 2048, _active_tools())
        except Exception as e:
            return {"answer": "", "error": str(e), "tools_used": tools_used, "rounds": rnd}
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            return {"answer": text.strip(), "tools_used": tools_used, "rounds": rnd + 1}
        results = []
        for tu in tus:
            name = tu.get("name", "")
            tools_used.append(name)
            try:
                fn = _EXECUTORS.get(name)
                out = await fn(tu.get("input") or {}) if fn else {"error": f"未知工具 {name}"}
            except Exception as e:
                out = {"error": str(e)}
            results.append({"type": "tool_result", "tool_use_id": tu.get("id"),
                            "content": _json.dumps(out, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})
    return {"answer": "（分析步数超限, 请换个问法或更具体）", "tools_used": tools_used, "rounds": _MAX_ROUNDS}
