"""美股行业/同行/公告。

这三个工具原来对美股一律报错, 是工具缺口台账第一轮捞出来的。用例都拿实盘抓下来的
载荷做回归 —— 尤其 SEC 那份 8-K: 它的 Item 编号才是语义载荷(1.01+3.02 = 签重大协议
+ 发未登记股份), 光看"8-K"只知道有大事。
"""
from unittest import mock

from services import us_fundamentals as usf


def setup_function():
    usf._cache.clear()


# ── 表单/条目翻译 ───────────────────────────────────────

def test_form_cn_handles_amendments():
    """"8-K/A" 不能用 rstrip("/A") 剥后缀 —— 那是按字符集剥, 会剩下 "8-"。"""
    assert usf._form_cn("8-K") == "重大事件临时报告"
    assert usf._form_cn("8-K/A") == "重大事件临时报告-修订"
    assert usf._form_cn("10-K/A") == "年报-修订"
    # SEC 实际返回的是 SCHEDULE 13G/A 而不是 SC 13G/A, 两种写法都要认
    assert usf._form_cn("SCHEDULE 13G/A") == "被动大股东申报(≥5%)-修订"
    assert usf._form_cn("SC 13D") == "主动大股东申报(≥5%)"
    assert usf._form_cn("NT 10-Q") == ""          # 不认识就留空, 不瞎猜


def test_items_cn_decodes_8k_items():
    got = usf._items_cn("1.01,3.02,9.01")
    assert got == "1.01 签署重大协议, 3.02 未登记股份发行, 9.01 财务报表与附件"
    # 不认识的条目原样保留, 不丢
    assert usf._items_cn("1.01,99.9") == "1.01 签署重大协议, 99.9"
    assert usf._items_cn("") == ""


def test_bare_symbol():
    assert usf._bare("US.MRVL") == "MRVL"
    assert usf._bare("mrvl") == "MRVL"
    assert usf._bare("") == ""


# ── 行业/概况 ───────────────────────────────────────────

SEARCH_Q = {"quotes": [
    {"symbol": "MRVLW", "shortname": "别的票"},
    {"symbol": "MRVL", "shortname": "Marvell Technology, Inc.",
     "longname": "Marvell Technology, Inc.", "sector": "Technology",
     "sectorDisp": "Technology", "industry": "Semiconductors",
     "industryDisp": "Semiconductors", "exchDisp": "NASDAQ", "typeDisp": "Equity",
     "prevName": "Marvell Technology Group Ltd.", "nameChangeDate": "2026-08-20"},
]}


def _patch(monkeypatch, payload):
    sess = mock.MagicMock()
    sess.get.return_value = mock.MagicMock(json=lambda: payload)
    monkeypatch.setattr(usf, "_session", lambda: sess)
    return sess


def test_profile_picks_exact_symbol_not_first_hit(monkeypatch):
    """search 会返回一串近似标的, 必须挑代码完全相同的那个。"""
    _patch(monkeypatch, SEARCH_Q)
    out = usf.us_profile("US.MRVL")
    assert out["name"] == "Marvell Technology, Inc."
    assert out["板块"] == "Technology" and out["行业"] == "Semiconductors"
    assert out["交易所"] == "NASDAQ"


def test_profile_reports_prev_name_without_date(monkeypatch):
    """曾用名要报(凭记忆答最容易在更名上翻车), 但不带 nameChangeDate ——
    实测 MRVL 返回的是当天日期, 而它实际是 2021 年改名, 这字段语义没核实清。"""
    _patch(monkeypatch, SEARCH_Q)
    out = usf.us_profile("MRVL")
    assert out["曾用名"] == "Marvell Technology Group Ltd."
    assert "更名日期" not in out


def test_profile_not_found(monkeypatch):
    _patch(monkeypatch, {"quotes": []})
    assert "error" in usf.us_profile("NOSUCH")


# ── 同行 ────────────────────────────────────────────────

