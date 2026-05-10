"""美股板块扫描 — 11 个 SPDR Sector ETF 作为 GICS 板块代理.

数据流:
  - 11 个 SPDR Select Sector SPDR ETF 拉日 K → 1d/5d/30d 涨幅 + 60d 收盘尾巴
  - akshare stock_us_hist 用 107.XXX 前缀 (NYSE Arca)
  - 缓存 10 min
  - 持仓 ticker → GICS sector 走硬编码映射 (akshare 美股没有现成的 sector 接口)

注意: 美股以前一交易日收盘为最新, 与 A 股 T+0 行情语义不同.
"""
from __future__ import annotations
import asyncio
import time
from datetime import datetime, timedelta


_CACHE_TTL = 600  # 10 min
_cache: tuple[dict, float] | None = None
_cache_lock = asyncio.Lock()
_CONCURRENCY = 6


# 11 GICS Sectors via Select Sector SPDR ETFs (NYSE Arca → 107.* prefix)
_SECTORS: list[tuple[str, str, str]] = [
    # (symbol, cn_name, en_name)
    ("XLK",  "信息技术",   "Technology"),
    ("XLF",  "金融",       "Financials"),
    ("XLE",  "能源",       "Energy"),
    ("XLV",  "医疗保健",   "Health Care"),
    ("XLY",  "非必需消费", "Consumer Discretionary"),
    ("XLP",  "必需消费",   "Consumer Staples"),
    ("XLI",  "工业",       "Industrials"),
    ("XLB",  "原材料",     "Materials"),
    ("XLU",  "公用事业",   "Utilities"),
    ("XLRE", "房地产",     "Real Estate"),
    ("XLC",  "通信服务",   "Communication Services"),
]

