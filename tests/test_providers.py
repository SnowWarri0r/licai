"""扩展数据源(provider)层: 默认关闭 / 动态加载 / 三种失败分得开 / 工具按能力挂载。

这一层存在的理由就是**默认什么都不发生**: 公开仓库不该在用户什么都没配的情况下带上一条
通往第三方私有接口的通道。所以第一组用例守的是"没配 = NullProvider = 零能力零请求";
第二组守配错了也只是退回默认、不把服务带崩; 第三组守三种失败在界面上不会被压成同一个空。
"""
import asyncio

import pytest

from services.providers import base, registry
from services.providers import gateway
from services.providers.base import (CAP_HOT_THEMES, CAP_STOCK_LHB, CapabilityUnavailable,
                                     CredentialField, MarketProvider, NullProvider,
                                     ProviderAuthError, ProviderError)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("MARKET_PROVIDER", raising=False)

    async def _no_cfg(_k):
        return None
    monkeypatch.setattr("database.get_config", _no_cfg)
    registry.reset_cache()
    yield
    registry.reset_cache()


# ── 默认关闭 ────────────────────────────────────────────

def test_nothing_configured_yields_null_provider():
    p = asyncio.run(registry.get_provider())
    assert isinstance(p, NullProvider)
    assert p.capabilities == frozenset()
    assert registry.load_error() == ""


def test_null_provider_refuses_every_capability():
    """每一项都得抛 CapabilityUnavailable —— 静默返回空会被上层当成"今天没数据"。"""
    p = NullProvider()
    calls = {"sentiment": (), "stock_lhb": ("600000",), "plate_bidding": (),
             "hot_themes": (), "inst_position": (), "limit_up_history": ("2026-09-04",),
             "limit_up_themes": ("2026-09-04",)}
    assert set(calls) == base.ALL_CAPABILITIES
    for name, args in calls.items():
        with pytest.raises(CapabilityUnavailable):
            asyncio.run(getattr(p, name)(*args))


# ── 动态加载 ────────────────────────────────────────────

class _Demo(MarketProvider):
    name = "demo"
    display_name = "示例源"
    capabilities = frozenset({CAP_HOT_THEMES})
    credential_fields = (CredentialField(key="k", label="Key"),)

    async def hot_themes(self):
        return {"有数据": True, "题材榜": []}


def test_env_spec_loads_the_class(monkeypatch):
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Demo")
    p = asyncio.run(registry.get_provider())
    assert p.name == "demo" and p.supports(CAP_HOT_THEMES)


def test_db_config_is_the_fallback_channel(monkeypatch):
    async def _cfg(k):
        return "tests.test_providers:_Demo" if k == "market_provider" else None
    monkeypatch.setattr("database.get_config", _cfg)
    assert asyncio.run(registry.get_provider()).name == "demo"


def test_env_wins_over_db(monkeypatch):
    async def _cfg(k):
        return "tests.test_providers:_Demo" if k == "market_provider" else None
    monkeypatch.setattr("database.get_config", _cfg)
    monkeypatch.setenv("MARKET_PROVIDER", "")          # 空 env 不算配置, 仍回落 DB
    assert asyncio.run(registry.get_provider()).name == "demo"
    monkeypatch.setenv("MARKET_PROVIDER", "nope:Nope")
    registry.reset_cache()
    assert isinstance(asyncio.run(registry.get_provider()), NullProvider)


@pytest.mark.parametrize("spec", ["没有冒号", "no.such.module:X", "tests.test_providers:_NotAProvider"])
def test_bad_spec_falls_back_instead_of_crashing(monkeypatch, spec):
    """配错的扩展源不该把整个服务带崩 —— 扩展数据源是可选项, 不是启动依赖。"""
    monkeypatch.setenv("MARKET_PROVIDER", spec)
    p = asyncio.run(registry.get_provider())
    assert isinstance(p, NullProvider)
    assert spec in registry.load_error()               # 但原因要留得住, 设置页要显示


class _NotAProvider:
    pass


