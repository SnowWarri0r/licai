"""港美股接入的几处口径: 交易时段(含夏令时/假日)、推送频率、收盘小结剔除、港股行情解析、港股搜索。"""
import asyncio
from datetime import datetime, timezone

import pytest

from services import market_data as md


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# ── 交易时段 ──────────────────────────────────────────

@pytest.mark.parametrize("now, expect", [
    (_utc(2026, 10, 9, 2, 0), True),      # 北京 10:00 周五
    (_utc(2026, 10, 9, 4, 0), False),     # 北京 12:00 午休
    (_utc(2026, 10, 9, 6, 30), True),     # 北京 14:30
    (_utc(2026, 10, 9, 7, 30), False),    # 北京 15:30 已收
    (_utc(2026, 10, 1, 2, 0), False),     # 国庆休市
])
def test_a_share_session(now, expect):
    assert md.market_open("A", now) is expect


@pytest.mark.parametrize("now, expect", [
    (_utc(2026, 10, 9, 1, 29), False),    # 港时 09:29 开盘前
    (_utc(2026, 10, 9, 1, 30), True),     # 09:30
    (_utc(2026, 10, 9, 4, 30), False),    # 12:30 午休
    (_utc(2026, 10, 9, 7, 59), True),     # 15:59, A 股已收港股还在交易
    (_utc(2026, 10, 9, 8, 0), False),     # 16:00 收盘
    (_utc(2026, 10, 10, 2, 0), False),    # 周六
    (_utc(2026, 10, 1, 2, 0), False),     # 港股国庆假日
    (_utc(2026, 12, 25, 2, 0), False),    # 圣诞
])
def test_hk_session(now, expect):
    assert md.market_open("HK", now) is expect


@pytest.mark.parametrize("now, expect", [
    # 夏令时(EDT, UTC-4): 09:30 美东 = 13:30 UTC = 北京 21:30
    (_utc(2026, 7, 15, 13, 29), False),
    (_utc(2026, 7, 15, 13, 30), True),
    (_utc(2026, 7, 15, 19, 59), True),
    (_utc(2026, 7, 15, 20, 0), False),
    # 冬令时(EST, UTC-5): 同一个 UTC 时刻还没开盘
    (_utc(2026, 12, 2, 13, 30), False),
    (_utc(2026, 12, 2, 14, 30), True),
    (_utc(2026, 12, 2, 20, 59), True),
    (_utc(2026, 12, 2, 21, 0), False),
    # 美东日期才是交易日: 北京周六凌晨 = 美东周五盘中
    (_utc(2026, 10, 9, 18, 0), True),
    (_utc(2026, 11, 26, 15, 0), False),   # 感恩节
])
def test_us_session_tracks_dst_and_holidays(now, expect):
    assert md.market_open("US", now) is expect


def test_markets_open_has_all_three_flags():
    r = md.markets_open(_utc(2026, 10, 9, 7, 30))     # 北京 15:30
    assert r == {"A": False, "HK": True, "US": False}


def test_push_interval_fast_only_when_a_held_market_is_open():
    from api.ws import _push_interval
    from config import config
    hk_open = {"A": False, "HK": True, "US": False}
    assert _push_interval(hk_open, [{"stock_code": "HK.00700"}]) == config.refresh_interval
    assert _push_interval(hk_open, [{"stock_code": "600519"}]) == config.idle_interval
    us_open = {"A": False, "HK": False, "US": True}
    assert _push_interval(us_open, [{"stock_code": "US.AAPL"}]) == config.refresh_interval
    assert _push_interval({"A": True, "HK": False, "US": False}, []) == config.refresh_interval


# ── 收盘小结 ──────────────────────────────────────────

def test_eod_skips_stale_and_unclosed_moves():
    from services.eod_summary import _hk_closed, _skip_reason
    assert _skip_reason("600519", None, False) is None
    assert _skip_reason("HK.00700", True, False) == "港股未收盘(16:00 港时收)"
    assert _skip_reason("HK.00700", True, True) is None
    assert _skip_reason("HK.00700", False, True) == "港股今日休市"
    assert _skip_reason("US.AAPL", True, False) is None
    assert _skip_reason("US.AAPL", False, False) == "美股行情非最近一场"
    assert _hk_closed(_utc(2026, 10, 9, 7, 10)) is False    # 北京 15:10 收盘小结那一刻
    assert _hk_closed(_utc(2026, 10, 9, 8, 10)) is True


def test_snapshot_hk_refresh_only_after_hk_close():
    from services.portfolio_snapshot import _needs_hk_refresh
    at = lambda h, m: datetime(2026, 10, 9, h, m)
    assert _needs_hk_refresh(at(16, 10), True, True, "") is True
    assert _needs_hk_refresh(at(15, 30), True, True, "") is False
    assert _needs_hk_refresh(at(16, 30), False, True, "") is False
    assert _needs_hk_refresh(at(16, 30), True, False, "") is False
    assert _needs_hk_refresh(at(16, 30), True, True, "2026-10-09") is False


