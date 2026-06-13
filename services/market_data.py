"""Multi-market stock data via Sina Finance.

Supported direct holdings:
- A-share: bare 6-digit code, e.g. 600362
- HK stock: HK.00700
- US stock: US.AAPL
"""
from __future__ import annotations
import asyncio
import os
import re
import time
from datetime import datetime, timedelta

# Bypass proxy for domestic A-share API calls
_saved_proxies = {}
for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
    if key in os.environ:
        _saved_proxies[key] = os.environ.pop(key)

# Patch requests to never use system proxy
import requests as _requests
_orig_session_init = _requests.Session.__init__
def _no_proxy_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    self.trust_env = False
_requests.Session.__init__ = _no_proxy_session_init

import akshare as ak
import pandas as pd

from config import config

# In-memory cache: {key: (data, timestamp)}
_cache: dict[str, tuple] = {}


def _cache_get(key: str, ttl: int):
    if key in _cache:
        data, ts = _cache[key]
        if time.time() - ts < ttl:
            return data
    return None


def _cache_set(key: str, data):
    _cache[key] = (data, time.time())


def get_fx_info(currency: str) -> dict:
    """Fetch CNY conversion info for a quote currency.

    Sina exposes bid/ask style fields; portfolio valuation uses their midpoint
    to avoid leaning on one side of the spread.
    """
    currency = (currency or "CNY").upper()
    if currency == "CNY":
        return {"rate": 1.0, "source": "CNY", "time": ""}
    fallback = {"USD": 7.2, "HKD": 0.92}.get(currency, 1.0)
    symbol = {"USD": "USDCNY", "HKD": "HKDCNY"}.get(currency)
    if not symbol:
        return {"rate": fallback, "source": "fallback", "time": ""}
    cache_key = f"fx_{currency}_cny"
    cached = _cache_get(cache_key, 300)
    if cached is not None:
        return cached
    try:
        resp = _requests.get(
            f"https://hq.sinajs.cn/list={symbol}",
            headers={"Referer": "https://finance.sina.com.cn"},
            timeout=5,
        )
        resp.encoding = "gbk"
        match = re.search(r'"([^"]+)"', resp.text)
        if match:
            fields = match.group(1).split(",")
            bid = float(fields[1]) if len(fields) > 1 and fields[1] else 0
            ask = float(fields[2]) if len(fields) > 2 and fields[2] else 0
            rate = round((bid + ask) / 2, 6) if bid > 0 and ask > 0 else bid
            if rate > 0:
                data = {
                    "rate": rate,
                    "source": "sina_bid_ask_mid",
                    "time": fields[0] if fields else "",
                }
                _cache_set(cache_key, data)
                return data
    except Exception:
        pass
    return {"rate": fallback, "source": "fallback", "time": ""}


def get_fx_rate(currency: str) -> float:
    """Fetch CNY conversion rate for a quote currency."""
    return float(get_fx_info(currency).get("rate") or 1.0)


def normalize_stock_code(stock_code: str) -> str:
    """Return the canonical holding code used across DB/API/UI."""
    raw = (stock_code or "").strip().upper()
    if raw.startswith("HK."):
        return f"HK.{raw[3:].zfill(5)}"
    if raw.startswith("HK") and raw[2:].isdigit():
        return f"HK.{raw[2:].zfill(5)}"
    if raw.startswith("US."):
        return f"US.{raw[3:].strip().upper()}"
    if raw.startswith("US") and not raw.startswith("USD"):
        rest = raw[2:].strip()
        if rest:
            return f"US.{rest.upper()}"
    return raw


def split_stock_code(stock_code: str) -> tuple[str, str]:
    """Return (market, symbol). market is A/HK/US."""
    code = normalize_stock_code(stock_code)
    if code.startswith("HK."):
        return "HK", code[3:]
    if code.startswith("US."):
        return "US", code[3:]
    return "A", code


def is_a_share(stock_code: str) -> bool:
    market, symbol = split_stock_code(stock_code)
    return market == "A" and len(symbol) == 6 and symbol.isdigit()


def _sina_symbol(stock_code: str) -> str:
    """Convert stock code to Sina symbol format (sh/sz prefix)."""
    stock_code = split_stock_code(stock_code)[1]
    # Shanghai: 6 (主板), 9 (B股), 5 (ETF/封基/可转债)
    # Shenzhen: 0/2/3 (主板/中小板/创业板), 1 (ETF/可转债, e.g. 159xxx)
    if stock_code[:1] in ("6", "9", "5"):
        return f"sh{stock_code}"
    return f"sz{stock_code}"


def _fetch_sina_quotes(stock_codes: list[str]) -> dict:
    """Fetch real-time quotes from Sina Finance API (hq.sinajs.cn).
    This API is stable, fast, and doesn't require auth.
    """
    if not stock_codes:
        return {}

    stock_codes = [normalize_stock_code(c) for c in stock_codes if is_a_share(c)]
    if not stock_codes:
        return {}
    symbols = [_sina_symbol(c) for c in stock_codes]
    url = f"https://hq.sinajs.cn/list={','.join(symbols)}"
    headers = {"Referer": "https://finance.sina.com.cn"}

    resp = _requests.get(url, headers=headers, timeout=10)
    resp.encoding = "gbk"
    text = resp.text

    result = {}
    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Format: var hq_str_sh601212="白银有色,8.14,8.54,8.13,...";
        match = re.match(r'var hq_str_(\w+)="(.*)";', line)
        if not match:
            continue

        symbol = match.group(1)
        data_str = match.group(2)
        if not data_str:
            continue

        fields = data_str.split(",")
        if len(fields) < 32:
            continue

        # Extract the 6-digit code from symbol
        code = symbol[2:]  # Remove sh/sz prefix

        try:
            name = fields[0]
            open_price = float(fields[1]) if fields[1] else 0
            prev_close = float(fields[2]) if fields[2] else 0
            price = float(fields[3]) if fields[3] else 0
            high = float(fields[4]) if fields[4] else 0
            low = float(fields[5]) if fields[5] else 0
            volume = float(fields[8]) if fields[8] else 0  # shares
            amount = float(fields[9]) if fields[9] else 0  # RMB

            change_pct = 0
            if prev_close > 0 and price > 0:
                change_pct = round((price - prev_close) / prev_close * 100, 2)

            amplitude = 0
            if prev_close > 0 and high > 0 and low > 0:
                amplitude = round((high - low) / prev_close * 100, 2)

            result[code] = {
                "stock_code": code,
                "stock_name": name,
                "market": "A",
                "currency": "CNY",
                "fx_rate": 1.0,
                "price": price,
                "open": open_price,
                "high": high,
                "low": low,
                "prev_close": prev_close,
                "volume": volume,
                "amount": amount,
                "change_pct": change_pct,
                "amplitude": amplitude,
                "turnover_rate": 0,  # Sina doesn't provide this directly
            }
        except (ValueError, IndexError):
            continue

    return result


