"""基金 top10 持仓拉取 (天天基金 fundf10.eastmoney.com).

返回每只股票的代码、名称、市场（CN/HK/US）、占基金净值比例.
按季度更新，所以 24h in-memory cache 即可。
"""
from __future__ import annotations
import asyncio
import re
import time
import requests as _requests

_HOLDINGS_TTL = 24 * 3600  # 24h
_holdings_cache: dict[str, tuple[list, float]] = {}


# 东方财富代码前缀 → 市场
_EM_MARKET = {
    "0":   "CN_SZ",   # 深A
    "1":   "CN_SH",   # 沪A
    "105": "US",      # NASDAQ
    "106": "US",      # NYSE
    "107": "US",      # AMEX
    "116": "HK",      # 港股
    "113": "OTHER",   # 其他 (跳过)
}


def _parse_em_code(em_code: str) -> tuple[str, str] | None:
    """'106.TSM' → ('US', 'TSM'); '116.00700' → ('HK', '00700'); '0.000001' → ('CN_SZ', '000001')"""
    if not em_code or "." not in em_code:
        return None
    prefix, ticker = em_code.split(".", 1)
    market = _EM_MARKET.get(prefix)
    if not market:
        return None
    return market, ticker


def _fetch_top10_sync(fund_code: str) -> list[dict]:
    """从天天基金抓 top10 持仓 HTML，解析出 [{code, name, market, weight}, ...]"""
    url = f"http://fundf10.eastmoney.com/FundArchivesDatas.aspx?type=jjcc&code={fund_code}&topline=10&year=&month="
    try:
        r = _requests.get(url, timeout=8, headers={"Referer": f"http://fundf10.eastmoney.com/ccmx_{fund_code}.html"})
        r.encoding = "utf-8"
        text = r.text
    except Exception as e:
        print(f"[holdings] {fund_code} fetch failed: {e}")
        return []

    # Each row pattern: 序号 td → code td (with <a href='.../r/CODE'>) → name td (with <a>NAME</a>) → ... → weight td
    # 简化提取: 先按 <tr> 分割,然后每个 tr 提取 (code, name, weight)
    rows = re.findall(r"<tr>.*?</tr>", text, re.DOTALL)
    out = []
    for row in rows:
        # 跳过表头
        if "<th" in row:
            continue
        # 提取 EM code (类似 106.TSM, 0.000001, 1.600000, 116.00700)
        m_code = re.search(r"/r/(\d+\.[A-Za-z0-9]+)'", row)
        if not m_code:
            continue
        em_code = m_code.group(1)
        parsed = _parse_em_code(em_code)
        if not parsed:
            continue
        market, ticker = parsed

        # 名称: 第二个 <a> 的文本 (第一个是代码自身)
        m_names = re.findall(r"<a[^>]*>([^<]+)</a>", row)
        name = m_names[1] if len(m_names) >= 2 else ticker

        # 占净值比例: 找 toc class 里的 X.XX%
        m_pct = re.search(r"<td class='toc'>([\d.]+)%</td>", row)
        if not m_pct:
            continue
        weight = float(m_pct.group(1)) / 100  # 转成 0.0 ~ 1.0

        out.append({
            "code": ticker,
            "name": name,
            "market": market,
            "weight": weight,
            "em_code": em_code,
        })
    return out[:10]


async def get_fund_top10(fund_code: str, force: bool = False) -> list[dict]:
    """异步获取 top10 持仓 (24h cache)."""
    now = time.time()
    if not force:
        c = _holdings_cache.get(fund_code)
        if c and now - c[1] < _HOLDINGS_TTL:
            return c[0]
    holdings = await asyncio.to_thread(_fetch_top10_sync, fund_code)
    if holdings:
        _holdings_cache[fund_code] = (holdings, now)
    return holdings


async def fetch_holding_quote(holding: dict) -> dict | None:
    """单只成分股实时报价。返回 {code, market, change_pct, price}."""
    market = holding["market"]
    code = holding["code"]
    quote = None
    try:
        if market == "HK":
            from services.market_data import _fetch_hk_stock_quote
            quote = await asyncio.to_thread(_fetch_hk_stock_quote, code)
        elif market == "US":
            from services.market_data import _fetch_us_stock_quote
            quote = await asyncio.to_thread(_fetch_us_stock_quote, code)
        elif market in ("CN_SH", "CN_SZ"):
            from services.market_data import get_realtime_quotes
            r = await get_realtime_quotes([code])
            q = r.get(code)
            if q:
                quote = {"price": q["price"], "change_pct": q["change_pct"]}
    except Exception as e:
        print(f"[holding-quote] {market}/{code} failed: {e}")
        return None
    if not quote:
        return None
    return {**holding, **quote}
