"""基金回归估算: 盘中给 A 股因子补今天的实时 K 线(新浪日 K 收盘后才发当天那根), 开盘前不补。"""
from datetime import datetime
from unittest import mock

import services.fund_nav_model as F

BARS = [{"date": "2026-01-02", "close": 100.0}, {"date": "2026-01-05", "close": 110.0}]
Q = {"sh000300": {"price": 113.3, "prev_close": 110.0, "open": 110.5, "high": 114.0, "low": 109.0}}


def _run(hhmm, trading=True, bars=BARS, kind="cn"):
    now = datetime(2026, 1, 6, hhmm // 100, hhmm % 100)
    with mock.patch.object(F, "_live_quotes", return_value=Q), \
            mock.patch("services.market_data._is_a_share_trading_day", return_value=trading), \
            mock.patch("services.market_data._cst_now", return_value=now):
        return F._with_today(kind, "sh000300", bars, now=now)


def test_live_bar_added_during_session_and_feeds_returns():
    out = _run(1405)
    assert out[-1]["date"] == "2026-01-06" and out[-1]["close"] == 113.3
    assert round(F._returns(out)["2026-01-06"], 2) == 3.0


def test_no_live_bar_before_open_or_off_day_or_us_factor():
    assert _run(915) == BARS                      # 开盘前实时价还是昨收
    assert _run(1405, trading=False) == BARS
    assert _run(1405, kind="us") == BARS


def test_no_duplicate_when_source_already_has_today():
    bars = BARS + [{"date": "2026-01-06", "close": 113.0}]
    assert _run(1600, bars=bars) == bars          # 昨收对不上最后一根 → 不补


def test_futures_bar_uses_last_close_not_settlement():
    bars = [{"date": "2026-01-05", "close": 800.0}]
    q = {"nf_AU0": {"price": 820.0, "prev_close": 805.0}}           # 昨收字段是昨结算, 与日 K 收盘对不上
    now = datetime(2026, 1, 6, 14, 5)
    with mock.patch.object(F, "_live_quotes", return_value=q), \
            mock.patch("services.market_data._is_a_share_trading_day", return_value=True):
        out = F._with_today("cn", "nf_AU0", bars, now=now)
        assert out[-1] == {"date": "2026-01-06", "close": 820.0, "live": True}
        assert F._with_today("cn", "nf_AU0", bars, now=datetime(2026, 1, 6, 21, 30)) == bars   # 夜盘属于下一交易日
