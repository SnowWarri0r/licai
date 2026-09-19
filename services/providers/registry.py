"""provider 注册与加载: 按配置动态挑一个实现, 什么都不配就是 NullProvider。

**默认关闭是这层的核心约定**: 仓库里即使带了某个适配器, 不显式配置也绝不会被 import、
更不会发出任何请求。所以"装了这个项目"与"接了某个第三方数据源"是两件事, 后者永远是用户
自己按一次开关的结果。

配置双通道(与 tdx/zsxq 同模式, env 优先 → 回落 DB config):
    env:  MARKET_PROVIDER=<spec>
    DB :  get_config('market_provider')

spec 两种写法, 都指向**使用者自己装的**包:
    · 完整导入路径  "mypkg.myprovider:Provider"
    · 入口点名字    "kpl"   —— 装好的包在 licai.providers 这个 entry point 组里登记的名字

**本仓库不自带任何 provider 实现**, 一个都没有。要接哪个源, 自己写一个包(或装一个别人写的)
实现 base.MarketProvider 的协议, 再把它填进配置。这条边界是有意的: 哪些数据源可以接、
凭证从哪来、与对方服务条款的关系如何 —— 那是使用者自己的判断与自己的账号, 不该由本仓库替
所有人预置一条直连通道。

discover() 把已装包登记的入口点列出来给设置页当候选, 省得手敲那串导入路径。它**只读包的
元数据、不 import 任何 provider 模块** —— 否则"没配就零 import"这条约定当场就破了: 光是
装上某个包, 它的代码就会在每次开设置页时被执行一遍。所以候选里只有名字和 spec, 没有
display_name 那种要实例化才拿得到的东西。

加载失败(拼错/模块不存在/不是 MarketProvider 子类)一律**退回 NullProvider 并记下原因**,
不让一个配错的扩展源把整个服务带崩 —— 扩展数据源是可选项, 不是启动依赖。
"""
from __future__ import annotations

import importlib
import os

from services.providers.base import MarketProvider, NullProvider

_ENV_KEY = "MARKET_PROVIDER"
_CONFIG_KEY = "market_provider"
_EP_GROUP = "licai.providers"       # 已装 provider 包在这个组里登记自己

_cache: MarketProvider | None = None
_cache_spec: str | None = None
_load_error: str = ""


async def _configured_spec() -> str:
    spec = (os.environ.get(_ENV_KEY) or "").strip()
    if spec:
        return spec
    try:
        from database import get_config
        return ((await get_config(_CONFIG_KEY)) or "").strip()
    except Exception:
        return ""


def _entry_points() -> list:
    """licai.providers 组里已登记的入口点。只读元数据, 不 import 任何 provider 模块。"""
    try:
        from importlib.metadata import entry_points
    except ImportError:
        return []
    try:
        return list(entry_points(group=_EP_GROUP))          # 3.10+
    except TypeError:
        return list((entry_points() or {}).get(_EP_GROUP, []))   # 3.9 的旧字典接口


def discover() -> list[dict]:
    """已装的 provider 候选: [{name, spec, dist, version}]。设置页拿它列选项。

    这里出现的每一个, 都是使用者自己 pip install 过的包 —— 本仓库不参与挑选, 也没有
    任何预置名单。列出来只是省得手敲导入路径。
    """
    out, seen = [], set()
    for ep in _entry_points():
        spec = getattr(ep, "value", "") or ""
        if not spec or spec in seen:
            continue
        seen.add(spec)
        dist = getattr(ep, "dist", None)
        out.append({"name": getattr(ep, "name", "") or spec,
                    "spec": spec,
                    "dist": getattr(dist, "name", "") or "",
                    "version": getattr(dist, "version", "") or ""})
    return sorted(out, key=lambda d: d["name"])


def _resolve(spec: str) -> str:
    """入口点名字 → 导入路径。已经是 '模块:类名' 的原样返回。"""
    if ":" in spec:
        return spec
    for ep in _entry_points():
        if getattr(ep, "name", "") == spec:
            return getattr(ep, "value", "") or spec
    known = ", ".join(d["name"] for d in discover()) or "无"
    raise ValueError(f"provider spec 要写成 '模块:类名', 或已装包登记的入口点名字"
                     f"(当前已装: {known}); 收到 {spec!r}")


def _instantiate(spec: str) -> MarketProvider:
    path = _resolve(spec)
    mod_name, _, attr = path.partition(":")
    obj = getattr(importlib.import_module(mod_name), attr)
    inst = obj() if isinstance(obj, type) else obj
    if not isinstance(inst, MarketProvider):
        raise TypeError(f"{path} 不是 MarketProvider 的实现")
    return inst


async def get_provider() -> MarketProvider:
    """当前生效的 provider。没配 / 配错 → NullProvider。按 spec 缓存, 配置变了自动重建。"""
    global _cache, _cache_spec, _load_error
    spec = await _configured_spec()
    if _cache is not None and _cache_spec == spec:
        return _cache
    if not spec:
        _cache, _cache_spec, _load_error = NullProvider(), spec, ""
        return _cache
    try:
        _cache, _load_error = _instantiate(spec), ""
    except Exception as e:
        _cache, _load_error = NullProvider(), f"加载 provider {spec!r} 失败: {e}"
    _cache_spec = spec
    return _cache


def load_error() -> str:
    """上一次加载失败的原因(空=没失败)。设置页用它把"配错了"和"没配"区分开。"""
    return _load_error


def reset_cache() -> None:
    """配置改动后调一次, 下次 get_provider 重新加载。"""
    global _cache, _cache_spec, _load_error
    _cache, _cache_spec, _load_error = None, None, ""
