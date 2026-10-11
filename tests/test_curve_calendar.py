"""净值曲线日期轴(A股 ∪ 港美股交易日)、相关性时差对齐、港美股基准对照。全部离线。"""
import asyncio

import pandas as pd
import pytest

from services.portfolio_curve import (foreign_market, held_session_days, observed_dates,
                                      paired_returns)


def _a(code, t, price, shares, day, fx=None, fee=0.0):
    return {"stock_code": code, "action_type": t, "price": price, "shares": shares,
            "trade_date": day, "fee": fee, "fx_rate": fx, "status": "confirmed"}


# ---------- 纯函数 ----------

def test_foreign_market():
    assert foreign_market("stock", "HK.00700") == "HK"
    assert foreign_market("stock", "US.AAPL") == "US"
    assert foreign_market("stock", "SH600000") is None
    assert foreign_market("px", "US.AAPL") is None


def test_held_session_days_only_held_or_traded():
    sess = ["2026-09-29", "2026-09-30", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"]
    acts = [_a("HK.00700", "BUY", 400, 100, "2026-09-30"),
            _a("HK.00700", "SELL", 410, 100, "2026-10-05")]
    got = held_session_days(acts, sess, "2026-09-01", "2026-10-09")
    # 09-29 未持有; 10-05 清仓当天有成交要留; 10-06 起空仓
    assert got == {"2026-09-30", "2026-10-02", "2026-10-05"}
    assert held_session_days(acts, sess, "2026-10-03", "2026-10-09") == {"2026-10-05"}


def test_paired_returns_same_calendar_is_daily():
    a = {"d1": 100, "d2": 110, "d3": 99}
    b = {"d1": 10, "d2": 9, "d3": 9.9}
    xs, ys = paired_returns(a, b)
    assert xs == pytest.approx([0.1, -0.1]) and ys == pytest.approx([-0.1, 0.1])


def test_paired_returns_accumulates_over_other_market_holiday():
    # A 股国庆休市 10-01..10-07, 港股照常: 节后 A 股那一天的收益要配港股整段累计, 不是配 10-08 单日
    a = {"2026-09-30": 100.0, "2026-10-08": 105.0}
    hk = {"2026-09-30": 50.0, "2026-10-02": 51.0, "2026-10-06": 52.0, "2026-10-08": 49.0}
    xs, ys = paired_returns(a, hk)
    assert xs == pytest.approx([0.05]) and ys == pytest.approx([49 / 50 - 1])


def test_us_observed_next_beijing_day():
    px = {"2026-10-08": 1.0, "2026-10-09": 2.0}
    assert observed_dates(px, "US.AAPL") == {"2026-10-09": 1.0, "2026-10-10": 2.0}
    assert observed_dates(px, "HK.00700") is px
    # 美东周五那场 → 北京周六观测, 与 A 股下周一配对(覆盖周五15:00→周一15:00这段)
    a = {"2026-10-08": 100.0, "2026-10-09": 101.0, "2026-10-12": 103.0}
    us = observed_dates({"2026-10-08": 10.0, "2026-10-09": 11.0}, "US.X")
    us["2026-10-13"] = 12.0           # 美东周一那场
    xs, ys = paired_returns(a, us)
    # 首个配对点 10-09(美东 10-08 那场北京 10-09 可见); 下一个 10-12: A 周一 vs 美东周五那场
    assert xs == pytest.approx([103 / 101 - 1]) and ys == pytest.approx([11 / 10 - 1])


def test_index_currency_and_dispatch(monkeypatch):
    from services import market_data as md
    assert md.index_currency("sh000300") == "CNY"
    assert md.index_currency("hkHSI") == "HKD"
    assert md.index_currency("gb_inx") == "USD"
    monkeypatch.setattr(md, "_kline_tencent_hk", lambda s, n: [{"date": "2026-10-02", "close": 1.5}])
    monkeypatch.setattr(md, "_kline_sina_us", lambda s, n: [{"date": "2026-10-01", "close": 2.5}])
    monkeypatch.setattr(md, "_fetch_benchmark_history",
                        lambda s, n: pd.DataFrame([{"date": "2026-09-30", "close": 3.5}]))
    assert md.fetch_index_history("hkHSI", 10).to_dict("records") == [{"date": "2026-10-02", "close": 1.5}]
    assert md.fetch_index_history("gb_ixic", 10).iloc[0]["close"] == 2.5
    assert md.fetch_index_history("sh000300", 10).iloc[0]["date"] == "2026-09-30"


# ---------- build_curve / benchmark 集成(假行情) ----------

A_DAYS = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"]
HK_DAYS = ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-02", "2026-10-05",
           "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09"]
