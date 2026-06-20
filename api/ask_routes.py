"""问股票为什么涨/跌 — agent 问答端点。"""
from __future__ import annotations
import json as _json
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.stock_agent import ask_stock, ask_stock_stream

router = APIRouter(prefix="/api/ask", tags=["ask"])


class AskIn(BaseModel):
    question: str


@router.post("/stock")
async def ask(data: AskIn):
    """自由问个股(为什么涨/跌、最近消息、跟持仓关系)。挂工具的 agent 自取数据后客观解读, 不给买卖建议。"""
    return await ask_stock(data.question)


@router.get("/stock/stream")
async def ask_stream(question: str):
    """SSE 流式版: 工具步骤实时推送, 末尾推完整答案。前端做步骤展示 + 打字机。"""
    async def gen():
        try:
            async for ev in ask_stock_stream(question):
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})
