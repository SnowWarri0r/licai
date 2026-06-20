"""问股票为什么涨/跌 — agent 问答端点。"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel

from services.stock_agent import ask_stock

router = APIRouter(prefix="/api/ask", tags=["ask"])


class AskIn(BaseModel):
    question: str


@router.post("/stock")
async def ask(data: AskIn):
    """自由问个股(为什么涨/跌、最近消息、跟持仓关系)。挂工具的 agent 自取数据后客观解读, 不给买卖建议。"""
    return await ask_stock(data.question)
