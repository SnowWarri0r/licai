"""资本配置台账: 换股并购不算融资 / 子公司少数股东不算股东 / 分红按报告期归属 / 只出台账不出评价。

这个模块最容易错的地方不是算术, 而是**口径**。三处已经踩过或差点踩:
  1. 用增发公告的 发行总数×发行价 当融资额 —— 会把换股吸收合并算成天量抽血;
  2. 吸收投资收到的现金里含子公司吸收少数股东投资 —— 那不是上市公司股东掏的钱;
  3. 分红按付现年份归属 —— 年报分红次年才付, 会让分红和它对应的利润错开一年。
所以下面的用例主要在钉口径, 不在钉小数点。
"""
import asyncio
import types
import sys

import pytest

from services.capital_allocation import (
    _summary, _window_years, _profit_by_year, ledger,
)


class _FakeDF:
    def __init__(self, rows):
        self._rows = rows

    @property
    def empty(self):
        return not self._rows

    def iterrows(self):
        return enumerate(self._rows)

    def __getitem__(self, key):                 # 支持 df[df["股票代码"] == code]
        if isinstance(key, _Mask):
            return _FakeDF([r for r in self._rows if r.get(key.col) == key.val])
        return _Col(self._rows, key)


class _Col:
    def __init__(self, rows, col):
        self.rows, self.col = rows, col

    def __eq__(self, val):
        return _Mask(self.col, val)


class _Mask:
    def __init__(self, col, val):
        self.col, self.val = col, val


def _fake_ak(monkeypatch, *, cashflow=(), fhps=(), repurchase=()):
    fake = types.ModuleType("akshare")
    fake.stock_cash_flow_sheet_by_yearly_em = lambda symbol: _FakeDF(list(cashflow))
    fake.stock_fhps_detail_em = lambda symbol: _FakeDF(list(fhps))
    fake.stock_repurchase_em = lambda: _FakeDF(list(repurchase))
    monkeypatch.setitem(sys.modules, "akshare", fake)


# ── 融资口径 ────────────────────────────────────────────

def test_share_swap_merger_brings_no_cash(monkeypatch):
    """换股吸收合并走的也是"定向增发", 但一分现金不进来。

    中国船舶 2025-09 定增 30.53亿股 × 37.59 = 1148 亿 —— 按发行额算它是史上最能抽血的公司,
    实际现金流量表里"吸收投资收到的现金"当年是空的(换股并购中国重工)。所以融资额一律取
    现金流量表, 不取发行公告。
    """
    from services.capital_allocation import _equity_cash_in
    _fake_ak(monkeypatch, cashflow=[
        {"REPORT_DATE": "2025-12-31", "ACCEPT_INVEST_CASH": float("nan"), "SUBSIDIARY_ACCEPT_INVEST": float("nan")},
    ])
    assert _equity_cash_in("600150") == {"2025": 0.0}


def test_subsidiary_minority_investment_is_not_shareholder_money(monkeypatch):
    """吸收投资收到的现金里含"子公司吸收少数股东投资" —— 那是别人往子公司投钱, 不是上市公司
    股东掏的。实测中国船舶 2018/2019 的 39.0/66.9 亿全部来自子公司少数股东, 母公司股东一分没掏。
    """
    from services.capital_allocation import _equity_cash_in
    _fake_ak(monkeypatch, cashflow=[
        {"REPORT_DATE": "2019-12-31", "ACCEPT_INVEST_CASH": 66.9e8, "SUBSIDIARY_ACCEPT_INVEST": 66.9e8},
        {"REPORT_DATE": "2020-12-31", "ACCEPT_INVEST_CASH": 38.31e8, "SUBSIDIARY_ACCEPT_INVEST": 0.03e8},
    ])
    r = _equity_cash_in("600150")
    assert r["2019"] == 0.0
    assert round(r["2020"] / 1e8, 2) == 38.28


def test_negative_difference_is_clamped_to_zero(monkeypatch):
    """少数股东那一项大于总额(口径错配/报表勾稽差)时不该出现负融资 —— 负数会在净额里变成
    "还给股东", 凭空多出一笔回报。"""
    from services.capital_allocation import _equity_cash_in
    _fake_ak(monkeypatch, cashflow=[
        {"REPORT_DATE": "2021-12-31", "ACCEPT_INVEST_CASH": 1e8, "SUBSIDIARY_ACCEPT_INVEST": 3e8},
    ])
    assert _equity_cash_in("600150") == {"2021": 0.0}


