"""Price/value fetchers for funds, crypto, and trading bots.

- FUND: 天天基金 realtime NAV estimate (场外公募/QDII/LOF/ETF联接)
- CRYPTO: OKX public market API (free, no key)
- BOT: manual value (user updates periodically)
"""
from __future__ import annotations
import asyncio
import json
import re
import time
import os
import requests as _requests

# Dedicated session for crypto exchanges (OKX/Binance often blocked without proxy).
# Respects CRYPTO_PROXY env var; defaults to local Clash port if running.
_CRYPTO_PROXY = os.environ.get("CRYPTO_PROXY", "http://127.0.0.1:7897")
_crypto_session = _requests.Session()
_crypto_session.trust_env = False
_crypto_session.proxies = {"http": _CRYPTO_PROXY, "https": _CRYPTO_PROXY}


_FUND_TTL = 120  # 2 min — NAV estimates update every minute during trading
_CRYPTO_TTL = 30
_fund_cache: dict[str, tuple[dict, float]] = {}
_crypto_cache: dict[str, tuple[dict, float]] = {}


# --- Fund (天天基金) ---

def _fetch_fund_name_sync(code: str) -> str:
    """Get fund display name from pingzhongdata."""
    try:
        r = _requests.get(f"http://fund.eastmoney.com/pingzhongdata/{code}.js", timeout=6)
        if r.ok:
            m = re.search(r'fS_name\s*=\s*"([^"]+)"', r.text)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def _fetch_fund_nav_sync(code: str) -> dict | None:
    """Latest published NAV via lsjz endpoint (works for QDII/LOF/closed funds)."""
    url = f"https://api.fund.eastmoney.com/f10/lsjz?fundCode={code}&pageIndex=1&pageSize=1"
    try:
        r = _requests.get(url, timeout=6,
                          headers={"Referer": "http://fundf10.eastmoney.com/",
                                   "User-Agent": "Mozilla/5.0"})
        d = r.json()
        items = (d.get("Data") or {}).get("LSJZList") or []
        if not items:
            return None
        it = items[0]
        return {
            "nav": float(it.get("DWJZ", 0) or 0),
            "change_pct": float(it.get("JZZZL", 0) or 0),
            "nav_date": it.get("FSRQ", ""),
        }
    except Exception as e:
        print(f"[fund-nav] {code} failed: {e}")
        return None


def _fetch_fund_meta_sync(code: str) -> dict | None:
    """Fallback: name from pingzhongdata + latest NAV from lsjz."""
    name = _fetch_fund_name_sync(code)
    if not name:
        return None
    nav_info = _fetch_fund_nav_sync(code) or {}
    return {
        "code": code,
        "name": name,
        "nav": round(float(nav_info.get("nav", 0) or 0), 4),
        "est_nav": round(float(nav_info.get("nav", 0) or 0), 4),
        "change_pct": float(nav_info.get("change_pct", 0) or 0),
        "nav_date": nav_info.get("nav_date", ""),
        "est_time": "",
        "realtime": False,
    }


def _fetch_fund_sync(code: str) -> dict | None:
    url = f"http://fundgz.1234567.com.cn/js/{code}.js"
    try:
        r = _requests.get(url, timeout=6, headers={"Referer": "http://fund.eastmoney.com/"})
        text = r.text.strip()
        m = re.match(r"jsonpgz\((.*)\);?", text)
        if m:
            payload = m.group(1).strip()
            if payload:
                d = json.loads(payload)
                if d and d.get("fundcode"):
                    nav = float(d.get("dwjz", 0))
                    est = float(d.get("gsz", 0)) if d.get("gsz") else nav
                    return {
                        "code": d.get("fundcode", code),
                        "name": d.get("name", ""),
                        "nav": round(nav, 4),
                        "est_nav": round(est, 4),
                        "change_pct": float(d.get("gszzl", 0)) if d.get("gszzl") else 0.0,
                        "nav_date": d.get("jzrq", ""),
                        "est_time": d.get("gztime", ""),
                        "realtime": True,
                    }
    except Exception as e:
        print(f"[fund] realtime {code} failed: {e}")
    # Fallback: scrape meta (QDII etc. often have no realtime estimate)
    return _fetch_fund_meta_sync(code)


def _is_onchain_etf(code: str) -> bool:
    """场内 ETF / LOF 代码识别（深交所 1xxxxx / 上交所 5xxxxx）。
    场外公募基金通常是 0xxxxx / 6xxxxx / 008xxx 这种联接基金代码。"""
    if not code or len(code) != 6 or not code.isdigit():
        return False
    return code[0] in ("1", "5")


