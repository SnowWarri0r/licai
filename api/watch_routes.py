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
