"""通达信(TDX) REST 数据源 —— 可插拔。

对接 https://github.com/oficcejo/tdx-api (Go 服务, 默认 localhost:8080), 提供
五档盘口 / 分时 / 逐笔, 补东财/新浪没有的数据。

可插拔: 不配 base_url 就整体禁用, 所有函数返回 None, 上层自动回退现有源。
连不上 / 报错也返回 None, 绝不抛到调用方。

单位换算: 价=厘(÷1000), 量=手(×100=股), 成交额=厘(÷1000)。
"""
from __future__ import annotations
import asyncio
import math

_BASE_URL = ""          # 空 = 禁用
_TIMEOUT = 3.0          # localhost, 短超时; 连不上快速回退


def configure(base_url: str = "") -> None:
    global _BASE_URL
    _BASE_URL = (base_url or "").rstrip("/")


def is_enabled() -> bool:
    return bool(_BASE_URL)


def _mkcode(code: str) -> str:
    """TDX 服务对裸代码自行推断市场, 但不认 588/56xxxx 等新段(报'代码长度错误')。
    一律显式带小写市场前缀(sh/sz/bj —— quote 端点只认小写), 复用 market_data 的市场判定
    (6/9/5→sh, 8/4/920→bj, 其余→sz)。"""
    c = (code or "").strip()
    if c[:2].lower() in ("sh", "sz", "bj"):
        return c[:2].lower() + c[2:]
    from services.market_data import _sina_symbol
    return _sina_symbol(c)


def _get_sync(path: str, params: dict) -> dict | None:
    if not _BASE_URL:
        return None
    import requests
    s = requests.Session()
    s.trust_env = False                     # 本地直连, 不走系统/环境代理
    try:
        r = s.get(f"{_BASE_URL}{path}", params=params, timeout=_TIMEOUT,
                  proxies={"http": None, "https": None})
        j = r.json()
    except Exception:
        return None
    if not isinstance(j, dict) or j.get("code") not in (0, "0", None):
        return None
    return j.get("data")


def _f(v, div=1000.0):
    try:
        return round(float(v) / div, 3)
    except (ValueError, TypeError):
        return None


def _normalize_quote(data) -> dict | None:
    """/api/quote 的 data(list) → 标准化第一只: 价/开高低/前收 + 五档 + 内外盘。"""
    if isinstance(data, list):
        data = data[0] if data else None
    if not isinstance(data, dict):
        return None
    k = data.get("K") or {}

    def level(arr):
        out = []
        for x in (arr or [])[:5]:
            price = _f(x.get("Price"))
            num = x.get("Number")           # TDX 盘口量单位是「手」(与 TotalHand/内外盘一致), 不是股
            if price is None:
                continue
            try:
                hand = int(round(float(num)))
            except (ValueError, TypeError):
                hand = None
            out.append({"price": price, "手": hand,
                        "股": (hand * 100 if hand is not None else None)})
        return out
    return {
        "code": data.get("Code"),
        "price": _f(k.get("Close")), "prev_close": _f(k.get("Last")),
        "open": _f(k.get("Open")), "high": _f(k.get("High")), "low": _f(k.get("Low")),
        "amount_yuan": _f(data.get("Amount"), 1.0),   # Amount 本就是元, 不再 ÷1000
        "volume_hand": data.get("TotalHand"),
        "内盘手": data.get("InsideDish"), "外盘手": data.get("OuterDisc"),
        "bids": level(data.get("BuyLevel")),   # 买一~买五
        "asks": level(data.get("SellLevel")),  # 卖一~卖五
    }


async def quote(code: str) -> dict | None:
    """五档盘口 + 实时价。返回标准化 dict 或 None(禁用/失败)。"""
    if not _BASE_URL:
        return None
    data = await asyncio.to_thread(_get_sync, "/api/quote", {"code": _mkcode(code)})
    return _normalize_quote(data) if data is not None else None


async def _ref_price(code: str):
    """拿 quote 的现价当锚, 用于判定分时/逐笔的价格基数(个股×1000 / ETF×10000)。
    取现价而非昨收: 当日分时/逐笔价贴的是现价, 昨收在新股首日/大幅波动时离得很远。"""
    q = await quote(code)
    if not q:
        return None
    return q.get("price") or q.get("prev_close")


