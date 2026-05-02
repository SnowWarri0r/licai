"""Market data REST endpoints."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter

from services.market_data import get_realtime_quotes, get_historical_data, get_intraday_data, get_market_indices

router = APIRouter(prefix="/api/market", tags=["market"])


@router.get("/trading-day")
async def trading_day_status():
    """Whether today (CST) is an A-share trading day. Excludes weekends + 法定假日.
    Also returns the next trading day for UX hints.
    """
    try:
        import chinese_calendar as cc
    except ImportError:
        # Fallback: weekend-only detection
        today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        is_weekend = today.weekday() >= 5
        return {
            "date": str(today),
            "is_trading_day": not is_weekend,
            "is_weekend": is_weekend,
            "is_holiday": False,
            "next_trading_day": None,
            "fallback": True,
        }

    cst_today = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    is_workday = cc.is_workday(cst_today)
    is_holiday = cc.is_holiday(cst_today)
    is_weekend = cst_today.weekday() >= 5

    # Find next trading day (cap search at 14 days for safety)
    next_td = None
    if not is_workday:
        d = cst_today + timedelta(days=1)
        for _ in range(14):
            if cc.is_workday(d):
                next_td = str(d)
                break
            d += timedelta(days=1)

    HOLIDAY_CN = {
        "New Year's Day": "元旦",
        "Spring Festival": "春节",
        "Tomb-sweeping Day": "清明",
        "Labour Day": "劳动节",
        "Dragon Boat Festival": "端午",
        "National Day": "国庆",
        "Mid-autumn Festival": "中秋",
        "Anti-Fascist 70th Day": "抗战胜利纪念",
    }
    holiday_name = ""
    if is_holiday and not is_weekend:
        try:
            _, name = cc.get_holiday_detail(cst_today)
            holiday_name = HOLIDAY_CN.get(name, name or "")
        except Exception:
            pass

    return {
        "date": str(cst_today),
        "is_trading_day": is_workday,
        "is_weekend": is_weekend,
        "is_holiday": is_holiday and not is_weekend,
        "holiday_name": holiday_name,
        "next_trading_day": next_td,
    }


@router.get("/quote/{stock_code}")
async def get_quote(stock_code: str):
    quotes = await get_realtime_quotes([stock_code])
    if stock_code not in quotes:
        return {"error": f"无法获取 {stock_code} 的行情数据"}
    return quotes[stock_code]


@router.get("/history/{stock_code}")
async def get_history(stock_code: str, days: int = 60):
    df = await get_historical_data(stock_code, days)
    if df.empty:
        return []
    # Return simplified format for chart consumption
    result = []
    for _, r in df.iterrows():
        result.append({
            "time": str(r.get("日期", ""))[:10],
            "open": float(r.get("开盘", 0)),
            "high": float(r.get("最高", 0)),
            "low": float(r.get("最低", 0)),
            "close": float(r.get("收盘", 0)),
            "volume": float(r.get("成交量", 0)),
        })
    return result


@router.get("/intraday/{stock_code}")
async def get_intraday(stock_code: str):
    df = await get_intraday_data(stock_code)
    if df.empty:
        return []
    records = df.to_dict("records")
    for r in records:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = str(v)
    return records


@router.get("/indices")
async def get_indices():
    return await get_market_indices()