def test_spec_change_rebuilds_the_cached_provider(monkeypatch):
    """设置页改了配置后不该还拿着上一个 provider —— 按 spec 缓存, spec 变了自动重建。"""
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Demo")
    assert asyncio.run(registry.get_provider()).name == "demo"
    monkeypatch.setenv("MARKET_PROVIDER", "")
    assert isinstance(asyncio.run(registry.get_provider()), NullProvider)


# ── 已装 provider 的发现 ────────────────────────────────

class _FakeDist:
    def __init__(self, name, version):
        self.name, self.version = name, version


class _FakeEP:
    def __init__(self, name, value, dist=None):
        self.name, self.value, self.dist = name, value, dist


def _stub_eps(monkeypatch, eps):
    monkeypatch.setattr(registry, "_entry_points", lambda: list(eps))


def test_installing_a_provider_does_not_enable_it(monkeypatch):
    """**这层的核心不变量**: 装上 ≠ 启用。

    加了入口点发现之后最容易破的就是这条 —— 一旦哪天图省事改成"只装了一个就自动用它",
    公开仓库就又变回"pip install 完就自带一条第三方直连通道"了。所以这里造一个**真能加载**
    的候选(不是坏 spec), 确认它被列了出来、却仍然没生效。
    """
    _stub_eps(monkeypatch, [_FakeEP("demo", "tests.test_providers:_Demo",
                                    _FakeDist("licai-provider-demo", "0.1"))])
    assert [d["name"] for d in registry.discover()] == ["demo"]      # 列得出来
    p = asyncio.run(registry.get_provider())
    assert isinstance(p, NullProvider) and p.capabilities == frozenset()   # 但没启用


def test_discover_lists_installed_entry_points(monkeypatch):
    _stub_eps(monkeypatch, [
        _FakeEP("zeta", "z.pkg:Provider", _FakeDist("licai-provider-zeta", "1.0")),
        _FakeEP("alpha", "a.pkg:Provider", _FakeDist("licai-provider-alpha", "0.2")),
    ])
    got = registry.discover()
    assert [d["name"] for d in got] == ["alpha", "zeta"]          # 按名字排稳, 别每次刷新换序
    assert got[0]["spec"] == "a.pkg:Provider" and got[0]["version"] == "0.2"


def test_discover_does_not_import_anything(monkeypatch):
    """列候选**只读元数据**。一 import 就等于"装上某个包 = 它的代码每次开设置页都跑一遍",
    "没配就零 import"这条约定当场破掉。所以指向一个 import 必炸的模块也照样列得出来。"""
    _stub_eps(monkeypatch, [_FakeEP("boom", "no.such.module.at.all:Provider")])
    assert registry.discover() == [{"name": "boom", "spec": "no.such.module.at.all:Provider",
                                    "dist": "", "version": ""}]


def test_discover_dedupes_and_survives_missing_dist(monkeypatch):
    """同一个 spec 被登记两次(装了两遍/名字不同)只算一个; dist 信息缺了也不能炸。"""
    _stub_eps(monkeypatch, [_FakeEP("a", "same:Provider"),
                            _FakeEP("b", "same:Provider"),
                            _FakeEP("c", "")])
    got = registry.discover()
    assert len(got) == 1 and got[0]["dist"] == "" and got[0]["version"] == ""


def test_entry_point_name_works_as_a_spec(monkeypatch):
    """设置页点一下候选存的就是这个名字 —— 不必让用户手敲导入路径。"""
    _stub_eps(monkeypatch, [_FakeEP("demo", "tests.test_providers:_Demo",
                                    _FakeDist("licai-provider-demo", "0.1"))])
    monkeypatch.setenv("MARKET_PROVIDER", "demo")
    assert asyncio.run(registry.get_provider()).name == "demo"


def test_unknown_bare_name_names_what_is_installed(monkeypatch):
    """打错名字时报错要说清"装了哪些可选" —— 只说"格式不对"没法自救。"""
    _stub_eps(monkeypatch, [_FakeEP("demo", "tests.test_providers:_Demo")])
    monkeypatch.setenv("MARKET_PROVIDER", "dmeo")
    assert isinstance(asyncio.run(registry.get_provider()), NullProvider)
    assert "demo" in registry.load_error()


def test_import_path_still_works_without_any_entry_point(monkeypatch):
    """入口点只是便利, 不是新的必经之路: 没登记过的包照样能用全路径挂上。"""
    _stub_eps(monkeypatch, [])
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Demo")
    assert asyncio.run(registry.get_provider()).name == "demo"


