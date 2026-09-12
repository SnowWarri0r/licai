"""Pydantic models for API request/response."""
from pydantic import BaseModel
from typing import Optional


class HoldingCreate(BaseModel):
    stock_code: str
    stock_name: str = ""
    shares: int
    cost_price: float
    trade_date: Optional[str] = None  # 首次买入日期 YYYY-MM-DD; 不填默认 today
    trade_time: Optional[str] = None  # 成交时刻 HH:MM (可选, 供分时图打点)
    broker: Optional[str] = None


class HoldingUpdate(BaseModel):
    stock_name: Optional[str] = None
    shares: Optional[int] = None
    cost_price: Optional[float] = None
    broker: Optional[str] = None


class HoldingResponse(BaseModel):
    stock_code: str
    stock_name: str
    market: str = "A"
    currency: str = "CNY"
    shares: int
    cost_price: float
    current_price: Optional[float] = None
    fx_rate: float = 1.0
    fx_time: Optional[str] = None
    fx_source: Optional[str] = None
    price_change_pct: Optional[float] = None
    unrealized_pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    original_cost_value: Optional[float] = None
    original_market_value: Optional[float] = None
    cost_value: Optional[float] = None
    market_value: Optional[float] = None
    sector: Optional[str] = None  # 顶级行业, e.g. "有色金属" / "汽车" — 用于前端分组
    broker: Optional[str] = None
    div_per_share: Optional[float] = None  # 持有期累计每股现金分红(已摊进 cost_price, 对齐券商摊薄成本)


class QuoteData(BaseModel):
    stock_code: str
    stock_name: str
    price: float
    open: float
    high: float
    low: float
    prev_close: float
    volume: float
    amount: float
    change_pct: float
    amplitude: float
    turnover_rate: float


class AlertEvent(BaseModel):
    stock_code: str
    stock_name: str
    alert_type: str
    price: float
    message: str
    zone: Optional[list[float]] = None