RECO = {"finance": {"result": [{"symbol": "MRVL", "recommendedSymbols": [
    {"symbol": "AVGO", "score": 0.177331}, {"symbol": "MU", "score": 0.164305},
    {"symbol": "MRVL", "score": 1.0},          # 自己也可能在列表里
    {"symbol": "ARM", "score": 0.136},
]}]}}


def test_peers_excludes_self_and_rounds_score(monkeypatch):
    _patch(monkeypatch, RECO)
    out = usf.us_peers("US.MRVL")
    assert [p["code"] for p in out] == ["US.AVGO", "US.MU", "US.ARM"]
    assert out[0]["相似度"] == 0.177


def test_peers_empty_when_source_gives_nothing(monkeypatch):
    _patch(monkeypatch, {"finance": {"result": []}})
    assert usf.us_peers("US.MRVL") == []


# ── SEC 公告 ────────────────────────────────────────────

TICKERS = {"0": {"cik_str": 1835632, "ticker": "MRVL", "title": "Marvell Technology, Inc."},
           "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}}

# 2026-08-19 实盘: 这份 8-K 就是给谷歌发认股权证那件事
SUBS = {"name": "Marvell Technology, Inc.", "sicDescription": "Semiconductors & Related Devices",
        "filings": {"recent": {
            "form": ["8-K", "4", "SCHEDULE 13G/A"],
            "filingDate": ["2026-08-19", "2026-08-17", "2026-08-06"],
            "primaryDocDescription": ["8-K", "FORM 4", ""],
            "accessionNumber": ["0001193125-26-356217", "0000000000-26-000001",
                                "0000000000-26-000002"],
            "items": ["1.01,3.02,9.01", "", ""],
        }}}


def test_filings_decodes_and_links(monkeypatch):
    def fake_get(url, **kw):
        payload = TICKERS if "company_tickers" in url else SUBS
        return mock.MagicMock(json=lambda: payload)

    sess = mock.MagicMock()
    sess.get.side_effect = fake_get
    monkeypatch.setattr(usf, "_session", lambda: sess)

    out = usf.us_filings("US.MRVL")
    assert out["name"] == "Marvell Technology, Inc."
    assert out["SEC行业"] == "Semiconductors & Related Devices"
    assert out["latest_time"] == "2026-08-19"
    top = out["公告"][0]
    assert top["表单"] == "8-K" and top["含义"] == "重大事件临时报告"
    assert "签署重大协议" in top["条目"] and "未登记股份发行" in top["条目"]
    # 链接要指向可打开的 index 页, CIK 去掉前导零
    assert top["链接"] == ("https://www.sec.gov/Archives/edgar/data/1835632/"
                          "000119312526356217/0001193125-26-356217-index.htm")


def test_filings_unknown_ticker(monkeypatch):
    sess = mock.MagicMock()
    sess.get.return_value = mock.MagicMock(json=lambda: TICKERS)
    monkeypatch.setattr(usf, "_session", lambda: sess)
    out = usf.us_filings("NOSUCH")
    assert "error" in out and "CIK" in out["error"]


def test_cik_table_is_cached(monkeypatch):
    """company_tickers.json 是 776KB 的静态映射, 不能每次公告查询都拉一遍。"""
    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return mock.MagicMock(json=lambda: TICKERS if "company_tickers" in url else SUBS)

    sess = mock.MagicMock()
    sess.get.side_effect = fake_get
    monkeypatch.setattr(usf, "_session", lambda: sess)

    usf.us_filings("MRVL")
    usf.us_filings("AAPL")
    assert sum(1 for u in calls if "company_tickers" in u) == 1


def test_sec_ua_carries_contact_not_url():
    """实测 SEC 的 WAF 拦 UA 里出现 URL 的请求(一律 403), 带邮箱形态才放行。"""
    ua = usf._SEC_UA["User-Agent"]
    assert "http" not in ua
    assert "@" in ua
