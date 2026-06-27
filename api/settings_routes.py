"""Settings REST endpoints for notification config and custom alerts."""
from __future__ import annotations
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional

from database import get_config, set_config, get_custom_alerts, add_custom_alert, delete_custom_alert
from services import feishu_notify, llm_client, tdx_client, proxy_config

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _assert_local_url(v: str) -> str:
    """TDX 服务只该在本机/私网。限制 base_url 的 host 为 loopback 或 RFC1918 私网,
    拒绝公网与 link-local(169.254, 含云元数据 169.254.169.254) —— 防 SSRF。"""
    if not v:
        return v
    if not v.startswith(("http://", "https://")):
        raise ValueError("base_url 必须以 http:// 或 https:// 开头")
    from urllib.parse import urlparse
    import ipaddress
    host = (urlparse(v).hostname or "").strip()
    if not host:
        raise ValueError("base_url 缺少主机名")
    if host in ("localhost", "localhost.localdomain"):
        return v.rstrip("/")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        raise ValueError("TDX base_url 只允许 localhost 或本机/私网 IP(防 SSRF), 不接受公网域名")
    if ip.is_link_local or ip.is_reserved or ip.is_multicast:
        raise ValueError("base_url 不允许 link-local/保留地址(如 169.254.169.254)")
    if not (ip.is_loopback or ip.is_private):
        raise ValueError("TDX base_url 只允许本机/私网地址(防 SSRF), 不接受公网 IP")
    return v.rstrip("/")


class TDXConfig(BaseModel):
    base_url: str = ""

    @field_validator("base_url")
    @classmethod
    def _v(cls, v: str) -> str:
        return _assert_local_url(v)


@router.get("/tdx")
async def get_tdx_config():
    """TDX 可插拔数据源配置 + 当前是否启用。"""
    return {"base_url": tdx_client._BASE_URL, "enabled": tdx_client.is_enabled()}


@router.post("/tdx")
async def set_tdx_config(data: TDXConfig):
    await set_config("tdx_base_url", data.base_url)
    tdx_client.configure(data.base_url)
    return {"message": "saved", "enabled": tdx_client.is_enabled()}


@router.post("/tdx/test")
async def test_tdx_config(data: TDXConfig):
    """连通性自检: 不改保存值, 用传入(或当前)地址试拉一只票。"""
    return await tdx_client.test_connection(data.base_url or tdx_client._BASE_URL)


# ── 本地代理 (OKX / 外发统一; 东财/新浪直连不受影响) ──────────────
class ProxyConfig(BaseModel):
    proxy: str = ""

    @field_validator("proxy")
    @classmethod
    def _v(cls, v: str) -> str:
        v = (v or "").strip()
        if v and not v.startswith(("http://", "https://", "socks5://", "socks5h://")):
            raise ValueError("代理地址须以 http(s):// 或 socks5:// 开头")
        return v


@router.get("/proxy")
async def get_proxy_config():
    """当前生效代理 + DB 存储值。"""
    return {"proxy": proxy_config.get_proxy(), "db_proxy": (await get_config("network_proxy")) or ""}


@router.post("/proxy")
async def set_proxy_config(data: ProxyConfig):
    """保存代理到 DB 并即时应用(通知 OKX/外发 session 更新)。"""
    import asyncio
    await set_config("network_proxy", data.proxy)
    proxy_config.configure(data.proxy)
    ok = await asyncio.to_thread(proxy_config._probe, data.proxy) if data.proxy else False
    return {"message": "saved", "proxy": proxy_config.get_proxy(), "ok": ok}


@router.post("/proxy/detect")
async def detect_proxy_config():
    """自动探测本机可用代理(扫在听端口 + 常见端口, 挑能够到 OKX 的); 命中即应用并存库。"""
    import asyncio
    found = await asyncio.to_thread(proxy_config.auto_detect)
    if found:
        await set_config("network_proxy", found)
        proxy_config.configure(found)
        return {"ok": True, "proxy": found}
    return {"ok": False, "proxy": "", "error": "未探测到可用代理(确认本地代理已开启)"}


