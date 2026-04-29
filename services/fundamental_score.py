"""Fundamental health scoring for unwind decisions.

Combines 4 signals into a single score in [-1, 1]:
- Sector index 5-day performance (weight 0.3)
- Related futures 5-day performance (weight 0.2)
- LLM news sentiment (weight 0.3)
- Company announcement sentiment (weight 0.2)

Then maps the score to a discrete health level: green / yellow / red.
"""
from __future__ import annotations
import asyncio
import json
import time

_sentiment_cache: dict[str, tuple[float, float]] = {}  # code -> (score, ts)
_ann_sentiment_cache: dict[str, tuple[float, float]] = {}
_SENTIMENT_TTL = 3600  # 1 hour

WEIGHT_SECTOR = 0.3
WEIGHT_FUTURES = 0.2
WEIGHT_NEWS = 0.3
WEIGHT_ANNOUNCEMENT = 0.2


def compute_score(
    sector_5d_perf: float = 0.0,
    futures_5d_perf: float = 0.0,
    llm_sentiment: float = 0.0,
    announcement_score: float = 0.0,
) -> float:
    """Weighted combination. All inputs are signed floats, roughly in [-1, 1]."""
    return (
        WEIGHT_SECTOR * sector_5d_perf
        + WEIGHT_FUTURES * futures_5d_perf
        + WEIGHT_NEWS * llm_sentiment
        + WEIGHT_ANNOUNCEMENT * announcement_score
    )


def classify_health(score: float) -> str:
    """Map score to health band.

    >= 0.5  → green  (freely add)
    >= -0.5 → yellow (only shallow tranches)
    <  -0.5 → red    (pause all adding)
    """
    if score >= 0.5:
        return "green"
    elif score >= -0.5:
        return "yellow"
    else:
        return "red"


SENTIMENT_SYSTEM = """你是 A 股新闻情感分析器。根据一批新闻标题对个股短期（1-5 日）的影响做判定。
输出严格 JSON（无 markdown 代码块）：
{"score": -1.0~1.0 的情感分, "rationale": "一句话说明"}

评分标尺：
+1.0 重大利好（业绩大超预期、政策扶持、大额订单）
+0.5 偏多（行业回暖、数据向好）
 0   中性或无显著信号
-0.5 偏空（下游需求疲弱、监管审查）
-1.0 重大利空（业绩暴雷、重大诉讼、黑天鹅）

只评估这家公司/所属板块的信号，忽略泛市场资金流、美联储等无关新闻。"""


async def _fetch_news_sentiment(stock_code: str, stock_name: str = "") -> float:
    """Fetch stock news and ask LLM for a sentiment score in [-1, 1]. Cached 1h."""
    cached = _sentiment_cache.get(stock_code)
    if cached and time.time() - cached[1] < _SENTIMENT_TTL:
        return cached[0]

    try:
        from services.news import get_stock_news
        from services.llm_client import call_claude
    except Exception:
        return 0.0

    try:
        news = await get_stock_news(stock_code, limit=10)
    except Exception:
        return 0.0
    if not news:
        _sentiment_cache[stock_code] = (0.0, time.time())
        return 0.0

    titles = "\n".join(f"- [{n.get('time','')[:10]}] {n.get('title','')}" for n in news[:10])
    prompt = f"【个股】{stock_name or stock_code}({stock_code})\n【近期新闻】\n{titles}\n\n请评估以上新闻对该股短期的综合情感影响，输出 JSON。"

    try:
        resp = await asyncio.to_thread(
            call_claude, prompt, SENTIMENT_SYSTEM, "claude-haiku-4-5-20251001", 200
        )
        resp = resp.strip()
        if resp.startswith("```"):
            resp = resp.split("```")[1]
            if resp.startswith("json"):
                resp = resp[4:]
            resp = resp.strip()
        data = json.loads(resp)
        score = float(data.get("score", 0.0))
        score = max(-1.0, min(1.0, score))
    except Exception:
        score = 0.0

    _sentiment_cache[stock_code] = (score, time.time())
    return score


