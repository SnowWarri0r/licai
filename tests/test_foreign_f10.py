"""港美股 F10: 币种折算、估值现算、分时摘要、公告/业绩日历/机构持仓解析。

全部用实盘抓下来的载荷形状离线回归, 网络与汇率一律打桩。最要紧的一条口径:
东财港股主要指标的数值是统一折成人民币的(CURRENCY 字段却写 HKD), PE 要先把人民币 EPS
折成港元再除 —— 拿 CNY 的 EPS 直接除港元股价会把腾讯的 PE 算低约 15%。
"""
import asyncio
from datetime import date

import pytest

from services import foreign_f10 as ff
from services import market_data as md
from services import stock_agent as sa

# 1 USD = 6.70 CNY, 1 HKD = 0.86 CNY  →  1 CNY = 1.1628 HKD, 1 USD = 7.7907 HKD
_FX = {"USD": 6.70, "HKD": 0.86}


@pytest.fixture(autouse=True)
def _fixed_fx(monkeypatch):
    monkeypatch.setattr(md, "get_fx_info", lambda c: {"rate": 1.0 if c == "CNY" else _FX[c],
                                                      "source": "test", "time": ""})
    ff._cache.clear()


# ── 汇率 ────────────────────────────────────────────────

def test_fx_cross_rate():
    assert md.fx_cross_rate("CNY", "HKD")["rate"] == pytest.approx(1 / 0.86, rel=1e-5)
    assert md.fx_cross_rate("USD", "HKD")["rate"] == pytest.approx(6.70 / 0.86, rel=1e-5)
    assert md.fx_cross_rate("HKD", "HKD") == {"rate": 1.0, "source": "same"}
    assert md.fx_cross_rate("TWD", "USD") is None


def test_convert_shows_each_step():
    c = ff.convert(25.9021, "CNY", "HKD")
    assert c["原币"] == "25.9021 CNY"
    assert c["换算汇率"] == "1 CNY = 1.1628 HKD"
    assert c["折算后"] == pytest.approx(25.9021 / 0.86, rel=1e-4)
    same = ff.convert(8.83, "USD", "USD")
    assert same["换算汇率"] == "同币种, 无需换算" and same["折算后"] == 8.83


def test_convert_falls_back_to_implied_rate_for_other_currencies():
    c = ff.convert(427.42, "TWD", "USD", implied=13.8905 / 427.42)
    assert c["汇率来源"] == "数据源隐含汇率"
    assert c["折算后"] == pytest.approx(13.8905, rel=1e-4)
    assert ff.convert(1.0, "TWD", "USD") is None


# ── 港股估值 ────────────────────────────────────────────

_TENCENT_ROWS = [
    {"REPORT_DATE": "2026-06-30 00:00:00", "DATE_TYPE_CODE": "002", "REPORT_TYPE": "2026年中报",
     "EPS_TTM": 25.902102631359, "BPS": 124.920517901338, "BASIC_EPS": 12.639,
     "ISSUED_COMMON_SHARES": 9092605595, "HK_COMMON_SHARES": 9092605595, "CURRENCY": "HKD",
     "OPERATE_INCOME": 401243000000, "OPERATE_INCOME_YOY": 10.07, "HOLDER_PROFIT": 114115000000,
     "HOLDER_PROFIT_YOY": 10.31, "GROSS_PROFIT_RATIO": 57.25, "NET_PROFIT_RATIO": 29.25,
     "ROE_YEARLY": 19.93, "DEBT_ASSET_RATIO": 43.39},
    {"REPORT_DATE": "2026-03-31 00:00:00", "DATE_TYPE_CODE": "003", "BASIC_EPS": 6.431},
    {"REPORT_DATE": "2025-12-31 00:00:00", "DATE_TYPE_CODE": "001", "BASIC_EPS": 24.749},
]


