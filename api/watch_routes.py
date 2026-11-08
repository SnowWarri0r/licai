"""盯盘员的发现: 页面顶部列表读取 / 收起。检测循环在 services/watcher.py 的 watcher_loop。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services import watcher

router = APIRouter(prefix="/api/watch", tags=["watch"])


@router.get("/findings")
async def findings(date: str = ""):
    """date 空 = 今天(北京时间)。只返回没收起的。"""
    return await watcher.list_findings(date or None)


@router.post("/findings/{finding_id}/dismiss")
async def dismiss(finding_id: int):
    if not await watcher.dismiss(finding_id):
        raise HTTPException(404, "没有这条发现")
    return {"ok": True}


@router.post("/findings/{finding_id}/dig")
async def dig(finding_id: int):
    """让盯盘员派子 agent 查这条发现的原因(跳过自动判定与每日上限)。{status} 或 404/400。"""
    from services import watch_dig
    r = await watch_dig.dig_now(finding_id)
    if r.get("error") == "没有这条发现":
        raise HTTPException(404, r["error"])
    if r.get("error"):
        raise HTTPException(400, r["error"])
    return r
