from services.external_ledger import compute_external_state, parse_fee_tiers, redeem_fee

TIERS = parse_fee_tiers("[[7, 0.015], [365, 0.005], [730, 0.0025]]")


def _buy(i, d, shares, nav):
    return {"id": i, "action_type": "ADD", "trade_date": d, "amount": shares * nav, "shares": shares,
            "unit_price": nav, "status": "confirmed"}


def test_fifo_lots_charged_by_holding_days():
    acts = [_buy(1, "2025-01-01", 100, 1.0), _buy(2, "2026-05-01", 100, 1.0), _buy(3, "2026-10-10", 100, 1.0),
            {"id": 4, "action_type": "REDEEM", "trade_date": "2026-10-13", "shares": 250, "status": "pending"}]
    fee, det = redeem_fee(acts, 4, 250, 2.0, "2026-10-13", TIERS)
    # 先进先出: 100 份持有 >1 年 0.25%, 100 份 <1 年 0.5%, 50 份 <7 天 1.5%
    assert [d["rate"] for d in det] == [0.0025, 0.005, 0.015]
    assert fee == round(100 * 2 * 0.0025 + 100 * 2 * 0.005 + 50 * 2 * 0.015, 2)


def test_earlier_redemption_consumes_lots_first():
    acts = [_buy(1, "2026-01-01", 100, 1.0), _buy(2, "2026-10-10", 100, 1.0),
            {"id": 3, "action_type": "REDEEM", "trade_date": "2026-03-01", "shares": 100, "amount": 100,
             "status": "confirmed"},
            {"id": 4, "action_type": "REDEEM", "trade_date": "2026-10-13", "shares": 100, "status": "pending"}]
    fee, det = redeem_fee(acts, 4, 100, 1.0, "2026-10-13", TIERS)
    assert det[0]["date"] == "2026-10-10" and fee == 1.5


def test_net_proceeds_lower_realized_pnl():
    acts = [_buy(1, "2026-05-01", 100, 1.0),
            {"id": 2, "action_type": "REDEEM", "trade_date": "2026-10-13", "shares": 100, "amount": 120 - 0.6,
             "fee": 0.6, "status": "confirmed"}]
    assert compute_external_state(acts, "FUND")["realized_pnl"] == 19.4


def test_no_tiers_no_fee():
    assert redeem_fee([_buy(1, "2026-10-12", 10, 1.0)], 9, 10, 1.0, "2026-10-13", parse_fee_tiers(None)) == (0.0, [])
