"""启动时把已存的配置(数据库 app_config / 环境变量 / config.py)应用到各客户端。

服务启动(run.py lifespan)和评测(evals/run.py)都调它。评测曾经漏了这一步: TDX 没配上,
所有问分时的题都只能答"分时拿不到", 测的其实是一条线上并不存在的降级路径(2026-10-14)。
"""
from __future__ import annotations
import asyncio
import os

from config import config
from database import get_config, set_config
from services import llm_client


async def apply_saved_config() -> None:
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

    # 可插拔扩展数据源: provider 插件 (env MARKET_PROVIDER > DB config > config.py)
    # registry 自己读 env 与 DB, 所以这里只在两者都空时把 config.py 那份塞进 env 兜底 ——
    # 直接无条件写 env 会让 config.py 反过来盖掉设置页存的值, 优先级就倒过来了。
    from services.providers import get_provider, load_error
    if not os.environ.get("MARKET_PROVIDER") and not (await get_config("market_provider")):
        _mp = getattr(config, "market_provider", "") or ""
        if _mp:
            os.environ["MARKET_PROVIDER"] = _mp
    _prov = await get_provider()
    if _prov.capabilities:
        print(f"扩展数据源已启用: {_prov.display_name} ({'/'.join(sorted(_prov.capabilities))})")
    elif load_error():
        print(f"扩展数据源未启用 —— {load_error()}")

    # 知识星球(可选, 只读观点面): 选中的星球存在 app_config, token 由 CLI 放系统 Keychain
    from services import zsxq_client
    import json as _json
    try:
        _zg = _json.loads((await get_config("zsxq_groups")) or "[]")
    except (ValueError, TypeError):
        _zg = []
    # URL 带 api_key: 只打印脱敏后的 host+path, 不打印整串
    zsxq_client.configure(os.environ.get("ZSXQ_MCP_URL") or (await get_config("zsxq_mcp_url")) or "",
                          _zg if isinstance(_zg, list) else [])
    if zsxq_client.is_enabled():
        print(f"知识星球已接入: {zsxq_client.endpoint_label()} · {len(zsxq_client.configured_groups())} 个星球(只读)")
