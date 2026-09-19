"""扩展数据源(provider)协议。

**为什么有这一层**: 本项目自带的行情源(东财/新浪/腾讯/Yahoo)都是公开 web 接口 —— 不要
凭证、不伪装客户端、不绕付费墙。但有些口径(带身份标签的深度龙虎榜、竞价异动、机构季度增仓)
只有某些商业终端才有, 接它们意味着要带用户自己的账号凭证去打对方的私有接口 —— 那是**用户
自己**与对方的关系, 不该由本仓库替所有人内置一条直连通道。

所以这里只定义**协议**: 能力清单 + 方法签名 + 凭证字段声明。具体实现由 provider 提供,
通过 registry 按配置动态加载; 什么都不配 = NullProvider, 所有能力返回"未接入", 上层照常
走自带的公开源。仓库自带的适配器一律**默认关闭**, 必须显式配置才会被加载(见 registry)。

对上层的约定(照着写就不会破坏"没接 provider 也能跑"这条底线):
  · 能力不支持 → 抛 CapabilityUnavailable, 上层翻译成 {available: false} 而不是报错页
  · 凭证失效   → 抛 ProviderAuthError,   上层翻译成 {need_login: true} 引导去设置页重填
  · 取数失败   → 抛 ProviderError 或返回空, 上层按"这次没取到"处理, 不连累主流程
这三者必须分得开: 把"没接入"和"登录过期"混成同一个空状态, 用户会对着空界面猜是哪种。
"""
from __future__ import annotations

from dataclasses import dataclass


# --- 能力标识 -------------------------------------------------------------
# provider 声明自己实现了哪几项; 路由/agent/回填链路据此决定挂不挂对应功能。
CAP_SENTIMENT = "sentiment"                  # 情绪面聚合(跳水榜/量能风向标/官方定性这类)
CAP_STOCK_LHB = "stock_lhb"                  # 个股深度龙虎榜(席位带身份标签)
CAP_PLATE_BIDDING = "plate_bidding"          # 板块集合竞价异动
CAP_HOT_THEMES = "hot_themes"                # 月度热门题材榜
CAP_INST_POSITION = "inst_position"          # 机构增仓/减仓榜(季报口径)
CAP_LIMIT_UP_HISTORY = "limit_up_history"    # 涨停档案历史回填(东财只有滚动 3 周)
CAP_LIMIT_UP_THEMES = "limit_up_themes"      # 涨停个股的题材标签(东财只给行业)

ALL_CAPABILITIES = frozenset({
    CAP_SENTIMENT, CAP_STOCK_LHB, CAP_PLATE_BIDDING, CAP_HOT_THEMES,
    CAP_INST_POSITION, CAP_LIMIT_UP_HISTORY, CAP_LIMIT_UP_THEMES,
})


# --- 异常 -----------------------------------------------------------------
class ProviderError(RuntimeError):
    """provider 取数失败(网络/解析/对方报错)。不是"没接入", 也不是"登录过期"。"""


class ProviderAuthError(ProviderError):
    """provider 凭证未配置或已失效。上层据此提示用户去设置页重填, 不静默成空数据。"""


class CapabilityUnavailable(ProviderError):
    """当前 provider 不提供这项能力(含没接 provider 的默认情况)。"""

    def __init__(self, capability: str, provider: str = ""):
        self.capability = capability
        self.provider = provider
        who = provider or "未接入 provider"
        super().__init__(f"{who} 不提供「{capability}」能力")


# --- 凭证字段声明 ----------------------------------------------------------
@dataclass(frozen=True)
class CredentialField:
    """provider 需要用户填的一个字段。设置页拿这张表通用渲染, 不为任何 provider 写死表单。

    secret=True 的字段永不回显明文(只回显是否已填); echo=True 的可以回显(如账号 ID)。
    """
    key: str
    label: str
    secret: bool = True
    echo: bool = False
    placeholder: str = ""


class MarketProvider:
    """provider 基类。子类声明 capabilities 并覆盖对应方法; 没声明的一律抛 CapabilityUnavailable。

    方法返回的 dict 形状沿用各功能既有的中文键结构(路由与前端直接透传), 换 provider 只要
    产出同形状即可, 不必改上层。
    """

    name: str = "null"
    display_name: str = "未接入"
    capabilities: frozenset[str] = frozenset()
    credential_fields: tuple[CredentialField, ...] = ()

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def _nope(self, capability: str):
        raise CapabilityUnavailable(capability, self.display_name)

    async def status(self) -> dict:
        """接入状态。至少给 {provider, display_name, configured, valid, note}。"""
        return {"provider": self.name, "display_name": self.display_name,
                "configured": False, "valid": False, "note": "未接入扩展数据源"}

    async def save_credentials(self, values: dict) -> dict:
        """存凭证并探活。默认: 不需要凭证。"""
        raise CapabilityUnavailable("credentials", self.display_name)

    async def clear_credentials(self) -> None:
        raise CapabilityUnavailable("credentials", self.display_name)

    # --- 能力方法(默认全部不支持) ---
    async def sentiment(self, day: str | None = None) -> dict:
        self._nope(CAP_SENTIMENT)

    async def stock_lhb(self, code: str, day: str | None = None) -> dict:
        self._nope(CAP_STOCK_LHB)

    async def plate_bidding(self) -> dict:
        self._nope(CAP_PLATE_BIDDING)

    async def hot_themes(self) -> dict:
        self._nope(CAP_HOT_THEMES)

    async def inst_position(self, date: str | None = None, top: int = 15) -> dict:
        self._nope(CAP_INST_POSITION)

    async def limit_up_history(self, day: str) -> list[dict]:
        """某日逐只涨停档案。行结构同 database.save_limit_up_pool 的入参。"""
        self._nope(CAP_LIMIT_UP_HISTORY)

    async def limit_up_themes(self, day: str) -> dict:
        """{6位代码: 题材标签}。只补 theme 一列, 不碰其它字段。"""
        self._nope(CAP_LIMIT_UP_THEMES)


class NullProvider(MarketProvider):
    """没配 provider 时的占位。所有能力不支持 —— 上层照常走自带的公开源。"""
