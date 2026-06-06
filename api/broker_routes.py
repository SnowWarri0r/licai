# api/broker_routes.py
from __future__ import annotations
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import (
    list_brokers, add_broker, update_broker, delete_broker, get_default_broker,
)

router = APIRouter(prefix="/api/brokers", tags=["brokers"])


class BrokerCreate(BaseModel):
    name: str
    stock_rate: float
    stock_min: float
    etf_rate: float
    etf_min: float


class BrokerUpdate(BaseModel):
    name: Optional[str] = None
    stock_rate: Optional[float] = None
    stock_min: Optional[float] = None
    etf_rate: Optional[float] = None
    etf_min: Optional[float] = None
    is_default: Optional[bool] = None


@router.get("")
async def get_brokers():
    return await list_brokers()


@router.post("")
async def create_broker(data: BrokerCreate):
    bid = await add_broker(data.name, data.stock_rate, data.stock_min, data.etf_rate, data.etf_min)
    return {"id": bid, "message": "created"}


@router.put("/{broker_id}")
async def modify_broker(broker_id: int, data: BrokerUpdate):
    payload = data.model_dump(exclude_unset=True)
    if "is_default" in payload:
        if payload["is_default"]:
            payload["is_default"] = 1
        else:
            payload.pop("is_default")   # 不允许直接取消默认(会变成0个默认); 改默认请对另一家设 true
    if not payload:
        raise HTTPException(400, "无可改字段")
    await update_broker(broker_id, **payload)
    return {"message": "updated"}


@router.delete("/{broker_id}")
async def remove_broker(broker_id: int):
    rows = await list_brokers()
    target = next((b for b in rows if b["id"] == broker_id), None)
    if not target:
        raise HTTPException(404, "券商不存在")
    if target["is_default"]:
        raise HTTPException(400, "默认券商不可删, 请先把别的设为默认")
    await delete_broker(broker_id)
    return {"message": "deleted"}
