"""Fundamental health scoring — 一只标的当下的"基本面健康度".

消费方两处: `stock_agent` 的红线清单工具、`morning_briefing` 的盘前简报。
(原本是解套档位的放行闸门, 那个特性已退役, 这个打分本身留下来了。)

四路信号加权成一个 [-1, 1] 的分, 再映射成 green / yellow / red:
- 所属行业板块近 5 日涨跌 (权重 0.3)
- 关联商品期货当日涨跌 (权重 0.2)
- 新闻情感 (权重 0.3)
- 公告重要性 (权重 0.2)

**四路都必须先归一到 [-1, 1] 再进来** —— 见 norm_pct。这条是踩出来的:
情感两路本来就是 [-1,1], 而行情两路早先直接喂 `change_pct / 100`(3% 涨 → 0.03),
于是名义上占一半权重的行情信号实际只贡献了万分之几, 分数几乎全由情感决定,
绿灯要两路情感同时打满才够 —— 等价于绿灯不可达、几乎恒黄。
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

# 归一满量程: 板块 5 日 / 商品当日 涨跌 ±5% 视作 ±1(超出截断)。
# 5% 不是拍的: 行业板块 5 日 ±5% 已是明显的板块级异动, 再大属极端行情, 截断不丢信息。
SECTOR_FULL_SCALE_PCT = 5.0
FUTURES_FULL_SCALE_PCT = 5.0


def norm_pct(pct: float | None, full_scale_pct: float) -> float | None:
    """把百分数(-3.2 表示 -3.2%)归一到 [-1, 1]。None 透传 —— 代表这路信号取不到。"""
    if pct is None or full_scale_pct <= 0:
        return None
    return max(-1.0, min(1.0, pct / full_scale_pct))


def compute_score(
    sector_perf: float | None = None,
    futures_perf: float | None = None,
    news_sentiment: float | None = None,
    announcement_score: float | None = None,
) -> float:
    """四路已归一信号的加权平均。None = 该路取不到, 按剩余权重重新归一。

    取不到的信号不能拿 0 顶替: 0 是"中性"这个真实判断, 而"没数据"混进去只会把
    分数往中间(黄灯)拖。四个权重之和恰为 1, 所以按存活权重重新归一之后, 分数
    仍然落在 [-1, 1], ±0.5 的档位边界才是可达的。
    """
    parts = (
        (WEIGHT_SECTOR, sector_perf),
        (WEIGHT_FUTURES, futures_perf),
        (WEIGHT_NEWS, news_sentiment),
        (WEIGHT_ANNOUNCEMENT, announcement_score),
    )
    live = [(w, v) for w, v in parts if v is not None]
    if not live:
        return 0.0
    total_w = sum(w for w, _ in live)
    return sum(w * v for w, v in live) / total_w


def classify_health(score: float) -> str:
    """Map score to health band.

    >= 0.5  → green   信号偏正
    >= -0.5 → yellow  信号中性/混杂
    <  -0.5 → red     信号偏负
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
        # 预算别给到 200: 模型的思考 token 走的是同一个 max_tokens, 难一点的输入会把额度
        # 全花在思考上, 正文一个字都出不来(实测 700 的预算被吃掉 698)。这里的失败还是静默
        # 的 —— 回落成 score=0.0, 看不出是"新闻中性"还是"根本没答上", 所以留足余量。
        resp = await asyncio.to_thread(
            call_claude, prompt, SENTIMENT_SYSTEM, "claude-sonnet-5", 600
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
            call_claude, prompt, ANNOUNCEMENT_SYSTEM, "claude-sonnet-5", 600
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


async def _sector_5d_pct(stock_code: str) -> tuple[float | None, str]:
    """所属行业板块近 5 日涨跌%。取不到给 (None, "")。

    走 sector_compare —— 板块雷达用的同一条行业解析链路(同花顺细分板块 → 硬编码 ETF
    代理), 所以"这只票该对标哪个板块"全项目只有一份判断。它自带 30 分钟缓存, 每只
    持仓调一次不会额外打网络。

    ⚠️ source == "fallback" 表示没匹配到对标板块、退到了沪深300。那是大盘不是板块,
    所以按"这路信号取不到"处理 —— 拿大盘冒充板块, 等于给每只票都加一份同样的偏置。
    """
    try:
        from services.sector_compare import get_sector_compare
        cmp = await get_sector_compare(stock_code)
    except Exception:
        return None, ""
    if not isinstance(cmp, dict) or cmp.get("source") == "fallback":
        return None, ""
    closes = [k.get("close") for k in (cmp.get("etf_kline") or []) if k.get("close")]
    if len(closes) < 6:
        return None, ""
    prior = closes[-6]
    if prior <= 0:
        return None, ""
    return round((closes[-1] / prior - 1) * 100, 2), (cmp.get("etf_name") or "")


async def _futures_1d_pct(stock_code: str) -> tuple[float | None, str]:
    """关联商品期货当日涨跌%。没有关联品种(多数标的)给 (None, "")。

    只有当日, 不是 5 日 —— 商品报价接口这里只给快照。早先版本拿当日涨跌除以 5
    "当 5 日代理", 那不是 5 日数据, 只是把同一个当日信号缩小到五分之一。
    """
    try:
        from services.market_data import get_commodity_for_stock
        commodity = await get_commodity_for_stock(stock_code)
    except Exception:
        return None, ""
    if not commodity:
        return None, ""
    pct = commodity.get("change_pct")
    if pct is None:
        return None, ""
    try:
        return round(float(pct), 2), (commodity.get("label") or "")
    except (TypeError, ValueError):
        return None, ""


async def fetch_health_snapshot(stock_code: str, stock_name: str = "") -> dict:
    """拉四路实时输入, 算健康度。

    Returns:
        {
            "score": float,                        # [-1, 1]
            "level": "green" | "yellow" | "red",
            "details": {                           # 每路都给原始值+归一值, 便于核对
                "sector":       {"板块": str, "近5日%": float|None, "归一": float|None},
                "futures":      {"品种": str, "当日%": float|None, "归一": float|None},
                "news_sentiment": float,
                "announcement_score": float,
                "参与打分": [str, ...],            # 实际进了加权的那几路
            }
        }

    四路并发取。行情两路取不到就是 None(不参与加权、按剩余权重重新归一), 不拿 0 顶替。
    """
    (sector_pct, sector_name), (fut_pct, fut_name), news, ann = await asyncio.gather(
        _sector_5d_pct(stock_code),
        _futures_1d_pct(stock_code),
        _fetch_news_sentiment(stock_code, stock_name),
        _fetch_announcement_sentiment(stock_code, stock_name),
    )

    sector_n = norm_pct(sector_pct, SECTOR_FULL_SCALE_PCT)
    fut_n = norm_pct(fut_pct, FUTURES_FULL_SCALE_PCT)

    score = compute_score(
        sector_perf=sector_n,
        futures_perf=fut_n,
        news_sentiment=news,
        announcement_score=ann,
    )
    live = ["新闻情感", "公告重要性"]
    if sector_n is not None:
        live.insert(0, f"板块近5日({sector_name})")
    if fut_n is not None:
        live.append(f"商品当日({fut_name})")

    return {
        "score": round(score, 3),
        "level": classify_health(score),
        "details": {
            "sector": {"板块": sector_name, "近5日%": sector_pct,
                       "归一": None if sector_n is None else round(sector_n, 4)},
            "futures": {"品种": fut_name, "当日%": fut_pct,
                        "归一": None if fut_n is None else round(fut_n, 4)},
            "news_sentiment": news,
            "announcement_score": ann,
            "参与打分": live,
        },
    }