# ── 分红口径 ────────────────────────────────────────────

def test_dividends_are_attributed_to_the_report_period(monkeypatch):
    """按报告期归属: 2024 年报的分红要算进 2024, 哪怕它在 2025 年中才付现。

    按付现年归会让分红去对下一年的利润, 分红率整体错开一年。
    """
    from services.capital_allocation import _dividends
    _fake_ak(monkeypatch, fhps=[
        {"报告期": "2024-12-31", "现金分红-现金分红比例": 2.4, "总股本": 4003136728},
    ])
    r = _dividends("600176")
    assert list(r.keys()) == ["2024"]
    assert round(r["2024"] / 1e8, 2) == 9.61          # 40.03亿股 / 10 × 2.4 元


def test_interim_and_annual_dividends_in_the_same_year_add_up(monkeypatch):
    """一年可能派两次(中报+年报)。取其中一条就会低估这一年的回报。"""
    from services.capital_allocation import _dividends
    _fake_ak(monkeypatch, fhps=[
        {"报告期": "2025-06-30", "现金分红-现金分红比例": 1.0, "总股本": 1000000000},
        {"报告期": "2025-12-31", "现金分红-现金分红比例": 2.0, "总股本": 1000000000},
    ])
    assert _dividends("600176")["2025"] == 3e8


def test_rows_without_a_dividend_are_skipped(monkeypatch):
    """只送转不派现的年份分红是 0, 不能因为有记录就算出一个数。"""
    from services.capital_allocation import _dividends
    _fake_ak(monkeypatch, fhps=[
        {"报告期": "2020-12-31", "现金分红-现金分红比例": None, "总股本": 1000000000},
        {"报告期": "2021-12-31", "现金分红-现金分红比例": 0.0, "总股本": 1000000000},
    ])
    assert _dividends("600176") == {}


# ── 回购 ────────────────────────────────────────────────

def test_only_executed_buybacks_count(monkeypatch):
    """只有董事会预案、还没买的那些不入账 —— 计划回购金额区间不是钱。"""
    from services.capital_allocation import _buybacks
    _fake_ak(monkeypatch, repurchase=[
        {"股票代码": "600176", "回购起始时间": "2026-09-04", "实施进度": "董事会预案",
         "已回购金额": None, "已回购股份数量": None,
         "已回购股份价格区间-下限": None, "已回购股份价格区间-上限": None},
    ])
    monkeypatch.setattr("services.quality_screen._abstract_series",
                        lambda c: {"年报期": ["20251231"], "每股净资产": [7.77]})
    assert _buybacks("600176") == []


def test_buyback_pb_uses_the_bvps_of_the_buyback_year(monkeypatch):
    """回购贵不贵要跟**当时**的每股净资产比。均价和净资产是同一时点的两个名义量, 相除有意义;
    拿均价直接跟现价比涨跌不行 —— 中间的分红送转会让两个名义价不可比。"""
    from services.capital_allocation import _buybacks
    _fake_ak(monkeypatch, repurchase=[
        {"股票代码": "600176", "回购起始时间": "2025-09-24", "实施进度": "完成实施",
         "已回购金额": 539657450.0, "已回购股份数量": 34528223.0,
         "已回购股份价格区间-下限": 14.8, "已回购股份价格区间-上限": 16.2},
    ])
    monkeypatch.setattr("services.quality_screen._abstract_series",
                        lambda c: {"年报期": ["20251231", "20241231"], "每股净资产": [7.768, 7.504]})
    b = _buybacks("600176")[0]
    assert b["均价"] == 15.63 and b["回购PB"] == 2.01 and b["PB基准年"] == "2025"


def test_current_year_buyback_falls_back_to_the_latest_bvps_and_says_which_year(monkeypatch):
    """今年的回购还没有本年年报。退回最近一期但必须标明用的是哪一年 —— 不标会被当成当年口径读。"""
    from services.capital_allocation import _buybacks
    _fake_ak(monkeypatch, repurchase=[
        {"股票代码": "000333", "回购起始时间": "2026-03-30", "实施进度": "实施中",
         "已回购金额": 8.019723e9, "已回购股份数量": 99797967.0,
         "已回购股份价格区间-下限": 73.66, "已回购股份价格区间-上限": 87.71},
    ])
    monkeypatch.setattr("services.quality_screen._abstract_series",
                        lambda c: {"年报期": ["20251231"], "每股净资产": [29.4]})
    b = _buybacks("000333")[0]
    assert b["PB基准年"] == "2025" and b["回购PB"] is not None


