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
        "amount_yuan": _f(data.get("Amount")),
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


async def minute(code: str) -> dict | None:
    """分时(当日 9:30-11:30 / 13:00-15:00, 至多 240 点)。返回 {date, points:[{time,price,手}]} 或 None。"""
    if not _BASE_URL:
        return None
    data = await asyncio.to_thread(_get_sync, "/api/minute", {"code": code})
    if not isinstance(data, dict):
        return None
    pts = []
    for x in (data.get("List") or []):
        p = _f(x.get("Price"))
        if p is None:
            continue
        pts.append({"time": x.get("Time"), "price": p, "手": x.get("Number")})
    if not pts:
        return None
    return {"date": data.get("date"), "points": pts}


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
