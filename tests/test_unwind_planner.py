"""Tests for unwind planner — budget allocation and tranche generation."""
from services.unwind_planner import (
    compute_priority,
    allocate_budgets,
    generate_tranches,
    check_tranche_feasibility,
)


def test_compute_priority_deep_loss_higher():
    p_a = compute_priority(cost_gap_pct=0.30, fundamental_score=0.5, volatility_ratio=0.03, trend_strength=0.0)
    p_b = compute_priority(cost_gap_pct=0.10, fundamental_score=0.5, volatility_ratio=0.03, trend_strength=0.0)
    assert p_a > p_b


def test_compute_priority_downtrend_penalty():
    p_normal = compute_priority(0.2, 0.5, 0.03, trend_strength=0.0)
    p_downtrend = compute_priority(0.2, 0.5, 0.03, trend_strength=0.5)
    assert p_downtrend < p_normal


def test_allocate_budgets_proportional():
    stocks = [
        {"stock_code": "A", "priority": 1.0},
        {"stock_code": "B", "priority": 3.0},
    ]
    result = allocate_budgets(stocks, total_budget=4000.0)
    assert result["A"] == 1000.0
    assert result["B"] == 3000.0


def test_allocate_budgets_handles_zero_priorities():
    stocks = [{"stock_code": "A", "priority": 0.0}]
    result = allocate_budgets(stocks, total_budget=1000.0)
    assert result["A"] == 1000.0


def test_generate_tranches_basic():
    tranches = generate_tranches(
        current_price=10.0,
        atr=0.5,
        supports=[9.2, 8.5],
        lower_bb=8.8,
        historical_low=7.5,
        budget=3000.0,
    )
    assert len(tranches) >= 3
    for t in tranches:
        assert "idx" in t
        assert "trigger_price" in t
        assert "shares" in t
        assert "requires_health" in t
        assert t["trigger_price"] < 10.0


def test_generate_tranches_pyramid_shares():
    tranches = generate_tranches(
        current_price=10.0, atr=0.5, supports=[9.2, 8.5],
        lower_bb=8.8, historical_low=7.5, budget=5000.0,
    )
    shares = [t["shares"] for t in tranches]
    assert shares == sorted(shares)


def test_generate_tranches_deeper_requires_green():
    tranches = generate_tranches(
        current_price=10.0, atr=0.5, supports=[9.2, 8.5],
        lower_bb=8.8, historical_low=7.5, budget=5000.0,
    )
    assert tranches[-1]["requires_health"] in ("yellow", "green")
    assert tranches[0]["requires_health"] == "any"


def test_check_tranche_feasibility_feasible():
    result = check_tranche_feasibility(
        old_shares=300, old_cost=12.74,
        add_shares=200, add_price=10.0,
        historical_high_3y=13.0,
        patience_years=2.0,
        risk_free_rate=0.03,
    )
    # new_cost = (3822 + 2000)/500 = 11.644, required = 11.644*1.03^2 = 12.353 < 13.0
    assert result["feasible"] is True


def test_check_tranche_feasibility_not_feasible():
    result = check_tranche_feasibility(
        old_shares=300, old_cost=20.0,
        add_shares=100, add_price=15.0,
        historical_high_3y=18.0,
        patience_years=2.0,
        risk_free_rate=0.03,
    )
    # new_cost = 18.75, required = 18.75*1.03^2 = 19.89 > 18.0
    assert result["feasible"] is False
    assert result["required_price"] > result["historical_high_3y"]
