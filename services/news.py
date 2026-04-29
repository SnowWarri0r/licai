"""News fetching service for stocks. Uses East Money's news API."""
from __future__ import annotations
import asyncio
import time
import requests as _requests

# In-memory cache: {stock_code: (news_list, ts)}
_news_cache: dict[str, tuple[list, float]] = {}
_NEWS_TTL = 600  # 10 minutes


def _market_prefix(stock_code: str) -> str:
    """East Money uses 0.xxxxxx (Shenzhen) or 1.xxxxxx (Shanghai)."""
    return "1" if stock_code.startswith("6") else "0"


def _fetch_stock_news(stock_code: str, limit: int = 10) -> list[dict]:
    """Fetch recent news for a stock from East Money."""
    market = _market_prefix(stock_code)
    url = (
        f"https://np-listapi.eastmoney.com/comm/wap/getListInfo"
        f"?cb=&client=wap&type=1&mTypeAndCode={market}.{stock_code}"
        f"&pageSize={limit}&pageIndex=1"
    )
    resp = _requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", {}).get("list", [])

    result = []
    for item in items:
        result.append({
            "title": item.get("Art_Title", ""),
            "source": item.get("Art_MediaName", ""),
            "time": item.get("Art_ShowTime", ""),
            "url": item.get("Art_Url", ""),
        })
    return result


async def get_stock_news(stock_code: str, limit: int = 10) -> list[dict]:
    """Get cached or fresh stock news."""
    cached = _news_cache.get(stock_code)
    if cached and time.time() - cached[1] < _NEWS_TTL:
        return cached[0][:limit]

    try:
        news = await asyncio.to_thread(_fetch_stock_news, stock_code, limit)
        if news:
            _news_cache[stock_code] = (news, time.time())
        return news
    except Exception as e:
        print(f"[news] Error fetching {stock_code}: {e}")
        return cached[0] if cached else []


def _fetch_sector_news(sector: str = "有色金属", limit: int = 10) -> list[dict]:
    """Fetch sector news from East Money. Uses board code lookup."""
    # 有色金属板块代码: BK0478
    sector_codes = {
        "有色金属": "BK0478",
        "黄金": "BK0892",
        "工业金属": "BK1015",
    }
    code = sector_codes.get(sector, "BK0478")
    url = (
        f"https://np-listapi.eastmoney.com/comm/wap/getListInfo"
        f"?cb=&client=wap&type=1&mTypeAndCode=90.{code}"
        f"&pageSize={limit}&pageIndex=1"
    )
    try:
        resp = _requests.get(url, timeout=10)
        data = resp.json()
        items = data.get("data", {}).get("list", [])
        return [{
            "title": i.get("Art_Title", ""),
            "source": i.get("Art_MediaName", ""),
            "time": i.get("Art_ShowTime", ""),
        } for i in items]
    except Exception:
        return []


async def get_sector_news(sector: str = "有色金属", limit: int = 5) -> list[dict]:
    cache_key = f"sector_{sector}"
    cached = _news_cache.get(cache_key)
    if cached and time.time() - cached[1] < _NEWS_TTL:
        return cached[0][:limit]

    news = await asyncio.to_thread(_fetch_sector_news, sector, limit)
    if news:
        _news_cache[cache_key] = (news, time.time())
    return news


# --- Company announcements (交易所公告) ---

_ann_cache: dict[str, tuple[list, float]] = {}
_ANN_TTL = 1800  # 30 minutes


def _fetch_stock_announcements(stock_code: str, limit: int = 15) -> list[dict]:
    """Fetch recent official announcements from East Money."""
    url = (
        "https://np-anotice-stock.eastmoney.com/api/security/ann"
        f"?sr=-1&page_size={limit}&page_index=1"
        "&ann_type=SHA,CYB,SZA,BJA&client_source=web"
        f"&stock_list={stock_code}&f_node=0&s_node=0"
    )
    resp = _requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    data = resp.json()
    items = data.get("data", {}).get("list", [])
    result = []
    for item in items:
        title = item.get("title", "")
        # Strip leading "股票名:" prefix for readability
        if ":" in title:
            title = title.split(":", 1)[1].strip()
        result.append({
            "title": title,
            "date": item.get("notice_date", "")[:10],
            "type": (item.get("columns") or [{}])[0].get("column_name", "") if item.get("columns") else "",
        })
    return result


async def get_stock_announcements(stock_code: str, limit: int = 15) -> list[dict]:
    """Get cached or fresh company announcements."""
    cached = _ann_cache.get(stock_code)
    if cached and time.time() - cached[1] < _ANN_TTL:
        return cached[0][:limit]
    try:
        anns = await asyncio.to_thread(_fetch_stock_announcements, stock_code, limit)
        if anns:
            _ann_cache[stock_code] = (anns, time.time())
        return anns
    except Exception as e:
        print(f"[announce] Error fetching {stock_code}: {e}")
        return cached[0] if cached else []
