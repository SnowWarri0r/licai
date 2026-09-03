import asyncio
import os
import tempfile
import pytest
import config as cfg
import database as db


@pytest.fixture
def fresh_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    # database.get_db() reads config.config.db_path (the singleton instance)
    monkeypatch.setattr("config.config.db_path", path)
    asyncio.run(db.init_db())
    yield path
    os.remove(path)


def test_seed_creates_zhaoshang_yinhe(fresh_db):
    rows = asyncio.run(db.list_brokers())
    names = {r["name"] for r in rows}
    assert "招商证券" in names and "银河证券" in names
    zs = next(r for r in rows if r["name"] == "招商证券")
    assert abs(zs["stock_rate"] - 0.0001854) < 1e-9
    assert zs["stock_min"] == 5 and zs["is_default"] == 1
    yh = next(r for r in rows if r["name"] == "银河证券")
    assert abs(yh["etf_rate"] - 0.00005) < 1e-9 and yh["etf_min"] == 0.1


from datetime import date
from services.position_ledger import (estimate_trade_fee, compute_position_state,
                                      allocate_trade_fees)


def test_min_commission_charged_once_per_order():
    """一张委托分几个价位成交时, 5 元最低佣金只收一次, 不是每笔各收一次。

    实测这份流水里 17 组分笔成交被重复收了 101.22 元。
    """
    # 同股同日同方向同时刻 = 一张单分两笔成交, 两笔各 100 股都不到 5 元门槛
    fills = [
        {"stock_code": "600000", "action_type": "BUY", "price": 20.45, "shares": 700,
         "trade_date": "2026-08-07", "trade_time": "09:36"},
        {"stock_code": "600000", "action_type": "BUY", "price": 20.46, "shares": 700,
         "trade_date": "2026-08-07", "trade_time": "09:36"},
    ]
    got = sum(allocate_trade_fees(fills))
    one = estimate_trade_fee("BUY", (20.45 * 700 + 20.46 * 700) / 1400, 1400, "600000")
    assert abs(got - one) < 0.01                    # 与「当成一笔 1400 股」等价
    per_fill = sum(estimate_trade_fee(f["action_type"], f["price"], f["shares"], f["stock_code"])
                   for f in fills)
    assert per_fill - got > 4.5                     # 旧口径确实多收了接近一个最低佣金

    # 不同时刻 = 两张独立委托, 该收两次最低佣金
    sep = [dict(fills[0]), dict(fills[1], trade_time="14:20")]
    assert abs(sum(allocate_trade_fees(sep)) - per_fill) < 0.01

    # 方向不同不能合并(买卖各自清算, 卖出还有印花税)
    mixed = [dict(fills[0]), dict(fills[1], action_type="SELL")]
    both = allocate_trade_fees(mixed)
    assert both[1] > both[0]                        # 卖出那笔多了印花税
    # 按成交额分摊, 两笔金额接近则费用接近
    same = allocate_trade_fees(fills)
    assert abs(same[0] - same[1]) < 0.02


def _sell(price, shares, time="09:52", code="600176", date="2026-09-03", **kw):
    return {"stock_code": code, "action_type": "SELL", "price": price, "shares": shares,
            "trade_date": date, "trade_time": time, **kw}


def _buy(price, shares, time="09:32", code="600183", date="2026-09-01", **kw):
    return {"stock_code": code, "action_type": "BUY", "price": price, "shares": shares,
            "trade_date": date, "trade_time": time, **kw}


def test_same_minute_but_far_apart_in_price_is_two_orders():
    """9-01 生益科技 148.62 + 148.95, 都在 09:32 —— 用户确认是两次下单。

    旧写法的分组键是 代码+日期+方向+成交时刻, **完全不看价格**, 于是这两笔被算成一张委托,
    5 元最低佣金只收了一次(6.91 元)。可它们差 33 个跳板, 一张委托的分笔成交吃不掉那么多档。
    """
    rows = [_buy(148.62, 100), _buy(148.95, 100)]
    got = allocate_trade_fees(rows, 0.000086, 5.0)
    assert abs(sum(got) - 11.91) < 0.02              # 两次最低佣金, 不是一次
    merged = allocate_trade_fees([_buy(148.62, 100), _buy(148.63, 100)], 0.000086, 5.0)
    assert sum(merged) < sum(got) - 4.5              # 只差 1 跳板时才该合成一张