async def get_realtime_quotes(stock_codes: list[str]) -> dict:
    """Get real-time quotes for A/HK/US stock holdings via Sina Finance."""
    if not stock_codes:
        return {}

    codes = [normalize_stock_code(c) for c in stock_codes]
    cache_key = "sina_quotes_" + ",".join(sorted(codes))
    cached = _cache_get(cache_key, config.quote_cache_ttl)
    if cached is not None:
        return cached

    try:
        a_codes = [c for c in codes if split_stock_code(c)[0] == "A"]
        hk_codes = [c for c in codes if split_stock_code(c)[0] == "HK"]
        us_codes = [c for c in codes if split_stock_code(c)[0] == "US"]

        result = {}
        if a_codes:
            result.update(await asyncio.to_thread(_fetch_sina_quotes, a_codes))

        async def fetch_one(code: str):
            market, symbol = split_stock_code(code)
            if market == "HK":
                q = await asyncio.to_thread(_fetch_hk_stock_quote, symbol)
            elif market == "US":
                q = await asyncio.to_thread(_fetch_us_stock_quote, symbol)
            else:
                q = None
            if not q:
                return None
            fx = get_fx_info(q.get("currency", "CNY"))
            return code, {
                "stock_code": code,
                "fx_rate": fx["rate"],
                "fx_time": fx.get("time", ""),
                "fx_source": fx.get("source", ""),
                **q,
            }

        overseas = await asyncio.gather(
            *(fetch_one(c) for c in [*hk_codes, *us_codes]),
            return_exceptions=True,
        )
        for item in overseas:
            if isinstance(item, Exception) or not item:
                continue
            code, quote = item
            result[code] = quote
        if result:
            _cache_set(cache_key, result)
        return result
    except Exception as e:
        print(f"[market_data] Error fetching Sina quotes: {e}")
        return {}


async def get_stock_name(stock_code: str) -> str:
    """Look up stock name by code."""
    stock_code = normalize_stock_code(stock_code)
    quotes = await get_realtime_quotes([stock_code])
    if stock_code in quotes:
        return quotes[stock_code]["stock_name"]
    return ""


_benchmark_cache: dict[str, tuple[pd.DataFrame, float]] = {}
_BENCHMARK_TTL = 3600  # 1 hour


def _fetch_benchmark_history(symbol: str = "sh000300", days: int = 400) -> pd.DataFrame:
    """Fetch index history directly by Sina symbol (bypasses stock-code prefix helper)."""
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
    resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=15)
    resp.encoding = "utf-8"
    import json as _json
    data = _json.loads(resp.text) if resp.text else []
    if not data:
        return pd.DataFrame()
    rows = [{"date": r["day"], "close": float(r["close"])} for r in data]
    return pd.DataFrame(rows)


async def get_benchmark_return(start_date: str, symbol: str = "sh000300") -> dict:
    """Compute realized return of an index from start_date to today.

    Returns:
        {"return_pct": float (e.g. 0.087 for +8.7%),
         "start_close": float, "end_close": float,
         "start_date": str, "end_date": str, "days": int}
        or {"return_pct": 0.0, ...} on failure.
    """
    import time
    import asyncio as _asyncio
    cache_key = f"bench_{symbol}"
    cached = _benchmark_cache.get(cache_key)
    if cached and time.time() - cached[1] < _BENCHMARK_TTL:
        df = cached[0]
    else:
        df = await _asyncio.to_thread(_fetch_benchmark_history, symbol, 400)
        if df is not None and not df.empty:
            _benchmark_cache[cache_key] = (df, time.time())
    if df is None or df.empty:
        return {"return_pct": 0.0, "start_close": 0.0, "end_close": 0.0, "start_date": start_date, "end_date": "", "days": 0}

    # Find closest row on/after start_date
    start = start_date[:10]
    filt = df[df["date"] >= start]
    if filt.empty:
        return {"return_pct": 0.0, "start_close": 0.0, "end_close": 0.0, "start_date": start, "end_date": "", "days": 0}
    start_row = filt.iloc[0]
    end_row = df.iloc[-1]
    start_close = float(start_row["close"])
    end_close = float(end_row["close"])
    ret = (end_close - start_close) / start_close if start_close > 0 else 0.0
    from datetime import datetime as _dt
    try:
        d0 = _dt.strptime(str(start_row["date"]), "%Y-%m-%d")
        d1 = _dt.strptime(str(end_row["date"]), "%Y-%m-%d")
        days = (d1 - d0).days
    except Exception:
        days = 0
    return {
        "return_pct": round(ret, 4),
        "start_close": round(start_close, 2),
        "end_close": round(end_close, 2),
        "start_date": str(start_row["date"]),
        "end_date": str(end_row["date"]),
        "days": days,
    }


def _fetch_history_sina(stock_code: str, days: int) -> pd.DataFrame:
    """Fetch daily K-line from Sina Finance API (no proxy issues)."""
    symbol = _sina_symbol(stock_code)
    # Sina provides historical K-line via money.finance.sina.com.cn
    # We use the simple daily K-line API
    url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData?symbol={symbol}&scale=240&ma=no&datalen={days}"
    resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=15)
    resp.encoding = "utf-8"
    import json as _json
    data = _json.loads(resp.text)
    if not data:
        return pd.DataFrame()

    rows = []
    for item in data:
        rows.append({
            "日期": item["day"],
            "开盘": float(item["open"]),
            "收盘": float(item["close"]),
            "最高": float(item["high"]),
            "最低": float(item["low"]),
            "成交量": float(item["volume"]),
            "成交额": 0,
            "振幅": 0,
            "涨跌幅": 0,
            "涨跌额": 0,
            "换手率": 0,
        })
    return pd.DataFrame(rows)


