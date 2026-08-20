"""工具缺口判定。

四类缺口全部对应真实发生过的情况, 尤其 stale 与 zero_fields —— 那两类工具不报错,
模型照着空/旧数据往下答, 是最难被发现的。
"""
from datetime import datetime, timedelta, timezone

from services.tool_gaps import (classify, _market_of, _stale_cutoff,
                                _sessions_between)

_CST = timezone(timedelta(hours=8))


def _days_ago(n: int) -> str:
    return (datetime.now(_CST) - timedelta(days=n)).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d %H:%M:%S")


# ── error ──────────────────────────────────────────────

def test_error_recorded():
    assert classify("get_lhb", {"code": "600519"}, {"error": "接口超时"}) == \
        ("error", "接口超时")


# ── empty ──────────────────────────────────────────────

def test_empty_is_normal_for_some_tools():
    """每天只有几十只票上龙虎榜, 查了没有是正常结果不是缺口。不排除的话台账顶部会被
    它长期占满, 真正的覆盖漏洞反而看不见(实测第一版就是这样)。"""
    assert classify("get_lhb", {"code": "600519"},
                    {"code": "600519", "rows": [], "note": "600519 近12日未上龙虎榜"}) is None
    assert classify("get_inst_flow", {"code": "600519"},
                    {"code": "600519", "seats": [], "note": "近30天没有机构席位披露"}) is None
    # 用户没给这只票记买入逻辑也是常态, 不是取不到数(台账实测被它顶到前排)
    assert classify("get_thesis", {"code": "600519"},
                    {"code": "600519", "thesis": "",
                     "note": "用户没记这只的买入逻辑。可提示他在持仓里补一句, 以后好复盘。"}) is None
    # 但这些工具真报错还是要记
    assert classify("get_lhb", {"code": "600519"}, {"error": "接口超时"})[0] == "error"
    assert classify("get_thesis", {"code": "600519"}, {"error": "库锁了"})[0] == "error"


def test_empty_list_with_note():
    kind, detail = classify("get_news", {"code": "US.MRVL"},
                            {"news": [], "note": "暂无个股新闻"})
    assert kind == "empty" and "暂无" in detail


def test_all_fields_empty_counts_as_empty():
    kind, _ = classify("get_peers", {"code": "600519"}, {"code": "600519", "peers": []})
    assert kind == "empty"


def test_note_saying_none_counts_even_with_other_keys():
    kind, _ = classify("get_fund_flow", {"code": "600519"},
                       {"code": "600519", "flows": [], "note": "该标的无数据"})
    assert kind == "empty"


def test_normal_result_is_not_a_gap():
    assert classify("get_quote", {"code": "600519"},
                    {"price": 1307.88, "open": 1300.0, "high": 1308.88,
                     "low": 1290.5, "change_pct": 0.76}) is None


# ── stale: 有数但比今天旧 ───────────────────────────────

def test_stale_news_flagged_with_age():
    """2026-08-19 的真实形态: get_news 对美股返回 10 条, 最新一条是 8-16。
    工具不报错, 模型因此以为消息面已覆盖。"""
    from datetime import date
    base = date(2026, 8, 19)
    out = {"news": [{"title": "x"}] * 10, "latest_time": "2026-08-16 20:29:59"}
    kind, detail = classify("get_news", {"code": "US.MRVL"}, out, cutoff=base)
    assert kind == "stale"
    assert "隔了" in detail


def test_today_news_not_stale():
    from datetime import date
    base = date(2026, 8, 19)
    out = {"news": [{"title": "x"}], "latest_time": "2026-08-19 20:38:12"}
    assert classify("get_news", {"code": "US.MRVL"}, out, cutoff=base) is None


def test_weekend_and_overnight_are_not_stale():
    """两类正常情况必须不报: 周一看到周五的新闻(差 3 个日历天但只隔 1 个交易日),
    以及凌晨看到前一天的新闻。按日历天算的话每个周一早上都会误报一遍。"""
    from datetime import date
    friday, monday = date(2026, 8, 14), date(2026, 8, 17)
    assert friday.weekday() == 4 and monday.weekday() == 0
    out = {"news": [{"title": "x"}], "latest_time": "2026-08-14 15:00:00"}
    assert classify("get_news", {"code": "600519"}, out, cutoff=monday) is None
    # 隔夜: 周三凌晨看到周二的
    out2 = {"news": [{"title": "x"}], "latest_time": "2026-08-18 20:00:00"}
    assert classify("get_news", {"code": "600519"}, out2,
                    cutoff=date(2026, 8, 19)) is None


def test_sessions_between_counts_trading_days_not_calendar_days():
    from datetime import date
    # 周五 → 周一: 3 个日历天, 1 个交易日
    assert _sessions_between(date(2026, 8, 14), date(2026, 8, 17), "A") == 1
    # 周二 → 周三: 1 个交易日
    assert _sessions_between(date(2026, 8, 18), date(2026, 8, 19), "A") == 1
    # 上周日 → 本周三: 周一/周二/周三 = 3 个交易日
    assert _sessions_between(date(2026, 8, 16), date(2026, 8, 19), "A") == 3


def test_stale_cutoff_lands_on_a_session():
    got = _stale_cutoff("A")
    assert got.weekday() < 5, f"基准日落在了周末: {got}"


def test_stale_only_for_time_sensitive_tools():
    """基本面/公司简介本来就不天天变, 旧不等于缺口 —— 否则台账会被噪音填满。"""
    from datetime import date
    out = {"pe": 20.1, "last_date": "2026-07-20"}
    assert classify("get_fundamentals", {"code": "600519"}, out,
                    cutoff=date(2026, 8, 19)) is None


def test_unparseable_date_is_not_stale():
    from datetime import date
    out = {"news": [{"title": "x"}], "latest_time": "上周"}
    assert classify("get_news", {"code": "600519"}, out, cutoff=date(2026, 8, 19)) is None


# ── zero_fields: 解析漏读 ───────────────────────────────

def test_zero_price_fields_flagged():
    """2026-08-19: 美股行情把 开/高/低 全返 0(源里同一行其实都有), 因为解析读到
    第 3 个字段就 return 了。这类"有价但没日内区间"能被自动认出来。"""
    out = {"price": 216.0, "change_pct": -7.82, "open": 0, "high": 0, "low": 0,
           "prev_close": 234.33}
    kind, detail = classify("get_quote", {"code": "US.MRVL"}, out)
    assert kind == "zero_fields"
    assert "216.0" in detail


def test_partial_zero_not_flagged():
    """只有一两个为 0(比如刚开盘还没有最低价)不算 —— 三个全 0 才是漏读的样子。"""
    out = {"price": 216.0, "open": 220.52, "high": 225.01, "low": 0}
    assert classify("get_quote", {"code": "US.MRVL"}, out) is None


def test_zero_fields_only_when_price_present():
    """没有 price 的工具不套这条规则。"""
    assert classify("get_trades", {}, {"open": 0, "high": 0, "low": 0, "rows": [1]}) is None


# ── 市场归类 ────────────────────────────────────────────

def test_market_classification():
    assert _market_of({"code": "US.MRVL"}) == "US"
    assert _market_of({"code": "MRVL"}) == "US"
    assert _market_of({"code": "HK.00700"}) == "HK"
    assert _market_of({"code": "00700"}) == "HK"
    assert _market_of({"code": "600519"}) == "A"
    assert _market_of({}) == ""


def test_non_dict_output():
    assert classify("get_x", {}, None) == ("empty", "返回空")
    assert classify("get_x", {}, [1, 2]) is None