def test_hk_valuation_converts_cny_eps_to_hkd_before_pe():
    v = ff.build_hk_valuation(_TENCENT_ROWS, "CNY", 424.8)
    assert v["数据币种"] == "CNY" and v["财报披露币种"] == "CNY" and v["报价币种"] == "HKD"
    eps_hkd = 25.902102631359 / 0.86
    assert v["EPS_TTM"]["折算后"] == pytest.approx(eps_hkd, rel=1e-4)
    assert v["PE_TTM"] == round(424.8 / round(eps_hkd, 4), 2)
    assert v["PE_TTM"] != round(424.8 / 25.9021, 2)          # 直接拿人民币 EPS 除会偏低
    assert v["最近年报EPS"]["报告期"] == "2025-12-31"
    assert v["PB"] == round(424.8 / round(124.920517901338 / 0.86, 4), 2)
    assert v["总市值亿HKD"] == round(424.8 * 9092605595 / 1e8, 1)
    assert "港股市值亿HKD" not in v


def test_hk_valuation_h_share_reports_both_caps():
    rows = [{**_TENCENT_ROWS[0], "ISSUED_COMMON_SHARES": 261600381459, "HK_COMMON_SHARES": 240417319880}]
    v = ff.build_hk_valuation(rows, "CNY", 8.0)
    assert v["总市值亿HKD"] == round(8.0 * 261600381459 / 1e8, 1)
    assert v["港股市值亿HKD"] == round(8.0 * 240417319880 / 1e8, 1)


def test_hk_report_currency_reads_summary_list(monkeypatch):
    monkeypatch.setattr(ff, "dc", lambda *a, **k: [{"REPORT_LIST": [
        {"REPORT_DATE": "2026-06-30 00:00:00", "CURRENCY": "美元", "REPORT_TYPE": "中报"}]}])
    assert ff.hk_report_currency("00005") == "USD"


def test_hk_fundamentals_labels_currency(monkeypatch):
    monkeypatch.setattr(ff, "hk_main_rows", lambda sym, size=8: _TENCENT_ROWS)
    monkeypatch.setattr(ff, "hk_report_currency", lambda sym: "USD")
    out = ff.hk_fundamentals("00005", 145.2)
    assert out["valuation"]["财报披露币种"] == "USD"
    assert out["financials"]["金额币种"] == "CNY"
    assert out["financials"]["营业收入亿"] == 4012.43


# ── 美股估值 ────────────────────────────────────────────

def test_us_valuation_same_currency():
    d = {"CURRENCY": "美元", "CURRENCY_ABBR": "USD", "RATIO_EPS_TTM": 8.834343553389, "EPS_TTM_USD": 8.834343553389,
         "BVPS": 7.367320397583, "ISSUED_COMMON_SHARES": 14594180000, "REPORT_DATE": "2026-06-27 00:00:00"}
    v = ff.build_us_valuation(d, 340.42)
    assert v["PE_TTM"] == round(340.42 / 8.8343, 2)
    assert v["PB"] == round(340.42 / 7.3673, 2)
    assert v["BPS"]["原币"] == "7.3673 USD"
    assert v["总市值亿USD"] == round(340.42 * 14594180000 / 1e8, 1)
    assert "口径说明" not in v


def test_us_adr_reporting_in_cny_is_converted():
    d = {"CURRENCY": "人民币", "CURRENCY_ABBR": "CNY", "RATIO_EPS_TTM": 29.567741788745,
         "EPS_TTM_USD": 4.361893993414, "BVPS": 424.730840903066, "ISSUED_COMMON_SHARES": 2487744939}
    v = ff.build_us_valuation(d, 140.0)
    assert v["财报披露币种"] == "CNY"
    assert v["EPS_TTM"]["换算汇率"] == "1 CNY = 0.1493 USD"
    assert v["PE_TTM"] == round(140.0 / round(29.567741788745 / 6.70, 4), 2)
    assert "ADS" in v["口径说明"]


def test_us_twd_uses_implied_rate():
    d = {"CURRENCY_ABBR": "TWD", "RATIO_EPS_TTM": 427.421167058776, "EPS_TTM_USD": 13.890488346984,
         "BVPS": 1240.2487701664}
    v = ff.build_us_valuation(d, 300.0)
    assert v["EPS_TTM"]["汇率来源"] == "数据源隐含汇率"
    assert v["PE_TTM"] == round(300.0 / 13.8905, 2)


# ── 港股公告 / 回购 / 业绩日历 ──────────────────────────