async def get_historical_data(stock_code: str, days: int = 60) -> pd.DataFrame:
    """Get historical daily OHLCV data.
    Layer 1: In-memory cache (5 min TTL)
    Layer 2: SQLite persistent cache (check if up-to-date)
    Layer 3: Sina Finance API → save to SQLite
    Layer 4: AKShare fallback
    """
    from database import get_cached_klines, get_cached_latest_date, save_klines
    stock_code = normalize_stock_code(stock_code)
    if not is_a_share(stock_code):
        return pd.DataFrame()

    cache_key = f"hist_{stock_code}_{days}"
    df = _cache_get(cache_key, config.history_cache_ttl)
    if df is not None:
        return df

    # Check if SQLite cache is fresh enough (has today or yesterday's data)
    today = datetime.now().strftime("%Y-%m-%d")
    latest = await get_cached_latest_date(stock_code)
    need_fetch = not latest or latest < today

    if not need_fetch:
        # SQLite cache is up-to-date, use it
        rows = await get_cached_klines(stock_code, days)
        if rows:
            df = pd.DataFrame(rows)
            df.rename(columns={"date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量"}, inplace=True)
            df["成交额"] = 0
            df["振幅"] = 0
            df["涨跌幅"] = 0
            df["涨跌额"] = 0
            df["换手率"] = 0
            _cache_set(cache_key, df)
            return df

    # Fetch fresh data from Sina
    try:
        # Fetch more than needed so we accumulate history
        fetch_days = max(days, 120)
        df = await asyncio.to_thread(_fetch_history_sina, stock_code, fetch_days)
        if df is not None and not df.empty:
            # Save all fetched data to SQLite
            await save_klines(stock_code, df.to_dict("records"))
            df = df.tail(days)
            _cache_set(cache_key, df)
            return df
    except Exception as e:
        print(f"[market_data] Sina history failed for {stock_code}: {e}")

    # Fallback to AKShare
    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
    try:
        df = await asyncio.to_thread(
            ak.stock_zh_a_hist, symbol=stock_code, period="daily",
            start_date=start_date, end_date=end_date, adjust="qfq",
        )
        if df is not None and not df.empty:
            df = df.tail(days)
            _cache_set(cache_key, df)
            return df
    except Exception:
        pass

    # Last resort: use SQLite cache even if stale
    rows = await get_cached_klines(stock_code, days)
    if rows:
        df = pd.DataFrame(rows)
        df.rename(columns={"date": "日期", "open": "开盘", "high": "最高", "low": "最低", "close": "收盘", "volume": "成交量"}, inplace=True)
        df["成交额"] = 0
        df["振幅"] = 0
        df["涨跌幅"] = 0
        df["涨跌额"] = 0
        df["换手率"] = 0
        _cache_set(cache_key, df)
        return df

    return pd.DataFrame()


async def get_intraday_data(stock_code: str) -> pd.DataFrame:
    """Get intraday 5-minute bars."""
    stock_code = normalize_stock_code(stock_code)
    if not is_a_share(stock_code):
        return pd.DataFrame()
    cache_key = f"intraday_{stock_code}"
    df = _cache_get(cache_key, 10)
    if df is not None:
        return df

    try:
        df = await asyncio.to_thread(
            ak.stock_zh_a_hist_min_em,
            symbol=stock_code,
            period="5",
            adjust="qfq",
        )
        if df is not None and not df.empty:
            today = datetime.now().strftime("%Y-%m-%d")
            if "时间" in df.columns:
                df = df[df["时间"].astype(str).str.startswith(today)]
            _cache_set(cache_key, df)
            return df
    except Exception as e:
        # Intraday failure is non-critical, just skip
        pass

    return pd.DataFrame()


# --- Commodity / Futures data ---

# Map stock codes to related commodity symbols (Sina futures format)
# Hard-coded overrides for known stocks (takes priority)
# Override only for stocks where EM2016 sub-category is misleading
_COMMODITY_OVERRIDE = {
    # 大部分股票靠 EM2016 自动匹配即可，这里放特殊情况
}

# Auto-mapping: industry keyword → commodity (checked in order, first match wins)
_INDUSTRY_COMMODITY_MAP = [
    ("铜", ("沪铜", "CU0")),
    ("铝", ("沪铝", "AL0")),
    ("黄金", ("沪金", "AU0")),
    ("金", ("沪金", "AU0")),
    ("银", ("沪银", "AG0")),
    ("锌", ("沪锌", "ZN0")),
    ("铅", ("沪铅", "PB0")),
    ("镍", ("沪镍", "NI0")),
    ("锡", ("沪锡", "SN0")),
    ("贵金属", ("沪金", "AU0")),
    ("工业金属", ("沪铜", "CU0")),
    ("基本金属", ("沪铜", "CU0")),
    ("小金属", ("沪铜", "CU0")),
    ("能源金属", ("沪镍", "NI0")),
    ("有色金属冶炼", ("沪铜", "CU0")),
]

# Runtime cache: stock_code → (label, symbol) or None
_commodity_cache: dict[str, tuple | None] = {}


# Name-based keyword matching as fallback when API is unavailable
_NAME_COMMODITY_MAP = {
    "铜": ("沪铜", "CU0"),
    "铝": ("沪铝", "AL0"),
    "金": ("沪金", "AU0"),
    "银": ("沪银", "AG0"),
    "锌": ("沪锌", "ZN0"),
    "镍": ("沪镍", "NI0"),
    "锡": ("沪锡", "SN0"),
    "钼": ("沪铜", "CU0"),  # no molybdenum futures
    "钴": ("沪铜", "CU0"),
}

# Name keywords that should NOT trigger matching (e.g. 白银有色 → 白银 is a city)
_NAME_EXCLUDE = {"白银有色"}


_sector_cache: dict[str, tuple[str, float]] = {}  # code -> (sector, ts)
_SECTOR_TTL = 86400  # 1 day


async def get_stock_sector(stock_code: str) -> str:
    """Return top-level sector name like '有色金属' / '医药生物' / '银行'.
    Caches for 1 day since sectors rarely change."""
    import time
    import asyncio as _asyncio
    stock_code = normalize_stock_code(stock_code)
    market, _ = split_stock_code(stock_code)
    if market == "HK":
        return "港股"
    if market == "US":
        return "美股"
    cached = _sector_cache.get(stock_code)
    if cached and time.time() - cached[1] < _SECTOR_TTL:
        return cached[0]
    try:
        industry = await _asyncio.to_thread(_lookup_industry, stock_code)
        sector = (industry or "").split("-")[0].strip() if industry else ""
    except Exception:
        sector = ""
    _sector_cache[stock_code] = (sector, time.time())
    return sector


async def get_stock_sector_detail(stock_code: str) -> str:
    """有色金属细分到二级(小金属/基本金属/贵金属/能源金属), 其它返回一级。
    钨钼锑稀土等 EM 归在 '有色金属-稀有金属', 这里映射成 '小金属' 跟用户口径一致。"""
    import time
    import asyncio as _asyncio
    sc = normalize_stock_code(stock_code)
    market, _ = split_stock_code(sc)
    if market == "HK":
        return "港股"
    if market == "US":
        return "美股"
    ck = sc + ":detail"
    cached = _sector_cache.get(ck)
    if cached and time.time() - cached[1] < _SECTOR_TTL:
        return cached[0]
    try:
        industry = await _asyncio.to_thread(_lookup_industry, sc)
    except Exception:
        industry = ""
    parts = [p.strip() for p in (industry or "").split("-") if p.strip()]
    if not parts:
        out = ""
    elif parts[0] == "有色金属" and len(parts) >= 2:
        out = "小金属" if parts[1] == "稀有金属" else parts[1]
    else:
        out = parts[0]
    _sector_cache[ck] = (out, time.time())
    return out


