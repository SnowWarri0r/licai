"""Morning briefing — once-a-day LLM-driven evaluation of each holding.

Pulls news + recent kline + current tranches + cost/price, asks Claude haiku to:
  1. Read the news for sentiment signals (gap risk, sector regime change)
  2. Decide a verdict per stock: lock/hold/raise/lower/add_now
  3. Call out the 1-2 key headlines that drove the verdict

The briefing is meant to run ~9:00 CST before market open and persist the result
for the rest of the day. User reads it once, no real-time noise.
"""
from __future__ import annotations
import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Any


SYSTEM_PROMPT = """你是 A 股持仓盘前信息助手。每天开盘前，基于某只持仓近期的新闻 / 公告 / K 线 / 基本面健康度，做一份**客观信息摘要 + 风险提示**。

**最重要的规则：只做信息汇总和风险提示，绝不给出买入 / 卖出 / 加仓 / 减仓 / 调档位 / 抄底 / 止盈止损等任何操作建议或价位指令。** 用户自己做决策，你只负责把今天该知道的事讲清楚。

输出**严格 JSON**（无多余文本，不要 markdown 包裹）：
{
  "signal": "偏暖 | 中性 | 偏冷 | 警惕",
  "summary": "一句话点出今天这只票最该知道的事，≤40 字，客观陈述不要套话",
  "points": ["2-4 条客观要点，每条 ≤30 字。来自新闻/公告/基本面/技术位，是事实不是建议"],
  "risk": "若有明确风险（业绩雷/监管处罚/板块利空/重要股东减持/技术明显破位）用一句话点出，否则空串",
  "confidence": "high | med | low"
}

signal 仅描述消息面/基本面**倾向**（不是操作指令）:
- 偏暖: 近期消息面/基本面偏正面
- 中性: 无明显信号，平稳
- 偏冷: 偏负面但未到风险级别
- 警惕: 有明确利空或风险需要注意

要点写法: 具体、可核对。好例:"Q3 净利同比 -38%，低于预期"、"控股股东拟减持不超 2%"、"现价已跌破 20 日线"。
避免:"建议观望""可逢低布局""注意控制仓位"这类空话和操作暗示。"""


def _kline_summary(df) -> dict:
    """Compress historical kline to a few stats the LLM can chew on."""
    if df is None or df.empty:
        return {}
    # akshare returns Chinese column names; tolerate either
    col = lambda zh, en: df[zh] if zh in df.columns else (df[en] if en in df.columns else None)
    close_s = col("收盘", "close")
    high_s = col("最高", "high")
    low_s = col("最低", "low")
    if close_s is None:
        return {}
    closes = close_s.astype(float)
    highs = high_s.astype(float) if high_s is not None else closes
    lows = low_s.astype(float) if low_s is not None else closes
    last = float(closes.iloc[-1])
    ma5 = float(closes.tail(5).mean()) if len(closes) >= 5 else None
    ma20 = float(closes.tail(20).mean()) if len(closes) >= 20 else None
    return {
        "近20日最高": round(float(highs.tail(20).max()), 2) if len(highs) else None,
        "近20日最低": round(float(lows.tail(20).min()), 2) if len(lows) else None,
        "近5日均价": round(ma5, 2) if ma5 else None,
        "近20日均价": round(ma20, 2) if ma20 else None,
        "趋势": ("下行" if ma5 and ma20 and ma5 < ma20 * 0.98
                else "上行" if ma5 and ma20 and ma5 > ma20 * 1.02
                else "震荡"),
        "最新收盘": round(last, 2),
    }


def _build_user_prompt(*, stock_code: str, stock_name: str, current_price: float,
                       cost_price: float, shares: int, pnl_pct: float, sector: str,
                       kline_stats: dict, news: list[dict], sector_news: list[dict],
                       announcements: list[dict], health: dict | None) -> str:
    parts = [
        f"## 标的\n{stock_name}({stock_code}) · 行业: {sector or '未知'}",
        f"\n## 当前持仓\n成本 ¥{cost_price:.2f} × {shares} 股 = ¥{cost_price*shares:,.0f}\n"
        f"现价 ¥{current_price:.2f} | 浮动盈亏 {pnl_pct:+.2f}%",
    ]
    if kline_stats:
        parts.append(f"\n## K线摘要\n" + "\n".join(f"- {k}: {v}" for k, v in kline_stats.items()))
    if health:
        parts.append(
            f"\n## 基本面健康度\n等级: {health.get('level')} | 评分: {health.get('score', 0):+.2f}"
        )
    if announcements:
        parts.append(
            f"\n## 近期公告 (最权威, 优先看)\n"
            + "\n".join(
                f"- [{(a.get('date') or a.get('time') or '')[:10]}] {a.get('title', '')}"
                for a in announcements[:8]
            )
        )
    if news:
        parts.append(
            f"\n## 个股新闻 (最近 7 天)\n"
            + "\n".join(
                f"- [{(n.get('time') or '')[:10]}] {n.get('title', '')}"
                for n in news[:8]
            )
        )
    if sector_news:
        parts.append(
            f"\n## {sector or '板块'}行业新闻 (最近 7 天)\n"
            + "\n".join(f"- {n.get('title', '')}" for n in sector_news[:5])
        )
    parts.append(
        "\n## 任务\n基于以上，输出今日 signal + summary + points + risk。"
        "只汇总客观信息和风险，不给任何操作建议。严格 JSON。"
    )
    return "\n".join(parts)


