"""Tests for economics module — time value of money calculations."""
import pytest
from services.economics import (
    real_cost,
    opportunity_cost,
    daily_opportunity_cost,
    required_exit_price,
)


def test_real_cost_zero_days_equals_nominal():
    assert real_cost(100.0, 0) == 100.0


def test_real_cost_one_year_at_3pct():
    result = real_cost(100.0, 365, annual_rate=0.03)
    assert abs(result - 103.0) < 0.01


def test_real_cost_compounds_with_time():
    result = real_cost(100.0, 730, annual_rate=0.03)
    assert abs(result - 106.09) < 0.01


def test_real_cost_custom_rate():
    result = real_cost(100.0, 365, annual_rate=0.05)
    assert abs(result - 105.0) < 0.01


def test_opportunity_cost_is_gain_not_kept():
    result = opportunity_cost(10000.0, 365, annual_rate=0.03)
    assert abs(result - 300.0) < 0.5


def test_daily_opportunity_cost():
    result = daily_opportunity_cost(10000.0, annual_rate=0.03)
    assert abs(result - 0.822) < 0.01


def test_required_exit_price_future_breakeven():
    result = required_exit_price(10.0, years_to_exit=2.0, annual_rate=0.03)
    assert abs(result - 10.609) < 0.01


from services.economics import hold_vs_cut_npv


def test_hold_vs_cut_cut_obviously_better():
    result = hold_vs_cut_npv(
        current_value=10000.0,
        expected_recovery_value=11000.0,
        recovery_probability=0.5,
        holding_years=2.0,
        index_annual_return=0.06,
    )
    assert result["recommendation"] == "cut"
    assert result["cut_loss_fv"] > result["hold_fv"]


def test_hold_vs_cut_hold_better():
    result = hold_vs_cut_npv(
        current_value=10000.0,
        expected_recovery_value=20000.0,
        recovery_probability=0.9,
        holding_years=2.0,
        index_annual_return=0.06,
    )
    assert result["recommendation"] == "hold"


def test_hold_vs_cut_neutral():
    result = hold_vs_cut_npv(
        current_value=10000.0,
        expected_recovery_value=12000.0,
        recovery_probability=1.0,
        holding_years=2.0,
        index_annual_return=0.06,
    )
    assert result["recommendation"] == "hold"
