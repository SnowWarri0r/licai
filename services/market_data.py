"""A-share market data with Sina Finance API for real-time quotes + AKShare for history."""
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


def _sina_symbol(stock_code: str) -> str:
    """Convert stock code to Sina symbol format (sh/sz prefix)."""
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
    """Get real-time quotes for given stock codes via Sina Finance."""
    if not stock_codes:
        return {}

    cache_key = "sina_quotes_" + ",".join(sorted(stock_codes))
    cached = _cache_get(cache_key, config.quote_cache_ttl)
    if cached is not None:
        return cached

    try:
        result = await asyncio.to_thread(_fetch_sina_quotes, stock_codes)
        if result:
            _cache_set(cache_key, result)
        return result
    except Exception as e:
        print(f"[market_data] Error fetching Sina quotes: {e}")
        return {}


async def get_stock_name(stock_code: str) -> str:
    """Look up stock name by code."""
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


def _lookup_industry(stock_code: str) -> str:
    """Look up stock industry via East Money CompanySurvey API (emweb domain, stable)."""
    try:
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
        return {"price": last, "prev": prev, "change_pct": change_pct}
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
        return {"price": last, "prev": prev, "change_pct": round(change_pct, 2)}
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


def _cst_now():
    from datetime import timezone
    return datetime.now(timezone.utc) + timedelta(hours=8)


def is_market_hours() -> bool:
    """Strict: during active trading sessions (9:30-11:30, 13:00-15:00).
    Used for price refresh frequency control."""
    cst = _cst_now()
    if cst.weekday() >= 5:
        return False
    t = cst.hour * 60 + cst.minute
    return (570 <= t <= 690) or (780 <= t <= 900)


def is_trading_day_active() -> bool:
    """Broad: from 9:15 to 15:00 on weekdays, including lunch break.
    Used for signal display — during lunch, signals are still valid for the afternoon."""
    cst = _cst_now()
    if cst.weekday() >= 5:
        return False
    t = cst.hour * 60 + cst.minute
    return 555 <= t <= 900  # 9:15 ~ 15:00