def test_same_minute_within_a_few_ticks_is_one_order():
    """9-03 中国巨石 42.40/42.41/42.42 三笔卖出同在 09:52, 首尾 2 跳板 —— 一张清仓单扫三档。"""
    rows = [_sell(42.40, 100), _sell(42.41, 400), _sell(42.42, 200)]
    got = allocate_trade_fees(rows, 0.000086, 5.0)
    assert abs(sum(got) - 21.75) < 0.02
    per_fill = sum(estimate_trade_fee(r["action_type"], r["price"], r["shares"], r["stock_code"],
                                      commission_rate=0.000086, commission_min=5.0) for r in rows)
    assert abs(per_fill - sum(got) - 10.0) < 0.02    # 逐笔各收一次会多收两个最低佣金


def test_old_rows_without_a_timestamp_are_not_lumped_into_one_order():
    """早期流水没记成交时刻, 旧写法把"整天同方向"并成一张委托 —— 那是文档里承认的少算。

    价差 86 个跳板(25.10 / 24.24)显然是两张单, 跳板判据能把它们分开, 不必依赖时刻。
    """
    rows = [_buy(25.10, 100, time=None, code="002202", date="2026-01-07"),
            _buy(24.24, 100, time=None, code="002202", date="2026-01-07")]
    got = allocate_trade_fees(rows, 0.000086, 5.0)
    assert sum(got) > 9.9                            # 两次最低佣金


def test_etf_uses_a_finer_tick_than_stocks():
    """场内 ETF 报价到 0.001。拿个股的 0.01 去量, 差 10 个 ETF 跳板会被看成 1 跳板而错合。"""
    rows = [_sell(2.135, 10000, code="510300"), _sell(2.145, 10000, code="510300")]
    got = allocate_trade_fees(rows, 0.000086, 5.0)
    assert sum(got) > 9.9                            # 10 个 ETF 跳板 → 两张委托
    near = allocate_trade_fees(
        [_sell(2.135, 10000, code="510300"), _sell(2.137, 10000, code="510300")], 0.000086, 5.0)
    assert sum(near) < sum(got) - 4.5                # 2 个跳板 → 一张委托


def test_commission_and_review_share_one_definition(monkeypatch):
    """"同一张委托"在项目里必须只有一份定义。

    两边各自演化过一次, 就对同一件事给出过两个答案(8-13 沪电 09:43+09:45: 复盘算一次决策、
    佣金算两张委托)。这条测试把跳板上限改小, 如果佣金那侧不是调同一份实现, 它不会跟着变。
    """
    import services.trade_fills as tf
    rows = [_sell(42.40, 100), _sell(42.42, 200)]    # 2 跳板
    before = sum(allocate_trade_fees(rows, 0.000086, 5.0))
    monkeypatch.setattr(tf, "_MAX_TICKS", 1)         # 收紧到 1 跳板 → 应该被拆成两张委托
    after = sum(allocate_trade_fees(rows, 0.000086, 5.0))
    assert after > before + 4.5
    assert len(tf.merge_fills([{"date": "2026-09-03", "code": "600176", "kind": "sell",
                                "price": 42.40, "shares": 100, "time": "09:52",
                                "asset_class": "stock"},
                               {"date": "2026-09-03", "code": "600176", "kind": "sell",
                                "price": 42.42, "shares": 200, "time": "09:52",
                                "asset_class": "stock"}])) == 2


def test_fee_uses_passed_commission():
    amt_shares = 100000  # 100万元 @ 10, 远超最低 5
    zs = estimate_trade_fee("BUY", 10.0, amt_shares, "000001",
                            commission_rate=0.0001854, commission_min=5)
    yh = estimate_trade_fee("BUY", 10.0, amt_shares, "000001",
                            commission_rate=0.000086, commission_min=5)
    assert abs((zs - yh) - 1000000 * (0.0001854 - 0.000086)) < 0.01


def test_compute_state_passes_commission():
    actions = [{"action_type": "BUY", "price": 10.0, "shares": 100000, "trade_date": "2026-01-01"}]
    s_zs = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001",
                                  commission_rate=0.0001854, commission_min=5)
    s_yh = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001",
                                  commission_rate=0.000086, commission_min=5)
    assert s_yh["cost_price"] < s_zs["cost_price"]


def test_compute_state_default_is_zhaoshang():
    actions = [{"action_type": "BUY", "price": 10.0, "shares": 100000, "trade_date": "2026-01-01"}]
    a = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001")
    b = compute_position_state(actions, today=date(2026, 6, 1), stock_code="000001",
                               commission_rate=0.0001854, commission_min=5)
    assert abs(a["cost_price"] - b["cost_price"]) < 1e-9


def test_set_default_is_exclusive(fresh_db):
    rows = asyncio.run(db.list_brokers())
    yh = next(r for r in rows if r["name"] == "银河证券")
    asyncio.run(db.update_broker(yh["id"], is_default=1))
    rows2 = asyncio.run(db.list_brokers())
    defaults = [r for r in rows2 if r["is_default"]]
    assert len(defaults) == 1 and defaults[0]["name"] == "银河证券"