def _lookup_industry(stock_code: str) -> str:
    """Look up stock industry via East Money CompanySurvey API (emweb domain, stable)."""
    try:
        if not is_a_share(stock_code):
            return ""
        prefix = "SH" if stock_code.startswith("6") else "SZ"
        url = f"https://emweb.eastmoney.com/PC_HSF10/CompanySurvey/PageAjax?code={prefix}{stock_code}"
        resp = _requests.get(url, timeout=10)
        data = resp.json()
        jbzl = data.get("jbzl", [])
        if jbzl:
            # EM2016 has detailed classification like "有色金属-基本金属-铜"
            em2016 = jbzl[0].get("EM2016", "")
            if em2016:
                return str(em2016).strip()
            # Fallback to CSRC industry
            csrc = jbzl[0].get("INDUSTRYCSRC1", "")
            if csrc:
                return str(csrc).strip()
    except Exception as e:
        print(f"[commodity] Industry lookup failed for {stock_code}: {e}")
    return ""


def _fetch_futures_quote(symbol: str) -> dict | None:
    """Fetch real-time futures quote from Sina. symbol like 'CU0' (主力合约)."""
    try:
        url = f"https://hq.sinajs.cn/list=nf_{symbol}"
        resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        resp.encoding = "gbk"
        text = resp.text.strip()
        match = re.match(r'var hq_str_nf_\w+="(.*)";', text)
        if not match or not match.group(1):
            return None
        fields = match.group(1).split(",")
        if len(fields) < 10:
            return None
        # Fields: 0=name, ... 6=close, 7=settlement, 3=open, 4=high, 5=low, 8=prev_settlement
        name = fields[0]
        price = float(fields[6]) if fields[6] else 0
        prev = float(fields[8]) if fields[8] else 0
        change_pct = round((price - prev) / prev * 100, 2) if prev > 0 and price > 0 else 0
        return {"name": name, "price": price, "prev": prev, "change_pct": change_pct}
    except Exception:
        return None


def _fetch_overseas_quote(symbol: str) -> dict | None:
    """Sina 外盘期货 / 海外指数. symbol like 'NQ' (纳指期货), 'GC' (COMEX 金), 'SI' (白银), 'ES' (标普), 'CL' (原油).
    URL: https://hq.sinajs.cn/list=hf_<SYMBOL>
    Field layout (comma-separated):
      0=last, 1=, 2=bid, 3=ask, 4=high, 5=low, 6=time, 7=open, 8=prev_close, ...
    """
    try:
        url = f"https://hq.sinajs.cn/list=hf_{symbol}"
        resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        # Sina 外盘 returns text in GBK with Chinese name; response.text decoded fine but name is garbled
        resp.encoding = "gbk"
        text = resp.text.strip()
        match = re.match(rf'var hq_str_hf_{symbol}="(.*)";', text)
        if not match or not match.group(1):
            return None
        fields = match.group(1).split(",")
        if len(fields) < 9:
            return None
        last = float(fields[0]) if fields[0] else 0
        prev = float(fields[8]) if fields[8] else 0
        change_pct = round((last - prev) / prev * 100, 2) if prev > 0 and last > 0 else 0
        return {"price": last, "prev": prev, "change_pct": change_pct}
    except Exception as e:
        print(f"[overseas] {symbol} failed: {e}")
        return None


def _fetch_hk_stock_quote(code: str) -> dict | None:
    """港股个股实时. code = '00700' (5位带前导0). Sina /list=hk<CODE>.
    字段: name_en, name_cn, prevclose, open, high, low, last, change_amt, change_pct, ...
    """
    try:
        url = f"https://hq.sinajs.cn/list=hk{code}"
        resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        resp.encoding = "gbk"
        text = resp.text.strip()
        m = re.match(r'var hq_str_hk\w+="(.*)";', text)
        if not m or not m.group(1):
            return None
        f = m.group(1).split(",")
        if len(f) < 10:
            return None
        prev = float(f[2]) if f[2] else 0
        last = float(f[6]) if f[6] else 0
        # f[8] 通常是涨跌幅；fallback 自己算
        try:
            change_pct = float(f[8])
        except (ValueError, IndexError):
            change_pct = round((last - prev) / prev * 100, 2) if prev > 0 else 0
        return {
            "stock_name": f[1] or f[0] or code,
            "price": last,
            "open": float(f[3]) if f[3] else 0,
            "high": float(f[4]) if f[4] else 0,
            "low": float(f[5]) if f[5] else 0,
            "prev_close": prev,
            "volume": float(f[12]) if len(f) > 12 and f[12] else 0,
            "amount": 0,
            "change_pct": change_pct,
            "amplitude": round((float(f[4]) - float(f[5])) / prev * 100, 2) if prev > 0 and f[4] and f[5] else 0,
            "turnover_rate": 0,
            "market": "HK",
            "currency": "HKD",
        }
    except Exception as e:
        print(f"[hk-stock] {code} failed: {e}")
        return None


def _fetch_us_stock_quote(symbol: str) -> dict | None:
    """美股个股实时 via Sina (gb_<lowercase>). 字段:
       name, last, change_pct, time, change_amt, open, high, low, 52w_high, 52w_low, ...
    亚洲盘外返回最新成交，盘内是 delayed real-time.
    """
    try:
        url = f"https://hq.sinajs.cn/list=gb_{symbol.lower()}"
        resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        resp.encoding = "gbk"
        text = resp.text.strip()
        m = re.match(r'var hq_str_gb_\w+="(.*)";', text)
        if not m or not m.group(1):
            return None
        f = m.group(1).split(",")
        if len(f) < 5:
            return None
        last = float(f[1]) if f[1] else 0
        change_pct = float(f[2]) if f[2] else 0
        # prev_close = last / (1 + change_pct/100)
        prev = round(last / (1 + change_pct / 100), 4) if last > 0 and change_pct != 0 else last
        return {
            "stock_name": f[0] or symbol.upper(),
            "price": last,
            "open": 0,
            "high": 0,
            "low": 0,
            "prev_close": prev,
            "volume": 0,
            "amount": 0,
            "change_pct": round(change_pct, 2),
            "amplitude": 0,
            "turnover_rate": 0,
            "market": "US",
            "currency": "USD",
        }
    except Exception as e:
        print(f"[us-stock] {symbol} failed: {e}")
        return None