def test_hk_announcements_collapse_daily_returns():
    rows = [
        {"notice_date": "2026-10-09 00:00:00", "title": "翌日披露报表", "columns": [{"column_name": "股份購回"}], "art_code": "A1"},
        {"notice_date": "2026-10-08 00:00:00", "title": "翌日披露报表", "columns": [{"column_name": "股份購回"}], "art_code": "A2"},
        {"notice_date": "2026-10-07 00:00:00", "title": "截至二零二六年九月三十日止之股份发行人的证券变动月报表",
         "columns": [{"column_name": "月報表"}], "art_code": "A3"},
        {"notice_date": "2026-09-24 00:00:00", "title": "董事会会议召开日期",
         "columns": [{"column_name": "董事會召開日期"}], "art_code": "A4"},
    ]
    out = ff.build_hk_announcements(rows, "00700")
    assert [a["title"][:4] for a in out["announcements"]] == ["截至二零", "董事会会"]
    assert out["announcements"][1]["key"] is True and out["announcements"][0]["key"] is False
    assert out["announcements"][0]["链接"] == "https://data.eastmoney.com/notices/detail/00700/A3.html"
    assert out["翌日披露报表"] == {"条数": 2, "其中股份购回": 2, "最近": "2026-10-09", "最早": "2026-10-08"}


def test_summarize_buybacks_window():
    rows = [{"REPO_DATE": "2026-10-09 00:00:00", "REPO_NUM": 239000, "REPO_AMT": 100605639.9, "CURRENCY": "HKD"},
            {"REPO_DATE": "2026-10-08 00:00:00", "REPO_NUM": 241000, "REPO_AMT": 100384886.8, "CURRENCY": "HKD"},
            {"REPO_DATE": "2026-03-01 00:00:00", "REPO_NUM": 999999, "REPO_AMT": 1e9, "CURRENCY": "HKD"}]
    s = ff.summarize_buybacks(rows, 90, today=date(2026, 10, 9))
    assert s["近90天回购天数"] == 2
    assert s["近90天回购股数"] == 480000
    assert s["近90天回购金额亿HKD"] == round((100605639.9 + 100384886.8) / 1e8, 2)
    assert s["区间"] == "2026-10-08 ~ 2026-10-09"
    assert ff.summarize_buybacks([]) == {"note": "近期无回购记录"}


def test_parse_board_meeting_traditional_text():
    txt = ("騰訊控股有限公司（「本公司 」）董事會宣佈，本公司將於2026年11月18日（星 期三）舉行董事會會議，"
           "董事會將於會上通過議案，其中包括批准本公司及其附屬公司截至2026年9月30日止三個月及九個月的未經審核第三季度業績")
    got = ff.parse_board_meeting(txt)
    assert got["会议日期"] == "2026-11-18"
    assert got["审议期间"].startswith("截至2026年9月30日止三個月及九個月")
    txt2 = "我們的董事會將於2026年8月19日舉行董事會會議 ... 該等業績及業績公告將於2026年8月20日由董事會或其代表批准。"
    got2 = ff.parse_board_meeting(txt2)
    assert got2["会议日期"] == "2026-08-19" and got2["业绩公告日"] == "2026-08-20"


def test_hk_earnings_future_board_meeting(monkeypatch):
    rows = [{"notice_date": "2026-09-24 00:00:00", "title": "董事会会议召开日期",
             "columns": [{"column_name": "董事會召開日期"}], "art_code": "B1"},
            {"notice_date": "2026-08-12 00:00:00", "title": "截至二零二六年六月三十日止三个月及六个月业绩公布",
             "columns": [{"column_name": "中期業績"}], "art_code": "R1"}]
    monkeypatch.setattr(ff, "hk_ann_rows", lambda *a, **k: rows)
    monkeypatch.setattr(ff, "_ann_text", lambda art: "本公司將於2026年11月18日舉行董事會會議")
    monkeypatch.setattr(ff, "hk_main_rows", lambda sym, size=8: _TENCENT_ROWS[:1])
    out = ff.hk_earnings("00700", today=date(2026, 10, 9))
    assert out["下次业绩"]["下次业绩董事会"] == "2026-11-18"
    assert out["最近业绩"]["公告日"] == "2026-08-12"
    assert out["最近业绩"]["财务摘要"]["金额币种"] == "CNY"
    out2 = ff.hk_earnings("00700", today=date(2026, 12, 1))
    assert "已过" in out2["下次业绩"]["note"]


