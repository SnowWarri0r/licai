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
from services import ask_runs
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


class RunIn(BaseModel):
    question: str                          # 给人看的原话(落库/历史标题用这个)
    agent_question: Optional[str] = None   # 真正喂 agent 的(抽屉会带上"名称(代码): "前缀)
    history: Optional[List[Turn]] = None
    images: Optional[List[str]] = None
    session_id: Optional[int] = None        # 空=新建会话
    title: Optional[str] = None
    scope: str = "market"                   # market | stock:<code>, 前端按它认领自己那条


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
    """SSE 流式版(POST, 带多轮历史): 工具步骤实时推送, 末尾推完整答案。

    注: 这一条的执行权在调用方 —— 断开连接这一轮就没了, 也不落库。界面已经改走
    /runs(服务端后台跑 + 自动落库); 这里留给脚本/一次性调用。"""
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


# --- 后台运行的一轮(run): 界面走这条 ---

@router.post("/runs")
async def start_run(data: RunIn):
    """开跑一轮并立刻返回 {run_id, session_id, cursor}。

    跟 /stock/stream 的区别就是"谁拿着执行权": 这条一开跑就归服务端, 前端切页/刷新/关页
    都不影响它跑完并落库; 问题在开跑那一刻就已经写进会话, 所以历史里马上能看到。"""
    hist = [t.model_dump() for t in (data.history or [])]
    imgs = data.images or []
    # 图片: 喂 agent 用原始 data URL, 落库用落盘后的 URL(DB 不存 base64)
    user_meta = _persist_images({"images": imgs}) if imgs else None
    run = await ask_runs.start(
        data.question, agent_question=data.agent_question, history=hist, images=imgs,
        session_id=data.session_id, title=data.title, scope=data.scope, user_meta=user_meta)
    return {"run_id": run.id, "session_id": run.session_id, "cursor": 0,
            "images": (user_meta or {}).get("images", [])}


@router.get("/runs")
async def list_runs(scope: Optional[str] = None):
    """内存里还认得的 run(在跑的 + 刚跑完的), 前端用来重挂: 切回来接着看那半截。"""
    return {"runs": await ask_runs.live(scope)}


@router.get("/runs/{run_id}/events")
async def run_events(run_id: str, cursor: int = 0):
    """从 cursor 起续拉事件流。每条带 cursor, 断开后照它接着拉; 断开不会影响 run 本身。"""
    async def gen():
        try:
            async for ev in ask_runs.follow(run_id, cursor):
                yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'type': 'error', 'error': str(e)}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                                      "Connection": "keep-alive"})


@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str):
    """明确停掉(点"新对话"这类)。跟"前端走开"区分: 走开不取消。"""
    return {"cancelled": ask_runs.cancel(run_id)}


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
