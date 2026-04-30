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


SYSTEM_PROMPT = """你是一个 A 股持仓诊断助手。每天开盘前，根据用户某只持仓的新闻 + 近期 K 线 + 当前分批加仓档位，给出当日操作建议。

输出**严格 JSON**（不要有多余文本，不要 markdown 包裹）。结构如下：
{
  "verdict": "lock_all | hold | raise | lower | add_now",
  "summary": "一句话不超过 30 字",
  "reasoning": "2-3 句解释，说人话不要客套",
  "key_news": ["最关键 1-2 条头条原文"],
  "tranche_action": "锁档 / 持有观望 / 上调档位 / 下调档位 / 立即加仓",
  "confidence": "high | med | low"
}

verdict 含义:
- lock_all: 板块/个股利空明显，所有 pending 档位先冻结，等止跌
- hold: 没明显信号，按原档位等触发
- raise: 信号偏暖，可以把档位上移（更早触发）
- lower: 跌势但没崩盘，把档位下移（更深位置接）
- add_now: 强信号支撑当前价就值得加仓

判断准则:
- 利空新闻（监管/业绩雷/板块崩盘）→ lock_all
- 价格已跌破近 20 日最低 + 无利好 → lock_all
- 价格震荡无方向 + 新闻中性 → hold
- 板块回暖 + 个股跟涨 → raise 或 add_now
- 持续阴跌但基本面未恶化 → lower（档位下挪等更深的低点）
"""


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
                       cost_price: float, shares: int, pnl_pct: float,
                       kline_stats: dict, news: list[dict], sector_news: list[dict],
                       tranches: list[dict], health: dict | None) -> str:
    parts = [
        f"## 标的\n{stock_name}({stock_code})",
        f"\n## 当前持仓\n成本 ¥{cost_price:.2f} × {shares} 股 = ¥{cost_price*shares:,.0f}\n"
        f"现价 ¥{current_price:.2f} | 浮亏 {pnl_pct:+.2f}%",
    ]
    if kline_stats:
        parts.append(f"\n## K线摘要\n" + "\n".join(f"- {k}: {v}" for k, v in kline_stats.items()))
    if health:
        parts.append(
            f"\n## 基本面健康度\n等级: {health.get('level')} | 评分: {health.get('score', 0):+.2f}"
        )
    if tranches:
        pending = [t for t in tranches if t.get("status") == "pending"]
        if pending:
            lines = ["\n## 当前 pending 档位"]
            for t in pending:
                lines.append(
                    f"- 档{t.get('idx')}: 触发价 ¥{t.get('trigger_price'):.2f} → +{t.get('shares')}股 "
                    f"(需健康度 {t.get('requires_health', 'any')})"
                )
            parts.append("\n".join(lines))
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
            f"\n## 板块新闻 (最近 7 天)\n"
            + "\n".join(f"- {n.get('title', '')}" for n in sector_news[:5])
        )
    parts.append(
        "\n## 任务\n基于以上，给出今日 verdict + tranche_action。严格 JSON。"
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
    from services.market_data import get_historical_data
    from services.news import get_stock_news, get_sector_news
    from services.fundamental_score import fetch_health_snapshot
    from database import get_tranches
    from services.llm_client import call_claude

    # Gather inputs concurrently
    hist_task = get_historical_data(stock_code)
    news_task = get_stock_news(stock_code, limit=10)
    sector_task = get_sector_news("有色金属", limit=5)
    health_task = fetch_health_snapshot(stock_code, stock_name)
    tranches_task = get_tranches(stock_code)
    hist_df, news, sector_news, health, tranches = await asyncio.gather(
        hist_task, news_task, sector_task, health_task, tranches_task,
        return_exceptions=True,
    )
    # Tolerate any single failure
    if isinstance(hist_df, Exception): hist_df = None
    if isinstance(news, Exception): news = []
    if isinstance(sector_news, Exception): sector_news = []
    if isinstance(health, Exception): health = None
    if isinstance(tranches, Exception): tranches = []

    pnl_pct = (current_price - cost_price) / cost_price * 100 if cost_price > 0 else 0
    kline_stats = _kline_summary(hist_df)

    user_prompt = _build_user_prompt(
        stock_code=stock_code, stock_name=stock_name,
        current_price=current_price, cost_price=cost_price, shares=shares,
        pnl_pct=pnl_pct, kline_stats=kline_stats,
        news=news or [], sector_news=sector_news or [],
        tranches=tranches or [], health=health,
    )

    try:
        raw = await asyncio.to_thread(
            call_claude, user_prompt, SYSTEM_PROMPT,
            "claude-haiku-4-5-20251001", 600,
        )
        parsed = json.loads(_strip_to_json(raw))
    except Exception as e:
        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "error": f"生成失败: {type(e).__name__}: {str(e)[:100]}",
            "verdict": "hold",
            "summary": "LLM 调用失败,默认观望",
            "reasoning": "",
            "key_news": [],
            "tranche_action": "持有观望",
            "confidence": "low",
        }

    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "current_price": current_price,
        "cost_price": cost_price,
        "pnl_pct": round(pnl_pct, 2),
        "verdict": parsed.get("verdict", "hold"),
        "summary": parsed.get("summary", ""),
        "reasoning": parsed.get("reasoning", ""),
        "key_news": parsed.get("key_news", []),
        "tranche_action": parsed.get("tranche_action", ""),
        "confidence": parsed.get("confidence", "med"),
        "kline_stats": kline_stats,
        "health_level": (health or {}).get("level"),
    }


async def generate_all_briefings() -> list[dict]:
    """Generate briefings for every A-share holding. Saves each to DB."""
    from database import get_all_holdings, save_briefing
    from services.market_data import get_realtime_quotes, is_a_share

    holdings = [h for h in await get_all_holdings() if is_a_share(h["stock_code"])]
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
