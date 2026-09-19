"""路由/agent 调 provider 的统一入口: 把三种失败翻成三种上层能分辨的结果。

为什么非要一个网关而不是各处自己 try: 因为"没接入 / 凭证失效 / 这次没取到"必须在界面上
长得不一样 —— 没接入该引导去装 provider, 凭证失效该引导去重填, 取数失败该说"稍后再试"。
散在各路由里写 try 的结果就是三者迟早被压成同一个空状态, 用户对着空界面猜是哪一种。

返回约定(前端据此分支):
    {"available": False, ...}          没接 provider / 这个 provider 不提供这项能力
    {"need_login": True, "error": ...} 凭证未配或已失效
    {"error": ...}                     这次没取到(网络/对方报错)
    其它                                provider 的正常返回, 原样透传
"""
from __future__ import annotations

from services.providers.base import CapabilityUnavailable, ProviderAuthError, ProviderError
from services.providers.registry import get_provider, load_error


def unavailable(capability: str, note: str = "") -> dict:
    err = load_error()
    return {"available": False, "capability": capability,
            "note": note or (err or "未接入扩展数据源。该口径由可选的 provider 提供, "
                             "配置 MARKET_PROVIDER 后可用; 不配也不影响其它功能。")}


async def call(capability: str, *args, **kwargs) -> dict:
    """调当前 provider 的某项能力。capability 同时是 base 上的方法名。"""
    p = await get_provider()
    if not p.supports(capability):
        return unavailable(capability)
    try:
        return await getattr(p, capability)(*args, **kwargs)
    except CapabilityUnavailable:
        return unavailable(capability)
    except ProviderAuthError as e:
        return {"error": str(e), "need_login": True}
    except ProviderError as e:
        return {"error": str(e)}
    except Exception as e:                    # provider 是外部代码, 不让它把路由带崩
        return {"error": f"扩展数据源取数失败: {e}"}


async def try_call(capability: str, *args, **kwargs):
    """只在**成功拿到数据**时返回结果, 其余一律 None。

    用于"锦上添花"的旁路(如情绪面的第二数据源): 那种地方拿不到就整块省掉, 不该把
    未接入/失效的提示混进主口径的返回里。
    """
    p = await get_provider()
    if not p.supports(capability):
        return None
    try:
        r = await getattr(p, capability)(*args, **kwargs)
    except Exception:
        return None
    return r or None


async def capabilities() -> frozenset:
    return (await get_provider()).capabilities