# ── 港股行情 ──────────────────────────────────────────

_RT = ('var hq_str_rt_hk00700="TENCENT,腾讯控股,415.000,411.400,425.800,414.800,423.800,12.400,3.014,'
       '423.600,423.800,6069981837.330,14432656,15.396,0.000,661.295,411.000,2026/10/09,14:45:13,30|3,'
       'N|Y|Y,0.000|0.000|0.000,0|||0.000|0.000|0.000, |0,Y";')


def test_hk_quote_fields_and_time():
    q = md._parse_sina_hk("00700", _RT)
    assert q["open"] == 415.0 and q["prev_close"] == 411.4       # [2] 今开 [3] 昨收
    assert q["price"] == 423.8 and q["change_pct"] == 3.01
    assert q["amount"] == pytest.approx(6069981837.33) and q["volume"] == 14432656
    assert q["quote_time"] == "2026-10-09 14:45:13"
    assert md.quote_session_date("HK", q) == "2026-10-09"


def test_hk_today_valid_checks_quote_date(monkeypatch):
    monkeypatch.setattr(md, "_cst_now", lambda: datetime(2026, 10, 9, 9, 10))
    assert md.quote_today_valid("HK", {"quote_time": "2026-10-09 09:10:00"}) is True
    assert md.quote_today_valid("HK", {"quote_time": "2026-10-08 16:08:00"}) is False   # 开盘前还是昨天的
    assert md.quote_today_valid("HK", {}) is True


def test_us_session_date_from_price_as_of():
    assert md.quote_session_date("US", {"price_as_of": "Oct 08 04:00PM EDT"}).endswith("-10-08")
    assert md.quote_session_date("US", {}) is None


# ── 港股搜索 ──────────────────────────────────────────

class _Resp:
    def __init__(self, text):
        self.text = text


def test_search_hk_filters_warrants_and_rmb_counter(monkeypatch):
    body = ('v_hint="hk~00700~\\u817e\\u8baf\\u63a7\\u80a1~txkg~GP^hk~80700~\\u817e\\u8baf\\u63a7\\u80a1r~txkgr~GP'
            '^hk~13005~\\u817e\\u8baf\\u6cd5\\u5174~txfx~QZ"')
    monkeypatch.setattr(md._requests, "get", lambda *a, **k: _Resp(body))
    md._hk_search_cache.clear()
    assert md.search_hk_stocks("腾讯") == [{"code": "HK.00700", "name": "腾讯控股"}]


@pytest.fixture
def resolver(tmp_path, monkeypatch):
    import config as cfg
    monkeypatch.setattr(cfg.config, "db_path", str(tmp_path / "t.db"))
    import database as d
    asyncio.run(d.init_db())
    import services.stock_agent as sa

    async def _maps():
        return {"贵州茅台": "600519"}, {"600519": "贵州茅台", "000700": "模塑科技"}

    async def _rows():
        return [("gzmt", "guizhoumaotai", "贵州茅台", "600519"), ("msk", "mosukeji", "模塑科技", "000700")]
    monkeypatch.setattr(sa, "_code_name_maps", _maps)
    monkeypatch.setattr(sa, "_pinyin_rows", _rows)
    hk = {"00700": [{"code": "HK.00700", "name": "腾讯控股"}],
          "腾讯": [{"code": "HK.00700", "name": "腾讯控股"}, {"code": "HK.01698", "name": "腾讯音乐sw"}]}
    monkeypatch.setattr(md, "search_hk_stocks", lambda q, limit=8: hk.get(q, []))
    return sa._tool_resolve_stock


def test_five_digit_code_resolves_to_hk(resolver):
    r = asyncio.run(resolver("00700"))
    assert r["code"] == "HK.00700" and r["market"] == "HK"
    assert asyncio.run(resolver("hk700"))["code"] == "HK.00700"


def test_chinese_name_finds_hk_listing(resolver):
    r = asyncio.run(resolver("腾讯"))
    assert [c["code"] for c in r["candidates"]][:1] == ["HK.00700"]


def test_a_share_lookup_unchanged(resolver):
    assert asyncio.run(resolver("600519"))["code"] == "600519"
    assert asyncio.run(resolver("贵州茅台"))["code"] == "600519"


def test_watchlist_code_keeps_market_prefix():
    from api.market_routes import _wl_code
    assert _wl_code("HK.00700") == "HK.00700"
    assert _wl_code("hk700") == "HK.00700"
    assert _wl_code("US.AAPL") == "US.AAPL"
    assert _wl_code("600519") == "600519"


def test_thesis_full_code_restores_hk_prefix():
    from services.thesis_drift import _full_code
    assert _full_code("00700") == "HK.00700"
    assert _full_code("HK.00700") == "HK.00700"
    assert _full_code("600176") == "600176"
    assert _full_code("AAPL") == "US.AAPL"


def test_thesis_fundamentals_none_for_foreign():
    from services.thesis_drift import _fundamentals
    assert asyncio.run(_fundamentals("HK.00700")) is None