# US ticker → GICS sector (匹配 _SECTORS 里的 cn_name).
# 覆盖中国投资者最常持有的美股大盘 + 中概. 不在表里的会找不到, held 标记缺失但不影响行情.
_TICKER_TO_SECTOR: dict[str, str] = {
    # 信息技术 XLK
    "AAPL": "信息技术", "MSFT": "信息技术", "NVDA": "信息技术", "ORCL": "信息技术",
    "CRM": "信息技术", "ADBE": "信息技术", "AMD": "信息技术", "AVGO": "信息技术",
    "INTC": "信息技术", "QCOM": "信息技术", "TXN": "信息技术", "MU": "信息技术",
    "ASML": "信息技术", "ARM": "信息技术", "PLTR": "信息技术", "NOW": "信息技术",
    "PANW": "信息技术", "SNOW": "信息技术", "CRWD": "信息技术", "DELL": "信息技术",
    "IBM": "信息技术", "ACN": "信息技术", "CSCO": "信息技术", "SHOP": "信息技术",
    "ANET": "信息技术", "MRVL": "信息技术", "AMAT": "信息技术", "KLAC": "信息技术",
    "LRCX": "信息技术", "SMCI": "信息技术", "TSM": "信息技术", "QQQ": "信息技术",
    "SOXX": "信息技术", "SMH": "信息技术",
    # 通信服务 XLC
    "GOOGL": "通信服务", "GOOG": "通信服务", "META": "通信服务", "NFLX": "通信服务",
    "DIS": "通信服务", "T": "通信服务", "VZ": "通信服务", "TMUS": "通信服务",
    "EA": "通信服务", "TTWO": "通信服务", "RBLX": "通信服务", "SPOT": "通信服务",
    "PINS": "通信服务", "SNAP": "通信服务", "WBD": "通信服务",
    # 金融 XLF
    "BRK.B": "金融", "BRK.A": "金融", "JPM": "金融", "BAC": "金融", "WFC": "金融",
    "C": "金融", "GS": "金融", "MS": "金融", "V": "金融", "MA": "金融",
    "AXP": "金融", "PYPL": "金融", "BLK": "金融", "SCHW": "金融", "COF": "金融",
    "USB": "金融", "PNC": "金融", "TFC": "金融", "AIG": "金融", "MET": "金融",
    "PRU": "金融", "ALL": "金融", "SPGI": "金融", "ICE": "金融", "CME": "金融",
    "MCO": "金融", "BX": "金融", "KKR": "金融", "COIN": "金融", "HOOD": "金融",
    "SOFI": "金融",
    # 能源 XLE
    "XOM": "能源", "CVX": "能源", "COP": "能源", "EOG": "能源", "SLB": "能源",
    "PSX": "能源", "OXY": "能源", "MPC": "能源", "VLO": "能源", "PXD": "能源",
    "FANG": "能源", "HES": "能源", "DVN": "能源", "WMB": "能源", "KMI": "能源",
    "EPD": "能源", "ET": "能源",
    # 医疗保健 XLV
    "LLY": "医疗保健", "UNH": "医疗保健", "JNJ": "医疗保健", "ABBV": "医疗保健",
    "MRK": "医疗保健", "PFE": "医疗保健", "TMO": "医疗保健", "ABT": "医疗保健",
    "DHR": "医疗保健", "BMY": "医疗保健", "AMGN": "医疗保健", "GILD": "医疗保健",
    "VRTX": "医疗保健", "REGN": "医疗保健", "ISRG": "医疗保健", "MDT": "医疗保健",
    "SYK": "医疗保健", "ELV": "医疗保健", "CVS": "医疗保健", "CI": "医疗保健",
    "HUM": "医疗保健", "BSX": "医疗保健", "BIIB": "医疗保健", "MRNA": "医疗保健",
    "NVO": "医疗保健",
    # 非必需消费 XLY (含汽车/零售/餐饮/旅游)
    "AMZN": "非必需消费", "TSLA": "非必需消费", "HD": "非必需消费", "MCD": "非必需消费",
    "NKE": "非必需消费", "SBUX": "非必需消费", "BKNG": "非必需消费", "LOW": "非必需消费",
    "TGT": "非必需消费", "ABNB": "非必需消费", "GM": "非必需消费", "F": "非必需消费",
    "RIVN": "非必需消费", "LI": "非必需消费", "NIO": "非必需消费", "XPEV": "非必需消费",
    "PDD": "非必需消费", "BABA": "非必需消费", "JD": "非必需消费", "MELI": "非必需消费",
    "EBAY": "非必需消费", "ETSY": "非必需消费", "DIS": "非必需消费",
    "CMG": "非必需消费", "YUM": "非必需消费", "MAR": "非必需消费", "HLT": "非必需消费",
    "LULU": "非必需消费", "ROST": "非必需消费", "TJX": "非必需消费",
    # 必需消费 XLP (含食品/饮料/日用品)
    "WMT": "必需消费", "COST": "必需消费", "PG": "必需消费", "KO": "必需消费",
    "PEP": "必需消费", "PM": "必需消费", "MO": "必需消费", "MDLZ": "必需消费",
    "CL": "必需消费", "KMB": "必需消费", "EL": "必需消费", "MNST": "必需消费",
    "GIS": "必需消费", "K": "必需消费", "STZ": "必需消费", "TGT": "必需消费",
    # 工业 XLI
    "BA": "工业", "CAT": "工业", "GE": "工业", "HON": "工业", "UNP": "工业",
    "RTX": "工业", "LMT": "工业", "DE": "工业", "UPS": "工业", "FDX": "工业",
    "NOC": "工业", "GD": "工业", "MMM": "工业", "ETN": "工业", "EMR": "工业",
    "PH": "工业", "CSX": "工业", "NSC": "工业", "WM": "工业", "RSG": "工业",
    "DAL": "工业", "UAL": "工业", "AAL": "工业", "LUV": "工业", "UBER": "工业",
    "LYFT": "工业",
    # 原材料 XLB
    "LIN": "原材料", "APD": "原材料", "SHW": "原材料", "ECL": "原材料", "FCX": "原材料",
    "NEM": "原材料", "DOW": "原材料", "DD": "原材料", "PPG": "原材料", "NUE": "原材料",
    "VMC": "原材料", "MLM": "原材料",
    # 公用事业 XLU
    "NEE": "公用事业", "SO": "公用事业", "DUK": "公用事业", "AEP": "公用事业", "D": "公用事业",
    "EXC": "公用事业", "SRE": "公用事业", "XEL": "公用事业", "WEC": "公用事业", "ED": "公用事业",
    "PCG": "公用事业",
    # 房地产 XLRE
    "PLD": "房地产", "AMT": "房地产", "EQIX": "房地产", "WELL": "房地产", "PSA": "房地产",
    "O": "房地产", "DLR": "房地产", "SPG": "房地产", "EQR": "房地产", "AVB": "房地产",
    "VICI": "房地产", "CCI": "房地产",
}


