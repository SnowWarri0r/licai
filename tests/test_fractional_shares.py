"""美股碎股: 小数股数进出账本不丢精度、卖光不留浮点尾数、合股零头保留; A 股/港股仍只收整数。"""
import pytest
from fastapi import HTTPException

from api.portfolio_routes import _check_shares
from models import HoldingCreate
from services.position_ledger import compute_position_state


def _a(t, price, shares, day, fx=7.0, fee=0.0):
    return {"action_type": t, "price": price, "shares": shares, "trade_date": day, "fx_rate": fx, "fee": fee}


def test_fractional_buy_sell():
    acts = [_a("BUY", 200.0, 1.5, "2026-09-01", fee=1.0), _a("BUY", 210.0, 0.25, "2026-09-02", fee=1.0),
            _a("SELL", 220.0, 0.75, "2026-09-03", fee=1.0)]
    st = compute_position_state(acts, stock_code="US.AAPL", fx_now=7.0)
    assert st["shares"] == 1.0
    assert st["realized_pnl"] != 0


def test_fractional_sell_out_leaves_no_residue():
    acts = [_a("BUY", 100.0, 0.1, "2026-09-01"), _a("BUY", 100.0, 0.2, "2026-09-02"),
            _a("SELL", 110.0, 0.3, "2026-09-03")]               # 0.1 + 0.2 − 0.3 ≠ 0 的浮点经典坑
    st = compute_position_state(acts, stock_code="US.AAPL", fx_now=7.0)
    assert st["shares"] == 0
    assert st["realized_pnl"] == pytest.approx(3.0)


def test_us_reverse_split_keeps_fraction():
    acts = [_a("BUY", 10.0, 25, "2026-09-01"),
            {"action_type": "SPLIT", "price": 0.1, "shares": 0, "trade_date": "2026-09-10"}]
    st = compute_position_state(acts, stock_code="US.XYZ", fx_now=7.0)
    assert st["shares"] == 2.5                                       # 美股碎股: 10 合 1 后 2.5 股留在账上


def test_hk_reverse_split_still_cash_in_lieu():
    acts = [_a("BUY", 10.0, 25, "2026-09-01", fx=0.9),
            {"action_type": "SPLIT", "price": 0.1, "shares": 0, "trade_date": "2026-09-10"}]
    st = compute_position_state(acts, stock_code="HK.00001", fx_now=0.9)
    assert st["shares"] == 2


def test_check_shares_by_market():
    _check_shares("US.AAPL", 0.5)
    _check_shares("US.AAPL", 1.2345)
    _check_shares("SH600000", 100)
    _check_shares("HK.00700", 100.0)
    for code, sh in [("US.AAPL", 0.12345), ("SH600000", 100.5), ("HK.00700", 1.5), ("US.AAPL", -1)]:
        with pytest.raises(HTTPException):
            _check_shares(code, sh)


def test_model_accepts_decimal():
    assert HoldingCreate(stock_code="US.AAPL", shares=0.5, cost_price=200).shares == 0.5
