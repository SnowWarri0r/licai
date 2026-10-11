"""港股 / 美股交易费用估算(原币: 港股 HKD、美股 USD)。

券商收费方案按「券商名 → 市场」配置, 内置一份盈立证券 PRO 账户「低佣金套餐」(官网帮助中心「PRO 帐户收费标准」),
可用 app_config 的 fee_schedules(JSON, 结构同 DEFAULT_SCHEDULES)整体或部分覆盖; 没配券商的港美股按 DEFAULT_BROKER 算。
法定 / 第三方费用(印花税、征费、SEC、FINRA TAF 等)与券商无关, 按成交日期取当时的费率。
估算只是为了让成本和盈亏接近真实; 拿到结单后把 action 的 fee 填成实际值即可覆盖。

一张委托算一次: 平台费、最低佣金、结算费上下限、TAF 封顶都是按单的, 由 position_ledger.allocate_trade_fees
把同一委托的分笔成交合并后调用 order_fee, 再按成交额分摊回每笔。
"""
from __future__ import annotations

import json
import math

DEFAULT_BROKER = "盈立证券"

DEFAULT_SCHEDULES: dict = {
    "盈立证券": {
        "HK": {"plan": "PRO 低佣金套餐", "commission_rate": 0.0006, "commission_min": 0.0, "platform_per_order": 18.0,
               "dividend_tax_rate": 0.0},     # 港股分红预扣因公司而异(H 股一般 10%, 开曼等离岸注册的不扣), 以结单为准手填
        "US": {"plan": "PRO 低佣金套餐", "commission_per_share": 0.01, "commission_min": 1.0,
               "platform_per_share": 0.009, "platform_min": 2.88, "platform_max_pct": 0.005,
               "dividend_tax_rate": 0.10},    # 已交 W-8BEN: 中美税收协定 10%
    },
}

# 港股法定费用(买卖双向): 印花税 0.1%(按整港元向上取整, 2023-11-17 起), 交易所交易费 0.00565%,
# 证监会交易征费 0.0027%, 会财局交易征费 0.00015%, 结算费 0.002%(每单 2~100 港元)
HK_STATUTORY = {"stamp_rate": 0.001, "trading_fee_rate": 0.0000565, "sfc_levy_rate": 0.000027,
                "afrc_levy_rate": 0.0000015, "settlement_rate": 0.00002, "settlement_min": 2.0, "settlement_max": 100.0}

# 美股卖出才收的规费, 按日期分段(起始日, 费率): SEC Section 31 按卖出金额; FINRA TAF 按股数、每单封顶。
# 来源: SEC Fee Rate Advisory 2026-2(2026-04-04 起 $20.60/百万); FINRA 2026 年 1~9 月 $0.000195/股封顶 $9.79,
# 2026-10-01~12-31 暂停为 0。新的费率公告出来后往表里加一行。
US_SEC_RATE = [("2024-05-22", 0.0000278), ("2025-05-14", 0.0), ("2026-04-04", 0.0000206)]
US_TAF = [("2024-01-01", 0.000166, 8.30), ("2026-01-01", 0.000195, 9.79), ("2026-10-01", 0.0, 0.0),
          ("2027-01-01", 0.000195, 9.79)]


def _on(table: list, day: str):
    """按日期取分段表里当时生效的一行(day 早于第一行时用第一行)。"""
    row = table[0]
    for r in table:
        if day >= r[0]:
            row = r
    return row


def schedules(overrides: str | None = None) -> dict:
    """内置方案 + app_config.fee_schedules 的覆盖(按券商、市场逐字段合并)。"""
    out = {b: {m: dict(v) for m, v in mk.items()} for b, mk in DEFAULT_SCHEDULES.items()}
    if overrides:
        try:
            for b, mk in json.loads(overrides).items():
                for m, v in (mk or {}).items():
                    out.setdefault(b, {}).setdefault(m, {}).update(v or {})
        except (ValueError, AttributeError):
            pass
    return out


def order_fee(market: str, side: str, amount: float, shares: float, day: str,
              broker: str | None = None, sched: dict | None = None) -> float:
    """一张委托的总费用(原币)。market: HK / US; side: "buy" / "sell"; day: 成交日 YYYY-MM-DD。"""
    if amount <= 0 or shares <= 0 or market not in ("HK", "US"):
        return 0.0
    sched = sched or schedules()
    plan = (sched.get(broker or "") or sched.get(DEFAULT_BROKER) or {}).get(market) or {}
    if market == "HK":
        commission = max(amount * plan.get("commission_rate", 0.0), plan.get("commission_min", 0.0))
        platform = plan.get("platform_per_order", 0.0)
        s = HK_STATUTORY
        stamp = math.ceil(amount * s["stamp_rate"])
        settle = min(max(amount * s["settlement_rate"], s["settlement_min"]), s["settlement_max"])
        levies = amount * (s["trading_fee_rate"] + s["sfc_levy_rate"] + s["afrc_levy_rate"])
        return round(commission + platform + stamp + settle + levies, 2)
    commission = max(shares * plan.get("commission_per_share", 0.0), plan.get("commission_min", 0.0))
    platform = max(shares * plan.get("platform_per_share", 0.0), plan.get("platform_min", 0.0))
    if plan.get("platform_max_pct"):
        platform = min(platform, amount * plan["platform_max_pct"])
    fee = commission + platform
    if side == "sell":
        fee += amount * _on(US_SEC_RATE, day)[1]
        _, taf, cap = _on(US_TAF, day)
        fee += min(shares * taf, cap)
    return round(fee, 2)


def dividend_tax_rate(market: str, broker: str | None = None, sched: dict | None = None) -> float:
    """分红预扣税默认税率(分红流水没手填 fee 时用)。"""
    sched = sched or schedules()
    plan = (sched.get(broker or "") or sched.get(DEFAULT_BROKER) or {}).get(market) or {}
    return float(plan.get("dividend_tax_rate", 0.0))
