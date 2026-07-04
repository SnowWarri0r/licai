"""问股票为什么涨/跌 — agent 问答端点。"""
from __future__ import annotations
import json as _json
from typing import List, Optional
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from services.stock_agent import ask_stock, ask_stock_stream
from database import (create_ask_session, add_ask_message, list_ask_sessions,
                      get_ask_session, delete_ask_session)

router = APIRouter(prefix="/api/ask", tags=["ask"])


class Turn(BaseModel):
    role: str        # user | assistant
    content: str


class AskIn(BaseModel):
    question: str
    history: Optional[List[Turn]] = None


class SessionMsg(BaseModel):
    session_id: Optional[int] = None     # 空=新建会话
    role: str                            # user | assistant
    content: str
    meta: Optional[dict] = None          # {tools_used, sources}
    title: Optional[str] = None          # 新建会话时用(取首个问题)


@router.post("/stock")
async def ask(data: AskIn):
    """自由问个股(为什么涨/跌、最近消息、跟持仓关系)。挂工具的 agent 自取数据后客观解读, 不给买卖建议。"""
    hist = [t.model_dump() for t in (data.history or [])]
    return await ask_stock(data.question, hist)


@router.post("/stock/stream")
async def ask_stream(data: AskIn):
    """SSE 流式版(POST, 带多轮历史): 工具步骤实时推送, 末尾推完整答案。前端做步骤展示 + 打字机。"""
    hist = [t.model_dump() for t in (data.history or [])]

    async def gen():
        try:
            async for ev in ask_stock_stream(data.question, hist):
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


# --- 会话历史 ---

@router.post("/messages")
async def save_message(m: SessionMsg):
    """保存一条对话消息。session_id 为空则新建会话(用 title/首问做标题), 返回 session_id。"""
    sid = m.session_id
    if not sid:
        sid = await create_ask_session((m.title or m.content)[:80])
    meta = _json.dumps(m.meta, ensure_ascii=False) if m.meta else ""
    await add_ask_message(sid, m.role, m.content, meta)
    return {"session_id": sid}


@router.get("/sessions")
async def get_sessions():
    """会话列表(最近在前, 标题+时间+消息数)。"""
    return {"sessions": await list_ask_sessions()}


@router.get("/sessions/{session_id}")
async def get_session(session_id: int):
    """单个会话全部消息(meta 解析回 dict)。"""
    s = await get_ask_session(session_id)
    if not s:
        return {"error": "会话不存在"}
    for msg in s.get("messages", []):
        try:
            msg["meta"] = _json.loads(msg["meta"]) if msg.get("meta") else None
        except (ValueError, TypeError):
            msg["meta"] = None
    return s


@router.delete("/sessions/{session_id}")
async def remove_session(session_id: int):
    await delete_ask_session(session_id)
    return {"ok": True}
