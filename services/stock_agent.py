"""问股票为什么涨/跌 — 挂工具的小 agent。

LLM 自己决定调哪些工具(查行情/走势/新闻/持仓/大盘情绪)拿数据, 再总结涨跌原因。
自由问答。硬规则: 仅做客观解读, 买卖/操作决策交给用户。
"""
from __future__ import annotations
import asyncio
import json as _json
import re as _re
import time as _time

import services.llm_client as _llm

_MODEL = "claude-opus-4-8"
_MAX_ROUNDS = 14

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
        hold_days = open_date = None
        try:
            acts = await get_position_actions(code, limit=500)
            if acts:
                rate, mn = await _broker_stock_fee(h.get("broker"))
                st = compute_position_state(acts, stock_code=code, commission_rate=rate, commission_min=mn)
                shares = float(st.get("shares") or 0)
                hold_days = st.get("weighted_days")
                lots = st.get("lots") or []
                if lots:
                    open_date = min(l["trade_date"] for l in lots)  # 当前段最早一笔=开仓日
        except Exception:
            pass  # ledger 算不出就退回表里的 shares
        if shares > 0:
            out.append({**h, "shares": shares, "hold_days": hold_days,
                        "open_date": _date_with_weekday(open_date)})
    return out


def _date_with_weekday(d: str | None) -> str | None:
    """把 'YYYY-MM-DD' 标上星期, 省得 LLM 自己换算星期出错(如把周五说成周四)。"""
    if not d:
        return d
    import datetime
    try:
        dt = datetime.date.fromisoformat(d[:10])
        wk = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][dt.weekday()]
        return f"{d}({wk})"
    except Exception:
        return d


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


def _a_limit_pct(bare: str, name: str) -> float | None:
    """A股当日涨跌停幅度: ST 5%, 创业板/科创板 20%, 北交所 30%, 主板 10%。港美股无涨跌停→None。"""
    if not (len(bare) == 6 and bare.isdigit()):
        return None
    if "ST" in (name or "").upper():
        return 0.05
    if bare[:1] in ("8", "4"):          # 北交所
        return 0.30
    if bare[:3] == "688" or bare[:2] == "30":   # 科创板 / 创业板
        return 0.20
    return 0.10                          # 沪深主板


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
    out = {
        "code": code, "name": name,
        "price": q.get("price"), "change_pct": q.get("change_pct"),
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "prev_close": q.get("prev_close"), "amount": q.get("amount"),
        "turnover_rate": q.get("turnover_rate"),
    }
    # 涨停/跌停 + 封板/炸板/冲高回落 判断 (仅 A 股)
    bare = code.split(".")[-1]
    pct = _a_limit_pct(bare, name)
    prev = float(q.get("prev_close") or 0)
    price = float(q.get("price") or 0)
    high = float(q.get("high") or 0)
    low = float(q.get("low") or 0)
    if pct and prev > 0 and price > 0:
        lu = round(prev * (1 + pct), 2)
        ld = round(prev * (1 - pct), 2)
        out["limit_up"], out["limit_down"] = lu, ld
        near = 0.005  # 容差
        touched_up = high >= lu - near
        sealed_up = price >= lu - near
        touched_dn = low <= ld + near and low > 0
        sealed_dn = price <= ld + near
        if sealed_up:
            out["盘口"] = "当前封涨停"
        elif touched_up:
            out["盘口"] = f"盘中触及涨停({lu})后炸板回落, 现价较最高回落{round((high - price) / high * 100, 2)}%"
        elif sealed_dn:
            out["盘口"] = "当前封跌停"
        elif touched_dn:
            out["盘口"] = f"盘中触及跌停({ld})后回升"
        else:
            out["盘口"] = "未触及涨跌停"
        out["日内振幅%"] = round((high - low) / prev * 100, 2) if high and low else None
    # TDX 数据源(可插拔)启用时, 补五档盘口 + 内外盘; 没配/连不上就跳过, 不影响主流程
    try:
        import services.tdx_client as _tdx
        if _tdx.is_enabled() and len(bare) == 6 and bare.isdigit():
            t = await _tdx.quote(bare)
            if t:
                out["五档"] = {"买盘": t.get("bids"), "卖盘": t.get("asks"),
                              "内盘手": t.get("内盘手"), "外盘手": t.get("外盘手")}
                # 封板时给出封单量(买一挂单)
                if out.get("盘口") == "当前封涨停" and t.get("bids"):
                    b1 = t["bids"][0]
                    out["盘口"] = f"当前封涨停, 封单 买一 {b1.get('手')}手@{b1.get('price')}"
                elif out.get("盘口") == "当前封跌停" and t.get("asks"):
                    a1 = t["asks"][0]
                    out["盘口"] = f"当前封跌停, 卖一砸单 {a1.get('手')}手@{a1.get('price')}"
                out["数据源"] = "盘口含TDX五档"
    except Exception:
        pass
    return out