def _fetch_hk_index_quote(symbol: str = "HSI") -> dict | None:
    """香港指数 via Sina. symbol like 'HSI' (恒生), 'HSCEI' (国企)."""
    try:
        url = f"https://hq.sinajs.cn/list=hkHSI" if symbol == "HSI" else f"https://hq.sinajs.cn/list=int_hangseng"
        resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
        resp.encoding = "gbk"
        text = resp.text.strip()
        # hkHSI format: ... contains last and prev_close
        # Try generic regex pickup
        match = re.search(r'"([^"]+)"', text)
        if not match:
            return None
        fields = match.group(1).split(",")
        if len(fields) < 7:
            return None
        # hkHSI fields: 0=name, 1=name_en, 2=last, 3=prev_close, ... 6=change, 7=change_pct
        try:
            last = float(fields[2])
            prev = float(fields[3])
            change_pct = round((last - prev) / prev * 100, 2) if prev > 0 else 0
            return {"price": last, "prev": prev, "change_pct": change_pct}
        except (ValueError, IndexError):
            return None
    except Exception as e:
        print(f"[hk] {symbol} failed: {e}")
        return None


async def _resolve_commodity_mapping(stock_code: str) -> tuple | None:
    """Resolve stock → commodity mapping.
    Priority: override > cache > industry API > stock name keywords.
    """
    if not is_a_share(stock_code):
        return None
    # 1. Hard-coded override
    if stock_code in _COMMODITY_OVERRIDE:
        return _COMMODITY_OVERRIDE[stock_code]

    # 2. Already resolved
    if stock_code in _commodity_cache:
        return _commodity_cache[stock_code]

    # 3. Try industry API
    try:
        industry = await asyncio.to_thread(_lookup_industry, stock_code)
        if industry:
            for keyword, mapping in _INDUSTRY_COMMODITY_MAP:
                if keyword in industry:
                    _commodity_cache[stock_code] = mapping
                    print(f"[commodity] Auto-mapped {stock_code} (行业:{industry}) → {mapping[0]}")
                    return mapping
            print(f"[commodity] {stock_code} 行业={industry}, no commodity match")
    except Exception:
        pass

    # 4. Fallback: match by stock name keywords from real-time quote
    try:
        quotes = await get_realtime_quotes([stock_code])
        q = quotes.get(stock_code)
        if q:
            name = q.get("stock_name", "")
            if name and name not in _NAME_EXCLUDE:
                for keyword, mapping in _NAME_COMMODITY_MAP.items():
                    if keyword in name:
                        _commodity_cache[stock_code] = mapping
                        print(f"[commodity] Name-mapped {stock_code} ({name}) → {mapping[0]} (keyword: {keyword})")
                        return mapping
    except Exception:
        pass

    _commodity_cache[stock_code] = None
    return None


async def get_commodity_for_stock(stock_code: str) -> dict | None:
    """Get related commodity data for a stock, if any."""
    mapping = await _resolve_commodity_mapping(stock_code)
    if not mapping:
        return None

    label, symbol = mapping
    cache_key = f"futures_{symbol}"
    cached = _cache_get(cache_key, 30)
    if cached is not None:
        return cached

    result = await asyncio.to_thread(_fetch_futures_quote, symbol)
    if result:
        data = {"label": label, **result}
        _cache_set(cache_key, data)
        return data
    return None


def _fetch_indices_sina() -> list[dict]:
    """Fetch key market indices from Sina: 上证, 深证, 有色板块."""
    symbols = {
        "sh000001": "上证指数",
        "sz399001": "深证成指",
        "sz399395": "有色金属",  # 有色金属板块指数
    }
    url = f"https://hq.sinajs.cn/list={','.join(symbols.keys())}"
    resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=5)
    resp.encoding = "gbk"
    result = []
    for line in resp.text.strip().split("\n"):
        match = re.match(r'var hq_str_(\w+)="(.*)";', line.strip())
        if not match or not match.group(2):
            continue
        sym = match.group(1)
        fields = match.group(2).split(",")
        if len(fields) < 4:
            continue
        try:
            price = float(fields[3]) if fields[3] else 0
            prev = float(fields[2]) if fields[2] else 0
            change_pct = round((price - prev) / prev * 100, 2) if prev > 0 and price > 0 else 0
            result.append({
                "symbol": sym,
                "name": symbols.get(sym, fields[0]),
                "price": round(price, 2),
                "change_pct": change_pct,
            })
        except (ValueError, IndexError):
            continue
    return result


async def get_market_indices() -> list[dict]:
    cache_key = "market_indices"
    cached = _cache_get(cache_key, 10)
    if cached is not None:
        return cached
    try:
        result = await asyncio.to_thread(_fetch_indices_sina)
        if result:
            _cache_set(cache_key, result)
        return result
    except Exception:
        return []


