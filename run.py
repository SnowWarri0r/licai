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
from database import init_db, get_config
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
from api.ws import router as ws_router, price_monitor_loop, premarket_push_loop, backup_loop, briefing_loop
from services import feishu_notify


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # Restore LLM proxy config
    proxy_url = await get_config("llm_proxy_url")
    if proxy_url:
        llm_client.configure_proxy(proxy_url)

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
    print(f"理财助手已启动: http://localhost:{config.port}")
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()


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