def _strip_to_json(text: str) -> str:
    """Best-effort: pull the first {...} block out of an LLM response."""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    return m.group(0) if m else text


async def generate_briefing_for_stock(stock_code: str, stock_name: str,
                                      cost_price: float, shares: int,
                                      current_price: float) -> dict:
    """Build briefing for one stock. Returns dict with verdict + metadata."""
    from services.market_data import get_historical_data, get_stock_sector
    from services.news import get_stock_news, get_sector_news, get_stock_announcements
    from services.fundamental_score import fetch_health_snapshot
    from services.llm_client import call_claude

    # 先拿真实行业(修掉硬编码"有色金属"), 再据此拉对应行业新闻
    try:
        sector = await get_stock_sector(stock_code)
    except Exception:
        sector = ""

    # Gather inputs concurrently
    hist_task = get_historical_data(stock_code)
    news_task = get_stock_news(stock_code, limit=10)
    sector_task = get_sector_news(sector or "大盘", limit=5)
    health_task = fetch_health_snapshot(stock_code, stock_name)
    ann_task = get_stock_announcements(stock_code, limit=10)
    hist_df, news, sector_news, health, announcements = await asyncio.gather(
        hist_task, news_task, sector_task, health_task, ann_task,
        return_exceptions=True,
    )
    # Tolerate any single failure
    if isinstance(hist_df, Exception): hist_df = None
    if isinstance(announcements, Exception): announcements = []
    if isinstance(news, Exception): news = []
    if isinstance(sector_news, Exception): sector_news = []
    if isinstance(health, Exception): health = None

    pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
    kline_stats = _kline_summary(hist_df)

    user_prompt = _build_user_prompt(
        stock_code=stock_code, stock_name=stock_name, sector=sector,
        current_price=current_price, cost_price=cost_price, shares=shares,
        pnl_pct=pnl_pct, kline_stats=kline_stats,
        news=news or [], sector_news=sector_news or [],
        announcements=announcements or [], health=health,
    )

    try:
        raw = await asyncio.to_thread(
            call_claude, user_prompt, SYSTEM_PROMPT,
            "claude-sonnet-5", 1200,
        )
        parsed = json.loads(_strip_to_json(raw))
    except Exception as e:
        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "error": f"生成失败: {type(e).__name__}: {str(e)[:100]}",
            "signal": "中性",
            "summary": "LLM 调用失败",
            "points": [],
            "risk": "",
            "confidence": "low",
        }

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "current_price": current_price,
        "cost_price": cost_price,
        "pnl_pct": round(pnl_pct, 2),
        "sector": sector,
        "signal": parsed.get("signal", "中性"),
        "summary": parsed.get("summary", ""),
        "points": parsed.get("points", []),
        "risk": parsed.get("risk", ""),
        "confidence": parsed.get("confidence", "med"),
        "kline_stats": kline_stats,
        "health_level": (health or {}).get("level"),
    }


async def generate_all_briefings() -> list[dict]:
    """Generate briefings for every A-share holding. Saves each to DB."""
    from database import get_all_holdings, save_briefing
    from services.market_data import get_realtime_quotes, is_a_share

    holdings = [h for h in await get_all_holdings()
                if is_a_share(h["stock_code"]) and (h.get("shares") or 0) > 0]
    if not holdings:
        return []
    codes = [h["stock_code"] for h in holdings]
    quotes = await get_realtime_quotes(codes)

    today = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")
    results: list[dict] = []
    # Run sequentially to be polite to the akshare news endpoint
    for h in holdings:
        code = h["stock_code"]
        q = quotes.get(code) or {}
        cur = q.get("price") or 0
        if cur <= 0:
            cur = h.get("cost_price", 0)
        try:
            briefing = await generate_briefing_for_stock(
                code, h.get("stock_name", "") or q.get("stock_name", ""),
                cost_price=h["cost_price"], shares=h["shares"],
                current_price=cur,
            )
            briefing["briefing_date"] = today
            await save_briefing(code, today, json.dumps(briefing, ensure_ascii=False))
            results.append(briefing)
        except Exception as e:
            print(f"[briefing] {code} failed: {e}")
    return results