# ── 三种失败分得开 ──────────────────────────────────────

class _Failing(MarketProvider):
    name = "failing"
    display_name = "会失败的源"
    capabilities = frozenset({CAP_STOCK_LHB, CAP_HOT_THEMES})

    async def stock_lhb(self, code, day=None):
        raise ProviderAuthError("凭证已失效")

    async def hot_themes(self):
        raise ProviderError("对方超时")


def test_unsupported_capability_reads_as_unavailable(monkeypatch):
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Demo")
    r = asyncio.run(gateway.call("stock_lhb", "600000"))
    assert r["available"] is False and r.get("need_login") is None


def test_auth_failure_reads_as_need_login(monkeypatch):
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Failing")
    r = asyncio.run(gateway.call("stock_lhb", "600000"))
    assert r["need_login"] is True and "失效" in r["error"]
    assert "available" not in r            # 接了、只是凭证过期, 不能读成"没接入"


def test_fetch_failure_is_neither_unavailable_nor_need_login(monkeypatch):
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Failing")
    r = asyncio.run(gateway.call("hot_themes"))
    assert "error" in r and "available" not in r and "need_login" not in r


def test_provider_exception_does_not_escape_to_the_route(monkeypatch):
    """provider 是外部代码。它抛什么都不该把路由带成 500。"""
    class _Boom(MarketProvider):
        capabilities = frozenset({CAP_HOT_THEMES})

        async def hot_themes(self):
            raise KeyError("provider 内部写错了")
    monkeypatch.setattr(registry, "_cache", _Boom())
    monkeypatch.setattr(registry, "_cache_spec", "")
    r = asyncio.run(gateway.call("hot_themes"))
    assert "error" in r


def test_try_call_returns_none_for_every_failure(monkeypatch):
    """旁路调用(情绪面第二数据源那种)只认成功 —— 未接入/失效/超时一律 None, 整块省掉,
    不能把"去设置页重填"的提示混进主口径的数字里。"""
    assert asyncio.run(gateway.try_call("sentiment")) is None      # 没接入
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Failing")
    assert asyncio.run(gateway.try_call("stock_lhb", "600000")) is None   # 凭证失效
    assert asyncio.run(gateway.try_call("hot_themes")) is None            # 取数失败


def test_try_call_drops_empty_results(monkeypatch):
    """provider 返回空 dict = 那天没有, 也当没有 —— 别让前端渲染一张空卡片。"""
    class _Empty(MarketProvider):
        capabilities = frozenset({CAP_HOT_THEMES})

        async def hot_themes(self):
            return {}
    monkeypatch.setattr(registry, "_cache", _Empty())
    monkeypatch.setattr(registry, "_cache_spec", "")
    assert asyncio.run(gateway.try_call("hot_themes")) is None


# ── agent 工具按能力挂载 ────────────────────────────────

def test_provider_tools_are_hidden_without_a_provider():
    """没接入时这四个工具不该塞给模型 —— 否则它会去调、拿一串"未接入", 白耗一轮,
    还容易把"没接入"读成"这只票查不到"。"""
    import services.stock_agent as sa
    names = {t.get("name") for t in asyncio.run(sa._active_tools())}
    assert not (names & set(sa._PROVIDER_TOOLS))


def test_only_the_supported_provider_tools_are_exposed(monkeypatch):
    """provider 只实现一部分能力是常态 —— 挂上去的必须正好是它声明的那些。"""
    import services.stock_agent as sa
    monkeypatch.setenv("MARKET_PROVIDER", "tests.test_providers:_Demo")
    names = {t.get("name") for t in asyncio.run(sa._active_tools())}
    assert "get_hot_themes" in names                      # _Demo 只声明了 hot_themes
    assert "get_deep_lhb" not in names and "get_inst_position" not in names


def test_every_provider_tool_maps_to_a_real_capability():
    """工具表与能力表对不上时, 工具会永远挂不上去(或永远挂着) —— 两边都得能对上。"""
    import services.stock_agent as sa
    assert set(sa._PROVIDER_TOOLS.values()) <= base.ALL_CAPABILITIES
    tool_names = {t.get("name") for t in sa._TOOLS}
    assert set(sa._PROVIDER_TOOLS) <= tool_names
    assert set(sa._PROVIDER_TOOLS) <= set(sa._EXECUTORS)