def _price_div(raw_prices, ref, code: str = "") -> float:
    """分时/逐笔基数: 个股 ÷1000, 场内 ETF/LOF ÷10000(TDX 对场内基金多给一位小数)。

    标准 6 位 A 股代码直接按前缀定档(1x/5x 场内基金, 其余个股)——确定性结论, 不受
    行情影响, 历史日同样成立。只有非标准代码才退回锚价, 在两档里选让价格最接近锚的
    那档(比值取对数距离)。

    原实现拿昨收当锚 + ">5 倍即 ÷10000" 阈值, 新股首日会翻车: 长鑫 688825 上市首日
    +466%, raw/1000 是昨收(发行价 8.66)的 5.4 倍, 个股被误判成 ETF, 整条分时缩小 10 倍
    (显示 3.7~5.6 元, 实际 38~55 元)。
    """
    bare = (code or "")[-6:]
    if len(bare) == 6 and bare.isdigit():     # 标准 A 股代码: 品种由前缀确定, 不看行情
        return 10000.0 if bare[0] in ("1", "5") else 1000.0   # 1x/5x 场内基金, 其余个股
    vals = sorted(p for p in (raw_prices or []) if isinstance(p, (int, float)) and p > 0)
    if not ref or ref <= 0 or not vals:
        return 1000.0
    mid = vals[len(vals) // 2]
    # 选让 mid/div 与 ref 比值最接近 1 的一档(对数距离, 边界在 √10 而非原来的 5)
    return min((1000.0, 10000.0),
               key=lambda d: abs(math.log((mid / d) / ref)) if mid / d > 0 else float("inf"))


async def minute(code: str, date: str = "") -> dict | None:
    """分时(9:30-11:30 / 13:00-15:00, 至多 240 点)。date=YYYY-MM-DD/YYYYMMDD 取历史某日, 空=今日。
    返回 {date, points:[{time,price,手}]} 或 None。"""
    if not _BASE_URL:
        return None
    params = {"code": _mkcode(code)}
    hist = bool(date)
    if hist:
        params["date"] = str(date).replace("-", "")
    data = await asyncio.to_thread(_get_sync, "/api/minute", params)
    if not isinstance(data, dict):
        return None
    raw = data.get("List") or []
    q = await quote(code)                       # 基数锚(现价); 历史日只用于判基数, 差几倍不影响档位判定
    ref = (q or {}).get("price") or (q or {}).get("prev_close")
    div = _price_div([x.get("Price") for x in raw], ref, code)
    pts = []
    for x in raw:
        p = _f(x.get("Price"), div)
        if p is None:
            continue
        pts.append({"time": x.get("Time"), "price": p, "手": x.get("Number")})
    if not pts:
        return None
    # TDX 分时从 09:31 起, 缺 09:30 开盘点。当日用 quote 开盘价(集合竞价结果)补锚点;
    # 历史日的开盘价不在 quote 里, 不补。手=0(竞价量不在分钟数据里, 不画量柱)。
    op = (q or {}).get("open")
    if not hist and op and not str(pts[0].get("time") or "").startswith("09:30"):
        pts.insert(0, {"time": "09:30", "price": op, "手": 0})
    return {"date": data.get("date"), "points": pts}


_KTYPES = {"minute1", "minute5", "minute15", "minute30", "hour", "day", "week", "month"}


async def kline(code: str, ktype: str = "day", limit: int = 200) -> dict | None:
    """多周期 K 线(TDX /api/kline-history)。ktype: day/week/month/hour/minute1/5/15/30。
    返回 {type, bars:[{date, open, high, low, close, volume手, amount元}]} 或 None。"""
    if not _BASE_URL:
        return None
    kt = ktype if ktype in _KTYPES else "day"
    data = await asyncio.to_thread(_get_sync, "/api/kline-history",
                                   {"code": _mkcode(code), "type": kt, "limit": str(int(limit or 200))})
    rows = (data or {}).get("List") if isinstance(data, dict) else None
    if not rows:
        return None
    bars = []
    for k in rows:
        c = _f(k.get("Close"))
        o, h, lo = _f(k.get("Open")), _f(k.get("High")), _f(k.get("Low"))
        if c is None or not o or not h or not lo:   # 跳过未成形/占位 bar(今日 OHLC 含 0)
            continue
        bars.append({"date": str(k.get("Time") or "")[:19].replace("T", " "),
                     "open": o, "high": h, "low": lo, "close": c,
                     "volume": k.get("Volume"), "amount": _f(k.get("Amount"))})
    return {"type": kt, "bars": bars} if bars else None


async def trade(code: str, limit: int = 60) -> dict | None:
    """当日逐笔成交(TDX /api/trade)。返回 {ticks:[{time, price, 手, dir}]}(最近在前) 或 None。
    dir: 买/卖/中性 (Status 0/1/2)。"""
    if not _BASE_URL:
        return None
    data = await asyncio.to_thread(_get_sync, "/api/trade", {"code": _mkcode(code)})
    rows = (data or {}).get("List") if isinstance(data, dict) else None
    if not rows:
        return None
    div = _price_div([x.get("Price") for x in rows], await _ref_price(code), code)
    dirs = {0: "买", 1: "卖", 2: "中性"}
    ticks = []
    for x in rows:
        p = _f(x.get("Price"), div)
        if p is None:
            continue
        t = str(x.get("Time") or "")
        ticks.append({"time": t[11:19] if "T" in t else t, "price": p,
                      "手": x.get("Volume") or 0, "dir": dirs.get(x.get("Status"), "")})
    ticks = ticks[::-1]   # 最近在前
    # 聚类: TDX 给的是分钟级逐笔, 同一分钟会拆成多条, 把同(时刻+价+方向)的归并、手数求和,
    # 否则同价分好几行。按首次出现顺序保留。
    merged, idx = [], {}
    for t in ticks:
        key = (t["time"], t["price"], t["dir"])
        if key in idx:
            merged[idx[key]]["手"] += (t["手"] or 0)
        else:
            idx[key] = len(merged)
            merged.append(dict(t))
    merged = merged[:int(limit or 60)]
    return {"ticks": merged} if merged else None


async def test_connection(base_url: str = "") -> dict:
    """连通性自检(给 settings 用): 试拉一只票的 quote。"""
    global _BASE_URL
    old = _BASE_URL
    if base_url:
        _BASE_URL = base_url.rstrip("/")
    try:
        q = await quote("000001")
        ok = bool(q and q.get("price"))
        return {"ok": ok, "sample": q if ok else None,
                "error": None if ok else "连不上或返回空(确认 TDX 服务已起、能连通达信服务器)"}
    finally:
        _BASE_URL = old
