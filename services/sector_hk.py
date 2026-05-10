"""港股板块扫描 — 12 个恒生综合行业指数 (HSCI*).

数据流:
  - HSCI 系列指数 K 线 (akshare stock_hk_index_daily_em) → 1d/5d/30d + 60d 尾
  - 缓存 10 min
  - 持仓 → 板块 走 akshare 公司资料 (`所属行业` 文本) + 关键词映射
"""
from __future__ import annotations
import asyncio
import time


_CACHE_TTL = 600
_cache: tuple[dict, float] | None = None
_cache_lock = asyncio.Lock()
_CONCURRENCY = 6


# 12 恒生综合行业指数 + 兜底 A 股上市的港股 ETF.
# 港股没有 12 个一一对应的窄基板块 ETF, 部分行业用宽基/中企指数兜底, 找不到的留空.
_SECTORS: list[tuple[str, str, str | None, str | None]] = [
    ("HSCIIT", "资讯科技",   "513130", "恒生科技ETF"),       # 华泰柏瑞
    ("HSCIFN", "金融",       "513190", "港股通金融ETF"),     # 华夏
    ("HSCIEN", "能源",       "159954", "恒生中国企业ETF"),   # 兜底: 中海油/中石油 蓝筹权重
    ("HSCICH", "医疗保健",   "513060", "恒生医疗ETF"),       # 博时
    ("HSCICD", "非必需消费", "513590", "港股通消费ETF"),     # 鹏华
    ("HSCICS", "必需消费",   "513590", "港股通消费ETF"),     # 兜底: 消费 ETF 含食品饮料
    ("HSCIIN", "工业",       "159954", "恒生中国企业ETF"),   # 兜底
    ("HSCIMT", "原材料",     None,     None),
    ("HSCIUT", "公用事业",   None,     None),
    ("HSCIPC", "地产建筑",   None,     None),                # 港股地产无窄基 ETF
    ("HSCITC", "电讯",       "159954", "恒生中国企业ETF"),   # 兜底: 中移动/中电信
    ("HSCICO", "综合企业",   "513660", "恒生ETF"),           # 兜底: 港股宽基
]

# akshare HK 公司资料 `所属行业` → HSCI 12 板块. 按子串包含匹配, 顺序敏感 (越具体越前).
_HK_INDUSTRY_KEYWORDS: list[tuple[list[str], str]] = [
    # 资讯科技 (软件 / 硬件 / 半导体 / 互联网平台)
    (["软件", "互联网", "电脑", "半导体", "电子设备", "信息", "资讯"], "资讯科技"),
    # 医疗保健
    (["药品", "医药", "医疗", "生物", "保健", "卫生"], "医疗保健"),
    # 金融 (银行 / 证券 / 保险 / 资产管理)
    (["银行", "证券", "保险", "资产管理", "财富管理", "金融服务"], "金融"),
    # 地产建筑
    (["地产", "物业", "建筑", "工程", "建设"], "地产建筑"),
    # 电讯
    (["电讯", "通讯", "电信", "移动通信"], "电讯"),
    # 公用事业
    (["电力", "燃气", "水务", "公用事业", "能源生产"], "公用事业"),
    # 能源 (石油 / 天然气 / 煤炭)
    (["石油", "天然气", "煤", "原油"], "能源"),
    # 原材料
    (["金属", "钢铁", "有色", "化工", "化学", "材料", "矿业", "水泥", "玻璃"], "原材料"),
    # 必需消费 (食品 / 饮料 / 零售必需 / 农林牧渔)
    (["食品", "饮料", "乳", "肉", "粮油", "烟草", "农", "林业", "畜牧"], "必需消费"),
    # 非必需消费 (汽车 / 服装 / 家电 / 旅游 / 餐饮 / 教育 / 零售非必需)
    (["汽车", "服装", "纺织", "家电", "旅游", "酒店", "餐饮", "娱乐", "教育",
      "百货", "零售", "电商", "钟表", "珠宝", "化妆"], "非必需消费"),
    # 工业 (基建 / 制造 / 物流 / 航运 / 重工)
    (["运输", "物流", "航运", "港口", "航空", "铁路", "公路", "工业", "机械",
      "重工", "制造业", "电气", "国防"], "工业"),
    # 综合企业
    (["综合企业", "多元化经营", "多元业务"], "综合企业"),
]


