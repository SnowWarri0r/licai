"""Settings REST endpoints for notification config and custom alerts."""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from database import get_config, set_config, get_custom_alerts, add_custom_alert, delete_custom_alert
from services import feishu_notify

router = APIRouter(prefix="/api/settings", tags=["settings"])


class FeishuConfig(BaseModel):
    webhook_url: str


class CustomAlertCreate(BaseModel):
    stock_code: str
    alert_type: str  # price_above, price_below, stop_loss
    price: float
    message: Optional[str] = ""


@router.get("/feishu")
async def get_feishu_config():
    url = await get_config("feishu_webhook_url") or ""
    return {
        "webhook_url": url,
        "enabled": feishu_notify.is_enabled(),
        "muted": feishu_notify.is_muted(),
    }


@router.post("/feishu")
async def save_feishu_config(data: FeishuConfig):
    await set_config("feishu_webhook_url", data.webhook_url)
    feishu_notify.configure(data.webhook_url)
    return {"message": "保存成功", "enabled": feishu_notify.is_enabled()}


class FeishuMute(BaseModel):
    muted: bool


@router.post("/feishu/mute")
async def set_feishu_mute(data: FeishuMute):
    """切换飞书推送静音 (用于前端"通知开/关"按钮联动后端)."""
    feishu_notify.set_muted(data.muted)
    await set_config("feishu_muted", "1" if data.muted else "0")
    return {"muted": data.muted, "enabled": feishu_notify.is_enabled()}


@router.post("/feishu/test")
async def test_feishu():
    if not feishu_notify.is_enabled():
        return {"success": False, "message": "请先配置飞书 Webhook URL"}
    ok = await feishu_notify.send_test()
    return {"success": ok, "message": "发送成功" if ok else "发送失败，请检查 Webhook URL"}


# --- Custom Alerts ---

@router.get("/alerts")
async def list_alerts(stock_code: str = None):
    return await get_custom_alerts(stock_code, enabled_only=False)


@router.post("/alerts")
async def create_alert(data: CustomAlertCreate):
    await add_custom_alert(data.stock_code, data.alert_type, data.price, data.message or "")
    return {"message": "创建成功"}


@router.delete("/alerts/{alert_id}")
async def remove_alert(alert_id: int):
    await delete_custom_alert(alert_id)
    return {"message": "删除成功"}


# --- Risk Config ---

class RiskConfig(BaseModel):
    max_daily_loss: Optional[float] = None


@router.get("/risk")
async def get_risk_config():
    val = await get_config("max_daily_loss")
    return {"max_daily_loss": float(val) if val else 500}


@router.post("/risk")
async def save_risk_config(data: RiskConfig):
    if data.max_daily_loss is not None:
        await set_config("max_daily_loss", str(data.max_daily_loss))
    return {"message": "保存成功"}
