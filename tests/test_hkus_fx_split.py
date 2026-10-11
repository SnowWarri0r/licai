"""港美股: 成交日汇率(人民币口径已实现/成本)、券商收费方案选择、按成交日取规费、拆股/合股。"""
import asyncio
import json

import pytest

from services.market_fees import order_fee, schedules
from services.position_ledger import (attach_auto_fees, compute_position_state, estimate_trade_fee,
                                      split_ratio)


def _a(t, price, shares, day, fx=None, fee=0.0, **kw):
    return {"action_type": t, "price": price, "shares": shares, "trade_date": day,
            "fx_rate": fx, "fee": fee, **kw}


# ---------- 成交日汇率 ----------

def test_realized_cny_uses_buy_and_sell_rates():
    acts = [_a("BUY", 100.0, 100, "2026-01-05", fx=7.0, fee=2.0),
            _a("SELL", 110.0, 100, "2026-06-01", fx=7.2, fee=3.0)]
    st = compute_position_state(acts, stock_code="US.AAPL", fx_now=6.5)
    assert st["realized_pnl"] == 1000 - 5.0                       # 原币不变
    # 79200 − 70000 − 3×7.2 − 2×7.0 = 9164.40; 按当前汇率 6.5 折会是 6467.5, 漏掉本金升值
    assert st["realized_pnl_cny"] == 9164.4
    assert st["realized_fx_pnl"] == 2000.0                        # 本金 10000 美元 × (7.2 − 7.0)
    assert st["realized_carry_cny"] == 9164.4


def test_partial_sell_fifo_and_remaining_cost_cny():
    acts = [_a("BUY", 100.0, 100, "2026-01-05", fx=7.0),
            _a("ADD", 120.0, 100, "2026-02-05", fx=7.1),
            _a("REDUCE", 130.0, 150, "2026-03-05", fx=7.3)]
    st = compute_position_state(acts, stock_code="US.AAPL", fx_now=7.0)
    # FIFO: 100 股出自第 1 笔、50 股出自第 2 笔
    assert st["realized_pnl_cny"] == round(150 * 130 * 7.3 - (100 * 100 * 7.0 + 50 * 120 * 7.1), 2)  # 29750
    assert st["shares"] == 50
    # 综合成本(人民币): 当前段净投入 = 70000 + 85200 − 142350
    assert st["cost_value_cny"] == 12850.0
    assert st["cost_price"] == round((10000 + 12000 - 19500) / 50, 4)


def test_missing_fx_falls_back_to_current_rate():
    acts = [_a("BUY", 100.0, 100, "2026-01-05"), _a("SELL", 110.0, 100, "2026-06-01")]
    st = compute_position_state(acts, stock_code="US.AAPL", fx_now=7.1)
    assert st["realized_pnl_cny"] == round(st["realized_pnl"] * 7.1, 2)
    assert st["realized_fx_pnl"] == 0.0
    # 只有一笔记了汇率: 另一笔按当前
    acts[0]["fx_rate"] = 7.0
    st = compute_position_state(acts, stock_code="US.AAPL", fx_now=7.1)
    assert st["realized_pnl_cny"] == round(110 * 100 * 7.1 - 100 * 100 * 7.0, 2)


def test_a_share_cny_equals_native_and_ignores_fx_rate():
    acts = [_a("BUY", 10.0, 1000, "2026-01-05", fx=7.0, fee=5.0),
            _a("SELL", 11.0, 500, "2026-02-05", fee=5.0)]
    st = compute_position_state(acts, stock_code="600000")
    assert st["realized_pnl_cny"] == st["realized_pnl"]
    assert st["cost_value_cny"] == round(st["cost_price"] * st["shares"], 2)


def test_no_network_when_every_row_has_fx(monkeypatch):
    import services.market_data as md

    def boom(*_):
        raise AssertionError("不该去取当前汇率")
    monkeypatch.setattr(md, "code_fx", boom)
    acts = [_a("BUY", 50.0, 200, "2026-01-05", fx=0.91), _a("DIVIDEND", 1.0, 200, "2026-03-05", fx=0.92)]
    st = compute_position_state(acts, stock_code="HK.00700")
    assert st["realized_pnl_cny"] == round(200 * 0.92, 2)  # 港股默认不预扣