@router.post("/proxy/test")
async def test_proxy_config(data: ProxyConfig):
    """连通性自检: 用传入(或当前)代理探一次 OKX, 不改存储值。"""
    import asyncio
    url = data.proxy or proxy_config.get_proxy()
    ok = await asyncio.to_thread(proxy_config._probe, url)
    return {"ok": ok, "proxy": url, "error": "" if ok else "代理连不上或够不到外部接口"}


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


# --- LLM Config ---

_llm_test_last_call: float = 0.0
_LLM_TEST_COOLDOWN_S = 5  # minimum seconds between test calls


class LLMConfig(BaseModel):
    base_url: str = ""
    api_key: str = ""
    api_key_header: str = "x-api-key"
    api_key_prefix: str = ""
    proxy: str = ""
    model_map: dict[str, str] = {}
    update_api_key: bool = True  # True = apply api_key field; False = keep existing key

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://")):
            raise ValueError("base_url 必须以 http:// 或 https:// 开头")
        return v

    @field_validator("proxy")
    @classmethod
    def validate_proxy(cls, v: str) -> str:
        if v and not v.startswith(("http://", "https://", "socks5://")):
            raise ValueError("proxy 必须以 http://、https:// 或 socks5:// 开头")
        return v


@router.get("/llm")
async def get_llm_config_api():
    """返回当前 LLM 配置 (脱敏)."""
    config = llm_client.get_llm_config()
    # overlay DB-stored values for fields that env var may override
    db_base_url = await get_config("llm_base_url")
    db_api_key_header = await get_config("llm_api_key_header")
    db_api_key_prefix = await get_config("llm_api_key_prefix")
    db_proxy = await get_config("llm_proxy")
    db_model_map_raw = await get_config("llm_model_map")
    db_model_map = {}
    if db_model_map_raw:
        try:
            import json
            db_model_map = json.loads(db_model_map_raw)
        except Exception:
            pass
    return {
        "base_url": config["base_url"],
        "has_api_key": config["has_api_key"],
        "api_key_header": config["api_key_header"],
        "api_key_prefix": config["api_key_prefix"],
        "proxy": config["proxy"],
        "model_map": config["model_map"],
        "using_oauth_fallback": config["using_oauth_fallback"],
        "db_base_url": db_base_url or "",
        "db_api_key_header": db_api_key_header or "x-api-key",
        "db_api_key_prefix": db_api_key_prefix or "",
        "db_proxy": db_proxy or "",
        "db_model_map": db_model_map,
    }


@router.post("/llm")
async def save_llm_config_api(data: LLMConfig):
    """保存 LLM 配置到 DB 并应用."""
    import json
    await set_config("llm_base_url", data.base_url)
    if data.update_api_key and data.api_key:
        await set_config("llm_api_key", data.api_key)
    await set_config("llm_api_key_header", data.api_key_header)
    await set_config("llm_api_key_prefix", data.api_key_prefix)
    await set_config("llm_proxy", data.proxy)
    if data.model_map:
        await set_config("llm_model_map", json.dumps(data.model_map, ensure_ascii=False))
    else:
        await set_config("llm_model_map", "")

    llm_client.configure_llm(
        base_url=data.base_url,
        api_key=data.api_key if data.update_api_key else "",
        api_key_header=data.api_key_header,
        api_key_prefix=data.api_key_prefix,
        proxy=data.proxy,
        model_map=data.model_map or None,
    )
    return {"message": "保存成功"}


@router.post("/llm/test")
async def test_llm_connection():
    """发送一条测试请求, 返回连接结果."""
    global _llm_test_last_call
    now = time.time()
    if now - _llm_test_last_call < _LLM_TEST_COOLDOWN_S:
        remaining = round(_LLM_TEST_COOLDOWN_S - (now - _llm_test_last_call), 1)
        raise HTTPException(
            status_code=429,
            detail=f"请 {remaining}s 后再试 (每次测试间隔 ≥ {_LLM_TEST_COOLDOWN_S}s)",
        )
    _llm_test_last_call = now

    import asyncio
    result = await asyncio.to_thread(llm_client.test_connection)
    return result
