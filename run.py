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

# 极简 .env 加载(不引第三方依赖): 只认 KEY=VALUE 行, 已在 os.environ 里的不覆盖。
# 用于扩展数据源凭证等 secrets —— .env 被 .gitignore 挡着, 不进库。
_envf = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(_envf):
    with open(_envf, encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if not _line or _line.startswith("#") or "=" not in _line:
                continue
            _key, _val = _line.split("=", 1)
            _key, _val = _key.strip(), _val.strip().strip('"').strip("'")
            if _key and _key not in os.environ:
                os.environ[_key] = _val

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
from api.plan_routes import router as plan_router
from api.assets_routes import router as assets_router
from api.briefing_routes import router as briefing_router
from api.sector_routes import router as sector_router
from api.cashflow_routes import router as cashflow_router
from api.export_routes import router as export_router
from api.dca_routes import router as dca_router
from api.news_routes import router as news_router, news_prewarm_loop
from api.ask_routes import router as ask_router
from services.sector_matrix import sector_matrix_prewarm_loop
from services.coiled_scanner import coiled_prewarm_loop
from services.portfolio_curve import curve_prewarm_loop
from api.broker_routes import router as broker_router
from api.ws import (router as ws_router, price_monitor_loop, backup_loop, briefing_loop,
                    dca_loop, eod_summary_loop, rule_mine_loop)
from services import feishu_notify


async def _symbol_dict_prewarm():
    """搜股底表(代码↔名称全表 + 拼音索引)后台预热。

    库为空时现拉要 20s+, 若等用户第一次搜索才建, 那一次就卡几十秒。启动后台建好,
    搜索永远走缓存。库里已有数据时这步只是读库 + 建拼音索引(<1s)。"""
    try:
        await asyncio.sleep(2)                     # 让端口先起来, 不拖慢启动
        from services.stock_agent import _code_name_maps, _pinyin_rows
        await _code_name_maps()
        rows = await _pinyin_rows()
        print(f"[startup] 搜股底表就绪: {len(rows)} 条")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        print(f"[startup] 搜股底表预热失败(搜索首次会现拉): {e}")


async def plan_monitor_loop():
    """交易计划盯价: A 股交易时段每 15 秒查一次, 触发/失效就推飞书 + 推页面。

    不挂在 price_monitor_loop 上: 那条只在有浏览器连着时才跑, 盯计划要的是人不在屏幕前也能收到。
    """
    from api.ws import broadcast
    from services import trade_plan as tp
    from services.market_data import market_open

    async def notify(text, ev):
        try:
            await feishu_notify.send_text(text)
        except Exception as e:
            print(f"[plan] 飞书推送失败: {e}")
        await broadcast(ev)

    last_expire = ""
    while True:
        try:
            if last_expire != tp.today():
                await tp.expire_old()          # 每天一次: 过了生效日还没触发的计划标过期
                last_expire = tp.today()
            if market_open("A"):
                await tp.monitor_once(notify)
                await asyncio.sleep(15)
            else:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[plan] 盯价出错: {e}")
            await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 上一个进程留下的"在跑"的 agent 轮次: 进程一没那些 task 就不存在了, 留着 running
    # 会让界面一直转。标成 interrupted, 过程留着(不自动重跑 —— 重跑要再烧一遍 token)。
    try:
        from database import interrupt_running_ask_runs
        _n = await interrupt_running_ask_runs()
        if _n:
            print(f"[startup] 上次没跑完的 agent 轮次 {_n} 条, 已标记为中断")
    except Exception as e:
        print(f"[startup] 标记中断轮次失败: {e}")
    # 模型、代理、各数据源的已存配置。评测脚本也调这同一个函数, 两边才是同一套取数链路
    from services.startup_config import apply_saved_config
    await apply_saved_config()

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
    task2 = asyncio.create_task(backup_loop())
    task3 = asyncio.create_task(briefing_loop())
    task4 = asyncio.create_task(dca_loop())
    task5 = asyncio.create_task(news_prewarm_loop())
    task6 = asyncio.create_task(sector_matrix_prewarm_loop())
    task7 = asyncio.create_task(coiled_prewarm_loop())
    task8 = asyncio.create_task(curve_prewarm_loop())
    task9 = asyncio.create_task(eod_summary_loop())
    task10 = asyncio.create_task(_symbol_dict_prewarm())
    task11 = asyncio.create_task(rule_mine_loop())
    task12 = asyncio.create_task(plan_monitor_loop())
    print(f"理财助手已启动: http://localhost:{config.port}")
    yield
    task1.cancel()
    task2.cancel()
    task3.cancel()
    task4.cancel()
    task5.cancel()
    task7.cancel()
    task6.cancel()
    task8.cancel()
    task9.cancel()
    task10.cancel()
    task11.cancel()
    task12.cancel()


app = FastAPI(title="理财助手", lifespan=lifespan)

from services.origin_guard import host_ok as _host_ok, LOCAL_HOSTS as _LOCAL_HOSTS


@app.middleware("http")
async def same_origin_only(request, call_next):
    """1) 所有请求的 Host 必须是本机名或 IP(防 DNS 重绑定);
    2) 改数据的请求只收本应用页面发来的: 浏览器里别的网站能悄悄朝 localhost 发无请求体的
    POST(不触发 CORS 预检), 比如批准一条待确认的画像。浏览器跨站请求一定带 Origin 或
    Sec-Fetch-Site, 据此判断; 两者都没有的(curl、本机脚本)照常放行。"""
    from urllib.parse import urlsplit
    from fastapi.responses import JSONResponse
    if not _host_ok(urlsplit("//" + request.headers.get("host", "")).hostname or ""):
        return JSONResponse({"detail": "Host 不在允许范围"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            o = urlsplit(origin)
            ok = o.netloc == request.headers.get("host", "") or (o.hostname or "") in _LOCAL_HOSTS
        else:
            ok = request.headers.get("sec-fetch-site", "same-origin") in ("same-origin", "none")
        if not ok:
            return JSONResponse({"detail": "跨站请求已拒绝"}, status_code=403)
    return await call_next(request)

# Mount static files (Vite build output)
app.mount("/assets", StaticFiles(directory="static/assets"), name="assets")

# Include routers
app.include_router(portfolio_router)
app.include_router(market_router)
app.include_router(settings_router)
app.include_router(plan_router)
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


@app.get("/api/app-version")
async def app_version():
    """当前 index.html 引用的 bundle 文件名。

    给已经开着的标签页判断"我这份 JS 是不是过期了"用: static/assets 从磁盘直接伺服,
    重新构建立刻生效, 但开着的页面不会自己换 bundle —— 旧 JS 对着新 API 跑出来的毛病
    最难查(界面上没有任何地方显示版本)。读不到就回空串, 让前端别乱报。
    """
    import re
    from pathlib import Path
    try:
        html = Path("static/index.html").read_text(encoding="utf-8")
        m = re.search(r'assets/(index-[A-Za-z0-9_\-]+\.js)', html)
        return {"bundle": m.group(1) if m else ""}
    except Exception:
        return {"bundle": ""}


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
