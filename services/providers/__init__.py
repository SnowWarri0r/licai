"""扩展数据源(provider)层。协议见 base, 加载见 registry, 用法见 docs/providers.md。"""
from services.providers.base import (  # noqa: F401
    ALL_CAPABILITIES, CAP_HOT_THEMES, CAP_INST_POSITION, CAP_LIMIT_UP_HISTORY,
    CAP_LIMIT_UP_THEMES, CAP_PLATE_BIDDING, CAP_SENTIMENT, CAP_STOCK_LHB,
    CapabilityUnavailable, CredentialField, MarketProvider, NullProvider,
    ProviderAuthError, ProviderError,
)
from services.providers.registry import (  # noqa: F401
    discover, get_provider, load_error, reset_cache,
)
