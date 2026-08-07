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
