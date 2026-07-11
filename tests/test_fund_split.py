"""基金份额拆分: SPLIT 流水缩放 lot + raw/qfq 检测纯函数。"""
from services.external_ledger import compute_external_state
from services.external_assets import _split_factor_from_series


def _act(i, t, amount=0, shares=None, unit_price=None, date="2026-06-01"):
    return {"id": i, "action_type": t, "amount": amount, "shares": shares,
            "unit_price": unit_price, "trade_date": date, "status": "confirmed"}


def test_split_scales_lots_cost_unchanged():
    acts = [
        _act(1, "BUY", 31891.79, 18900, 1.6873, "2026-05-10"),
        _act(2, "SPLIT", 0, 2.0, None, "2026-07-06"),
    ]
    st = compute_external_state(acts, "FUND")
    assert abs(st["shares"] - 37800) < 1e-6            # 份额×2
    assert abs(st["cost_amount"] - 31891.79) < 0.01     # 成本不动
    assert st["realized_pnl"] == 0                      # 拆分不产生盈亏
    assert abs(st["lots"][0]["unit_price"] - 1.6873 / 2) < 1e-6


def test_split_then_partial_redeem_fifo_uses_scaled_unit():
    acts = [
        _act(1, "BUY", 10000, 2500, 4.0, "2026-05-10"),
        _act(2, "SPLIT", 0, 2.0, None, "2026-07-06"),
        # 拆分后 5000 份, 单价成本 2.0; 以 2.2 卖 1000 份 → realized = (2.2-2.0)*1000 = +200
        _act(3, "REDEEM", 2200, 1000, 2.2, "2026-07-07"),
    ]
    st = compute_external_state(acts, "FUND")
    assert abs(st["realized_pnl"] - 200) < 0.01
    assert abs(st["shares"] - 4000) < 1e-6
    assert abs(st["cost_amount"] - 8000) < 0.01


def test_split_only_scales_lots_before_split_date():
    acts = [
        _act(1, "BUY", 4000, 1000, 4.0, "2026-05-10"),
        _act(2, "SPLIT", 0, 2.0, None, "2026-07-06"),
        _act(3, "BUY", 2000, 1000, 2.0, "2026-07-07"),   # 拆分后按新价买入, 不受缩放
    ]
    st = compute_external_state(acts, "FUND")
    assert abs(st["shares"] - 3000) < 1e-6               # 2000(拆后) + 1000(新买)
    assert abs(st["cost_amount"] - 6000) < 0.01


def test_split_factor_detection():
    dates = ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-06"]
    # 07-06 拆分 1拆3: raw 从 3.60 断崖到 1.21, qfq 平滑(当日实际涨 ~0.83%)
    raw = [3.55, 3.58, 3.60, 1.21]
    qfq = [1.1833, 1.1933, 1.2000, 1.2100]
    hit = _split_factor_from_series(dates, raw, qfq)
    assert hit is not None
    day, f = hit
    assert day == "2026-07-06" and f == 3.0              # 贴近整数取整

    # 无拆分的普通波动不误报
    assert _split_factor_from_series(dates, qfq, qfq) is None
