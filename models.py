"""Pydantic models for API request/response."""
from pydantic import BaseModel
from typing import Optional


class HoldingCreate(BaseModel):
    stock_code: str
    stock_name: str = ""
    shares: int
    cost_price: float


class HoldingUpdate(BaseModel):
    stock_name: Optional[str] = None
    shares: Optional[int] = None
    cost_price: Optional[float] = None


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


# --- Unwind models ---

class TrancheItem(BaseModel):
    id: Optional[int] = None
    idx: int
    trigger_price: float
    shares: int
    requires_health: str = "any"
    status: str = "pending"
    executed_price: Optional[float] = None
    source: Optional[str] = None


class UnwindPlanResponse(BaseModel):
    stock_code: str
    stock_name: str
    cost_price: float
    current_price: float
    shares: int
    holding_days: int
    nominal_loss_pct: float
    real_cost: float
    real_loss_pct: float
    opportunity_cost_accumulated: float
    daily_opportunity_cost: float
    price_progress: float
    cost_progress: float
    total_budget: float
    used_budget: float
    tranches: list[TrancheItem] = []
    fundamental: dict = {}


class UnwindPlanSave(BaseModel):
    total_budget: float
    tranches: Optional[list[TrancheItem]] = None


class TrancheExecute(BaseModel):
    executed_price: float
    executed_shares: Optional[int] = None
