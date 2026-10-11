"""港美股费用估算: 盈立 PRO 低佣金套餐 + 法定费用, 按委托合并、按日期取规费。"""
from services.market_fees import order_fee, schedules
from services.position_ledger import allocate_trade_fees
from services.trade_fills import tick_for_code


def test_hk_order_fee_buy():
    # 腾讯 400 股 × 411.8 = 164720 港元
    amt = 164720.0
    fee = order_fee("HK", "buy", amt, 400, "2026-10-08")
    expect = amt * 0.0006 + 18 + 165 + min(max(amt * 0.00002, 2), 100) + amt * (0.0000565 + 0.000027 + 0.0000015)
    assert abs(fee - round(expect, 2)) < 0.01        # 印花税 164.72 向上取整 = 165


def test_us_buy_and_sell_rules():
    # 100 股 × 336.67: 佣金 max(1.00, 1.00) + 平台 max(0.90, 2.88)=2.88
    assert order_fee("US", "buy", 33667.0, 100, "2026-10-08") == 3.88
    # 卖出 2026-10-08: SEC 20.60/百万, TAF 暂停为 0
    assert order_fee("US", "sell", 33667.0, 100, "2026-10-08") == round(3.88 + 33667 * 0.0000206, 2)
    # 卖出 2026-06-01: 加 TAF 0.000195/股
    assert order_fee("US", "sell", 33667.0, 100, "2026-06-01") == round(3.88 + 33667 * 0.0000206 + 100 * 0.000195, 2)
    # 低价股: 平台费封顶 0.5% 成交额(1000 股 × 1 美元 → 平台 min(9, 5) = 5, 佣金 10)
    assert order_fee("US", "buy", 1000.0, 1000, "2026-10-08") == 15.0


def test_overrides_merge():
    s = schedules('{"盈立证券": {"HK": {"platform_per_order": 0}}}')
    assert s["盈立证券"]["HK"]["platform_per_order"] == 0
    assert s["盈立证券"]["HK"]["commission_rate"] == 0.0006


def test_allocate_merges_one_order():
    # 同一委托分两笔成交(同一分钟, 价差 1 跳板 = 0.2 港元): 平台费只收一次, 按成交额分摊
    acts = [{"stock_code": "HK.00700", "action_type": "BUY", "price": 411.8, "shares": 200, "trade_date": "2026-10-08", "trade_time": "10:01"},
            {"stock_code": "HK.00700", "action_type": "BUY", "price": 412.0, "shares": 200, "trade_date": "2026-10-08", "trade_time": "10:01"}]
    fees = allocate_trade_fees(acts)
    total = order_fee("HK", "buy", 411.8 * 200 + 412.0 * 200, 400, "2026-10-08")
    assert abs(sum(fees) - total) < 0.02
    assert tick_for_code("HK.00700", 411.8) == 0.2


def test_a_share_unchanged():
    acts = [{"stock_code": "600519", "action_type": "BUY", "price": 1500.0, "shares": 100, "trade_date": "2026-10-08", "trade_time": "10:01"}]
    fee = allocate_trade_fees(acts)[0]
    amt = 150000.0
    assert abs(fee - (max(amt * 0.0001854, 5) + amt * (0.00001 + 0.0000341 + 0.00002))) < 1e-6


def test_dividend_withholding():
    from services.position_ledger import compute_position_state
    acts = [{"action_type": "BUY", "price": 300.0, "shares": 100, "trade_date": "2026-10-01", "fee": 3.88},
            {"action_type": "DIVIDEND", "price": 0.26, "shares": 100, "trade_date": "2026-11-12"}]
    st = compute_position_state(acts, stock_code="US.AAPL")
    assert st["income_realized"] == round(26 * 0.9, 2) and st["dividend_tax"] == 2.6
    acts[1]["fee"] = 0.0                                  # 手填 0(比如结单上没扣)
    assert compute_position_state(acts, stock_code="US.AAPL")["income_realized"] == 26.0
    acts_a = [dict(acts[0], fee=5.0), {"action_type": "DIVIDEND", "price": 1.0, "shares": 100, "trade_date": "2026-11-12"}]
    assert compute_position_state(acts_a, stock_code="600519")["income_realized"] == 100.0   # A 股不变
