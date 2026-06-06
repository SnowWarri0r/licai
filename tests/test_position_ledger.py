"""Tests for FIFO position ledger."""
from datetime import date
from services.position_ledger import compute_position_state


def test_single_buy():
    actions = [
        {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2025-10-21"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    # ~183 days held
    assert s["shares"] == 100
    assert s["cost_price"] == 10.0
    assert 180 <= s["weighted_days"] <= 185


def test_two_buys_different_dates_weighted_days():
    # 100 shares @10 bought 365 days ago
    # 100 shares @10 bought 1 day ago
    # weighted days = (1000*365 + 1000*1) / 2000 = 183
    actions = [
        {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2025-04-22"},
        {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2026-04-21"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    assert s["shares"] == 200
    assert s["cost_price"] == 10.0
    assert 180 <= s["weighted_days"] <= 185


def test_weighted_avg_cost():
    actions = [
        {"action_type": "BUY", "price": 12.0, "shares": 100, "trade_date": "2025-01-01"},
        {"action_type": "BUY", "price": 8.0, "shares": 200, "trade_date": "2026-01-01"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    # avg = (12*100 + 8*200) / 300 = 2800/300 = 9.333
    assert s["shares"] == 300
    assert 9.32 < s["cost_price"] < 9.34


def test_fifo_sell_consumes_oldest_first():
    actions = [
        {"action_type": "BUY", "price": 12.0, "shares": 100, "trade_date": "2025-01-01"},
        {"action_type": "BUY", "price": 8.0, "shares": 100, "trade_date": "2026-01-01"},
        {"action_type": "SELL", "price": 11.0, "shares": 50, "trade_date": "2026-04-01"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    # FIFO remaining lots: 50@12 + 100@8 = 150 shares, avg = 9.333
    # 综合成本法: (100*12 + 100*8 - 50*11) / 150 = (2000 - 550)/150 = 9.667
    assert s["shares"] == 150
    assert 9.65 < s["cost_price"] < 9.68   # 综合成本法 — matches broker display
    assert 9.32 < s["fifo_cost_price"] < 9.34
    assert len(s["lots"]) == 2


def test_complete_selloff_returns_zero():
    actions = [
        {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2025-01-01"},
        {"action_type": "SELL", "price": 12.0, "shares": 100, "trade_date": "2026-01-01"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    assert s["shares"] == 0
    assert s["cost_price"] == 0
    assert s["lots"] == []


def test_add_treated_as_acquisition():
    actions = [
        {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2025-01-01"},
        {"action_type": "ADD", "price": 9.0, "shares": 100, "trade_date": "2025-06-01"},
        {"action_type": "ADD", "price": 8.0, "shares": 100, "trade_date": "2026-01-01"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    assert s["shares"] == 300
    # avg = (10+9+8)/3 = 9.0
    assert s["cost_price"] == 9.0


def test_clear_then_rebuy_resets_cost_basis():
    # 全部卖出归零后再买入 = 全新一段持仓, 旧的那轮已实现不再摊进新成本。
    actions = [
        {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2025-01-01"},
        {"action_type": "SELL", "price": 12.0, "shares": 100, "trade_date": "2025-06-01"},  # 清仓, 赚 +200
        {"action_type": "BUY", "price": 8.0, "shares": 100, "trade_date": "2026-01-01"},   # 重新建仓 @8
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    assert s["shares"] == 100
    # 重新建仓后成本就是 8.0, 不被上一轮 +200 盈利摊低 (旧实现会算成 6.0)
    assert s["cost_price"] == 8.0
    # 总已实现仍含上一轮 +200
    assert s["realized_pnl"] == 200.0
    # realized_carry = 已清仓那轮的已实现 (没被算进当前浮动里), 应=200
    assert s["realized_carry"] == 200.0


def test_clear_then_rebuy_at_loss_carry():
    # 格林美场景: 9.41 买 → 9.11 卖清仓(亏) → 7.88 重新买。无手续费 (stock_code="")
    actions = [
        {"action_type": "BUY", "price": 9.41, "shares": 100, "trade_date": "2026-04-29"},
        {"action_type": "SELL", "price": 9.11, "shares": 100, "trade_date": "2026-05-07"},
        {"action_type": "BUY", "price": 7.88, "shares": 100, "trade_date": "2026-05-29"},
    ]
    s = compute_position_state(actions, today=date(2026, 6, 2))
    assert s["shares"] == 100
    assert abs(s["cost_price"] - 7.88) < 1e-6        # 新成本就是 7.88, 不含旧亏损
    assert abs(s["realized_pnl"] - (-30.0)) < 1e-6   # (9.11-9.41)*100
    assert abs(s["realized_carry"] - (-30.0)) < 1e-6


def test_partial_sell_within_episode_still_folds():
    # 同一段持仓内部分卖出(做T) 仍走综合成本法摊薄, 不重置。
    actions = [
        {"action_type": "BUY", "price": 12.0, "shares": 100, "trade_date": "2025-01-01"},
        {"action_type": "BUY", "price": 8.0, "shares": 100, "trade_date": "2026-01-01"},
        {"action_type": "SELL", "price": 11.0, "shares": 50, "trade_date": "2026-04-01"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    assert s["shares"] == 150
    assert 9.65 < s["cost_price"] < 9.68   # 综合成本法摊薄 (一段内不重置)
    assert s["realized_carry"] == 0.0      # 没清过仓 → carry=0, 当前浮动已含这部分


def test_out_of_order_input_sorted_by_trade_date():
    actions = [
        {"action_type": "SELL", "price": 12.0, "shares": 50, "trade_date": "2026-04-01"},
        {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2025-01-01"},
    ]
    s = compute_position_state(actions, today=date(2026, 4, 22))
    # Should process BUY first then SELL
    # FIFO: 50 shares @10 remain
    # 综合成本法: (100*10 - 50*12) / 50 = 8.0 (profitable sell lowers effective cost)
    assert s["shares"] == 50
    assert s["cost_price"] == 8.0
    assert s["fifo_cost_price"] == 10.0