def _close_pct(closes: list[float], n: int) -> float | None:
    if len(closes) < n + 1:
        return None
    last = closes[-1]
    prior = closes[-n - 1]
    if prior <= 0:
        return None
    return round((last / prior - 1) * 100, 2)


def _fetch_index_kline_sync(symbol: str) -> list[dict]:
    try:
        import akshare as ak
        df = ak.stock_hk_index_daily_em(symbol=symbol)
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.tail(120).iterrows():
            try:
                out.append({
                    "date": str(r["date"]),
                    "close": float(r["latest"]),
                })
            except (ValueError, TypeError, KeyError):
                continue
        return out
    except Exception as e:
        print(f"[sector_hk] {symbol} kline failed: {e}")
        return []


_industry_cache: dict[str, str | None] = {}  # ticker → HSCI sector cn_name (or None)


def _fetch_hk_industry_sync(ticker: str) -> str | None:
    """ticker (e.g. '00700') → HSCI 12 板块名 or None."""
    if ticker in _industry_cache:
        return _industry_cache[ticker]
    try:
        import akshare as ak
        df = ak.stock_hk_company_profile_em(symbol=ticker)
        if df is None or df.empty:
            _industry_cache[ticker] = None
            return None
        raw = str(df.iloc[0].get("所属行业", "")).strip()
        if not raw:
            _industry_cache[ticker] = None
            return None
        for keywords, sector in _HK_INDUSTRY_KEYWORDS:
            if any(k in raw for k in keywords):
                _industry_cache[ticker] = sector
                return sector
        _industry_cache[ticker] = None
        return None
    except Exception as e:
        print(f"[sector_hk] industry lookup {ticker} failed: {e}")
        _industry_cache[ticker] = None
        return None


async def _resolve_held_sectors(held_codes: list[str]) -> set[str]:
    tickers: list[str] = []
    for code in held_codes:
        c = (code or "").upper()
        if c.startswith("HK."):
            tickers.append(c[3:].strip())
    if not tickers:
        return set()
    sectors = await asyncio.gather(*(
        asyncio.to_thread(_fetch_hk_industry_sync, t) for t in tickers
    ), return_exceptions=True)
    held: set[str] = set()
    for s in sectors:
        if isinstance(s, str) and s:
            held.add(s)
    return held


async def _scan_uncached(held_codes: list[str]) -> dict:
    sem = asyncio.Semaphore(_CONCURRENCY)
    held_sectors = await _resolve_held_sectors(held_codes)

    async def fetch_one(code: str, cn: str, etf_code: str | None, etf_name: str | None) -> dict:
        async with sem:
            kline = await asyncio.to_thread(_fetch_index_kline_sync, code)
        closes = [k["close"] for k in kline if k.get("close")]
        tail = kline[-min(60, len(kline)):]
        return {
            "name": cn,
            "symbol": code,
            "change_1d": _close_pct(closes, 1),
            "change_5d": _close_pct(closes, 5),
            "change_30d": _close_pct(closes, 30),
            "kline_tail": [{"date": k["date"], "close": k["close"]} for k in tail],
            "etf_code": etf_code,
            "etf_name": etf_name,
            "held": cn in held_sectors,
        }

    rows = await asyncio.gather(*(fetch_one(*s) for s in _SECTORS))

    def sort_key(r: dict):
        c5 = r.get("change_5d")
        c1 = r.get("change_1d")
        return (-(c5 if c5 is not None else -999),
                -(c1 if c1 is not None else -999))
    rows.sort(key=sort_key)
    return {
        "sectors": rows,
        "total": len(rows),
        "market": "HK",
        "held_boards": sorted(held_sectors),
    }


async def scan_hk_sectors(held_codes: list[str] | None = None, force: bool = False) -> dict:
    """Scan HK sectors. held_codes 形如 ['HK.00700']."""
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
