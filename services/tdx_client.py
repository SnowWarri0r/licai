"""通达信(TDX) REST 数据源 —— 可插拔。

对接 https://github.com/oficcejo/tdx-api (Go 服务, 默认 localhost:8080), 提供
五档盘口 / 分时 / 逐笔, 补东财/新浪没有的数据。

可插拔: 不配 base_url 就整体禁用, 所有函数返回 None, 上层自动回退现有源。
连不上 / 报错也返回 None, 绝不抛到调用方。

单位换算: 价=厘(÷1000), 量=手(×100=股), 成交额=厘(÷1000)。
"""
from __future__ import annotations
import asyncio

_BASE_URL = ""          # 空 = 禁用
_TIMEOUT = 3.0          # localhost, 短超时; 连不上快速回退


def configure(base_url: str = "") -> None:
    global _BASE_URL
    _BASE_URL = (base_url or "").rstrip("/")


def is_enabled() -> bool:
    return bool(_BASE_URL)


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
            num = x.get("Number")           # 股
            if price is None:
                continue
            try:
                lots = round(float(num) / 100, 1)
            except (ValueError, TypeError):
                lots = None
            out.append({"price": price, "手": lots, "股": num})
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
    data = await asyncio.to_thread(_get_sync, "/api/quote", {"code": code})
    return _normalize_quote(data) if data is not None else None


async def _ref_price(code: str):
    """拿 quote 的昨收/现价当锚, 用于判定分时/逐笔的价格基数(个股×1000 / ETF×10000)。"""
    q = await quote(code)
    if not q:
        return None
    return q.get("prev_close") or q.get("price")


def _price_div(raw_prices, ref) -> float:
    """分时/逐笔基数: 个股 ÷1000, ETF/基金 ÷10000。
    用 quote 锚价判定: 若 raw/1000 是锚价的 ~10 倍, 说明该 ÷10000。锚拿不到则退回 ÷1000。"""
    if not ref or ref <= 0:
        return 1000.0
    vals = sorted(p for p in (raw_prices or []) if isinstance(p, (int, float)) and p > 0)
    if not vals:
        return 1000.0
    mid = vals[len(vals) // 2]
    return 10000.0 if (mid / 1000.0) / ref > 5 else 1000.0


async def minute(code: str) -> dict | None:
    """分时(当日 9:30-11:30 / 13:00-15:00, 至多 240 点)。返回 {date, points:[{time,price,手}]} 或 None。"""
    if not _BASE_URL:
        return None
    data = await asyncio.to_thread(_get_sync, "/api/minute", {"code": code})
    if not isinstance(data, dict):
        return None
    raw = data.get("List") or []
    div = _price_div([x.get("Price") for x in raw], await _ref_price(code))
    pts = []
    for x in raw:
        p = _f(x.get("Price"), div)
        if p is None:
            continue
        pts.append({"time": x.get("Time"), "price": p, "手": x.get("Number")})
    if not pts:
        return None
    return {"date": data.get("date"), "points": pts}


_KTYPES = {"minute1", "minute5", "minute15", "minute30", "hour", "day", "week", "month"}


async def kline(code: str, ktype: str = "day", limit: int = 200) -> dict | None:
    """多周期 K 线(TDX /api/kline-history)。ktype: day/week/month/hour/minute1/5/15/30。
    返回 {type, bars:[{date, open, high, low, close, volume手, amount元}]} 或 None。"""
    if not _BASE_URL:
        return None
    kt = ktype if ktype in _KTYPES else "day"
    data = await asyncio.to_thread(_get_sync, "/api/kline-history",
                                   {"code": code, "type": kt, "limit": str(int(limit or 200))})
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
    data = await asyncio.to_thread(_get_sync, "/api/trade", {"code": code})
    rows = (data or {}).get("List") if isinstance(data, dict) else None
    if not rows:
        return None
    div = _price_div([x.get("Price") for x in rows], await _ref_price(code))
    dirs = {0: "买", 1: "卖", 2: "中性"}
    ticks = []
    for x in rows:
        p = _f(x.get("Price"), div)
        if p is None:
            continue
        t = str(x.get("Time") or "")
        ticks.append({"time": t[11:19] if "T" in t else t, "price": p,
                      "手": x.get("Volume"), "dir": dirs.get(x.get("Status"), "")})
    ticks = ticks[::-1][:int(limit or 60)]   # 最近在前
    return {"ticks": ticks} if ticks else None


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