# ============================================================
# 宏观仪表盘: 全球指数 / 汇率 / 商品.
# Sina 不同 prefix 解析字段不同, 用 (group, symbol, label) 表驱动.
# ============================================================
MACRO_SYMBOLS = [
    # A 股大盘
    ("a_index", "sh000001", "上证指数"),
    ("a_index", "sz399001", "深证成指"),
    ("a_index", "sh000300", "沪深300"),
    ("a_index", "sz399006", "创业板指"),
    ("a_index", "sh000688", "科创50"),
    # 港股
    ("hk_index", "hkHSI", "恒生指数"),
    ("hk_index", "hkHSTECH", "恒生科技"),
    ("hk_index", "hkHSCEI", "恒生国企"),
    # 美股
    ("us_index", "gb_dji", "道琼斯"),
    ("us_index", "gb_ixic", "纳斯达克"),
    ("us_index", "gb_inx", "标普500"),
    # 海外指数 (日韩欧, 看外围风险情绪)
    ("overseas_index", "int_nikkei", "日经225"),
    ("overseas_index", "znb_KOSPI", "韩国KOSPI"),
    ("overseas_index", "int_ftse", "伦敦FTSE"),
    # AI / 算力 全产业链 (A股 ETF/指数实时市价): 主题→算力硬件→云软件→应用→机器人→电力→上游金属
    ("ai_tech", "sz159819", "人工智能"),
    ("ai_tech", "sz399363", "算力指数"),
    ("ai_tech", "sz159995", "芯片"),
    ("ai_tech", "sh561980", "半导体设备"),    # 国产替代/设备
    ("ai_tech", "sh515880", "通信/CPO"),     # 光模块/数据中心互联
    ("ai_tech", "sz159890", "云计算"),
    ("ai_tech", "sh515230", "软件"),
    ("ai_tech", "sz159363", "创业板AI"),
    ("ai_tech", "sh512980", "传媒AIGC"),     # AIGC 内容应用
    ("ai_tech", "sh561600", "消费电子"),      # AI 手机/AIPC/AI 终端
    ("ai_tech", "sz159770", "机器人"),
    ("ai_tech", "sz159611", "电力"),         # 算力耗电, AI 扩张拉动用电
    # 汇率 (USD 计价对其他)
    ("fx", "fx_susdcnh", "USD/CNH 离岸"),
    ("fx", "fx_susdcny", "USD/CNY 在岸"),
    ("fx", "fx_seurusd", "EUR/USD"),
    ("fx", "fx_susdjpy", "USD/JPY"),
    # 有色金属 (基本金属, 跟有色持仓最相关)
    ("metal_base", "nf_CU0", "沪铜"),
    ("metal_base", "nf_AL0", "沪铝"),
    ("metal_base", "nf_ZN0", "沪锌"),
    ("metal_base", "nf_NI0", "沪镍"),
    ("metal_base", "nf_SN0", "沪锡"),
    ("metal_base", "nf_PB0", "沪铅"),
    ("metal_base", "hf_HG", "COMEX 铜"),
    # 小金属 (钨钼锑稀土 — 跟你中钨/有色持仓最相关; 真实现货价数据源缺, 用板块 ETF 实时价代理)
    ("metal_minor", "sh512400", "有色金属ETF"),
    ("metal_minor", "sz159608", "稀有金属"),    # 钨钼钽锑
    ("metal_minor", "sz159713", "稀土"),        # 永磁/稀土
    # 贵金属
    ("metal_precious", "nf_AU0", "沪金"),
    ("metal_precious", "nf_AG0", "沪银"),
    ("metal_precious", "hf_GC", "COMEX 黄金"),
    ("metal_precious", "hf_SI", "COMEX 白银"),
    # 新能源金属 / 黑色
    ("energy_new_black", "nf_LC0", "碳酸锂"),
    ("energy_new_black", "nf_SI0", "工业硅"),
    ("energy_new_black", "nf_RB0", "螺纹钢"),
    ("energy_new_black", "nf_I0", "铁矿石"),
    ("energy_new_black", "nf_J0", "焦炭"),
    ("energy_new_black", "nf_JM0", "焦煤"),
    # 能化 / 农产品
    ("energy_chem", "nf_SC0", "原油 SC"),
    ("energy_chem", "hf_CL", "WTI 原油"),
    ("energy_chem", "nf_TA0", "PTA"),
    ("energy_chem", "nf_MA0", "甲醇"),
    ("energy_chem", "nf_FG0", "玻璃"),
    ("energy_chem", "nf_SA0", "纯碱"),
    ("energy_chem", "nf_V0", "PVC"),
    ("energy_chem", "nf_M0", "豆粕"),
    ("energy_chem", "nf_P0", "棕榈油"),
]


def _parse_macro_line(sym: str, body: str) -> dict | None:
    """各 prefix 字段不同: 提取统一的 {price, prev_close, change_pct}.
    返回 None 表示空值或字段异常.
    """
    if not body:
        return None
    fields = body.split(",")
    try:
        # A 股 sh/sz: 名,昨,开,当前,最高,最低,...
        if sym.startswith("sh") or sym.startswith("sz"):
            if len(fields) < 4:
                return None
            prev = float(fields[2]) if fields[2] else 0
            price = float(fields[3]) if fields[3] else 0
        # 港股 hk: 代号,名,昨,开,高,低,当前,涨跌,涨跌%,...
        elif sym.startswith("hk"):
            if len(fields) < 9:
                return None
            prev = float(fields[2]) if fields[2] else 0
            price = float(fields[6]) if fields[6] else 0
        # 美股 gb_: 名,当前,涨跌%,时间,涨跌,昨收,开,高,...
        elif sym.startswith("gb_"):
            if len(fields) < 6:
                return None
            price = float(fields[1]) if fields[1] else 0
            prev = float(fields[5]) if fields[5] else 0
        # 海外指数 int_/znb_: 名,当前,涨跌额,涨跌%,... → 昨收 = 当前 - 涨跌额
        elif sym.startswith("int_") or sym.startswith("znb_"):
            if len(fields) < 4:
                return None
            price = float(fields[1]) if fields[1] else 0
            change = float(fields[2]) if fields[2] else 0
            prev = price - change
        # 国际商品 hf_: 当前,买,卖,高,低,时间,昨收,开,涨,跌...
        # 实际格式: 当前,(空),买,卖,高,低,时间,昨收,开... 但有的源是 高,低,...
        elif sym.startswith("hf_"):
            if len(fields) < 8:
                return None
            price = float(fields[0]) if fields[0] else 0
            # hf_ 的昨收在 index 7
            prev = float(fields[7]) if fields[7] else 0
        # 国内期货 nf_: 名,固定,昨,开,高,低,当前(或买),... 高=4 低=5 当前=8
        # 实测: 铜连续,150000,104500,104840,103690,104620,104620,104630,104620,...
        # → 字段1=昨收 字段7或8=当前. 用最近收盘价: fields[8]
        elif sym.startswith("nf_"):
            if len(fields) < 9:
                return None
            prev = float(fields[2]) if fields[2] else 0
            price = float(fields[8]) if fields[8] else 0
        # 汇率 fx_: 时间,买,卖,价3,价4,昨买,昨卖,昨当前 → 用 fields[1] 当前, fields[5] 昨收
        elif sym.startswith("fx_"):
            if len(fields) < 8:
                return None
            price = float(fields[1]) if fields[1] else 0
            prev = float(fields[5]) if fields[5] else 0
        else:
            return None
        if price <= 0:
            return None
        change_pct = round((price - prev) / prev * 100, 3) if prev > 0 else 0
        return {
            "price": round(price, 4),
            "prev_close": round(prev, 4),
            "change_pct": change_pct,
        }
    except (ValueError, IndexError):
        return None


def _fetch_macro_sina() -> dict:
    """一次批量拉所有宏观符号, 按 group 分桶返回."""
    syms = [s for _, s, _ in MACRO_SYMBOLS]
    url = f"https://hq.sinajs.cn/list={','.join(syms)}"
    resp = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn"}, timeout=8)
    resp.encoding = "gbk"
    parsed: dict[str, dict] = {}
    for line in resp.text.strip().split("\n"):
        m = re.match(r'var hq_str_(\w+)="(.*)";', line.strip())
        if not m:
            continue
        sym, body = m.group(1), m.group(2)
        parsed[sym] = _parse_macro_line(sym, body)
    groups: dict[str, list[dict]] = {}
    for grp, sym, label in MACRO_SYMBOLS:
        p = parsed.get(sym)
        if not p:
            continue
        groups.setdefault(grp, []).append({
            "symbol": sym,
            "name": label,
            **p,
        })
    return groups


async def get_macro_quotes() -> dict:
    cache_key = "macro_quotes"
    cached = _cache_get(cache_key, 30)
    if cached is not None:
        return cached
    try:
        result = await asyncio.to_thread(_fetch_macro_sina)
        if result:
            _cache_set(cache_key, result)
        return result
    except Exception:
        return {}