ANNOUNCEMENT_SYSTEM = """你是 A 股公司公告重要性评估器。根据一批公司交易所公告标题评估对股价的短期影响。
输出严格 JSON（无 markdown 代码块）：
{"score": -1.0~1.0 的影响分, "rationale": "一句话说明"}

评分标尺（只看实质性影响，忽略程序性公告）：
+1.0 重大利好（业绩大超预期、重大中标、被收购要约、重要产品获批）
+0.5 偏多（回购计划、股东增持、业绩预告略超预期、战略合作）
 0   中性（股东会通知、H股类别股东会、年报披露这类例行公告、信息披露制度等）
-0.5 偏空（股东减持计划、业绩预告不及预期、限售解禁、未解决的监管问询）
-1.0 重大利空（巨额计提、财务造假立案、重大诉讼败诉、实控人被调查）

年报/季报本身不带好坏——只有"业绩预告"才携带情感。"""


async def _fetch_announcement_sentiment(stock_code: str, stock_name: str = "") -> float:
    """Fetch exchange announcements and ask LLM for materiality score. Cached 1h."""
    cached = _ann_sentiment_cache.get(stock_code)
    if cached and time.time() - cached[1] < _SENTIMENT_TTL:
        return cached[0]

    try:
        from services.news import get_stock_announcements
        from services.llm_client import call_claude
    except Exception:
        return 0.0

    try:
        anns = await get_stock_announcements(stock_code, limit=15)
    except Exception:
        return 0.0
    if not anns:
        _ann_sentiment_cache[stock_code] = (0.0, time.time())
        return 0.0

    lines = "\n".join(f"- [{a.get('date','')}] {a.get('title','')}" for a in anns[:15])
    prompt = f"【个股】{stock_name or stock_code}({stock_code})\n【近期交易所公告】\n{lines}\n\n评估这些公告对股价的综合影响，输出 JSON。"

    try:
        resp = await asyncio.to_thread(
            call_claude, prompt, ANNOUNCEMENT_SYSTEM, "claude-haiku-4-5-20251001", 200
        )
        resp = resp.strip()
        if resp.startswith("```"):
            resp = resp.split("```")[1]
            if resp.startswith("json"):
                resp = resp[4:]
            resp = resp.strip()
        data = json.loads(resp)
        score = float(data.get("score", 0.0))
        score = max(-1.0, min(1.0, score))
    except Exception:
        score = 0.0

    _ann_sentiment_cache[stock_code] = (score, time.time())
    return score


async def fetch_health_snapshot(stock_code: str, stock_name: str = "") -> dict:
    """Fetch live inputs and compute health score.

    Returns:
        {
            "score": float,
            "level": "green" | "yellow" | "red",
            "details": {
                "sector_5d_perf": ...,
                "futures_5d_perf": ...,
                "llm_sentiment": ...,
                "announcement_score": ...,
            }
        }

    LLM sentiment and announcement scoring are stubbed to 0 for MVP.
    Will wire in later iterations.
    """
    from services.market_data import get_market_indices, get_commodity_for_stock

    sector_perf = 0.0
    try:
        indices = await get_market_indices()
        for idx in indices:
            if "有色" in idx.get("name", ""):
                sector_perf = idx.get("change_pct", 0) / 100.0
                break
    except Exception:
        pass

    futures_perf = 0.0
    try:
        commodity = await get_commodity_for_stock(stock_code)
        if commodity:
            futures_perf = commodity.get("change_pct", 0) / 100.0 / 5  # rough 5d proxy
    except Exception:
        pass

    llm_sent, ann = await asyncio.gather(
        _fetch_news_sentiment(stock_code, stock_name),
        _fetch_announcement_sentiment(stock_code, stock_name),
    )

    score = compute_score(
        sector_5d_perf=sector_perf,
        futures_5d_perf=futures_perf,
        llm_sentiment=llm_sent,
        announcement_score=ann,
    )
    return {
        "score": round(score, 3),
        "level": classify_health(score),
        "details": {
            "sector_5d_perf": round(sector_perf, 4),
            "futures_5d_perf": round(futures_perf, 4),
            "llm_sentiment": llm_sent,
            "announcement_score": ann,
        },
    }