# ── Nasdaq 解析 ─────────────────────────────────────────

def test_nq_int():
    assert ff._nq_int("1,426,283,914") == 1426283914
    assert ff._nq_int("(673,972)") == -673972
    assert ff._nq_int("$480,187") == 480187
    assert ff._nq_int("1.5") == 1.5
    assert ff._nq_int("") is None


def test_parse_institutional():
    data = {"ownershipSummary": {"SharesOutstandingPCT": {"value": "76.55%"},
                                 "ShareoutstandingTotal": {"value": "14,594"},
                                 "TotalHoldingsValue": {"value": "$3,761,073"}},
            "activePositions": {"rows": [{"positions": "Increased Positions", "holders": "2,854", "shares": "323,399,563"},
                                         {"positions": "Decreased Positions", "holders": "3,172", "shares": "237,792,808"}]},
            "newSoldOutPositions": {"rows": [{"positions": "New Positions", "holders": "188", "shares": "33,138,036"}]},
            "holdingsTransactions": {"table": {"rows": [
                {"ownerName": "Vanguard Group Inc", "date": "6/30/2026", "sharesHeld": "1,426,283,914",
                 "sharesChange": "26,856,752", "sharesChangePCT": "1.919%", "marketValue": "$480,187,005"}]}}}
    out = ff.parse_nq_institutional(data)
    assert out["机构持股占比"] == "76.55%"
    assert out["持仓变动"]["增持"] == {"机构数": 2854, "股数": 323399563}
    assert out["持仓变动"]["新进"]["机构数"] == 188
    assert out["前十大机构"][0]["持股"] == 1426283914
    assert ff.parse_nq_institutional({}) == {}


@pytest.mark.parametrize("txt, status, timing", [
    ("Apple Inc. Common Stock is estimated to report earnings on  10/29/2026. The upcoming ...", "预计(按历史规律估算)", None),
    ("Coca-Cola Company (The) Common Stock is expected* to report earnings on  10/27/2026 before market open.",
     "已排期(公司公告口径)", "盘前"),
])
def test_parse_earnings_date(txt, status, timing):
    out = ff.parse_nq_earnings_date({"reportText": txt, "announcement": "x"})
    assert out["日期性质"] == status
    assert out["下次业绩日"] in ("2026-10-29", "2026-10-27")
    assert out.get("时段") == timing


def test_parse_surprise():
    rows = ff.parse_nq_surprise({"earningsSurpriseTable": {"rows": [
        {"fiscalQtrEnd": "Jun 2026", "dateReported": "7/30/2026", "eps": 1.91,
         "consensusForecast": "1.88", "percentageSurprise": "1.6"}]}})
    assert rows == [{"财季": "Jun 2026", "公布日": "7/30/2026", "EPS": 1.91, "一致预期": "1.88", "超预期%": "1.6"}]


def test_orghold_trend_flags_partial_quarter(monkeypatch):
    monkeypatch.setattr(ff, "us_secucode", lambda s: "AAPL.O")
    monkeypatch.setattr(ff, "dc", lambda *a, **k: [
        {"REPORT_DATE": "2026-09-30 00:00:00", "SUM": 194, "HOLD_RATIO_SUM": 0.132, "XJNUM": 2, "ZCNUM": 71, "JCNUM": 106},
        {"REPORT_DATE": "2026-06-30 00:00:00", "SUM": 6254, "HOLD_RATIO_SUM": 66.7152, "XJNUM": 168, "ZCNUM": 2616, "JCNUM": 3076},
        {"REPORT_DATE": "2026-03-31 00:00:00", "SUM": 6155, "HOLD_RATIO_SUM": 64.6832, "XJNUM": 178, "ZCNUM": 2410, "JCNUM": 3167}])
    t = ff.em_us_orghold_trend("AAPL", n=2)
    assert t[0]["申报未齐"] is True
    assert "申报未齐" not in t[1] and t[1]["持股占比%"] == 66.72