async def _tool_intraday(code: str) -> dict:
    """当日分时(TDX): 开盘/最高(及时间)/最低(及时间)/现价 + 是否冲高回落 + 关键点。需启用 TDX 数据源, 仅 A 股。"""
    import services.tdx_client as _tdx
    from services.market_data import normalize_stock_code
    if not _tdx.is_enabled():
        return {"error": "分时需启用 TDX 数据源(设置→TDX base_url), 当前未配置"}
    bare = normalize_stock_code(_norm_code(code)).split(".")[-1]
    if not (len(bare) == 6 and bare.isdigit()):
        return {"error": "分时仅支持 A 股"}
    m = await _tdx.minute(bare)
    if not m or not m.get("points"):
        return {"error": "分时暂不可达(TDX 连不上或非交易日)"}
    pts = m["points"]
    prices = [(p["price"], p["time"]) for p in pts if p.get("price")]
    if not prices:
        return {"error": "分时无有效数据"}
    hi = max(prices, key=lambda x: x[0])
    lo = min(prices, key=lambda x: x[0])
    open_p, last_p = prices[0][0], prices[-1][0]
    # 每 ~30 分钟取一个采样点, 给 LLM 看大致路径(避免 240 点刷屏)
    step = max(1, len(pts) // 8)
    path = [{"time": pts[i]["time"], "price": pts[i]["price"]} for i in range(0, len(pts), step)]
    return {"date": m.get("date"), "开盘": open_p, "现价/收盘": last_p,
            "最高": {"price": hi[0], "time": hi[1]}, "最低": {"price": lo[0], "time": lo[1]},
            "较最高回落%": round((hi[0] - last_p) / hi[0] * 100, 2) if hi[0] else None,
            "路径采样": path,
            "note": "分时路径采样(约每30分钟一个点) + 高低点带时间; '较最高回落'大=冲高回落/炸板特征。"}


def _us_daily_k_sync(symbol: str, datalen: int) -> list:
    """美股个股日K(新浪 US_MinKService, 裸 symbol 如 AAPL; 返回升序 [{date,close}], 取末尾 N 条)。"""
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
    if not isinstance(arr, list):
        return []
    return [{"date": str(d.get("d") or ""), "close": float(d["c"])}
            for d in arr[-(datalen + 1):] if d.get("c")]


async def _tool_get_trend(code: str, days: int = 20) -> dict:
    """近 N 日走势: 每日涨跌幅 + 累计。A 股走新浪历史; 港股走腾讯日K; 美股走新浪 US 日K。"""
    from services.market_data import (get_historical_data, normalize_stock_code, is_a_share,
                                       split_stock_code, _kline_tencent_hk)
    raw = normalize_stock_code(_norm_code(code))
    days = max(5, min(int(days or 20), 60))
    market, symbol = split_stock_code(raw)
    def _ff(v):
        try:
            f = float(v)
            return f if f > 0 else None
        except (TypeError, ValueError):
            return None
    bars: list = []  # [(date_str, close, high|None, low|None)] 升序; date 可能为空(数据源缺)
    if is_a_share(raw):
        df = await get_historical_data(raw, days + 5)
        if df is None or df.empty:
            return {"error": "无历史数据"}
        n = len(df)
        dcol = df["日期"].tolist() if "日期" in df.columns else [""] * n
        hcol = df["最高"].tolist() if "最高" in df.columns else [None] * n
        lcol = df["最低"].tolist() if "最低" in df.columns else [None] * n
        bars = [(str(d)[:10], float(c), _ff(h), _ff(l))
                for d, c, h, l in zip(dcol, df["收盘"].tolist(), hcol, lcol)]
    elif market == "HK":
        rows = await asyncio.to_thread(_kline_tencent_hk, f"hk{symbol.zfill(5)}", days + 5)
        bars = [(str(r.get("date") or "")[:10], float(r["close"]), _ff(r.get("high")), _ff(r.get("low")))
                for r in (rows or []) if r.get("close")]
    elif market == "US":
        rows = await asyncio.to_thread(_us_daily_k_sync, symbol, days)
        bars = [(str(r.get("date") or "")[:10], float(r["close"]), _ff(r.get("high")), _ff(r.get("low")))
                for r in (rows or []) if r.get("close")]
    else:
        return {"error": "走势暂不支持该市场"}
    bars = bars[-(days + 1):]
    if len(bars) < 2:
        return {"error": "无历史数据"}
    code = raw
    closes = [b[1] for b in bars]
    # 每条逐日涨跌挂真实日期 + 当天最高/最低相对昨收的幅度(看历史某天日内摸没摸到涨停/封板还是冲高回落, 无需分时)
    daily = []
    for i in range(1, len(bars)):
        pc = closes[i - 1]
        e = {"date": bars[i][0], "pct": round((closes[i] / pc - 1) * 100, 2)}
        h, l = bars[i][2], bars[i][3]
        if h and pc > 0:
            e["high_pct"] = round((h / pc - 1) * 100, 2)
        if l and pc > 0:
            e["low_pct"] = round((l / pc - 1) * 100, 2)
        daily.append(e)
    cum = round((closes[-1] / closes[0] - 1) * 100, 2)
    up = sum(1 for d in daily if d["pct"] > 0)
    return {
        "code": code, "days": len(daily),
        "cum_pct": cum, "up_days": up, "down_days": len(daily) - up,
        "last_date": bars[-1][0], "last_close": round(closes[-1], 3),
        # 最近 10 日逐日涨跌, 每条带 date(YYYY-MM-DD) + high_pct/low_pct(当日最高/最低相对昨收)。最后一条即最新交易日。
        "daily_pct": daily[-min(10, len(daily)):],
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


def _fetch_realtime_fund_flow(secid: str, headers: dict, hosts: list) -> dict | None:
    """今日主力资金流(东财 push2 实时, ulist.np/get?secids=): 与榜单 clist f62 同源, 盘中实时滚动。
    f62 主力净 = f66 超大单净 + f72 大单净; f78 中单净; f84 小单净 (单位元)。
    注意: stock/get 不填这些资金流字段, 必须走 ulist.np。"""
    import requests as _rq
    import time as _t
    from datetime import datetime, timezone, timedelta
    params = {"secids": secid, "invt": "2", "fltt": "2",
              "fields": "f12,f14,f62,f66,f72,f78,f84,f184"}
    for i in range(6):
        try:
            diff = (_rq.get(f"https://{hosts[i % len(hosts)]}/api/qt/ulist.np/get", params=params,
                            timeout=7, headers=headers).json().get("data") or {}).get("diff") or []
            if diff and diff[0].get("f62") not in (None, "", "-"):
                d = diff[0]
                today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
                _g = lambda k: round(float(d.get(k) or 0) / 1e8, 2)
                return {"date": today, "main": _g("f62"), "xlarge": _g("f66"),
                        "big": _g("f72"), "mid": _g("f78"), "small": _g("f84")}
        except Exception:
            _t.sleep(0.3)
    return None


def _fetch_fund_flow_sync(code: str) -> dict:
    """个股主力资金流: 今日各单类净额走 push2 实时(stock/get f62/f66/f72/f78/f84, 与榜单 f62 同源, 盘中实时);
    近几日主力净流入趋势走 fflow/kline 日线。两个口径统一: today 永远是实时值, 不再用滞后的历史末根。
    fflow/kline 每行: 日期,主力净,小单净,中单净,大单净,超大单净 (单位元)。"""
    import requests as _rq
    import time as _t
    ck = f"ff_{code}"
    c = _fflow_cache.get(ck)
    if c and _t.time() - c[1] < 120:  # 实时口径, 缓存收紧到 2 分钟
        return c[0]
    secid = _em_secid(code)
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    hosts = ["push2.eastmoney.com", "push2delay.eastmoney.com", "1.push2.eastmoney.com"]
    his_hosts = ["push2his.eastmoney.com", "push2.eastmoney.com", "push2delay.eastmoney.com"]
    rt = _fetch_realtime_fund_flow(secid, hdr, hosts)  # 今日实时主力资金流(权威)
    # 历史趋势序列(近几日), 末根可能滞后, 仅用于画趋势
    params = {"lmt": "8", "klt": "101", "secid": secid,
              "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56"}
    rows = []
    for i in range(9):
        host = his_hosts[i % len(his_hosts)]
        try:
            r = _rq.get(f"https://{host}/api/qt/stock/fflow/kline/get", params=params, timeout=7, headers=hdr)
            kl = (r.json().get("data") or {}).get("klines")
            if kl:
                for ln in kl[-6:]:
                    p = ln.split(",")
                    if len(p) < 6:
                        continue
                    rows.append({"date": p[0], "main": round(float(p[1]) / 1e8, 2),
                                 "small": round(float(p[2]) / 1e8, 2), "mid": round(float(p[3]) / 1e8, 2),
                                 "big": round(float(p[4]) / 1e8, 2), "xlarge": round(float(p[5]) / 1e8, 2)})
                break
        except Exception:
            _t.sleep(0.3)
    # today 优先用实时值; 实时拿不到才回退历史末根
    today = rt or (rows[-1] if rows else None)
    if today is None:
        return {"error": "资金流暂不可达(东财源抖动)"}
    series = [{"date": r["date"], "主力净流入亿": r["main"]} for r in rows]
    # 用实时今日值覆盖/补上趋势序列里的同日点, 保持序列末尾与 today 一致
    if rt:
        if series and series[-1]["date"] == rt["date"]:
            series[-1]["主力净流入亿"] = rt["main"]
        else:
            series.append({"date": rt["date"], "主力净流入亿": rt["main"]})
    out = {"unit": "亿元", "today": today, "today_realtime": bool(rt),
           "main_net_series": series}
    _fflow_cache[ck] = (out, _t.time())
    return out


async def _tool_fund_flow(code: str) -> dict:
    """个股主力资金流: 今日主力/超大单/大单/中单/小单净额 + 近几日主力净流入趋势(判断谁在买/在卖)。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "资金流仅支持 A 股"}
    out = await asyncio.to_thread(_fetch_fund_flow_sync, raw)
    if "error" not in out:
        t = out["today"]
        out["note"] = (f"最新交易日主力净{'流入' if t['main'] >= 0 else '流出'}{abs(t['main'])}亿"
                       f"(超大单{t['xlarge']}/大单{t['big']}/中单{t['mid']}/小单{t['small']}亿); "
                       "主力=超大单+大单, 正=资金净买入; 该值具体属哪个交易日见系统提示的交易日状态。")
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


_profile_cache: dict = {}


def _fetch_company_profile_sync(bare: str) -> dict:
    """公司是做什么的: 东财 F10 公司简介(ORG_PROFILE) + 细分行业(EM2016) + 主营构成(产品/地区收入占比+毛利率)。"""
    import requests as _rq
    import time as _t
    ck = f"prof_{bare}"
    c = _profile_cache.get(ck)
    if c and _t.time() - c[1] < 86400:
        return c[0]
    pfx = "SH" if bare[:1] in ("6", "9", "5") else "SZ"
    hdr = {"User-Agent": "Mozilla/5.0"}
    out: dict = {"code": bare}
    # 1) 公司简介 + 行业
    try:
        d = _rq.get(f"https://emweb.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={pfx}{bare}",
                    timeout=9, headers=hdr).json()
        jb = (d.get("jbzl") or [{}])[0]
        prof = (jb.get("ORG_PROFILE") or "").strip()
        out.update({
            "name": jb.get("ORG_NAME") or jb.get("SECURITY_NAME_ABBR"),
            "industry": jb.get("EM2016") or jb.get("INDUSTRYCSRC1"),
            "profile": prof[:400] or None,
            "h_share": jb.get("STR_CODEH"),
            "employees": jb.get("EMP_NUM"),
        })
    except Exception:
        pass
    # 2) 主营构成(优先按产品, 其次地区, 再行业); ITEM_NAME 占比 毛利率
    try:
        d2 = _rq.get(f"https://emweb.eastmoney.com/PC_HSF10/BusinessAnalysis/PageAjax?code={pfx}{bare}",
                     timeout=9, headers=hdr).json()
        rows = d2.get("zygcfx") or []
        # 只取最新报告期(zygcfx 含多期, 跨期会重复)
        if rows:
            latest = max(str(r.get("REPORT_DATE") or "") for r in rows)
            rows = [r for r in rows if str(r.get("REPORT_DATE") or "") == latest]
        # 同一期里优先按产品(2), 其次地区(3), 再行业(1)
        for prefer in ("2", "3", "1"):
            sub = [r for r in rows if str(r.get("MAINOP_TYPE")) == prefer]
            if sub:
                rows = sub
                break
        comp = []
        for r in rows[:6]:
            ratio = r.get("MBI_RATIO")
            gross = r.get("GROSS_RPOFIT_RATIO")
            comp.append({
                "项目": r.get("ITEM_NAME"),
                "营收占比%": round(float(ratio) * 100, 1) if ratio not in (None, "") else None,
                "毛利率%": round(float(gross) * 100, 1) if gross not in (None, "") else None,
            })
        if comp:
            out["main_business"] = comp
            out["report_date"] = str((rows[0].get("REPORT_DATE") or ""))[:10]
    except Exception:
        pass
    # 3) 控股/实控背景(判断国资/央企/地方国企/中科院系/民营/外资 等性质)
    try:
        d3 = _rq.get(f"https://emweb.eastmoney.com/PC_HSF10/ShareholderResearch/PageAjax?code={pfx}{bare}",
                     timeout=9, headers=hdr).json()
        sj = (d3.get("sjkzr") or [{}])[0]
        ctrl = (sj.get("HOLDER_NAME") or "").strip()
        if ctrl and ctrl != "None":
            out["controller"] = ctrl
        sdgd = d3.get("sdgd") or []
        if sdgd:
            latest = max(str(r.get("END_DATE") or "") for r in sdgd)
            top = []
            for r in [x for x in sdgd if str(x.get("END_DATE") or "") == latest][:3]:
                nm = (r.get("HOLDER_NAME") or "").strip()
                if not nm or nm == "None":
                    continue
                ratio = r.get("HOLD_NUM_RATIO")
                top.append({"股东": nm,
                            "持股%": round(float(ratio), 2) if ratio not in (None, "", "None") else None})
            if top:
                out["top_holders"] = top
    except Exception:
        pass
    if not out.get("profile") and not out.get("main_business"):
        return {"error": "公司简介暂不可达(东财F10抖动)"}
    out["note"] = ("profile=公司简介(做什么); industry=细分行业; main_business=主营构成(收入占比+毛利率); "
                   "controller=实际控制人, top_holders=第一/前三大股东(据股东名判断国资/央企/地方国企/中科院系/民营/外资等公司性质)。仅 A 股。")
    _profile_cache[ck] = (out, _t.time())
    return out


async def _tool_company_profile(code: str) -> dict:
    """公司是做什么的: 公司简介 + 细分行业 + 主营构成(收入占比/毛利率)。回答'这家公司主营什么、靠什么赚钱'时用。仅 A 股。"""
    from services.market_data import normalize_stock_code, is_a_share
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "公司简介仅支持 A 股"}
    bare = raw.split(".")[-1] if "." in raw else raw
    return await asyncio.to_thread(_fetch_company_profile_sync, bare)


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
                       "note": f"同属【{ind}】板块, 按最新交易日主力净流入排序; PE/PB 横向比可看谁贵谁便宜, 涨跌幅看谁领涨。主力净流入亿为榜单快照, 用于榜内横向比较; 单只票的精确金额以 get_fund_flow 为准。"}
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


# 红线关键词 → (命中词, 类别, 级别, 排除词)。扫公告标题, 命中且不含排除词=该风险有事实依据。
# 排除词用于剔除例行/反向公告(如 审核问询=IPO例行、终止减持=减持结束、专项说明=年报例行)。
_RED_LINE_RULES = [
    (("立案", "被调查", "行政处罚", "责令改正", "警示函", "监管措施", "纪律处分"), "监管处罚", "高", ()),
    (("*ST", "退市风险", "终止上市", "暂停上市", "其他风险警示"), "退市风险", "高", ()),
    (("预亏", "首亏", "续亏", "预减", "由盈转亏", "净利润大幅下降", "业绩大幅下滑"), "业绩预警", "中", ()),
    (("商誉减值", "计提.*减值", "大额减值"), "资产/商誉减值", "中", ("审计说明", "专项说明")),
    (("问询函", "关注函", "监管工作函"), "交易所问询", "中", ("审核问询", "保荐", "注册", "回复")),
    (("违规担保", "违规占用", "重大诉讼", "重大仲裁"), "违规/诉讼", "中", ("专项说明", "专项审计", "审计说明")),
    (("拟减持", "减持计划", "询价转让", "大宗交易减持"), "股东减持", "低", ("终止", "完成", "不减持", "届满", "结果")),
    (("平仓风险", "高比例质押", "质押.*预警"), "股权质押", "低", ()),
]


async def _tool_red_flags(code: str) -> dict:
    """客观红线清单: 扫公告(监管处罚/退市/业绩预警/减值/问询/减持等) + 解禁抛压 + 基本面健康度, 列出有事实依据的风险点。
    命中=该风险确有依据, 不是买卖建议; 把雷摆给用户自己判断。仅 A 股。"""
    import re as _re2
    from services.market_data import normalize_stock_code, is_a_share, get_stock_name
    raw = normalize_stock_code(_norm_code(code))
    if not is_a_share(raw):
        return {"error": "红线清单仅支持 A 股"}
    bare = raw.split(".")[-1] if "." in raw else raw
    from services.news import get_stock_announcements
    from services.fundamental_score import fetch_health_snapshot

    name = ""
    try:
        name = await get_stock_name(raw) or ""
    except Exception:
        pass
    anns, shholders, health = await asyncio.gather(
        get_stock_announcements(bare, limit=30),
        asyncio.to_thread(_fetch_shareholders_sync, raw),
        fetch_health_snapshot(raw, name),
        return_exceptions=True,
    )
    if isinstance(anns, Exception): anns = []
    if isinstance(shholders, Exception): shholders = {}
    if isinstance(health, Exception): health = None

    flags, hit_cats = [], set()
    # 1) 公告关键词扫描(取最近一条命中作依据)
    for a in (anns or []):
        title = a.get("title") or ""
        date = (a.get("date") or "")[:10]
        for kws, cat, lvl, excludes in _RED_LINE_RULES:
            if cat in hit_cats:
                continue
            if any(x in title for x in excludes):
                continue
            if any((_re2.search(k, title) if ".*" in k else k in title) for k in kws):
                flags.append({"类别": cat, "级别": lvl, "依据": f"[{date}] {title}"})
                hit_cats.add(cat)
    # 2) 解禁抛压
    unlock = (shholders or {}).get("upcoming_unlock")
    if isinstance(unlock, list) and unlock:
        u0 = unlock[0]
        pct = u0.get("占流通市值%") or 0
        lvl = "中" if pct and pct >= 5 else "低"
        flags.append({"类别": "解禁抛压", "级别": lvl,
                      "依据": f"{u0.get('date')} 解禁约占流通市值 {pct}% ({u0.get('类型') or ''})"})
    # 3) 基本面健康度
    if health and isinstance(health, dict):
        lv = health.get("level")
        if lv == "red":
            flags.append({"类别": "基本面健康度", "级别": "中", "依据": f"健康度红灯 (评分 {health.get('score')})"})
        elif lv == "yellow":
            flags.append({"类别": "基本面健康度", "级别": "提示", "依据": f"健康度黄灯 (评分 {health.get('score')})"})

    order = {"高": 0, "中": 1, "低": 2, "提示": 3}
    flags.sort(key=lambda f: order.get(f["级别"], 9))
    checked = ["监管处罚", "退市风险", "业绩预警", "资产/商誉减值", "交易所问询", "违规/诉讼", "股东减持", "解禁抛压", "基本面健康度"]
    clear = [c for c in checked if c not in {f["类别"] for f in flags}]
    return {"code": bare, "name": name, "red_flags": flags, "checked_clear": clear,
            "note": "客观红线扫描(近30条公告+解禁+健康度): red_flags=有事实依据的风险点(按高/中/低/提示排), "
                    "checked_clear=已扫描未命中的项。命中仅代表'存在该风险事实', 属信息呈现, 供用户自行判断。"
                    "公告源仅覆盖近期, 更早或非公告类风险可能未覆盖, 表述时限定为'近期公告/解禁/健康度未扫到明显红线'并说明覆盖范围。"}


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


_ACTION_CN = {"BUY": "买入", "ADD": "加仓", "SELL": "卖出", "REDUCE": "减仓",
              "BONUS": "送转股", "DIVIDEND": "现金分红"}


async def _tool_trades(code: str = "", start: str = "", end: str = "") -> dict:
    """成交记录(含个股/场内ETF/场外基金): 传 code→该标的流水 + (A股)持仓状态(综合成本/已实现盈亏/持有天数);
    不传→最近全部成交(三类合并)。start/end (YYYY-MM-DD) 按成交日期筛选区间。"""
    from database import get_position_actions, get_all_holdings
    from services.position_ledger import compute_position_state
    from api.portfolio_routes import _broker_stock_fee

    s, e = (start or "").strip()[:10], (end or "").strip()[:10]

    def in_range(d: str) -> bool:
        d = (d or "")[:10]
        if s and d < s:
            return False
        if e and d > e:
            return False
        return True

    def act_date(x) -> str:
        # trade_date 缺失时回退 created_at(同步/导入生成的 initial/add-lot 只有 created_at), 对齐前端口径
        return (x.get("trade_date") or str(x.get("created_at") or ""))[:10]

    def fmt(a: dict) -> dict:
        amt = float(a.get("price") or 0) * float(a.get("shares") or 0)
        return {"date": (a.get("trade_date") or str(a.get("created_at") or ""))[:10],
                "动作": _ACTION_CN.get(a.get("action_type"), a.get("action_type")),
                "price": a.get("price"), "shares": a.get("shares"),
                "金额": round(amt, 2) if amt else None,
                "fee": a.get("fee"), "note": a.get("note") or ""}

    if code:
        bare = _norm_code(code)
        acts = await get_position_actions(bare, limit=500)
        if not acts:
            # 不在个股账本 → 可能是基金/ETF(外部资产账本)
            try:
                from database import list_external_assets, list_external_actions
                q = (code or "").strip()
                for a in await list_external_assets():
                    ac, an = str(a.get("code") or ""), (a.get("name") or "")
                    if a.get("asset_type") == "FUND" and (q == ac or q in an or q == bare):
                        fa = {"BUY": "申购", "ADD": "加仓", "REDEEM": "赎回",
                              "DEPOSIT": "转入", "WITHDRAW": "转出"}
                        recs = []
                        for x in await list_external_actions(a["id"]):
                            if not in_range(act_date(x)):
                                continue
                            r = {"date": act_date(x),
                                 "动作": fa.get((x.get("action_type") or "").upper(), x.get("action_type")),
                                 "price": x.get("unit_price"), "shares": x.get("shares"),
                                 "金额": x.get("amount"), "note": x.get("note") or ""}
                            if (x.get("status") or "confirmed") != "confirmed":
                                r["状态"] = "待确认(T+1未出净值)"
                            recs.append(r)
                        return {"code": ac, "name": an, "asset_class": "基金/ETF", "trades": recs,
                                "range": {"start": s or None, "end": e or None},
                                "note": "基金/ETF 申赎流水(外部资产账本); 综合成本/盈亏请用看板。"}
            except Exception:
                pass
            return {"code": bare, "trades": [], "note": "该标的无成交记录"}
        # 名称
        name = ""
        for h in await get_all_holdings():
            if h.get("stock_code") == bare:
                name = h.get("stock_name") or ""
                break
        recs = sorted([fmt(a) for a in acts if in_range(a.get("trade_date") or str(a.get("created_at") or ""))],
                      key=lambda x: x["date"])
        summary = {}
        try:
            rate, mn = await _broker_stock_fee(None)
            st = compute_position_state(acts, stock_code=bare, commission_rate=rate, commission_min=mn)
            # 已实现盈亏用 realized_carry(已平仓段+分红, 不含浮动)。注意 realized_pnl 与 carry
            # 在清仓后是同一笔, 不能相加; 当前持仓段的浮盈在 综合成本 里体现, 不算"已实现"。
            summary = {"当前持股": st.get("shares"), "综合成本": st.get("cost_price"),
                       "已实现盈亏": round(float(st.get("realized_carry") or 0), 2),
                       "其中累计分红": st.get("income_realized"), "加权持有天数": st.get("weighted_days"),
                       "累计手续费": st.get("total_fees")}
        except Exception:
            pass
        return {"code": bare, "name": name, "trades": recs, "position": summary,
                "range": {"start": s or None, "end": e or None},
                "note": "trades=区间内成交流水(含手续费,按日期升序); position=按全历史算的当前状态(不受区间影响)。同日有买有卖=做T。已实现盈亏含已平仓段+分红。"}

    # 无 code: 最近全部成交 —— 自己组装(个股 + 场内ETF + 场外基金), 含 pending(T+1待确认)申购, 标出来
    try:
        merged = []
        name_by = {h.get("stock_code"): h.get("stock_name") for h in await get_all_holdings()}
        # A 股个股
        for a in await get_position_actions(None, limit=200):
            d = (a.get("trade_date") or str(a.get("created_at") or ""))[:10]
            if not in_range(d):
                continue
            amt = float(a.get("price") or 0) * float(a.get("shares") or 0)
            merged.append({"date": d, "code": a.get("stock_code"), "name": name_by.get(a.get("stock_code"), ""),
                           "动作": _ACTION_CN.get(a.get("action_type"), a.get("action_type")), "类型": "个股",
                           "price": a.get("price"), "shares": a.get("shares"), "金额": round(amt, 2) if amt else None})
        # 场内ETF / 场外基金 (含待确认)
        from database import list_external_assets, list_external_actions
        from services.external_assets import _is_onchain_etf
        fa = {"BUY": "买入", "ADD": "买入", "REDEEM": "卖出"}
        for a in await list_external_assets():
            if a.get("asset_type") != "FUND":
                continue
            cls = "场内ETF" if _is_onchain_etf(str(a.get("code") or "")) else "场外基金"
            for x in await list_external_actions(a["id"]):
                act = fa.get((x.get("action_type") or "").upper())
                d = act_date(x)
                if not act or not in_range(d):
                    continue
                pend = (x.get("status") or "confirmed") != "confirmed"
                rec = {"date": d, "code": str(a.get("code") or ""),
                       "name": a.get("name") or "", "动作": act, "类型": cls, "price": x.get("unit_price"),
                       "shares": x.get("shares"), "金额": x.get("amount")}
                if pend:
                    rec["状态"] = "待确认(T+1未出净值)"
                merged.append(rec)
        merged.sort(key=lambda x: x["date"], reverse=True)
        return {"recent_trades": merged[:80], "range": {"start": s or None, "end": e or None},
                "note": "成交(时间倒序), 含个股/场内ETF/场外基金三类; 含待确认申购(状态=待确认)。日期缺失的成交按录入时间(created_at)归日。"}
    except Exception:
        acts = await get_position_actions(None, limit=40)
        name_by = {h.get("stock_code"): h.get("stock_name") for h in await get_all_holdings()}
        recs = [{**fmt(a), "code": a.get("stock_code"), "name": name_by.get(a.get("stock_code"), "")} for a in acts]
        return {"recent_trades": recs, "note": "最近成交(仅个股, 基金账本读取失败)。"}


async def _tool_get_thesis(code: str) -> dict:
    """读用户当初记的买入逻辑(thesis-tracker), 用于复盘'当初为什么买、逻辑还成不成立'。不带 code 看全部。"""
    from database import get_thesis, list_theses
    from services.market_data import normalize_stock_code
    if not code:
        ts = await list_theses()
        return {"theses": ts, "note": "用户记录的各持仓买入逻辑; 复盘时对照现状看逻辑是否还成立。"}
    raw = normalize_stock_code(_norm_code(code))
    bare = raw.split(".")[-1] if "." in raw else raw
    t = await get_thesis(bare)
    if not t:
        return {"code": bare, "thesis": None, "note": "用户没记这只的买入逻辑。可提示他在持仓里补一句, 以后好复盘。"}
    # 算"记于多久之前", 让复盘能锚定时间(几个月前的逻辑 vs 昨天刚记的, 复盘意义不同)
    days_since = None
    created = (t.get("created_at") or "")[:10]
    try:
        import datetime as _dt
        if created:
            today = (_dt.datetime.utcnow() + _dt.timedelta(hours=8)).date()
            days_since = (today - _dt.date.fromisoformat(created)).days
    except Exception:
        pass
    return {"code": bare, "name": t.get("name"), "thesis": t.get("thesis"),
            "recorded_at": created, "updated_at": (t.get("updated_at") or "")[:10], "days_since": days_since,
            "note": "thesis=用户记的买入逻辑; recorded_at=当初记下的日期, days_since=距今天数。"
                    "复盘时先点明'这是你X天前/X个月前记的逻辑', 再逐条对照现价/基本面/消息/红线客观判定每条是否仍成立, 买卖结论由用户自定。"}


async def _tool_get_holdings() -> dict:
    try:
        hs = await _active_holdings()
        return {"holdings": [{"code": h.get("stock_code"), "name": h.get("stock_name"),
                              "shares": h.get("shares"),
                              "持有天数": h.get("hold_days"), "开仓日": h.get("open_date")} for h in hs],
                "note": "仅当前在持(已清仓的票不在此列, 按综合成本法现算 shares>0)。"
                        "持有天数=资金加权持有天数(0=今天才开/加的仓); 开仓日=当前持仓段最早一笔买入日(=今天则是今日新开仓), 已带好星期, 说开仓是周几时以括号内星期为准照抄。"}
    except Exception as e:
        return {"error": str(e)}


_ASSET_CLASS_CN = {"CASH": "现金", "WEALTH": "理财", "FUND": "基金",
                   "CRYPTO": "加密", "BOT": "量化机器人"}


async def _tool_asset_allocation() -> dict:
    """全量资产配置快照: 各大类(股票/现金/理财/基金/加密/机器人)市值+占比 + 现金/理财逐笔明细(金额/年化/持有天数)。
    供'现金理财怎么分/应急金够不够/结构合不合理'这类资产配置讨论, 不涉及个股买卖。单位元(CNY)。"""
    from api.assets_routes import list_assets
    from services.market_data import get_realtime_quotes
    try:
        data = await list_assets()
    except Exception as e:
        return {"error": f"读资产失败: {e}"}
    by_type = (data.get("summary") or {}).get("by_type") or {}
    assets = data.get("assets") or []

    classes: dict[str, float] = {}
    for t, v in by_type.items():
        cn = _ASSET_CLASS_CN.get(t, t)
        classes[cn] = round(classes.get(cn, 0.0) + float(v.get("value") or 0), 2)

    # A股/港美股市值(CNY)
    try:
        hs = await _active_holdings()
        if hs:
            codes = [h["stock_code"] for h in hs]
            quotes = await get_realtime_quotes(codes)
            stock_val = 0.0
            for h in hs:
                q = quotes.get(h["stock_code"]) or {}
                px = q.get("price") or 0
                fx = q.get("fx_rate") or 1
                stock_val += px * float(h.get("shares") or 0) * fx
            if stock_val > 0:
                classes["股票"] = round(stock_val, 2)
    except Exception:
        pass

    total = round(sum(classes.values()), 2)
    breakdown = [{"类别": k, "金额": v, "占比%": round(v / total * 100, 1) if total > 0 else 0}
                 for k, v in sorted(classes.items(), key=lambda kv: -kv[1])]

    # 现金 + 理财 逐笔明细(配置讨论的重点: 流动性 & 收益)
    liquid = []
    for a in assets:
        if a.get("asset_type") not in ("CASH", "WEALTH"):
            continue
        q = a.get("quote") or {}
        liquid.append({
            "名称": a.get("name"), "类别": _ASSET_CLASS_CN.get(a.get("asset_type")),
            "金额": round(a.get("current_value") or 0, 2),
            "年化%": round((a.get("annual_yield_rate") or q.get("annual_yield_rate") or 0) * 100, 2) or None,
            "持有天数": q.get("days_held"),
        })

    return {"unit": "元(CNY)", "total_asset": total, "breakdown": breakdown,
            "cash_and_wealth_detail": liquid,
            "note": "breakdown=各大类市值与占比; cash_and_wealth_detail=现金/理财逐笔(流动性与收益)。"
                    "据此可分析流动性分层/应急金/收益-期限权衡, 但具体怎么分由用户自己决定。"}


async def _tool_sector_momentum(days: int = 10) -> dict:
    """板块趋势矩阵: 各行业近 N 日累计涨跌/连涨动能/净流入 → 看动量是否延续(动量风格) 还是冲高回落(退潮/反转)。"""
    try:
        from services.sector_matrix import get_sector_matrix
        m = await get_sector_matrix(days=int(days or 10))
        rows = m.get("rows") or []
        if not rows:
            return {"error": "板块矩阵暂无数据"}
        def brief(r):
            return {"板块": r["name"], "最新交易日涨幅%": r.get("today_pct"), f"近{m.get('days')}日累计": r.get("cum_pct"),
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
    return {"top_concepts": out, "note": "按最新交易日涨幅排序; 主力净流入正=资金流入"}


_board_list_cache: dict = {}


def _fetch_board_code_sync(name: str) -> tuple | None:
    """板块/概念名 → (BK代码, 标准名, 类型)。直接传 BKxxxx 也认。搜概念(t:3)+行业(t:2)板块列表。"""
    import requests as _rq
    import time as _t
    q = (name or "").strip()
    if not q:
        return None
    if q.upper().startswith("BK") and q[2:].isdigit():
        return (q.upper(), q.upper(), "板块")
    cache = _board_list_cache.get("boards")
    if not cache or _t.time() - cache[1] > 600:
        hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
        hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com"]
        boards = []
        # 翻页拉全量(EM 单页截 ~100; 概念 400+/行业 90+ 必须分页, 否则只拿到涨幅靠前的)
        for fs, kind in (("m:90 t:3", "概念"), ("m:90 t:2", "行业")):
            for pn in range(1, 8):
                page = None
                for h in hosts:
                    try:
                        page = (_rq.get(f"https://{h}/api/qt/clist/get", timeout=7, headers=hdr,
                                        params={"pn": str(pn), "pz": "100", "po": "1", "np": "1", "fltt": "2",
                                                "invt": "2", "fid": "f3", "fs": fs, "fields": "f12,f14"}).json()
                                .get("data") or {}).get("diff")
                        break
                    except Exception:
                        _t.sleep(0.3)
                if not page:
                    break
                boards += [(x.get("f14"), x.get("f12"), kind) for x in page if x.get("f12")]
                if len(page) < 100:
                    break
        if boards:
            _board_list_cache["boards"] = (boards, _t.time())
            cache = _board_list_cache["boards"]
    if not cache:
        return None
    boards = cache[0]
    for nm, cd, kind in boards:                       # 精确
        if nm == q:
            return (cd, nm, kind)
    hits = [(nm, cd, kind) for nm, cd, kind in boards if q in (nm or "")]
    if hits:
        hits.sort(key=lambda x: len(x[0]))            # 最短名优先(最贴近)
        return (hits[0][1], hits[0][0], hits[0][2])
    return None


def _fetch_board_stocks_sync(name: str, top: int = 12) -> dict:
    """某板块/概念成分股按今日涨幅 top: 涨跌幅/现价/换手/主力净流入。"""
    import requests as _rq
    import time as _t
    resolved = _fetch_board_code_sync(name)
    if not resolved:
        return {"error": f"找不到板块/概念「{name}」(试试更标准的名字, 或先用 get_hot_concepts 看在榜的概念名)"}
    bk, std, kind = resolved
    hdr = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
    params = {"pn": "1", "pz": str(max(top, 12)), "po": "1", "np": "1", "fltt": "2", "invt": "2",
              "fid": "f3", "fs": f"b:{bk}", "fields": "f12,f14,f2,f3,f8,f62"}
    for i in range(9):
        host = ["push2delay.eastmoney.com", "push2.eastmoney.com", "1.push2.eastmoney.com"][i % 3]
        try:
            diff = (_rq.get(f"https://{host}/api/qt/clist/get", params=params, timeout=7,
                            headers=hdr).json().get("data") or {}).get("diff")
            if diff:
                rows = []
                for x in diff[:top]:
                    try:
                        rows.append({"name": x.get("f14"), "code": x.get("f12"),
                                     "涨跌幅": x.get("f3"), "现价": x.get("f2"),
                                     "换手%": x.get("f8"),
                                     "主力净流入亿": round(float(x.get("f62") or 0) / 1e8, 2)})
                    except (ValueError, TypeError):
                        continue
                if rows:
                    return {"板块": std, "类型": kind, "code": bk, "top_stocks": rows,
                            "note": f"「{std}」成分股按最新交易日涨幅排序; 主力净流入正=资金净买入(榜单快照, 用于看资金集中在哪几只; 单只票精确金额以 get_fund_flow 为准)。"}
        except Exception:
            _t.sleep(0.3)
    return {"error": f"「{std}」成分股暂不可达(东财源抖动)"}


async def _tool_board_stocks(board: str, top: int = 12) -> dict:
    """查某个板块/概念里今日涨幅 top-N 的个股(龙头), 带涨跌幅/现价/换手/主力净流入。
    board 传概念或行业名(如 玻璃基板/CPO/光通信/小金属)或 BK 代码。"""
    return await asyncio.to_thread(_fetch_board_stocks_sync, board, int(top or 12))


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
    """全市场财经快讯(东财+财联社+同花顺+金十), 含政策面/国家调控 + 全球宏观/地缘/央行。用来把宏观政策、监管动向、
    产业政策、央行财政、重要会议、海外市场扰动等市场背景因素纳入分析。
    policy_news=政策关键词筛选; important_flash=金十标重要的快讯(全球宏观/地缘/央行, 对 A 股情绪影响大)。"""
    try:
        from api.news_routes import market_news
        mn = await market_news()
        items = (mn.get("items") or [])[:max(limit, 40)]
        def is_pol(t):
            return any(k in (t or "") for k in _POLICY_KW)
        heads = [{"title": it.get("title"), "time": it.get("time"), "source": it.get("source")} for it in items]
        policy = [h for h in heads if is_pol(h["title"])]
        important = [{"title": it.get("title"), "time": it.get("time")}
                     for it in items if it.get("important")][:12]
        return {"policy_news": policy[:18], "important_flash": important, "headlines": heads[:limit],
                "note": "policy_news=政策/调控相关筛选; important_flash=金十重要快讯(全球宏观/地缘/央行); headlines=全部要闻(时间倒序)"}
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
    {"name": "get_quote", "description": "查个股实时行情: 现价/当日涨跌幅/开高低/成交额/换手。code 直接用 resolve_stock 返回的 code 原样传(A股是裸6位如 600667 / 000657; 港美股 HK.00700 / US.AAPL), 保持原样、A股无需 sh/sz 前缀。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_trend", "description": "查个股近 N 个交易日走势: 累计涨跌/逐日涨跌/上涨天数。支持 A 股/港股/美股。daily_pct 每条是 {date, pct, high_pct, low_pct}: date 是该日真实交易日(YYYY-MM-DD), pct 是收盘涨跌, high_pct/low_pct 是当日最高/最低相对昨收的幅度。最后一条即 last_date(最新交易日)。引用某天涨跌时日期以 date 字段为准。判断历史某天日内有没有摸到涨停/封板还是冲高回落: 看 high_pct——high_pct≈涨停幅度(主板10/创业板科创20)且 pct=high_pct 即收在涨停(封板), high_pct 到了涨停而 pct 明显更低即日内触板后回落。这样无需分时即可还原历史某天盘中。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "days": {"type": "integer", "description": "默认20"}}, "required": ["code"]}},
    {"name": "get_intraday", "description": "当日分时走势(开盘/最高及时间/最低及时间/现价 + 冲高回落幅度 + 路径采样): 判断盘中是不是冲高回落/炸板/尾盘拉升时用, 比日K细。需启用 TDX 数据源, 仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_news", "description": "查个股最近新闻(标题+摘要+时间), 用来找涨跌的消息面原因。支持 A股/港股/美股(东财)。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_announcements", "description": "查个股公告(分红/回购/增减持/业绩预告/重组/股权激励/关联交易等), 结构化且比新闻权威。看公司层面有没有实质事件驱动。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_fund_flow", "description": "查个股主力资金流(谁在买/卖): 今日主力/超大单/大单/中单/小单净额(亿, 实时, 与榜单 f62 同源) + 近几日主力净流入趋势。今日值盘中实时滚动, 主力=超大单+大单。回答'为什么涨/跌、是不是主力在拉、资金进还是出'的关键。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_lhb", "description": "龙虎榜: 传 code→该股近期是否上榜及净买额/机构还是游资席位/上榜原因(看是谁在拉); 不传 code→最近交易日资金净买额榜(主力/游资当天在打哪些票, 看资金主线)。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选; 留空看全市场榜"}}}},
    {"name": "get_red_flags", "description": "客观红线清单: 扫近期公告(监管处罚/立案/退市风险/业绩预亏/商誉减值/交易所问询/违规占用/股东减持等) + 解禁抛压 + 基本面健康度, 列出有事实依据的风险点(按高/中/低排)。回答'这票有没有雷/风险/暴雷过吗/能不能放心拿'时用。命中=有该风险事实, 非卖出建议。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_company_profile", "description": "查公司是做什么的 + 什么背景: 公司简介(主营业务) + 细分行业 + 主营构成(各产品/地区收入占比和毛利率) + 控股/实际控制人/前三大股东(判断国资/央企/地方国企/中科院系/民营/外资性质)。回答'这家公司主营什么、靠什么赚钱、谁控股、什么背景、和同行业务差异'时必用。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
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
    {"name": "get_thesis", "description": "读用户当初记录的买入逻辑(为什么买这只)。回答'我当初为什么买X、X的逻辑还成立吗、帮我复盘X'时用: 拿到 thesis 后对照现价/基本面/消息/红线, 客观说每条理由还成不成立。不传 code 看全部持仓的逻辑。仅当用户记过才有。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选, 留空看全部"}}}},
    {"name": "get_asset_allocation", "description": "查用户全量资产配置: 各大类(股票/现金/理财/基金/加密/机器人)市值+占比 + 现金与理财逐笔明细(金额/年化/持有天数)。回答'现金/理财怎么分配、应急金够不够、资产结构合不合理、流动性够不够'这类资产配置问题时用。不涉及个股买卖。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_trades", "description": "查用户成交记录(含个股/场内ETF/场外基金): 传 code→该标的买卖/加减仓/分红或申赎流水(A股另给综合成本/已实现盈亏/持有天数, 同日有买有卖=做T); 不传→最近全部成交(三类合并)。可用 start/end(YYYY-MM-DD)按成交日期筛区间('这周/6月/上个月'自己换算成日期传)。回答'我什么时候买的、成本多少、做过几次T、这票赚没赚、持有多久、最近/某段时间交易了啥'时用。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string", "description": "可选; 留空看全部"}, "start": {"type": "string", "description": "可选, 起始日 YYYY-MM-DD"}, "end": {"type": "string", "description": "可选, 截止日 YYYY-MM-DD"}}}},
    {"name": "get_market_sentiment", "description": "查大盘打板情绪(涨停数/连板高度/炸板率/赚钱效应/热点板块), 判断是个股原因还是大盘普涨普跌; 也用于判断市场风格(打板赚钱效应高=追涨/动量有效; 炸板率高+亏钱效应=高位分歧/反转)。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_sector_momentum", "description": "板块趋势矩阵: 各行业近N日累计涨跌/连涨动能/净流入。看哪些板块在持续走强(动量延续)、哪些冲高回落(退潮), 判断市场是动量风格还是高低切/轮动, 资金主线在哪。days 默认10。",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "get_hot_rank", "description": "资金人气榜(东财): 关注度最高的个股, 标出哪些在用户持仓。看资金主线/抱团方向。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_hot_concepts", "description": "今日热门概念板块榜(概念粒度, 比行业更细, 如 CPO/HBM/先进封装/玻璃基板/固态电池等): 涨幅+主力净流入。回答'量化/资金这几天在冲哪个具体概念、概念怎么切'时用它。",
     "input_schema": {"type": "object", "properties": {"top": {"type": "integer", "description": "默认15"}}}},
    {"name": "get_board_stocks", "description": "查某个板块/概念里今日涨幅 top-N 的个股(龙头): 涨跌幅/现价/换手/主力净流入。找到主线概念后看里面哪几只领涨、资金集中在谁身上。board 传概念或行业名(如 玻璃基板/CPO/光通信/小金属)或 BK 代码。",
     "input_schema": {"type": "object", "properties": {"board": {"type": "string"}, "top": {"type": "integer", "description": "默认12"}}, "required": ["board"]}},
    {"name": "get_market_news", "description": "全市场财经快讯(含政策面/国家调控: 货币财政、央行、证监会/部委监管、产业政策、行业调控、出口管制/关税、国常会/政治局等重要会议)。分析市场背景、判断政策驱动/调控影响时必看; policy_news 是政策相关筛选。",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "默认40"}}}},
    # Anthropic 服务端联网搜索: 本地工具查不到/可能过期的事实(海外公司是否上市/IPO/代码/政策/最新消息)用它核实, 以联网结果为准而非凭记忆。
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 12},
]

_EXECUTORS = {
    "resolve_stock": lambda a: _tool_resolve_stock(a.get("query", "")),
    "get_quote": lambda a: _tool_get_quote(a.get("code", "")),
    "get_trend": lambda a: _tool_get_trend(a.get("code", ""), a.get("days", 20)),
    "get_intraday": lambda a: _tool_intraday(a.get("code", "")),
    "get_news": lambda a: _tool_get_news(a.get("code", "")),
    "get_announcements": lambda a: _tool_announcements(a.get("code", "")),
    "get_fund_flow": lambda a: _tool_fund_flow(a.get("code", "")),
    "get_lhb": lambda a: _tool_lhb(a.get("code", "")),
    "get_red_flags": lambda a: _tool_red_flags(a.get("code", "")),
    "get_company_profile": lambda a: _tool_company_profile(a.get("code", "")),
    "get_stock_concepts": lambda a: _tool_stock_concepts(a.get("code", "")),
    "get_fundamentals": lambda a: _tool_fundamentals(a.get("code", "")),
    "get_commodity": lambda a: _tool_commodity(a.get("code", "")),
    "get_peers": lambda a: _tool_peers(a.get("code", "")),
    "get_shareholders": lambda a: _tool_shareholders(a.get("code", "")),
    "get_holdings": lambda a: _tool_get_holdings(),
    "get_thesis": lambda a: _tool_get_thesis(a.get("code", "")),
    "get_asset_allocation": lambda a: _tool_asset_allocation(),
    "get_trades": lambda a: _tool_trades(a.get("code", ""), a.get("start", ""), a.get("end", "")),
    "get_market_sentiment": lambda a: _tool_market_sentiment(),
    "get_sector_momentum": lambda a: _tool_sector_momentum(a.get("days", 10)),
    "get_hot_rank": lambda a: _tool_hot_rank(),
    "get_hot_concepts": lambda a: _tool_hot_concepts(a.get("top", 15)),
    "get_board_stocks": lambda a: _tool_board_stocks(a.get("board", ""), a.get("top", 12)),
    "get_market_news": lambda a: _tool_market_news(a.get("limit", 40)),
}


async def _run_tool(tu: dict) -> dict:
    """跑单个工具调用, 把异常/未知工具兜成 {"error":...}。供 asyncio.gather 并发执行。"""
    try:
        fn = _EXECUTORS.get(tu.get("name"))
        if not fn:
            return {"error": f"未知工具 {tu.get('name')}"}
        return await fn(tu.get("input") or {})
    except Exception as e:
        return {"error": str(e)}


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
    "你是市场&个股解读 + 理财规划助手。用户自由提问: 个股为什么涨跌/消息面/跟持仓关系, 【市场风格】类问题"
    "(这周市场在奖励什么打法、是动量追涨还是低吸反转、是题材轮动还是抱团、高低切迹象、资金主线在哪、情绪处在什么周期), "
    "以及【资产配置/现金理财】类问题(现金和理财怎么分、应急金够不够、流动性够不够、整体结构合不合理)。\n"
    "工具: resolve_stock(名字转代码)、get_quote(个股实时行情)、get_trend(个股近N日走势)、get_news(个股新闻)、"
    "get_fund_flow(个股主力资金流:谁在买卖)、get_lhb(龙虎榜:游资/机构席位)、get_stock_concepts(个股所属概念板块)、"
    "get_fundamentals(基本面+估值:营收净利/ROE/PE/PB)、get_commodity(关联金属期货价)、"
    "get_holdings(用户持仓)、get_market_sentiment(大盘打板情绪)、get_sector_momentum(板块趋势矩阵:动量/退潮/资金流)、"
    "get_hot_concepts(热门概念榜)、get_hot_rank(资金人气榜)、get_market_news(政策面)。\n"
    "【个股问题】先 resolve_stock 取代码, 再 get_quote+get_trend; 分析涨跌原因时调用 get_fund_flow(主力资金净流向、主导买卖的资金类型)"
    "+get_news(消息面)+get_announcements(公司公告: 分红回购/业绩预告/重组/股权激励等实质事件, 权威性高于新闻), 异动明显时调用 get_lhb(是否登榜、游资还是机构主导); 用 get_stock_concepts 确认所属概念, "
    "再与 get_hot_concepts/get_sector_momentum 交叉判断是否处于当下资金主线; 需要时用 get_market_sentiment 区分个股事件还是大盘普涨跌; "
    "  · 【说明公司主营与背景】问题涉及某只票时(尤其'为什么涨/两只票对比/值不值得关注'), 调用 get_company_profile 取主营业务+细分行业+主营构成+控股/实控背景, "
    "用一句话说明其盈利来源、与同行的业务差异(如'中芯=大陆晶圆代工龙头、先进制程为主'对'华虹=特色工艺代工、功率器件/8寸为主'); "
    "并依据 controller/top_holders 的股东名指明公司性质——国资/央企/地方国企/中科院系院所背景/民营/外资(如第一大股东'北京中科算源'=中科院计算所系国企)。主营与背景置于涨跌分析之前。\n"
    "  · 【客观红线清单】问'这票有没有雷/风险/出过风险吗/能否放心持有', 或复盘/评估一只票时, 调用 get_red_flags 取客观红线(监管处罚/退市风险/业绩预亏/商誉减值/交易所问询/违规占用/股东减持/解禁抛压/健康度), "
    "按高/中/低列出命中的风险点(附公告日期/依据), 未命中的项一并说明'已扫描且未命中'。此为客观风险事实, 属信息呈现; red_flags 为空时表述为'近期公告/解禁/健康度未扫到明显红线', 同时说明非公告类或更早期的风险可能未覆盖。\n"
    "  · 【说明市场在追捧的题材】涨跌/对比类问题落到驱动题材: 用 get_stock_concepts(所属概念)+get_hot_concepts(近几日资金主攻的概念)+get_news/get_market_news(催化: 政策/涨价/新品/业绩/事件)交叉, "
    "明确指出'本轮资金追捧的题材为 XX/催化为 XX'(如国产替代、存储涨价、算力、设备验证突破), 落到具体题材与催化, 而非笼统表述。\n"
    "若该票或所属板块对政策敏感(有色/小金属/地产/半导体/医药/军工/新能源/平台经济等), 另调 get_market_news 确认有无政策催化或调控压制。\n"
    "  · 【单只个股的主力净流入数字以 get_fund_flow 为准】其 today 值盘中实时滚动(当天累计主力净额, 收盘定格), 与榜单 f62 同源同口径, 另带超大单/大单/中单/小单拆解和近几日趋势, 是该股资金流的权威口径。"
    "get_peers/get_board_stocks/get_hot_concepts 中的'主力净流入亿'用于榜单内横向比较强弱; 表述单只票'今日主力净流入/流出金额'时, 引用 get_fund_flow 的 today 值, 全篇保持同一数值。\n"
    "【基本面/估值】问'估值高低、业绩优劣、盈利质地、有无业绩拐点'时调用 get_fundamentals"
    "(营收/净利及同比、ROE/毛利率/净利率、资产负债率、PE/PB/总市值); 即便仅问涨跌, 涉及'涨幅能否支撑当前估值、估值是否偏高'时, 一并对照基本面位置。"
    "有色/资源股涨跌可另调 get_commodity 查对应金属期货价(铜铝金锌镍锡), 判断是否同步驱动。\n"
    "【同行对比】问'同业里贵不贵、谁是龙头、资金更偏好谁、相对估值'时调 get_peers(同行业 PE/PB/涨幅/主力净流入对照), 配合 get_fundamentals 判断相对位置。\n"
    "【筹码面】问'谁在持股、控股股东/国家队/北向在加减仓、有没有解禁抛压'时调 get_shareholders(十大流通股东增减+北向变动+未来解禁)。\n"
    "【我的成交/持仓盈亏】问'何时买入/成本多少/做过几次T/这票盈亏/持有多久/最近成交记录'时调用 get_trades"
    "(含个股+场内ETF+场外基金; 带 code 查该标的流水, A股另给综合成本+已实现盈亏; 不带 code 查最近全部成交; "
    "问'本周/本月/6月/上个月/最近三天'这类时间范围时, 用下方提供的今天日期换算为 start/end(YYYY-MM-DD)传入筛选)。\n"
    "  · 【依据持有天数表述】get_holdings/get_trades 带每只票的持有天数和开仓日。持有天数=0 或开仓日=今天的, 如实表述为'今日新开的仓, 现价较成本X%'; "
    "'抗跌/防御/长期持有'这类叙事仅用于持有天数较长、确已经历下跌的持仓, 使用前先核对持有天数。\n"
    "  · 【复盘成交需补充当下对照】梳理用户买卖后, 对涉及的个股调用 get_quote(取现价/今日涨跌幅/盘口: 封涨停/炸板/冲高回落), "
    "需要时调用 get_trend 查近日走势, 给出'你卖出的X今日仍在上涨/你买入的Y冲涨停后炸板/现价较你成本X%'这类当下对照, 在罗列成交日期价格的基础上补充。\n"
    "  · 【买入逻辑复盘·thesis】用户问'当初为何买X/X的逻辑是否仍成立/帮我复盘X'时, 先 get_thesis 取其记录的买入逻辑, "
    "将每条理由逐条对照现状(get_quote/get_fundamentals/get_news/get_red_flags), 客观判定'此条仍成立/此条已变化(附依据)'。"
    "用户未记录 thesis 时, 提示其在持仓中补充买入逻辑以便后续复盘。此为客观事实复盘, 逻辑是否变化如实陈述, 买卖决策由用户自定。\n"
    "  · 【历史涨跌的日期以工具返回值为准】表述'X月X日涨跌幅'时, 日期取 get_trend.daily_pct 中对应条目的 date 字段, 或 get_quote/get_intraday 的当天数据。"
    "daily_pct 已按真实交易日标注(周末/节假日自然断档), 照此引用; 某条对应日期不明确时, 仅表述涨跌幅度。\n"
    "(港美股可用 get_quote+get_trend+get_news+get_fundamentals; 资金流/龙虎榜/概念/同行/筹码/商品/公告 仅 A 股支持, 港美股无数据时如实说明。)\n"
    "【资产配置/现金理财——给出框架与分析】问'现金/理财如何分配、应急金是否充足、流动性是否充足、结构是否合理'时, "
    "调用 get_asset_allocation 查全量结构(各大类占比 + 现金/理财逐笔金额/年化/持有天数), 然后给出:① 流动性分层框架(活期应急金 / 短期可取理财 / 长期增值, 应急金一般覆盖 3-6 个月支出)、"
    "② 货币基金 vs 银行理财 vs 国债逆回购 vs 定期存款 的收益-流动性-期限权衡(各自适配的层级)、③ 指出用户当前结构的具体问题(如现金占比过高承受贬值、理财全为活期未获取长期溢价、应急金不足、过度集中某一类)。"
    "此为理财规划框架 + 现状分析, 属允许范围。**收尾说明: 以上为通用框架与你当前结构的分析, 非持牌投顾建议, 具体如何分配、选择哪只产品由你自定。** "
    "框架与现状问题充分展开; 具体产品择时(如'选这只货基/这款理财')留给用户决定。\n"
    "【'能否进场/明日走势/能否持有'这类问题】完整给出客观分析"
    "(涨跌原因、消息面、政策面、走势位置、与持仓关系、双向风险), 充分提供决策依据, "
    "方向性的进出/仓位由用户决定, 结尾以'方向性的进出/仓位你自己定, 我只给客观信息'收尾。\n"
    "【主动关联题材与用户持仓——任何事件/题材/板块/宏观/新闻问题均先执行】用户问某个事件或题材时(如'欧洲空调卖疯了''CPO还能涨吗''降息利好谁''钢铁怎么样'), "
    "**先用 web_search 核实事件本身与关键数据**(销量/同比/政策细节均经联网获取, 不依赖记忆下结论), 再调用 get_holdings 查其持仓, 主动将该题材与其持仓关联作答: 指出哪只持仓属于该产业链(从股票名可判断时直接说明, 名称无法判断主营时用 get_company_profile/get_stock_concepts 确认), 并说明属直接受益还是间接关联。"
    "持仓中确无相关标的时, 如实表述'你当前持仓中无直接关联此题材的标的, 最接近的是X(附原因)'或'此题材与你的持仓无关'。"
    "'与我持仓的关系'是用户问题材时最核心的落点, 即便未点名某只票也主动作答; 之后再询问是否展开查看某只受益标的的数据。\n"
    "【市场风格问题】用 get_market_sentiment(打板赚钱效应高=追涨/动量有效; 炸板率高+亏钱效应=高位分歧/反转占优) + "
    "get_sector_momentum(连涨板块多=动量延续; 普遍冲高回落=退潮/高低切) + get_hot_concepts(概念主攻) + get_hot_rank(资金主线/抱团) 综合判断, "
    "用具体数字描述'本周市场在奖励何种行为、惩罚何种行为、资金流向何处'。此为对市场资金行为的客观描述, 是否跟随由用户判断。\n"
    "【政策面/国家调控——市场背景必看】分析市场背景、或个股/板块异动疑似政策驱动时, 必须调 get_market_news 看政策面"
    "(货币/财政: 降准降息/LPR/逆回购/专项债; 监管: 证监会/部委/反垄断/平台经济; 产业政策与行业调控: 收储/去产能/反内卷/限价/补贴; "
    "地缘: 出口管制/关税/制裁/实体清单; 重要会议: 国常会/政治局/发改委部署), 必要时再 web_search 补最新政策细节。"
    "需指出'本轮行情/该板块背后有无政策催化或调控压制'(如 收储拉动有色、AI/算力产业政策、地产/化债/反内卷、关税扰动出口链), 用快讯标题/日期佐证。\n"
    "【分析框架·一线打板资金视角】(客观套用, 不点名出处, 不据此给操作建议):\n"
    "  · 量化/游资以【板块/概念】为维度运作, 而非单票。研判市场=研判资金近几日主攻的板块概念及其节奏"
    "(概念切换可能在一两日内发生, 如从 A 概念直接切至 B 概念)。需识别资金主线板块及有无概念轮动切换。\n"
    "  · 概念粒度优先用 get_hot_concepts(可取 CPO/HBM/先进封装/玻璃基板 等具体概念名 + 主力净流入), "
    "其粒度比 get_sector_momentum 的行业级更细, 是研判'量化主攻哪个概念'的关键; 两者结合使用(概念定位主攻方向, 行业动量判断延续性)。"
    "锁定主线概念后, 用 get_board_stocks(传入概念名)查看其中今日涨幅 top 的个股——确认龙头及资金集中的标的, 是'板块→龙头'落地的关键一步。\n"
    "  · 个股位置分层判断'看逻辑 vs 纯资金博弈': 短线打板股 3板以下看逻辑(题材/催化/空间是否扎实)、3板以上逻辑让位于纯资金接力; "
    "趋势股 涨幅1倍(100%)以内看逻辑、超1倍转为纯资金博弈。即低位看逻辑、高位看资金, 指出领涨标的当前所处阶段。\n"
    "  · 据此描述: 资金的板块主线、概念切换的轮动节奏、领涨票处于'看逻辑'还是'资金博弈'区。\n"
    "  · 数据粒度: get_hot_concepts 提供概念级(今日榜), get_sector_momentum 提供行业级近N日动量, 配合使用。"
    "概念榜为当日快照, '近几日如何切换'的多日轨迹结合行业动量推断; 概念榜偶发不可达(数据源抖动)时退回行业级并说明。概念名一律取自榜单返回值。\n"
    "每个结论均需工具数据支撑。\n"
    "【硬规则·个股层面】个股/场内标的的回答停留在客观信息与市场逻辑层面: 主营与背景、异动原因、消息与政策、走势位置、资金结构、与持仓的关系、双向风险, 均充分给出。"
    "方向性的买卖与择时由用户自定, 充分提供依据供其决策, 结尾以'进出/仓位你自己定, 我只给客观信息'收尾。"
    "陈述'市场在奖励动量'这类客观规律, 落到用户身上时停留在'市场正如此运作', 由其自行判断是否跟随。信息不足时表述为不确定, 数字与新闻一律引用工具返回值。\n"
    "(资产配置/现金理财层面的通用框架 + 现状分析照常给出, 见上方【资产配置/现金理财】, 同样停留在框架与现状层面, 具体产品择时交由用户。)\n"
    "【知识边界·先搜再答】你的训练知识有截止日, 海外公司上市/IPO/重组/政策/某公司近况/近期事件这类时效性强的事实, 一律以联网结果为准。"
    "涉及外部世界近期事实(事件真伪、销量/出口/同比、政策细节、某公司最新动态)时, 先用 web_search 获取当前事实再作答; 以检索结果为准并标注来源/日期, 检索到标的代码时再用 get_quote 查实时行情; web_search 亦无结果时如实表述'查不到/无法确认, 建议你自行核实'。\n"
    "【正文中的具体数字需有据】同比/金额/销量/份额/排名/价格 这类具体数字, 一律来自 web_search 结果或本地工具返回方写入正文, 并尽量附来源/时间。"
    "仅存于记忆、未经联网或工具核实的数字, 用定性表述替代(如'出口明显放量''需求高增''普及率很低'), 或明确标注'具体数字需联网核实'——区分'量级估计'与'确切数字', 记忆中的数字以定性表述呈现而非作为实测报出。\n"
    "【信息分级——结论依赖的关键数字/事实标来源等级】三档: "
    "[实测]=本地工具实时/接口返回(行情/资金流/走势/基本面/主营/股东/持仓/成交), 最硬, 直接用; "
    "[联网]=web_search 搜到的有出处二手信息(媒体/研报/公告转述), 带上来源与时间; "
    "[待核实]=只来自你的记忆、未经工具或联网证实, 用定性说法或明确标[待核实]。"
    "仅对**支撑结论的关键项**标注等级(如'今日主力净流入23亿[实测]''欧洲出口同比+39.5%[联网·东财2026冷年]'), 无需逐个数字标注, 标签控制在关键项。\n"
    "【多源校验——重要外部数字尽量核对第二来源】出口/销量/同比/份额/市占 这类影响结论的外部数字, 尽量检索一个独立来源核对: "
    "本地工具值与联网值、或两条联网结果明显不一致时, 列出两个数值并指明'两源不一致, 倾向以X为准(原因)/暂存疑', 将分歧呈现给用户; 一致时正常引用。单一来源获取的如实标注[联网]单源, 据实说明未经交叉验证。\n"
    "【时效——分清'行情'和'消息面'两类数据, 各按各的节奏取】问'这两天/最近/周末在炒什么、情绪、还在发酵吗'这类时:\n"
    "  · 消息面(新闻/政策/社媒情绪/研报)不随交易日休市, 周末持续更新。周末时主动调用 web_search, 按日期检索周六周日及最近几天的新消息, 这是研判下周开盘前题材酝酿的关键窗口。检索到周末或近几天日期的新催化, 即为当前正在发酵的题材, 据实陈述。\n"
    "  · 【广覆盖·检索分散到不同板块】研判'在炒什么/情绪/发酵'这类全市场问题, 检索目标是覆盖尽量多的不同板块, 而非把最热的一个板块反复搜。先调 get_hot_concepts + get_sector_momentum 取当周实际活跃的板块/概念清单(通常有 6-10 个不同板块, 含强势与异动), 据此让每次 web_search 锚定一个不同板块, 一个板块仅搜一次。"
    "在板块维度之上, 另搜跨市场的几条横向线索各一次: 大盘情绪与赚钱效应、政策面与重要会议、海外市场与地缘扰动、机构周末策略与下周展望。合计发起 8-12 次 web_search, 同一轮里并行发起多个(每轮 4-6 个), 用 2-3 轮检索完。"
    "板块分布要均衡: 科技链(半导体/算力/光模块等)合计至多占 2-3 次检索, 其余分给消费/医药/有色金属/金融/地产/军工/新能源/周期/AI应用 等不同方向, 确保覆盖面铺开。"
    "每个角度用'日期/本周末/最新 + 该板块关键词'组织检索词(如'2026年6月27日 有色金属 铜 周末 消息''本周末 创新药 政策 最新''机构 下周 A股 策略')。\n"
    "  · 【去重·按板块归并呈现】多次检索常返回同一条新闻, 同一事件只陈述一次, 归到其所属板块下; 各板块下给该板块独有的催化, 不重复转述其他板块已讲过的内容。最终输出按板块分段(每个板块一节: 催化+日期+对下周影响), 各板块篇幅大致均衡, 避免通篇集中于单一最热板块。\n"
    "  · 行情数据(价格/资金流/涨跌/情绪温度)在周末定格于上一交易日收盘快照: get_market_sentiment/get_sector_momentum/get_hot_concepts 周末返回周五读数, 引用时统一表述为'周五收盘快照', 当前热度以这份周五读数为准陈述。\n"
    "  · 每个事件标注真实日期: 数月前的政策/数据/价格归入背景脉络并标注真实月份(如1月出口管制、2月钨价同比), 周末及近几天新增的消息归入正在发酵, 两者分段陈述。\n"
    "  · 仅在检索后确认近几天无新进展时, 表述为'近两天无新消息, 以下为更早的背景脉络', 即先检索后陈述。\n"
    "回答用简体中文, 简洁直接, 分点列出证据(数字), 工具数据支撑的客观结论明确给出。"
)


def _system() -> str:
    """系统提示 + 当前日期(让 agent 能把'这周/本月/上个月'换算成 get_trades 的 start/end)。"""
    import datetime as _dt
    d = _dt.date.today()
    wk = "一二三四五六日"[d.weekday()]
    monday = (d - _dt.timedelta(days=d.weekday())).isoformat()
    weekend = d.weekday() >= 5  # 周六/周日 A股休市(节假日未单列, 以工具返回日期为准)
    last_trade = d - _dt.timedelta(days=d.weekday() - 4) if weekend else d
    ltwk = "一二三四五六日"[last_trade.weekday()]
    mkt = (f"今天周末休市, 行情/资金/情绪类工具返回的是最近交易日 周{ltwk}({last_trade.isoformat()}) 的收盘快照。"
           f"引用这些数据时, 用'周{ltwk}收盘'或具体日期({last_trade.isoformat()})指代, 当前问题里以'今日/今天/盘中'指代的均换算为该交易日, 周末无盘中数据。"
           if weekend else
           "今天是交易日, 行情类工具盘中返回实时滚动值、收盘后返回当日收盘值, 可用'今日'指代。")
    return _SYSTEM + (f"\n【今天】{d.isoformat()} 周{wk}; 本周一={monday}, 本月1号={d.replace(day=1).isoformat()}。"
                      f"用户问时间范围时据此换算 start/end。\n【交易日状态】{mkt}")


_TOOL_CN = {
    "resolve_stock": "解析代码", "get_quote": "查行情", "get_trend": "查走势",
    "get_news": "查新闻", "get_intraday": "查分时", "get_announcements": "查公告", "get_fund_flow": "查资金流", "get_lhb": "查龙虎榜",
    "get_company_profile": "查公司主营", "get_red_flags": "查红线风险", "get_stock_concepts": "查所属概念", "get_fundamentals": "查基本面", "get_commodity": "查商品价",
    "get_peers": "同行对比", "get_shareholders": "查股东解禁",
    "get_holdings": "看持仓", "get_thesis": "看买入逻辑", "get_asset_allocation": "看资产配置", "get_trades": "查成交记录", "get_market_sentiment": "看大盘情绪",
    "get_sector_momentum": "看板块动量", "get_hot_rank": "看资金热度",
    "get_hot_concepts": "看热门概念", "get_board_stocks": "查板块龙头", "get_market_news": "看政策快讯", "web_search": "联网搜索",
}


def _clean_answer(text: str) -> str:
    """模型 web_search 后常在正文里内联 <cite index="3-1">...</cite> 这种引用标签(非结构化 citation),
    前端按纯文本渲染会原样露出。剥掉标签保留里面文字。"""
    t = text or ""
    t = _re.sub(r"</?cite[^>]*>", "", t)          # <cite index="x">/<cite ...>/</cite>
    t = _re.sub(r"[ \t]+\n", "\n", t)
    return t.strip()


def _collect_sources(content: list, acc: list, seen: set) -> list:
    """从一轮响应里抽 web_search 命中的网页来源(标题+url), 按 url 去重累加到 acc。
    结构: web_search_tool_result 块的 content 是 [{type:web_search_result, title, url, page_age}]。
    返回本轮新增的来源(供流式实时推送)。"""
    fresh = []
    for b in content:
        if b.get("type") != "web_search_tool_result":
            continue
        inner = b.get("content")
        if not isinstance(inner, list):
            continue
        for r in inner:
            if r.get("type") != "web_search_result":
                continue
            url = (r.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            item = {"title": (r.get("title") or url).strip(), "url": url, "age": r.get("page_age")}
            acc.append(item)
            fresh.append(item)
    return fresh


def _assemble_cited_text(content: list, sources: list, seen: set) -> str:
    """拼接最终答案文本, 在每个带结构化 citations 的文字段尾插入 ⟦N⟧ 角标。
    N = 该来源在 sources 列表中的 1-based 序号(与底部'联网来源'列表同号, 前端渲染成可点上标)。
    citations 引到但 web_search_tool_result 没收录的 url, 补进 sources 末尾。"""
    url2idx = {s["url"]: i + 1 for i, s in enumerate(sources)}
    parts = []
    for b in content:
        if b.get("type") != "text":
            continue
        parts.append(b.get("text") or "")
        cits = b.get("citations")
        if not isinstance(cits, list) or not cits:
            continue
        marks, here = [], set()
        for ci in cits:
            url = (ci.get("url") or "").strip()
            if not url or url in here:
                continue
            here.add(url)
            if url not in url2idx:
                sources.append({"title": (ci.get("title") or url).strip(), "url": url, "age": None})
                seen.add(url)
                url2idx[url] = len(sources)
            marks.append(f"⟦{url2idx[url]}⟧")
        if marks:
            parts.append("".join(marks))
    return _clean_answer("".join(parts))


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
    sources: list = []
    seen_urls: set = set()
    for rnd in range(_MAX_ROUNDS):
        try:
            resp = await asyncio.to_thread(
                _llm.call_claude_messages, messages, _system(), _MODEL, 4096, _active_tools())
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
        # 抽取本轮 web_search 命中的网页来源, 实时推给前端(去重累加)
        fresh = _collect_sources(content, sources, seen_urls)
        if fresh:
            yield {"type": "sources", "sources": fresh}
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            before = len(sources)
            text = _assemble_cited_text(content, sources, seen_urls)
            if len(sources) > before:        # citations 引到的新 url 补推一条 sources 事件, 保证角标可解析
                yield {"type": "sources", "sources": sources[before:]}
            yield {"type": "answer", "text": text}
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
        # 同一轮里的工具相互独立(各自的网络请求), 并发跑, 顺序保留, 单个失败不连累其他
        outs = await asyncio.gather(*[_run_tool(tu) for tu in tus])
        results = [{"type": "tool_result", "tool_use_id": tu.get("id"),
                    "content": _json.dumps(out, ensure_ascii=False)}
                   for tu, out in zip(tus, outs)]
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
    sources: list = []
    seen_urls: set = set()
    for rnd in range(_MAX_ROUNDS):
        try:
            resp = await asyncio.to_thread(
                _llm.call_claude_messages, messages, _system(), _MODEL, 4096, _active_tools())
        except Exception as e:
            return {"answer": "", "error": str(e), "tools_used": tools_used, "rounds": rnd}
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        # 服务端 web_search 也计入 tools_used(它是 server_tool_use, 不在 tus 里, 否则会被漏记成"没联网")
        tools_used.extend("web_search" for b in content
                          if b.get("type") == "server_tool_use" and b.get("name") == "web_search")
        _collect_sources(content, sources, seen_urls)
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            text = _assemble_cited_text(content, sources, seen_urls)
            return {"answer": text, "tools_used": tools_used, "rounds": rnd + 1, "sources": sources}
        tools_used.extend(tu.get("name", "") for tu in tus)
        # 同一轮里的工具并发跑(相互独立), 顺序保留, 单个失败不连累其他
        outs = await asyncio.gather(*[_run_tool(tu) for tu in tus])
        results = [{"type": "tool_result", "tool_use_id": tu.get("id"),
                    "content": _json.dumps(out, ensure_ascii=False)}
                   for tu, out in zip(tus, outs)]
        messages.append({"role": "user", "content": results})
    return {"answer": "（分析步数超限, 请换个问法或更具体）", "tools_used": tools_used, "rounds": _MAX_ROUNDS}