# ============================================================
# K 线数据源 (sparkline + 放大图真 K 线用)
# 不同符号家族走不同接口, 单 symbol 拉 30/60 日序列.
# 返回值统一: [{date, close, open?, high?, low?}, ...] 按时间升序.
# close 必有 (sparkline 用); open/high/low 有则画真 K 线蜡烛, 缺则前端回退折线.
# ============================================================
def _mk_ohlc(date, c, o=None, h=None, l=None) -> dict | None:
    """构造统一 K 线点。close 必填且 >0; open/high/low 三者齐全且 >0 才带上 (画蜡烛)。"""
    try:
        c = float(c or 0)
    except (TypeError, ValueError):
        return None
    if c <= 0:
        return None
    row = {"date": date or "", "close": c}
    try:
        if o not in (None, "") and h not in (None, "") and l not in (None, ""):
            o, h, l = float(o), float(h), float(l)
            if o > 0 and h > 0 and l > 0:
                row["open"], row["high"], row["low"] = o, h, l
    except (TypeError, ValueError):
        pass
    return row


def _kline_sina_ashare(sym: str, datalen: int = 30) -> list[dict]:
    """sh*/sz* A 股 / A 股板块指数."""
    url = (f"http://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
           f"CN_MarketData.getKLineData?symbol={sym}&scale=240&ma=5&datalen={datalen}")
    r = _requests.get(url, timeout=6)
    arr = r.json()
    if not isinstance(arr, list):
        return []
    out = []
    for d in arr:
        row = _mk_ohlc(d.get("day"), d.get("close"), d.get("open"), d.get("high"), d.get("low"))
        if row:
            out.append(row)
    return out


def _kline_tencent_hk(sym: str, datalen: int = 30) -> list[dict]:
    """hkHSI / hkHSTECH 等港股指数 - 腾讯 gtimg."""
    url = (f"http://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get"
           f"?_var=kline_dayqfq&param={sym},day,,,{datalen},qfq")
    r = _requests.get(url, timeout=6)
    txt = r.text.strip()
    eq = txt.find("=")
    if eq < 0:
        return []
    try:
        import json as _json
        payload = _json.loads(txt[eq + 1:])
    except Exception:
        return []
    data = payload.get("data") or {}
    sym_data = data.get(sym) or {}
    rows = sym_data.get("day") or sym_data.get("qfqday") or []
    out = []
    for r in rows[-datalen:]:
        if len(r) < 3:
            continue
        # 腾讯日线数组: [日期, 开, 收, 高, 低, ...]
        o = r[1] if len(r) > 1 else None
        h = r[3] if len(r) > 3 else None
        lo = r[4] if len(r) > 4 else None
        row = _mk_ohlc(r[0], r[2], o, h, lo)
        if row:
            out.append(row)
    return out


def _kline_sina_us(sym: str, datalen: int = 30) -> list[dict]:
    """gb_dji / gb_ixic 等美股指数 - sina usstock.
    腾讯 fqkline 对美股指数只给当天, sina 这个接口能给完整历史."""
    # gb_dji → .DJI, gb_ixic → .IXIC
    inner = sym[3:].upper() if sym.startswith("gb_") else sym.upper()
    sina_sym = "." + inner
    url = (f"http://stock.finance.sina.com.cn/usstock/api/jsonp.php/"
           f"var%20_{inner}=/US_MinKService.getDailyK?symbol={sina_sym}&num={max(datalen, 60)}")
    r = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn/stock/usstock/"}, timeout=6)
    txt = r.text
    eq = txt.find("=(")
    if eq < 0:
        return []
    try:
        import json as _json
        end = txt.rfind(");")
        if end < 0:
            return []
        arr = _json.loads(txt[eq + 2:end])
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out = []
    for d in arr[-datalen:]:
        row = _mk_ohlc(d.get("d") or d.get("date"), d.get("c") or d.get("close"),
                       d.get("o"), d.get("h"), d.get("l"))
        if row:
            out.append(row)
    return out


def _kline_sina_futures_cn(sym: str, datalen: int = 30) -> list[dict]:
    """nf_CU0 → CU0 国内期货连续合约."""
    # 去掉 nf_ 前缀
    inner = sym[3:] if sym.startswith("nf_") else sym
    url = (f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           f"var%20_{inner}_30=/InnerFuturesNewService.getDailyKLine"
           f"?symbol={inner}&datalen={datalen}")
    r = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn/futures/quotes/"}, timeout=6)
    txt = r.text
    eq = txt.find("=(")
    if eq < 0:
        return []
    try:
        import json as _json
        # 取 =(  到  ); 之间
        end = txt.rfind(");")
        if end < 0:
            return []
        arr = _json.loads(txt[eq + 2:end])
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out = []
    for d in arr[-datalen:]:
        row = _mk_ohlc(d.get("d") or d.get("date"), d.get("c") or d.get("close"),
                       d.get("o"), d.get("h"), d.get("l"))
        if row:
            out.append(row)
    return out


def _kline_eastmoney_fx(sym: str, datalen: int = 30) -> list[dict]:
    """fx_susdcnh / fx_susdcny / fx_seurusd / fx_susdjpy → EastMoney qt kline.
    EastMoney secid 编码: 133.XXX 是港币/在岸/离岸人民币, 119.XXX 是国际汇率.
    """
    # 简化映射: 我们用到的 4 个固定关系
    fx_map = {
        "fx_susdcnh": "133.USDCNH",
        "fx_susdcny": "133.USDCNYC",
        "fx_seurusd": "119.EURUSD",
        "fx_susdjpy": "119.USDJPY",
    }
    secid = fx_map.get(sym)
    if not secid:
        return []
    url = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": secid,
        "klt": 101, "fqt": 1, "lmt": max(datalen, 60),
        "end": "20500000",
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "ut": "f057cbcbce2a86e2866ab8877db1d059",
        "forcect": 1,
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/",
    }
    r = _requests.get(url, params=params, headers=headers, timeout=8)
    j = r.json()
    klines = (j.get("data") or {}).get("klines") or []
    out = []
    for line in klines[-datalen:]:
        # 格式: "2026-05-11,open,close,high,low,volume,amount,amplitude%"
        parts = line.split(",")
        if len(parts) < 3:
            continue
        try:
            out.append({"date": parts[0], "close": float(parts[2])})
        except Exception:
            continue
    return out