HK_PX = dict(zip(HK_DAYS, [100, 100, 100, 110, 110, 121, 121, 121, 121]))
A_PX = dict(zip(A_DAYS, [10, 10, 10, 11, 11]))


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    import database as db
    from services import market_data as md
    from services import portfolio_curve as pc
    monkeypatch.setattr("config.config.db_path", str(tmp_path / "t.db"))
    asyncio.run(db.init_db())
    pc._cache.clear()
    pc._price_cache.clear()
    csi = pd.DataFrame([{"date": d, "close": 4000.0 + i} for i, d in enumerate(A_DAYS)])
    monkeypatch.setattr(md, "_fetch_benchmark_history", lambda s, n: csi)

    async def _ph(kind, code, days):
        return dict(HK_PX) if code.startswith("HK.") else dict(A_PX)
    monkeypatch.setattr(pc, "_price_hist", _ph)
    monkeypatch.setattr(md, "code_fx", lambda c: 1.0 if not c.startswith(("HK.", "US.")) else 0.9)
    monkeypatch.setattr(md, "fx_history", lambda c: {})

    async def _snap():
        return {}
    import services.portfolio_snapshot as ps
    monkeypatch.setattr(ps, "snapshot_map", _snap)
    return db


def _curve():
    from services.portfolio_curve import build_curve
    return asyncio.run(build_curve(20))


def test_pure_a_axis_is_csi_days(fake_env):
    db = fake_env
    asyncio.run(db.add_holding("SH600000", "浦发", 100, 10))
    asyncio.run(db.add_position_action("SH600000", "BUY", 10, 100, trade_date="2026-09-28", fee=0))
    r = _curve()
    assert r["dates"] == A_DAYS
    assert "港美股开市" not in r["note"]


def test_hk_moves_land_on_hk_dates(fake_env):
    db = fake_env
    asyncio.run(db.add_holding("HK.00700", "腾讯", 100, 100))
    asyncio.run(db.add_position_action("HK.00700", "BUY", 100, 100, trade_date="2026-09-28",
                                       fee=0, fx_rate=0.9))
    r = _curve()
    assert r["dates"] == HK_DAYS
    tw = dict(zip(r["dates"], r["twr"]))
    # 10%涨在 10-02、10%涨在 10-06, 10-08(节后首个 A 股日)不再一次吃下 21%
    assert tw["2026-10-02"] == pytest.approx(110, abs=1e-3)
    assert tw["2026-10-06"] == pytest.approx(121, abs=1e-3)
    assert tw["2026-10-08"] == pytest.approx(tw["2026-10-07"])
    # 沪深300 在 A 股休市日沿用前值
    bench = dict(zip(r["dates"], r["bench"]["series"]))
    assert bench["2026-10-02"] == bench["2026-09-30"]
    assert "4 个 A 股休市而港美股开市" in r["note"]


def test_hk_trade_during_a_holiday_flows_on_its_day(fake_env):
    db = fake_env
    asyncio.run(db.add_holding("SH600000", "浦发", 100, 10))
    asyncio.run(db.add_position_action("SH600000", "BUY", 10, 100, trade_date="2026-09-28", fee=0))
    asyncio.run(db.add_holding("HK.00700", "腾讯", 10, 110))
    asyncio.run(db.add_position_action("HK.00700", "BUY", 110, 10, trade_date="2026-10-02",
                                       fee=0, fx_rate=0.9))
    r = _curve()
    # 只在建仓后的港股交易日补点; 当天按收盘价买入 → 当天 TWR 不动
    assert "2026-10-02" in r["dates"] and "2026-09-29" in r["dates"]
    tw = dict(zip(r["dates"], r["twr"]))
    assert tw["2026-10-02"] == pytest.approx(tw["2026-09-30"])
    assert tw["2026-10-06"] > tw["2026-10-05"]


def test_benchmark_includes_hk_in_cny_against_hsi(fake_env, monkeypatch):
    db = fake_env
    import api.portfolio_routes as pr
    from services import market_data as md
    asyncio.run(db.add_holding("HK.00700", "腾讯", 100, 100))
    asyncio.run(db.add_position_action("HK.00700", "BUY", 100, 100, trade_date="2026-09-30",
                                       fee=0, fx_rate=0.9))
    hsi = pd.DataFrame([{"date": "2026-09-30", "close": 20000.0}, {"date": "2026-10-08", "close": 22000.0}])
    monkeypatch.setattr(md, "fetch_index_history", lambda s, n: hsi)
    monkeypatch.setattr(md, "fx_rate_on", lambda c, d: 0.9)
    monkeypatch.setattr(md, "get_fx_rate", lambda c: 0.8)

    async def _q(codes):
        return {"HK.00700": {"price": 121.0, "fx_rate": 0.8}}
    monkeypatch.setattr(pr, "get_realtime_quotes", _q)
    r = asyncio.run(pr.benchmark_compare(symbol="hkHSI", days=0))
    assert r["scope"]["fx_actions"] == 1 and r["scope"]["a_share_actions"] == 0
    assert r["user"]["buy_total"] == pytest.approx(9000)            # 100×100 港元 × 0.9
    assert r["user"]["current_mv"] == pytest.approx(121 * 100 * 0.8)
    # 9000 元按 0.9 换港币买恒指 20000 → 0.5 份; 现值 0.5×22000×0.8
    assert r["benchmark"]["shares"] == pytest.approx(0.5)
    assert r["benchmark"]["current_mv"] == pytest.approx(8800)
    assert r["benchmark"]["currency"] == "HKD"