def test_fx_rate_on_dates(monkeypatch):
    import services.market_data as md
    hist = {"2026-09-30": 7.10, "2026-10-08": 7.12}
    monkeypatch.setattr(md, "fx_history", lambda c: hist if c == "USD" else {})
    monkeypatch.setattr(md, "get_fx_rate", lambda c: 7.30)
    assert md.fx_rate_on("USD", "2026-10-05") == 7.10           # 国庆假期 → 前一个工作日
    assert md.fx_rate_on("USD", "2026-10-08") == 7.12
    assert md.fx_rate_on("USD", "2099-01-01") == 7.30           # 今天及以后 = 实时
    assert md.fx_rate_on("USD", "2020-01-01") == 7.30           # 早于历史 → 实时兜底
    assert md.fx_rate_on("HKD", "2026-10-08") == 7.30           # 历史取不到 → 实时兜底
    assert md.code_fx_on("600000", "2026-10-08") is None
    assert md.code_fx_on("US.AAPL", "2026-10-08") == 7.12


# ---------- 费用: 持仓券商、按成交日的规费 ----------

def test_attach_auto_fees_uses_holding_broker_schedule():
    ovr = json.dumps({"某券商": {"US": {"commission_per_share": 0.0, "commission_min": 0.0,
                                         "platform_per_share": 0.0, "platform_min": 9.0}}})
    acts = [{"stock_code": "US.AAPL", "action_type": "BUY", "price": 100.0, "shares": 10,
             "trade_date": "2026-06-01", "trade_time": "22:00"}]
    asyncio.run(attach_auto_fees(acts, "US.AAPL", "某券商", resolve=lambda n: (None, None), fee_overrides=ovr))
    assert acts[0]["_auto_fee"] == 9.0
    # 本笔自己写了券商(盈立)就按本笔
    acts2 = [dict(acts[0], broker="盈立证券")]
    asyncio.run(attach_auto_fees(acts2, "US.AAPL", "某券商", resolve=lambda n: (None, None), fee_overrides=ovr))
    assert acts2[0]["_auto_fee"] == order_fee("US", "buy", 1000.0, 10, "2026-06-01")
    # 两个券商同一分钟的单不合并成一张委托
    acts3 = [dict(acts[0]), dict(acts[0], broker="盈立证券")]
    asyncio.run(attach_auto_fees(acts3, "US.AAPL", "某券商", resolve=lambda n: (None, None), fee_overrides=ovr))
    assert acts3[0]["_auto_fee"] == 9.0 and acts3[1]["_auto_fee"] == acts2[0]["_auto_fee"]


def test_estimate_trade_fee_prices_sec_taf_on_trade_date():
    # 2025-06-01: SEC 暂为 0, TAF 0.000166/股
    assert estimate_trade_fee("SELL", 100.0, 100, "US.AAPL", trade_date="2025-06-01") == round(3.88 + 100 * 0.000166, 2)
    # 2026-06-01: SEC 20.60/百万 + TAF 0.000195/股
    assert estimate_trade_fee("SELL", 100.0, 100, "US.AAPL", trade_date="2026-06-01") == \
        round(3.88 + 10000 * 0.0000206 + 100 * 0.000195, 2)
    ovr = json.dumps({"某券商": {"US": {"commission_per_share": 0.0, "commission_min": 0.0,
                                         "platform_per_share": 0.0, "platform_min": 0.0}}})
    assert estimate_trade_fee("BUY", 100.0, 100, "US.AAPL", trade_date="2026-06-01",
                              broker="某券商", fee_overrides=ovr) == 0.0
    assert schedules(ovr)["某券商"]["US"]["platform_min"] == 0.0


# ---------- 拆股 / 合股 ----------

def test_forward_split():
    acts = [_a("BUY", 400.0, 100, "2026-01-05", fx=7.0),
            {"action_type": "SPLIT", "price": 4.0, "shares": 0, "trade_date": "2026-03-01"},
            _a("SELL", 110.0, 200, "2026-04-01", fx=7.0)]
    st = compute_position_state(acts[:2], stock_code="US.NVDA", fx_now=7.0)
    assert st["shares"] == 400 and st["cost_price"] == 100.0
    assert st["cost_value_cny"] == 280000.0 and st["realized_pnl"] == 0
    st = compute_position_state(acts, stock_code="US.NVDA", fx_now=7.0)
    assert st["shares"] == 200 and st["realized_pnl"] == 2000.0     # (110 − 100) × 200
    assert st["lots"][0]["price"] == 100.0