def _close_pct(closes: list[float], n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    last = closes[-1]
    prior = closes[-n - 1]
    if prior <= 0:
        return None
    return round((last / prior - 1) * 100, 2)


def _fetch_etf_kline_sync(symbol: str) -> list[dict]:
    try:
        import akshare as ak
        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=120)).strftime("%Y%m%d")
        df = ak.stock_us_hist(symbol=f"107.{symbol}", period="daily",
                              start_date=start, end_date=end, adjust="qfq")
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            try:
                out.append({"date": str(r["日期"]), "close": float(r["收盘"])})
            except (ValueError, TypeError, KeyError):
                continue
        return out
    except Exception as e:
        print(f"[sector_us] {symbol} kline failed: {e}")
        return []


def _resolve_held_sectors(held_codes: list[str]) -> set[str]:
    """US.AAPL → 信息技术 (cn_name). 不在硬编码表里的 ticker 静默忽略."""
    held: set[str] = set()
    for code in held_codes:
        c = (code or "").upper()
        if c.startswith("US."):
            ticker = c[3:].strip()
            sector = _TICKER_TO_SECTOR.get(ticker)
            if sector:
                held.add(sector)
    return held


async def _scan_uncached(held_codes: list[str]) -> dict:
    sem = asyncio.Semaphore(_CONCURRENCY)
    held_sectors = _resolve_held_sectors(held_codes)

    async def fetch_one(sym: str, cn: str, en: str) -> dict:
        async with sem:
            kline = await asyncio.to_thread(_fetch_etf_kline_sync, sym)
        closes = [k["close"] for k in kline if k.get("close")]
        tail = kline[-min(60, len(kline)):]
        return {
            "name": cn,
            "name_en": en,
            "symbol": sym,
            "change_1d": _close_pct(closes, 1),
            "change_5d": _close_pct(closes, 5),
            "change_30d": _close_pct(closes, 30),
            "kline_tail": [{"date": k["date"], "close": k["close"]} for k in tail],
            "etf_code": f"US.{sym}",
            "etf_name": f"{cn} ETF (SPDR)",
            "held": cn in held_sectors,
        }

    rows = await asyncio.gather(*(fetch_one(s, cn, en) for s, cn, en in _SECTORS))

    def sort_key(r: dict):
        c5 = r.get("change_5d")
        c1 = r.get("change_1d")
        return (-(c5 if c5 is not None else -999),
                -(c1 if c1 is not None else -999))
    rows.sort(key=sort_key)
    return {
        "sectors": rows,
        "total": len(rows),
        "market": "US",
        "held_boards": sorted(held_sectors),
    }


async def scan_us_sectors(held_codes: list[str] | None = None, force: bool = False) -> dict:
    """Scan US sectors. held_codes 形如 ['US.AAPL', 'US.NVDA']."""
    global _cache
    held_codes = held_codes or []
    cache_key = ",".join(sorted(held_codes))
    now = time.time()
    if not force and _cache and now - _cache[1] < _CACHE_TTL and _cache[0].get("_key") == cache_key:
        return _cache[0]
    async with _cache_lock:
        if not force and _cache and now - _cache[1] < _CACHE_TTL and _cache[0].get("_key") == cache_key:
            return _cache[0]
        result = await _scan_uncached(held_codes)
        result["_key"] = cache_key
        _cache = (result, time.time())
    return result
