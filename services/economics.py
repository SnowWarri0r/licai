"""Economics: time value of money calculations for unwind decisions."""
from __future__ import annotations
import math
from statistics import NormalDist


DEFAULT_RISK_FREE_RATE = 0.03
_N = NormalDist().cdf


def real_cost(nominal_cost: float, holding_days: int, annual_rate: float = DEFAULT_RISK_FREE_RATE) -> float:
    """Cost adjusted for opportunity cost of capital.

    Formula: nominal_cost × (1 + r)^(days/365)
    Represents "the price your position needs to reach to truly break even",
    accounting for what the capital could have earned risk-free.
    """
    T = holding_days / 365.0
    return nominal_cost * (1 + annual_rate) ** T


def opportunity_cost(trapped_capital: float, holding_days: int, annual_rate: float = DEFAULT_RISK_FREE_RATE) -> float:
    """Total risk-free income foregone by having capital trapped.

    Formula: trapped_capital × ((1 + r)^T - 1)
    """
    T = holding_days / 365.0
    return trapped_capital * ((1 + annual_rate) ** T - 1)


def daily_opportunity_cost(trapped_capital: float, annual_rate: float = DEFAULT_RISK_FREE_RATE) -> float:
    """How much each additional day of holding costs in opportunity terms."""
    return trapped_capital * annual_rate / 365.0


def required_exit_price(new_cost: float, years_to_exit: float, annual_rate: float = DEFAULT_RISK_FREE_RATE) -> float:
    """Price level needed at exit to truly break even (compound adjusted)."""
    return new_cost * (1 + annual_rate) ** years_to_exit


def estimate_recovery_probability(
    current_price: float,
    target_price: float,
    annualized_vol: float,
    years: float,
    drift: float = 0.0,
) -> dict:
    """P(股价在 years 年内触达或超过 target_price) under lognormal GBM.

    Uses the reflection principle for "first passage to upper barrier":
        P = N(-z1) + (H/S)^(2ν/σ²) × N(-z2)
    where z1 = (log(H/S) - νT) / (σ√T),  z2 = (log(H/S) + νT) / (σ√T),
          ν = drift - σ²/2   (risk-neutral adjusted drift)

    Args:
        annualized_vol: 年化波动率 (log-return std × √252)
        drift: 年化预期收益率 (可含基本面判断)

    Returns dict with probability + the intermediate quantities so callers can explain.
    """
    if current_price <= 0 or target_price <= 0 or annualized_vol <= 0 or years <= 0:
        return {"probability": 0.5, "required_log_return": 0.0, "implied_cagr": 0.0,
                "vol_sigma_units": 0.0}
    if target_price <= current_price:
        return {"probability": 0.99, "required_log_return": 0.0, "implied_cagr": 0.0,
                "vol_sigma_units": 0.0}

    log_ratio = math.log(target_price / current_price)
    sqrt_t = math.sqrt(years)
    nu = drift - 0.5 * annualized_vol ** 2

    z1 = (log_ratio - nu * years) / (annualized_vol * sqrt_t)
    z2 = (log_ratio + nu * years) / (annualized_vol * sqrt_t)

    try:
        power = 2 * nu / annualized_vol ** 2
        mult = math.pow(target_price / current_price, power)
    except (OverflowError, ValueError):
        mult = 0.0

    prob = (1 - _N(z1)) + mult * _N(-z2)
    prob = max(0.02, min(0.98, prob))

    implied_cagr = math.exp(log_ratio / years) - 1

    return {
        "probability": round(prob, 3),
        "required_log_return": round(log_ratio, 4),
        "implied_cagr": round(implied_cagr, 4),
        "vol_sigma_units": round(log_ratio / (annualized_vol * sqrt_t), 2),
        "annualized_vol": round(annualized_vol, 4),
        "drift": round(drift, 4),
    }


def hold_vs_cut_npv(
    current_value: float,
    expected_recovery_value: float,
    recovery_probability: float,
    holding_years: float,
    index_annual_return: float = 0.06,
) -> dict:
    """Compare expected future value of cutting loss vs continuing to hold.

    cut_loss_fv: if you cut and re-invest at index return
    hold_fv: expected value of continuing to hold, weighted by recovery probability

    Returns dict with both values + recommendation ("cut" / "hold" / "neutral").
    Threshold: recommend "cut" only if cut_fv exceeds hold_fv by 20%+.
    """
    cut_fv = current_value * (1 + index_annual_return) ** holding_years
    hold_fv = expected_recovery_value * recovery_probability

    if cut_fv > hold_fv * 1.2:
        rec = "cut"
    elif hold_fv > cut_fv:
        rec = "hold"
    else:
        rec = "neutral"

    return {
        "cut_loss_fv": round(cut_fv, 2),
        "hold_fv": round(hold_fv, 2),
        "cut_better_by": round(cut_fv - hold_fv, 2),
        "recommendation": rec,
    }
