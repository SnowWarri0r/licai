"""配置模板。复制为 config.py 并按需修改。

>>> cp config.example.py config.py
"""
from dataclasses import dataclass, field


@dataclass
class Config:
    # --- 数据库 ---
    db_path: str = "portfolio.db"

    # --- 行情刷新 ---
    quote_cache_ttl: int = 5         # 实时报价缓存秒数
    history_cache_ttl: int = 300     # K 线缓存秒数
    refresh_interval: int = 5        # 盘内推送间隔
    idle_interval: int = 60          # 盘外推送间隔

    # --- 技术指标 ---
    ma_periods: list[int] = field(default_factory=lambda: [5, 10, 20, 60])
    atr_period: int = 14
    rsi_period: int = 14
    bollinger_period: int = 20
    bollinger_std: float = 2.0
    support_resistance_lookback: int = 20

    # --- T-trade 参数 (legacy, 当前默认不启用做T) ---
    buy_zone_max_pct: float = 0.03
    sell_zone_max_pct: float = 0.03
    min_spread_ratio: float = 0.003

    # --- A股 交易成本 (按你的券商实际费率改) ---
    stamp_tax_rate: float = 0.0005       # 印花税 0.05%, 卖侧 only
    commission_rate: float = 0.00025     # 默认 万 2.5; 主流券商默认 万3, 谈判后常见 万 1.5-2.5
    commission_min: float = 5.0          # 单笔最低 5 元 (绝大多数券商通行)
    transfer_fee_rate: float = 0.00001   # 沪市过户费 0.001% (双边)

    # --- 告警 ---
    alert_cooldown: int = 300            # 同 alert 防抖秒数

    # --- 解套 / 经济性参数 ---
    risk_free_rate: float = 0.03         # 无风险年化 (用于 NPV)
    patience_years: float = 2.0          # 最大耐心年限 (超过即建议割肉换指数)
    index_annual_return: float = 0.06    # 沪深300 长期年化基准
    default_unwind_budget: float = 16000.0  # 解套档位默认子弹预算 ¥

    # --- 服务器 ---
    host: str = "0.0.0.0"
    port: int = 8888


config = Config()