# ── 取数兜底 ────────────────────────────────────────────

def test_get_json_returns_none_on_reset(monkeypatch):
    class Boom:
        def get(self, *a, **k):
            raise ConnectionResetError("RST")
    monkeypatch.setattr(ff, "_session", lambda: Boom())
    monkeypatch.setattr(ff.time, "sleep", lambda s: None)
    assert ff._get_json("https://example.com/x", {"a": 1}, {}, 60) is None
    assert ff.dc("RPT_X", '(SECUCODE="00700.HK")') == []


# ── 分时摘要 ────────────────────────────────────────────

def _pts(spec):
    return [{"time": t, "price": p, "手": v, "额": p * v} for t, p, v in spec]


def test_hk_minute_summary():
    m = {"date": "2026-10-09", "prev_close": 100.0, "points": _pts([
        ("09:30", 101.0, 300), ("10:45", 103.0, 100), ("13:30", 99.0, 100), ("15:59", 102.0, 300), ("16:08", 102.0, 200)])}
    out = sa.summarize_foreign_minute("HK", m)
    assert out["交易日"] == "2026-10-09" and "香港时间" in out["时区"]
    assert out["最高"] == {"price": 103.0, "time": "10:45", "涨跌%": 3.0}
    assert out["最低"]["time"] == "13:30" and out["高点先于低点"] is True
    assert out["VWAP"] == round((101 * 300 + 103 * 100 + 99 * 100 + 102 * 500) / 1000, 3)
    assert out["日内振幅%"] == 4.0
    segs = {s["时段"].split()[0]: s["成交占比%"] for s in out["量能分段"]}
    assert segs == {"早盘首小时": 30.0, "早盘后段": 10.0, "午后": 10.0, "尾盘": 50.0}


def test_us_minute_summary_uses_quote_extremes_and_price_volume_vwap():
    m = {"date": "2026-10-08", "prev_close": 200.0, "points": [
        {"time": "09:30", "price": 201.0, "手": 100, "额": 0.0},
        {"time": "12:30", "price": 204.0, "手": 100, "额": 0.0},
        {"time": "16:00", "price": 202.0, "手": 200, "额": 0.0}]}
    out = sa.summarize_foreign_minute("US", m, {"high": 204.5, "low": 200.5})
    assert "美东" in out["时区"]
    assert out["最高"]["price"] == 204.5 and out["最高"]["time"] == "12:30"
    assert out["最低"]["price"] == 200.5
    assert out["VWAP"] == round((201 * 100 + 204 * 100 + 202 * 200) / 400, 3)
    assert out["量能分段"][-1]["成交占比%"] == 50.0


# ── 工具路由 ────────────────────────────────────────────

def test_hkus_code_detection():
    assert sa._hkus("HK.00700") == ("HK", "00700")
    assert sa._hkus("00700") == ("HK", "00700")
    assert sa._hkus("US.AAPL") == ("US", "AAPL")
    assert sa._hkus("600519") is None
    assert sa._hkus("sh600519") is None


def test_inst_position_routes_by_market(monkeypatch):
    monkeypatch.setattr(ff, "us_inst_position", lambda sym: {"code": f"US.{sym}", "维度": "个股 13F 机构持仓"})
    assert asyncio.run(sa._tool_inst_position("", "US.KO"))["维度"] == "个股 13F 机构持仓"
    hk = asyncio.run(sa._tool_inst_position("", "HK.00700"))
    assert hk["supported"] is False and "get_shareholders" in hk["note"]


def test_earnings_routes_hkus_away_from_a_share_board(monkeypatch):
    monkeypatch.setattr(ff, "us_earnings", lambda sym: {"code": f"US.{sym}", "下次业绩": {"下次业绩日": "2026-10-29"}})
    monkeypatch.setattr(ff, "hk_earnings", lambda sym: {"code": f"HK.{sym}", "下次业绩": {}})
    out = asyncio.run(sa._tool_earnings("US.AAPL"))
    assert out["下次业绩"]["下次业绩日"] == "2026-10-29" and "预喜" not in out
    assert asyncio.run(sa._tool_earnings("HK.00700"))["code"] == "HK.00700"