def test_reverse_split_drops_fraction_keeps_cost():
    acts = [_a("BUY", 2.0, 15, "2026-01-05", fx=7.0),
            {"action_type": "SPLIT", "price": 0.1, "shares": 0, "trade_date": "2026-03-01"}]
    st = compute_position_state(acts, stock_code="US.XYZ", fx_now=7.0)
    assert st["shares"] == 1                       # 1.5 股 → 1 股, 零头券商折现金
    assert st["cost_price"] == 30.0                # 总成本 30 美元留在剩下这 1 股上
    acts.append(_a("SELL", 40.0, 1, "2026-04-01", fx=7.0))
    st = compute_position_state(acts, stock_code="US.XYZ", fx_now=7.0)
    assert st["shares"] == 0 and st["realized_pnl"] == 10.0


def test_split_ratio_both_conventions():
    assert split_ratio({"action_type": "SPLIT", "price": 0.1, "shares": 0}) == 0.1
    assert split_ratio({"action_type": "SPLIT", "shares": 2.0, "unit_price": None}) == 2.0


def test_curve_rescales_history_across_split():
    from services.portfolio_curve import (adjust_to_final_scale, day_flows, share_balance_series,
                                          stock_split_rows)
    acts = [{"id": 1, "action_type": "BUY", "price": 400.0, "shares": 100, "trade_date": "2026-01-05",
             "fx_rate": 7.0, "status": "confirmed"},
            {"id": 2, "action_type": "SPLIT", "price": 4.0, "shares": 0, "trade_date": "2026-03-02",
             "status": "confirmed"}]
    dates = ["2026-01-05", "2026-02-27", "2026-03-02", "2026-03-03"]
    rows = stock_split_rows(acts)
    bal = adjust_to_final_scale(rows, dates, share_balance_series(rows, dates))
    assert bal == [400, 400, 400, 400]                      # 与前复权价(拆后标度)一致, 拆分日无跳变
    qfq = [100.0, 100.0, 100.0, 100.0]
    assert len({b * p for b, p in zip(bal, qfq)}) == 1
    fl = day_flows(rows, dates, stock=True, fx=lambda a: float(a.get("fx_rate") or 0) or 7.3)
    assert fl == {"2026-01-05": 280000.0}                   # 买入按成交日汇率, 拆分无现金流


def test_curve_fx_series():
    from services.portfolio_curve import fx_series
    hist = {"2026-09-30": 7.10, "2026-10-08": 7.12}
    out = fx_series(hist, ["2026-09-29", "2026-09-30", "2026-10-02", "2026-10-09"], 7.2, "2026-10-09")
    assert out == [7.2, 7.10, 7.10, 7.2]


def test_today_pnl_flow_reads_split_ratio_and_auto_fee():
    asyncio.set_event_loop(asyncio.new_event_loop())     # 模块级 asyncio.Lock() 在 3.9 下要有当前 loop
    from services.today_pnl import _flow
    acts = [{"action_type": "SPLIT", "price": 4.0, "shares": 0, "trade_date": "2026-10-09"},
            {"action_type": "BUY", "price": 10.0, "shares": 100, "trade_date": "2026-10-09", "fee": None,
             "_auto_fee": 3.88}]
    f = _flow(acts, "2026-10-09")
    assert f["split"] == 4.0 and f["bought_amount"] == 1003.88


# ---------- 库 ----------

@pytest.fixture
def fresh_db(monkeypatch, tmp_path):
    import database as db
    monkeypatch.setattr("config.config.db_path", str(tmp_path / "t.db"))
    asyncio.run(db.init_db())
    return db


def test_db_fx_rate_column(fresh_db):
    db = fresh_db
    aid = asyncio.run(db.add_position_action("US.AAPL", "BUY", 100.0, 10, trade_date="2026-06-01", fx_rate=7.15))
    row = asyncio.run(db.get_position_actions("US.AAPL"))[0]
    assert row["fx_rate"] == 7.15
    asyncio.run(db.update_position_action(aid, fx_rate=7.2))
    assert asyncio.run(db.get_position_actions("US.AAPL"))[0]["fx_rate"] == 7.2
    asyncio.run(db.update_position_action(aid, fx_rate=0))         # 0 = 清空
    assert asyncio.run(db.get_position_actions("US.AAPL"))[0]["fx_rate"] is None
    asyncio.run(db.update_position_action(aid, note="x"))          # 不传不动
    assert asyncio.run(db.get_position_actions("US.AAPL"))[0]["fx_rate"] is None