# ── 窗口对齐 ────────────────────────────────────────────

def test_window_stops_where_the_cash_flow_data_stops():
    """窗口起点由**现金流量表**的覆盖范围决定, 不是两个源的并集。

    分红记录常常比现金流量表回溯得更远。取并集的话, 那些只有分红没有融资数据的年份会以
    「融资 0」的身份进台账 —— 数据缺口被读成"那几年没融过资", 净还给股东凭空变大。
    (窗口还有个 10 年截断, 所以这个坑只在现金流覆盖不足 10 年时露头 —— 正好是新上市公司。)
    """
    ab = {"年报期": [f"{y}1231" for y in range(2025, 2011, -1)]}
    cf = {str(y): 0.0 for y in range(2020, 2026)}       # 现金流只有 6 年
    fh = {str(y): 1e8 for y in range(2012, 2026)}       # 分红回溯到 2012
    ys = _window_years(ab, cf, fh)
    assert ys == [str(y) for y in range(2020, 2026)]


def test_window_is_capped_at_ten_years():
    ab = {"年报期": [f"{y}1231" for y in range(2025, 2005, -1)]}
    cf = {str(y): 0.0 for y in range(2010, 2026)}
    fh = {str(y): 1e8 for y in range(2010, 2026)}
    ys = _window_years(ab, cf, fh)
    assert len(ys) == 10 and ys[0] == "2016" and ys[-1] == "2025"


def test_window_is_empty_when_a_source_is_missing():
    ab = {"年报期": ["20251231"]}
    assert _window_years(ab, {}, {}) == []
    assert _window_years({}, {"2025": 1.0}, {"2025": 1.0}) == []


def test_profit_by_year_aligns_on_year_not_position():
    """按年份对齐而不是按下标 —— 年报期序列里少一年就会让利润整体错位一年。"""
    ab = {"年报期": ["20251231", "20241231", "20221231"],
          "归母净利润": [32.9e8, 24.4e8, 66.1e8]}
    got = _profit_by_year(ab, ["2022", "2024", "2025"])
    assert got == {"2022": 66.1e8, "2024": 24.4e8, "2025": 32.9e8}


def test_missing_year_gets_none_not_zero():
    ab = {"年报期": ["20251231"], "归母净利润": [10e8]}
    assert _profit_by_year(ab, ["2024", "2025"])["2024"] is None


# ── 结论文案: 台账不是评价 ──────────────────────────────

def test_summary_states_direction_without_adjectives():
    """「抽血」「厚道」这类词是评价。台账只陈述方向和金额。"""
    o = {"窗口": "2016-2025(10 个年报期)", "净还给股东亿": 106.63, "分红率%": 34.7,
         "拿": {"股权融资到账亿": 9.73}, "还": {"现金分红亿": 110.96, "回购亿": 5.4}}
    s = _summary(o)
    assert "净还给股东 106.63 亿" in s and "34.7%" in s
    for bad in ("抽血", "厚道", "优秀", "差", "建议", "应该"):
        assert bad not in s, bad


def test_summary_flips_wording_when_the_company_is_a_net_taker():
    o = {"窗口": "2016-2025(10 个年报期)", "净还给股东亿": -50.0, "分红率%": 5.0,
         "拿": {"股权融资到账亿": 80.0}, "还": {"现金分红亿": 30.0, "回购亿": 0}}
    assert "净取自股东 50.0 亿" in _summary(o)


def test_summary_omits_buybacks_when_there_are_none():
    o = {"窗口": "2016-2025(10 个年报期)", "净还给股东亿": 14.59, "分红率%": 39.7,
         "拿": {"股权融资到账亿": 38.28}, "还": {"现金分红亿": 52.87, "回购亿": 0}}
    assert "回购" not in _summary(o)


def test_summary_says_so_when_data_is_missing():
    assert "台账建不起来" in _summary({"窗口": None})