def _kline_sina_futures_intl(sym: str, datalen: int = 30) -> list[dict]:
    """hf_GC → GC COMEX 国际期货."""
    inner = sym[3:] if sym.startswith("hf_") else sym
    url = (f"https://stock2.finance.sina.com.cn/futures/api/jsonp.php/"
           f"var%20_{inner}_30=/GlobalFuturesService.getGlobalFuturesDailyKLine"
           f"?symbol={inner}&datalen={datalen}")
    r = _requests.get(url, headers={"Referer": "https://finance.sina.com.cn/futures/quotes/"}, timeout=6)
    txt = r.text
    eq = txt.find("=(")
    if eq < 0:
        return []
    try:
        import json as _json
        end = txt.rfind(");")
        if end < 0:
            return []
        arr = _json.loads(txt[eq + 2:end])
    except Exception:
        return []
    if not isinstance(arr, list):
        return []
    out = []
    for d in arr[-datalen:]:
        row = _mk_ohlc(d.get("date"), d.get("close"), d.get("open"), d.get("high"), d.get("low"))
        if row:
            out.append(row)
    return out


# 海外指数 → 新浪环球市场代码 (gi.finance.sina.com.cn/hq/daily)
_SINA_GLOBAL_IDX = {
    "int_nikkei": "NKY",     # 日经225
    "znb_KOSPI": "KOSPI",    # 首尔综合
    "int_ftse": "UKX",       # 英国富时100
}


def _kline_sina_global_index(sym: str, datalen: int = 30) -> list[dict]:
    """日经/韩国/英国等环球指数日K, 走新浪 gi.finance (eastmoney push2his 本网络不可达)."""
    code = _SINA_GLOBAL_IDX.get(sym)
    if not code:
        return []
    url = "https://gi.finance.sina.com.cn/hq/daily"
    r = _requests.get(url, params={"symbol": code, "num": max(datalen, 60)},
                      headers={"Referer": "https://finance.sina.com.cn"}, timeout=6)
    rows = (r.json().get("result") or {}).get("data") or []
    out = []
    for d in rows[-datalen:]:
        row = _mk_ohlc(d.get("d"), d.get("c"), d.get("o"), d.get("h"), d.get("l"))
        if row:
            out.append(row)
    return out


# 汇率历史回退源: frankfurter (ECB 口径, 境外直连可达; 东财 push2his 本网络不可达时用它).
# 注: ECB 参考价与盘中市价略有差异 (~0.2-1%), 只用于画 sparkline 趋势, 不替代实时价.
_FRANKFURTER_FX = {
    "fx_susdcny": ("CNY", False),
    "fx_susdcnh": ("CNY", False),   # frankfurter 无离岸 CNH, 用在岸 CNY 近似走势
    "fx_seurusd": ("EUR", True),    # EUR/USD = 1 / (USD->EUR)
    "fx_susdjpy": ("JPY", False),
}
_fx_direct_session = None


def _kline_frankfurter_fx(sym: str, datalen: int = 30) -> list[dict]:
    global _fx_direct_session
    conf = _FRANKFURTER_FX.get(sym)
    if not conf:
        return []
    cur, invert = conf
    from datetime import date, timedelta
    end = date.today()
    start = end - timedelta(days=datalen * 2 + 15)
    if _fx_direct_session is None:
        _fx_direct_session = _requests.Session()
        _fx_direct_session.trust_env = False  # 境外源, 直连不走代理
    url = "https://api.frankfurter.app/{}..{}".format(start, end)
    r = _fx_direct_session.get(url, params={"base": "USD", "symbols": cur}, timeout=8)
    rates = r.json().get("rates") or {}
    out = []
    for d in sorted(rates.keys()):
        v = rates[d].get(cur)
        if not v:
            continue
        try:
            close = (1.0 / float(v)) if invert else float(v)
        except Exception:
            continue
        out.append({"date": d, "close": round(close, 4)})
    return out[-datalen:]


def _kline_for_symbol(sym: str, datalen: int = 30) -> list[dict]:
    """根据 symbol 前缀分发到对应接口. 失败返回 []."""
    try:
        if sym.startswith("sh") or sym.startswith("sz"):
            return _kline_sina_ashare(sym, datalen)
        if sym.startswith("hk"):
            return _kline_tencent_hk(sym, datalen)
        if sym.startswith("gb_") or sym.startswith("us"):
            return _kline_sina_us(sym, datalen)
        if sym.startswith("int_") or sym.startswith("znb_"):
            return _kline_sina_global_index(sym, datalen)
        if sym.startswith("nf_"):
            return _kline_sina_futures_cn(sym, datalen)
        if sym.startswith("hf_"):
            return _kline_sina_futures_intl(sym, datalen)
        if sym.startswith("fx_"):
            # 优先东财 (盘中市价口径); 不可达/空则回退 frankfurter (ECB 口径)
            try:
                k = _kline_eastmoney_fx(sym, datalen)
                if k:
                    return k
            except Exception:
                pass
            return _kline_frankfurter_fx(sym, datalen)
        return []
    except Exception:
        return []


async def get_macro_klines(symbols: list[str]) -> dict[str, list[dict]]:
    """批量拉 K 线, 5 分钟缓存. 返回 {symbol: [{date, close}, ...]}"""
    cache_key = "macro_klines_" + ",".join(sorted(symbols))
    cached = _cache_get(cache_key, 300)
    if cached is not None:
        return cached
    # 并发拉
    tasks = [asyncio.to_thread(_kline_for_symbol, s) for s in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    out: dict[str, list[dict]] = {}
    for sym, res in zip(symbols, results):
        if isinstance(res, list):
            out[sym] = res
    _cache_set(cache_key, out)
    return out


def _cst_now():
    from datetime import timezone
    return datetime.now(timezone.utc) + timedelta(hours=8)


def _is_a_share_trading_day(d) -> bool:
    """A 股交易日: 工作日 + 排除中国法定节假日 + 调休补班也算.
    chinese_calendar.is_workday() 已经把 节假日 / 周末 / 调休 都算进去了.
    库不可用时退回到周末判断 (节假日仍误报为交易日, 但至少不全错)."""
    if d.weekday() >= 5:
        return False
    try:
        import chinese_calendar as cc
        return cc.is_workday(d)
    except Exception:
        return True


def is_market_hours() -> bool:
    """Strict: during active trading sessions (9:30-11:30, 13:00-15:00).
    Used for price refresh frequency control."""
    cst = _cst_now()
    if not _is_a_share_trading_day(cst.date()):
        return False
    t = cst.hour * 60 + cst.minute
    return (570 <= t <= 690) or (780 <= t <= 900)


def is_trading_day_active() -> bool:
    """Broad: from 9:15 to 15:00 on weekdays (excl. holidays), including lunch break.
    Used for signal display — during lunch, signals are still valid for the afternoon."""
    cst = _cst_now()
    if not _is_a_share_trading_day(cst.date()):
        return False
    t = cst.hour * 60 + cst.minute
    return 555 <= t <= 900  # 9:15 ~ 15:00
