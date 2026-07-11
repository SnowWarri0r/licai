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


# ---------------------------------------------------------------------------
# DOM 级正文提取 (readability-lite): 按 <p> 文本密度找正文容器
# ---------------------------------------------------------------------------

_ARTICLE_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def extract_article_text(url: str, timeout: int = 10) -> dict | None:
    """直连抓 HTML, 用 BeautifulSoup 按 <p> 文本密度定位正文容器并抽正文。
    导航/面包屑/分享条/APP推广都在正文容器之外, 整块天然剔除 —— 比对整页
    markdown 做行级启发式清洗稳得多。服务端渲染的中文新闻站(东财/界面/新浪/
    财联社等)都适用; JS 渲染页抽不到会返回 None, 由上层回退 Firecrawl。"""
    try:
        from bs4 import BeautifulSoup
        s = _requests.Session()
        s.trust_env = False          # 中文新闻站直连; 海外站交给上层的 Firecrawl 兜底
        r = s.get(url, timeout=timeout, headers={"User-Agent": _ARTICLE_UA})
        if r.status_code != 200 or not r.text:
            return None
        if not r.encoding or r.encoding.lower() == "iso-8859-1":   # 未声明字符集时按内容猜(gbk 站常见)
            r.encoding = r.apparent_encoding
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "form", "iframe", "figure"]):
            tag.decompose()
        # 聚合 <p> 文本量: 按父容器与祖父容器两个粒度记分(有的站每段各包一层 div),
        # 得分最高的容器即正文
        cand: dict[int, list] = {}
        for p in soup.find_all("p"):
            t = p.get_text(" ", strip=True)
            if len(t) < 10:
                continue
            for anc in (p.parent, p.parent.parent if p.parent is not None else None):
                if anc is None or anc.name in ("html", "body", None):
                    continue
                e = cand.setdefault(id(anc), [anc, 0])
                e[1] += len(t)
        if not cand:
            return None
        best, score = max(cand.values(), key=lambda e: e[1])
        if score < 200:              # 正文体量不足: 可能是 JS 渲染页/占位页
            return None
        paras = [p.get_text(" ", strip=True) for p in best.find_all("p")]
        body = "\n\n".join(t for t in paras if len(t) >= 2)
        h1 = soup.find("h1")
        title = (h1.get_text(strip=True) if h1 else "") or (soup.title.get_text(strip=True) if soup.title else "")
        # 死链保护: 有的站 404 也返回 200 + '页面不存在' 推广页, p 密度照样能凑够分
        if any(k in (title + body[:120]) for k in ("页面不存在", "页面未找到", "404")):
            return None
        return {"title": title, "text": body.strip(), "via": "dom"}
    except Exception:
        return None