def test_ledger_output_carries_the_caveats(monkeypatch):
    """三条必须跟着数字一起走: 不是评价 / 拿得多不等于缺点 / 回购均价别跟现价比。"""
    import services.capital_allocation as ca
    monkeypatch.setattr("services.quality_screen._abstract_series",
                        lambda c: {"年报期": ["20251231"], "归母净利润": [10e8], "每股净资产": [5.0]})
    monkeypatch.setattr(ca, "_equity_cash_in", lambda c: {"2025": 0.0})
    monkeypatch.setattr(ca, "_dividends", lambda c: {"2025": 3e8})
    monkeypatch.setattr(ca, "_buybacks", lambda c: [])
    monkeypatch.setattr(ca, "_valuation", lambda c: {"PB": 2.0, "行业": "玻璃玻纤"})
    r = asyncio.run(ca.ledger("600176", "巨石"))
    assert r["分红率%"] == 30.0 and r["净还给股东亿"] == 3.0
    assert "台账不是评价" in r["note"]
    assert "本来就该融资" in r["note"]
    assert "不要直接和现价比涨跌" in r["note"]
    # 不能拿"打分/评分"当禁词扫全文 —— 护栏本身就写着"不打分、不排名", 一扫就误报。
    # 要查的是有没有真的给出评价性输出: 星级、分数字段、买卖动词。
    assert "不打分" in r["note"] and "不排名" in r["note"]
    for bad in ("★", "目标价", "建议买", "建议卖", "推荐买"):
        assert bad not in str(r), bad
    assert not [k for k in r if any(x in k for x in ("评分", "得分", "星级"))]


def test_ledger_reports_missing_sources(monkeypatch):
    import services.capital_allocation as ca
    monkeypatch.setattr("services.quality_screen._abstract_series", lambda c: {})
    monkeypatch.setattr(ca, "_equity_cash_in", lambda c: {})
    monkeypatch.setattr(ca, "_dividends", lambda c: {})
    monkeypatch.setattr(ca, "_buybacks", lambda c: [])
    monkeypatch.setattr(ca, "_valuation", lambda c: {})
    r = asyncio.run(ca.ledger("600176"))
    assert len(r["取数缺口"]) == 3 and r["窗口"] is None
    assert "台账建不起来" in r["一句话"]


def test_payout_ratio_needs_positive_profit(monkeypatch):
    """窗口内累计亏损时分红率没有意义(负分母会算出负比例) —— 给 None 而不是一个荒谬的数。"""
    import services.capital_allocation as ca
    monkeypatch.setattr("services.quality_screen._abstract_series",
                        lambda c: {"年报期": ["20251231"], "归母净利润": [-10e8], "每股净资产": [5.0]})
    monkeypatch.setattr(ca, "_equity_cash_in", lambda c: {"2025": 0.0})
    monkeypatch.setattr(ca, "_dividends", lambda c: {"2025": 1e8})
    monkeypatch.setattr(ca, "_buybacks", lambda c: [])
    monkeypatch.setattr(ca, "_valuation", lambda c: {})
    r = asyncio.run(ca.ledger("600176"))
    assert r["分红率%"] is None


# ── 与原 skill 的取舍 ───────────────────────────────────

def test_module_does_not_score_management():
    """原 skill 以「诚信35%+战略执行25%+资本配置25%+治理15%」加权打1-5分收尾, 还有段永平三问的
    星级。打分把不可测的东西装进一个可比的数字, 且终点是买不买 —— 这部分刻意不搬。"""
    from services import capital_allocation as ca
    assert not [n for n in dir(ca)
                if any(k in n.lower() for k in ("score", "rating", "star", "grade", "rank"))]


def test_agent_tool_is_registered_with_the_caveats():
    from services.stock_agent import _TOOLS, _EXECUTORS
    assert "get_capital_allocation" in _EXECUTORS
    t = next(t for t in _TOOLS if t["name"] == "get_capital_allocation")
    assert "台账不是评价" in t["description"]
    assert "换股吸收合并" in t["description"]      # 口径出处要让模型转述时带上
    assert "不评价管理层人品" in t["description"]


def test_agent_tool_rejects_non_a_shares():
    from services.stock_agent import _tool_capital_allocation
    out = asyncio.run(_tool_capital_allocation("US.AAPL"))
    assert "error" in out and "A 股" in out["error"]


def test_agent_tool_reports_failure_instead_of_raising(monkeypatch):
    import services.capital_allocation as ca

    async def _boom(code, name=""):
        raise RuntimeError("分红源挂了")
    monkeypatch.setattr(ca, "ledger", _boom)
    from services.stock_agent import _tool_capital_allocation
    out = asyncio.run(_tool_capital_allocation("600176"))
    assert "error" in out and "分红源挂了" in out["error"]
