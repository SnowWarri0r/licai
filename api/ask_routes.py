"""问股票为什么涨/跌 — agent 问答端点。"""
from __future__ import annotations
import json as _json
import os
import re
import base64
import uuid
from typing import List, Optional
from fastapi import APIRouter
from fastapi.responses import StreamingResponse, FileResponse, Response
from pydantic import BaseModel

from config import config
from services.stock_agent import ask_stock, ask_stock_stream
from database import (create_ask_session, add_ask_message, list_ask_sessions,
                      get_ask_session, delete_ask_session)

# 会话图片落盘目录(跟 DB 同级), 存压缩光栅图文件, DB 只留 URL — 不把 base64 塞进库
_MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(config.db_path)) or ".", "ask_media")
# 只允许光栅图; 明确排除 SVG(可含脚本 → 存储型 XSS)及其它可执行/未知类型
_ALLOWED_IMG = {"jpeg": ("jpg", "image/jpeg"), "jpg": ("jpg", "image/jpeg"),
                "png": ("png", "image/png"), "gif": ("gif", "image/gif"),
                "webp": ("webp", "image/webp")}


def _persist_images(meta: Optional[dict]) -> Optional[dict]:
    """把 meta.images 里的 data URL 落盘成文件, 替换为 /api/ask/image/<name> URL。已是 URL 的原样保留。
    只接受光栅图(jpg/png/gif/webp), SVG 等非白名单类型一律丢弃, 防存储型 XSS。"""
    if not isinstance(meta, dict):
        return meta
    imgs = meta.get("images")
    if not isinstance(imgs, list) or not imgs:
        return meta
    os.makedirs(_MEDIA_DIR, exist_ok=True)
    out = []
    for s in imgs:
        if not isinstance(s, str) or not s:
            continue
        if s.startswith("/api/ask/image/"):
            out.append(s); continue
        if s.startswith("data:"):
            try:
                head, b64 = s.split(",", 1)
                m = re.match(r"data:image/([a-zA-Z0-9.+-]+)", head)
                subtype = (m.group(1).lower() if m else "")
                if subtype not in _ALLOWED_IMG:    # svg+xml / 未知类型 → 丢弃
                    continue
                ext = _ALLOWED_IMG[subtype][0]
                name = f"{uuid.uuid4().hex}.{ext}"
                with open(os.path.join(_MEDIA_DIR, name), "wb") as f:
                    f.write(base64.b64decode(b64))
                out.append(f"/api/ask/image/{name}")
            except Exception:
                continue
    meta = dict(meta)
    meta["images"] = out
    return meta

router = APIRouter(prefix="/api/ask", tags=["ask"])


class Turn(BaseModel):
    role: str        # user | assistant
    content: str


class AskIn(BaseModel):
    question: str
    history: Optional[List[Turn]] = None
    images: Optional[List[str]] = None    # data URL 或裸 base64, 最多 4 张, 走多模态


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
    return await ask_stock(data.question, hist, data.images)


@router.post("/stock/stream")
async def ask_stream(data: AskIn):
    """SSE 流式版(POST, 带多轮历史): 工具步骤实时推送, 末尾推完整答案。前端做步骤展示 + 打字机。"""
    hist = [t.model_dump() for t in (data.history or [])]

    async def gen():
        try:
            async for ev in ask_stock_stream(data.question, hist, data.images):
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
    meta_obj = _persist_images(m.meta)   # base64 落盘 → URL, DB 不存 base64
    meta = _json.dumps(meta_obj, ensure_ascii=False) if meta_obj else ""
    await add_ask_message(sid, m.role, m.content, meta)
    return {"session_id": sid}


_EXT_CT = {"jpg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}


@router.get("/image/{name}")
async def get_ask_image(name: str):
    """读会话图片(落盘的压缩光栅图)。basename 防目录穿越; 按扩展名强制安全 Content-Type + nosniff, 不让浏览器嗅探执行。"""
    safe = os.path.basename(name)
    ext = safe.rsplit(".", 1)[-1].lower() if "." in safe else ""
    ct = _EXT_CT.get(ext)
    path = os.path.join(_MEDIA_DIR, safe)
    if not ct or not os.path.isfile(path):    # 只服务白名单光栅图
        return Response(status_code=404)
    return FileResponse(path, media_type=ct, headers={"X-Content-Type-Options": "nosniff"})


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