# ── HTTP 接缝 ───────────────────────────────────────────

def _client():
    from fastapi.testclient import TestClient
    from run import app
    return TestClient(app)


def test_market_endpoints_say_unavailable_without_a_provider():
    """没接入时这些路由要**成功返回** available=false, 而不是 4xx/5xx ——
    前端据此静默不渲染那几块; 报错的话会在界面上留下一串红字。"""
    c = _client()
    for url in ("/api/market/provider/plate-bidding",
                "/api/market/provider/hot-themes",
                "/api/market/provider/inst-position",
                "/api/market/provider/stock-lhb/600000"):
        r = c.get(url)
        assert r.status_code == 200, url
        assert r.json()["available"] is False, url


def test_settings_reports_not_installed(monkeypatch):
    async def _cfg(_k):
        return None
    monkeypatch.setattr("database.get_config", _cfg)
    # settings_routes 是 `from database import get_config` 绑死的名字, 只打 database 上那份
    # 不够 —— 路由里那个引用还指着原函数, 会真去读 app_config 表。
    monkeypatch.setattr("api.settings_routes.get_config", _cfg)
    registry.reset_cache()
    j = _client().get("/api/settings/provider").json()
    assert j["spec"] == "" and j["capabilities"] == [] and j["fields"] == []
    assert isinstance(j["discovered"], list)      # 没装任何 provider 时是空表, 不是缺字段


def test_settings_offers_installed_providers_as_candidates(monkeypatch):
    """装了包但还没选 —— 设置页要能把它列出来当候选, 省得用户手敲导入路径。"""
    async def _cfg(_k):
        return None
    monkeypatch.setattr("database.get_config", _cfg)
    monkeypatch.setattr("api.settings_routes.get_config", _cfg)
    monkeypatch.setattr(registry, "_entry_points",
                        lambda: [_FakeEP("demo", "tests.test_providers:_Demo",
                                         _FakeDist("licai-provider-demo", "0.1"))])
    registry.reset_cache()
    j = _client().get("/api/settings/provider").json()
    assert j["spec"] == ""                                    # 列出来 ≠ 自动启用
    assert j["discovered"] == [{"name": "demo", "spec": "tests.test_providers:_Demo",
                                "dist": "licai-provider-demo", "version": "0.1"}]


def test_settings_never_echoes_secret_fields(monkeypatch):
    """凭证字段只标形状不回显值 —— 接口里任何地方都不该出现刚存进去的那串。"""
    stored = {}

    async def _set_cfg(k, v):
        stored[k] = v

    async def _get_cfg(k):
        return "tests.test_providers:_Secretive" if k == "market_provider" else stored.get(k)
    monkeypatch.setattr("database.get_config", _get_cfg)
    monkeypatch.setattr("database.set_config", _set_cfg)
    monkeypatch.setattr("api.settings_routes.get_config", _get_cfg)
    monkeypatch.setattr("api.settings_routes.set_config", _set_cfg)
    registry.reset_cache()
    r = _client().post("/api/settings/provider", json={"values": {"token": "s3cret-value"}})
    assert r.status_code == 200
    assert "s3cret-value" not in r.text
    assert stored.get("tok") == "s3cret-value"          # 确实存下去了, 只是不回显
    fields = {f["key"]: f for f in r.json()["fields"]}
    assert fields["token"]["secret"] is True


class _Secretive(MarketProvider):
    name = "secretive"
    display_name = "要凭证的源"
    capabilities = frozenset({CAP_HOT_THEMES})
    credential_fields = (CredentialField(key="token", label="Token", secret=True),)

    async def status(self):
        return {"provider": self.name, "display_name": self.display_name,
                "configured": True, "valid": True, "note": "ok"}

    async def save_credentials(self, values):
        from database import set_config
        await set_config("tok", values.get("token", ""))
        return {"ok": True, "valid": True, "note": "已保存"}

    async def hot_themes(self):
        return {"有数据": False}
