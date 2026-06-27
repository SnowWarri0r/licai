"""理财助手 (licai) — 个人理财本地驾驶舱.

入口: python run.py → http://localhost:8888
"""
from __future__ import annotations
import asyncio
import os
import warnings
from contextlib import asynccontextmanager

# macOS 系统 Python 3.9 用 LibreSSL 2.8.3, urllib3 v2 会嗷嗷叫。无害, 屏蔽
warnings.filterwarnings("ignore", message=".*OpenSSL.*", module="urllib3")

# 清掉所有代理 env, 避免 akshare / requests 走系统代理被 EM/Sina 拒
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
    os.environ.pop(_k, None)

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import config
from database import init_db, get_config, set_config
from services import llm_client
from api.portfolio_routes import router as portfolio_router
from api.market_routes import router as market_router
from api.settings_routes import router as settings_router
from api.unwind_routes import router as unwind_router
from api.assets_routes import router as assets_router
from api.briefing_routes import router as briefing_router
from api.sector_routes import router as sector_router
from api.cashflow_routes import router as cashflow_router
from api.export_routes import router as export_router
from api.dca_routes import router as dca_router
from api.news_routes import router as news_router, news_prewarm_loop
from api.ask_routes import router as ask_router
from services.sector_matrix import sector_matrix_prewarm_loop
from api.broker_routes import router as broker_router
from api.ws import router as ws_router, price_monitor_loop, premarket_push_loop, backup_loop, briefing_loop, dca_loop
from services import feishu_notify


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Restore saved LLM config (多厂商: base_url / key / header / prefix / proxy / model_map)
    llm_base_url = await get_config("llm_base_url")
    llm_api_key = await get_config("llm_api_key")
    llm_api_key_header = await get_config("llm_api_key_header")
    llm_api_key_prefix = await get_config("llm_api_key_prefix")
    llm_proxy = await get_config("llm_proxy")
    llm_model_map_raw = await get_config("llm_model_map")
    llm_model_map = None
    if llm_model_map_raw:
        try:
            import json as _json
            llm_model_map = _json.loads(llm_model_map_raw)
        except Exception:
            pass
    # 兼容旧键: 老版本只存了 llm_proxy_url
    if not llm_proxy:
        llm_proxy = await get_config("llm_proxy_url") or ""
    llm_client.configure_llm(
        base_url=llm_base_url or "",
        api_key=llm_api_key or "",
        api_key_header=llm_api_key_header or "",
        api_key_prefix=llm_api_key_prefix or "",
        proxy=llm_proxy or "",
        model_map=llm_model_map,
    )

    # 本地代理(OKX/外发统一): env CRYPTO_PROXY > DB network_proxy > 自动探测。
    # 代理软件端口漂移时自动探测命中, 不用手改 env(OKX 同步反复失效的根因)。
    from services import proxy_config
    stored_proxy = (await get_config("network_proxy")) or ""
    pr = await asyncio.to_thread(proxy_config.resolve_and_apply, stored_proxy)
    if pr.get("proxy"):
        tag = "自动探测" if pr.get("source") == "auto" else "配置"
        print(f"本地代理({tag}{'·可用' if pr.get('ok') else '·不通'}): {pr['proxy']}")
        if pr.get("source") == "auto" and pr.get("ok"):
            await set_config("network_proxy", pr["proxy"])   # 探测到的回存, 下次直接用

    # 可插拔数据源: 通达信 TDX REST 服务 (env TDX_BASE_URL > DB config > config.py)
    from services import tdx_client
    tdx_url = os.environ.get("TDX_BASE_URL") or (await get_config("tdx_base_url")) or getattr(config, "tdx_base_url", "") or ""
    tdx_client.configure(tdx_url)
    if tdx_url:
        print(f"TDX 数据源已启用: {tdx_url}")

    # Restore saved feishu webhook config + 静音状态
    url = await get_config("feishu_webhook_url")
    if url:
        feishu_notify.configure(url)
    muted_val = await get_config("feishu_muted")
    if muted_val == "1":
        feishu_notify.set_muted(True)
    if url:
        print(f"飞书推送 {'静音中' if feishu_notify.is_muted() else '已启用'}")
    task1 = asyncio.create_task(price_monitor_loop())
    task2 = asyncio.create_task(premarket_push_loop())
    task3 = asyncio.create_task(backup_loop())
    task4 = asyncio.create_task(briefing_loop())
    task5 = asyncio.create_task(dca_loop())
    task6 = asyncio.create_task(news_prewarm_loop())
    task7 = asyncio.create_task(sector_matrix_prewarm_loop())
    print(f"理财助手已启动: http://localhost:{config.port}")
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    task5.cancel()
    task6.cancel()
    task7.cancel()


app = FastAPI(title="理财助手", lifespan=lifespan)

# Mount static files (Vite build output)
app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")

# Include routers
app.include_router(portfolio_router)
app.include_router(market_router)
app.include_router(settings_router)
app.include_router(unwind_router)
app.include_router(assets_router)
app.include_router(briefing_router)
app.include_router(sector_router)
app.include_router(cashflow_router)
app.include_router(export_router)
app.include_router(dca_router)
app.include_router(news_router)
app.include_router(ask_router)
app.include_router(broker_router)
app.include_router(ws_router)


@app.get("/")
async def index():
    # Never cache the entry HTML so SW updates / new bundle hashes are picked up
    return FileResponse(
        "static/index.html",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/sw.js")
async def sw_js():
    return FileResponse(
        "static/sw.js",
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# PWA files
@app.get("/manifest.json")
async def manifest():
    return FileResponse("static/manifest.json", media_type="application/manifest+json")

@app.get("/icon-192.svg")
async def icon192():
    return FileResponse("static/icon-192.svg", media_type="image/svg+xml")

@app.get("/icon-512.svg")
async def icon512():
    return FileResponse("static/icon-512.svg", media_type="image/svg+xml")


if __name__ == "__main__":
    uvicorn.run(
        "run:app",
        host=config.host,
        port=config.port,
        reload=False,
    )