async def _fetch_onchain_etf_quote(code: str) -> dict | None:
    """场内 ETF 用 A股实时行情接口，返回二级市场成交价（用户在券商 App 看到的"现价"）。"""
    from services.market_data import get_realtime_quotes
    quotes = await get_realtime_quotes([code])
    q = quotes.get(code)
    if not q or not q.get("price"):
        return None
    price = q["price"]
    return {
        "code": code,
        "name": q.get("stock_name", ""),
        "nav": price,          # 字段复用：场内场景下这是市价
        "est_nav": price,
        "change_pct": q.get("change_pct", 0),
        "nav_date": "",
        "est_time": "",
        "realtime": True,
        "source": "onchain",   # 标记数据来源
    }


async def get_fund_quote(code: str) -> dict | None:
    cached = _fund_cache.get(code)
    if cached and time.time() - cached[1] < _FUND_TTL:
        return cached[0]
    # 场内 ETF/LOF 优先用实时行情；拉不到则回退到天天基金 NAV
    if _is_onchain_etf(code):
        try:
            data = await _fetch_onchain_etf_quote(code)
            if data:
                _fund_cache[code] = (data, time.time())
                return data
        except Exception as e:
            print(f"[fund] onchain quote {code} failed, fallback to NAV: {e}")
    data = await asyncio.to_thread(_fetch_fund_sync, code)
    if data:
        _fund_cache[code] = (data, time.time())
    return data


async def search_fund_by_name(keyword: str, limit: int = 5) -> list[dict]:
    """Query 天天基金 fund search. Returns list of {code, name, type}."""
    def _sync():
        url = "http://fund.eastmoney.com/FundSearch/api/FundSearchAPI.ashx"
        try:
            r = _requests.get(url, params={"callback": "", "key": keyword, "m": 4}, timeout=6)
            text = r.text.strip()
            # Response format: `{"key":"...","ErrorMsg":null,"Datas":{"Funds":[...]}}`
            if text.startswith("("):
                text = text[1:-1]
            data = json.loads(text)
            funds = (data.get("Datas") or {}).get("Funds") or []
            return [{
                "code": f.get("CODE"),
                "name": f.get("NAME"),
                "type": f.get("FundBaseInfo", {}).get("FTYPE") or f.get("CATEGORY"),
            } for f in funds[:limit]]
        except Exception as e:
            print(f"[fund-search] {keyword} failed: {e}")
            return []
    return await asyncio.to_thread(_sync)


# --- Crypto (OKX public) ---

def _fetch_okx_sync(symbol: str) -> dict | None:
    """symbol like 'BTC-USDT', 'ETH-USDT'. OKX returns real-time ticker."""
    url = f"https://www.okx.com/api/v5/market/ticker?instId={symbol}"
    # Try via proxy first (CN network usually blocks OKX direct)
    for session, label in [(_crypto_session, "proxied"), (_requests, "direct")]:
        try:
            r = session.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
            d = r.json()
            if d.get("code") != "0" or not d.get("data"):
                continue
            t = d["data"][0]
            last = float(t["last"])
            open24 = float(t["open24h"]) if t.get("open24h") else last
            change_pct = (last - open24) / open24 * 100 if open24 > 0 else 0
            return {
                "symbol": symbol,
                "price": last,
                "change_pct": round(change_pct, 2),
                "high24h": float(t.get("high24h", 0)),
                "low24h": float(t.get("low24h", 0)),
                "timestamp": int(t.get("ts", 0)),
            }
        except Exception as e:
            print(f"[crypto] {label} fetch {symbol} failed: {e}")
            continue
    return None


_USDCNY_CACHE: dict[str, float] = {}
_USDCNY_TS = [0.0]


def _fetch_usdcny_sync() -> float:
    """Fetch USD/CNY rate via a public source. Cached 1 hour."""
    if time.time() - _USDCNY_TS[0] < 3600 and _USDCNY_CACHE:
        return _USDCNY_CACHE.get("rate", 7.2)
    try:
        r = _requests.get("https://hq.sinajs.cn/list=USDCNY",
                          headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        r.encoding = "gbk"
        m = re.search(r'"([\d.]+),', r.text)
        if m:
            rate = float(m.group(1))
            _USDCNY_CACHE["rate"] = rate
            _USDCNY_TS[0] = time.time()
            return rate
    except Exception:
        pass
    return _USDCNY_CACHE.get("rate", 7.2)  # fallback


async def get_crypto_quote(symbol: str) -> dict | None:
    cached = _crypto_cache.get(symbol)
    if cached and time.time() - cached[1] < _CRYPTO_TTL:
        return cached[0]
    data = await asyncio.to_thread(_fetch_okx_sync, symbol)
    if data:
        # Enrich with CNY value
        rate = await asyncio.to_thread(_fetch_usdcny_sync)
        data["usdcny"] = rate
        data["price_cny"] = round(data["price"] * rate, 2)
        _crypto_cache[symbol] = (data, time.time())
    return data
