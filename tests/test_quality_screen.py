"""去劣筛选: 7 条门限 / 3 条豁免 / 送转不算稀释 / 缺数据不许冒充通过 / 不出买卖。

判定层(evaluate)是纯函数, 所以这里绝大多数用例不联网 —— 门限逻辑值得逐条钉死: 一个方向
写反(比如把"股本膨胀>20%排除"写成 <20%)会让筛选静默地反过来, 而输出照样是一张完整的表。
"""
import asyncio
import math

import pytest

from services.quality_screen import evaluate, _share_history, _fcf_total, _CRITERIA


def metrics(**kw):
    """一家 7 条全过的公司。逐条测试从它出发, 只改一个数。"""
    base = {
        "ROE10均%": 16.7, "ROE长期均%": 16.7, "FCF5累计亿": 264.2, "利息覆盖倍": 14.4,
        "毛利率长期均%": 37.2, "OCF比NI长期均": 1.16, "净利率长期均%": 23.0, "真实稀释%": 0.0,
        "行业": "玻璃玻纤", "年报期数": 30, "近2年净利率%": [18.1, 16.0],
        "近2年经营现金流为正": True, "净利率上升趋势": None,
    }
    base.update(kw)
    return base


def by_name(r, name):
    return next(x for x in r["逐条"] if x["指标"] == name)


# ── 7 条各自的方向 ──────────────────────────────────────

@pytest.mark.parametrize("name,key,bad", [
    ("10年平均ROE", "ROE10均%", 7.9),
    ("5年累计自由现金流", "FCF5累计亿", -1.0),
    ("利息覆盖倍数", "利息覆盖倍", 1.9),
    ("长期毛利率", "毛利率长期均%", 14.9),
    ("经营现金流/净利润", "OCF比NI长期均", 0.69),
    ("长期净利率", "净利率长期均%", 4.9),
    ("5年真实股本膨胀", "真实稀释%", 20.1),
])
def test_each_criterion_excludes_on_the_right_side(name, key, bad):
    """门限方向逐条钉死。写反一个方向, 筛选会静默反过来而输出照样完整。"""
    # 豁免可能救回这一条, 所以把豁免的前置条件同时压掉(毛利率降到30以下、ROE降到20以下)
    m = metrics(**{key: bad, "毛利率长期均%": 20.0, "ROE长期均%": 15.0,
                   "净利率长期均%": 10.0, "OCF比NI长期均": 0.9})
    m[key] = bad
    r = evaluate(m)
    assert by_name(r, name)["结论"] == "排除", (name, by_name(r, name))
    assert r["结论"] == "排除" and name in r["说明"]


def test_values_just_inside_the_threshold_pass():
    """门限是严格不等号: 恰好 8.0 的 ROE 不该被排除 —— 边界归通过侧。"""
    r = evaluate(metrics(**{"ROE10均%": 8.0, "利息覆盖倍": 2.0, "毛利率长期均%": 15.0,
                            "OCF比NI长期均": 0.7, "净利率长期均%": 5.0, "真实稀释%": 20.0,
                            "FCF5累计亿": 0.0}))
    assert [x["结论"] for x in r["逐条"]] == ["通过"] * 7
    assert r["结论"] == "未被排除"


def test_all_seven_criteria_are_present():
    """7 条就是 7 条。少一条不会报错, 只会让筛选变松。"""
    assert len(_CRITERIA) == 7
    r = evaluate(metrics())
    assert len(r["逐条"]) == 7


# ── 缺数据 ──────────────────────────────────────────────

def test_missing_data_is_undecidable_not_a_pass():
    """缺数据 → 判不了。混进"通过"就是用缺失冒充合格, 而这套筛选的全部价值在于排除。"""
    r = evaluate(metrics(**{"利息覆盖倍": None}))
    row = by_name(r, "利息覆盖倍数")
    assert row["结论"] == "判不了" and "利息费用" in row["说明"]
    assert r["结论"] == "判不了"


def test_nan_counts_as_missing():
    """财务源返回 NaN 很常见。NaN 参与比较永远为 False, 会被当成"没触发门限"= 通过。"""
    r = evaluate(metrics(**{"毛利率长期均%": float("nan")}))
    assert by_name(r, "长期毛利率")["结论"] == "判不了"


def test_an_exclusion_outranks_missing_data():
    """一条排除 + 一条判不了 → 结论是排除。排除是确定的坏消息, 不该被不确定性冲淡。"""
    r = evaluate(metrics(**{"净利率长期均%": 2.0, "毛利率长期均%": 12.0, "ROE长期均%": 9.0,
                            "利息覆盖倍": None}))
    assert r["结论"] == "排除"


def test_undecidable_verdict_says_what_is_missing():
    r = evaluate(metrics(**{"FCF5累计亿": None, "真实稀释%": None}))
    assert "5年累计自由现金流" in r["说明"] and "5年真实股本膨胀" in r["说明"]


# ── 3 条豁免 ────────────────────────────────────────────

def test_exemption_a_rescues_a_young_company_from_the_ten_year_roe_test():
    """上市不足10年 + 毛利率>30% + 近2年经营现金流为正 → 第1条豁免。

    没有豁免的话结论会停在"判不了", 一家只有 6 年年报的好公司永远评不出来。
    """
    m = metrics(**{"年报期数": 6, "ROE10均%": None, "毛利率长期均%": 42.0,
                   "近2年经营现金流为正": True, "ROE长期均%": 15.0})
    r = evaluate(m)
    row = by_name(r, "10年平均ROE")
    assert row["结论"] == "豁免" and row["原判"] == "判不了" and "豁免A" in row["说明"]
    assert r["结论"] == "未被排除"


def test_exemption_a_needs_all_three_conditions():
    """毛利率不到 30% 就不该豁免 —— 豁免是给"年轻但优质", 不是给"年轻"。"""
    m = metrics(**{"年报期数": 6, "ROE10均%": None, "毛利率长期均%": 22.0,
                   "近2年经营现金流为正": True, "ROE长期均%": 15.0})
    assert by_name(evaluate(m), "10年平均ROE")["结论"] == "判不了"


def test_exemption_b_rescues_a_high_gross_margin_company_from_the_net_margin_test():
    m = metrics(**{"净利率长期均%": 3.0, "毛利率长期均%": 55.0, "近2年净利率%": [6.0, 7.0],
                   "ROE长期均%": 15.0})
    row = by_name(evaluate(m), "长期净利率")
    assert row["结论"] == "豁免" and "豁免B" in row["说明"]


def test_exemption_b_also_accepts_a_rising_trend():
    m = metrics(**{"净利率长期均%": 3.0, "毛利率长期均%": 55.0, "近2年净利率%": [4.0, 3.0],
                   "净利率上升趋势": True, "ROE长期均%": 15.0})
    assert by_name(evaluate(m), "长期净利率")["结论"] == "豁免"


def test_exemption_c_says_out_loud_that_the_business_model_was_not_verified():
    """豁免C 的第三个条件是「会员/平台/薄利模式」—— 定性判断, 我们没核实。

    按数字放行但必须写明这一条未核实。不标注就是假精度: 我们并不知道它是不是薄利平台。
    """
    m = metrics(**{"毛利率长期均%": 11.0, "净利率长期均%": 3.0, "ROE长期均%": 26.0,
                   "OCF比NI长期均": 1.4, "近2年净利率%": [3.0, 2.0]})
    r = evaluate(m)
    for n in ("长期毛利率", "长期净利率"):
        row = by_name(r, n)
        assert row["结论"] == "豁免", n
        assert "未核实" in row["说明"], n


def test_exemption_never_covers_a_criterion_that_already_passed():
    """给一条自己就过了的指标盖"豁免"章, 等于把靠实力过的说成靠豁免过的。

    实测中国巨石踩过这个: 净利率 23% 本来轻松通过, 却被豁免B 抢先标成豁免。
    """
    r = evaluate(metrics(**{"净利率长期均%": 23.0, "毛利率长期均%": 37.2}))
    assert by_name(r, "长期净利率")["结论"] == "通过"
    assert r["豁免"] == []


def test_only_applied_exemptions_are_listed():
    m = metrics(**{"净利率长期均%": 3.0, "毛利率长期均%": 55.0, "近2年净利率%": [6.0, 7.0],
                   "ROE长期均%": 15.0})
    r = evaluate(m)
    assert [e["条"] for e in r["豁免"]] == ["净利率长期均%"]


# ── 行业适配 ────────────────────────────────────────────

def test_interest_cover_is_not_applicable_to_banks():
    """银行的利息支出是主营成本不是融资成本, 这条对它没有意义 —— 标不适用而非排除。"""
    r = evaluate(metrics(**{"行业": "银行Ⅱ", "利息覆盖倍": None}))
    row = by_name(r, "利息覆盖倍数")
    assert row["结论"] == "不适用" and "主营成本" in row["说明"]


def test_a_bank_with_a_low_ratio_is_still_not_excluded_by_that_rule():
    r = evaluate(metrics(**{"行业": "保险Ⅱ", "利息覆盖倍": 0.4}))
    assert by_name(r, "利息覆盖倍数")["结论"] == "不适用"


def test_non_financials_still_get_the_interest_test():
    r = evaluate(metrics(**{"行业": "玻璃玻纤", "利息覆盖倍": 0.4}))
    assert by_name(r, "利息覆盖倍数")["结论"] == "排除"


# ── 送转 ≠ 稀释(第 7 条的自制部分) ──────────────────────

class _FakeDF:
    """最小的 DataFrame 替身: 只要 empty 与 iterrows。"""
    def __init__(self, rows):
        self._rows = rows

    @property
    def empty(self):
        return not self._rows

    def iterrows(self):
        return enumerate(self._rows)


def _fh(monkeypatch, rows):
    import services.quality_screen as qs
    import sys
    import types
    fake = types.ModuleType("akshare")
    fake.stock_fhps_detail_em = lambda symbol: _FakeDF(rows)
    monkeypatch.setitem(sys.modules, "akshare", fake)
    return qs._share_history("600176")


def test_a_pure_bonus_issue_is_not_dilution(monkeypatch):
    """10送1.43 让股本涨 14.3%, 但每个股东持股比例分毫未变 —— 这不是稀释。

    实测中国巨石: 5 年股本 35.02→40.03 亿股(+14.3%), 全部由 2020 年报的 10送1.43 解释,
    真实稀释 0%。不扣送转的话它会在第 7 条上差一点就被排除。
    """
    rows = [
        {"报告期": "2020-12-31", "总股本": 3502306849, "送转股份-送转总比例": 1.43, "除权除息日": "2021-06-25"},
        {"报告期": "2025-12-31", "总股本": 4003136728, "送转股份-送转总比例": None, "除权除息日": ""},
    ]
    r = _fh(monkeypatch, rows)
    assert r["股本膨胀%"] == 14.3
    assert r["送转贡献%"] == 14.3
    assert r["真实稀释%"] == 0.0


def test_a_real_placement_shows_up_as_dilution(monkeypatch):
    """定增/股权激励带来的股本增长要如实算成稀释。"""
    rows = [
        {"报告期": "2020-12-31", "总股本": 1000000000, "送转股份-送转总比例": None, "除权除息日": ""},
        {"报告期": "2025-12-31", "总股本": 1400000000, "送转股份-送转总比例": None, "除权除息日": ""},
    ]
    r = _fh(monkeypatch, rows)
    assert r["送转贡献%"] == 0.0 and r["真实稀释%"] == 40.0


def test_bonus_outside_the_window_is_not_credited(monkeypatch):
    """窗口外实施的送转不能拿来抵扣窗口内的股本增长 —— 否则一次旧送股能永久掩盖后来的定增。"""
    rows = [
        {"报告期": "2014-12-31", "总股本": 1000000000, "送转股份-送转总比例": 5.0, "除权除息日": "2015-06-30"},
        {"报告期": "2020-12-31", "总股本": 1000000000, "送转股份-送转总比例": None, "除权除息日": ""},
        {"报告期": "2025-12-31", "总股本": 1300000000, "送转股份-送转总比例": None, "除权除息日": ""},
    ]
    r = _fh(monkeypatch, rows)
    assert r["送转贡献%"] == 0.0 and r["真实稀释%"] == 30.0


def test_missing_ex_date_falls_back_to_the_next_year_convention(monkeypatch):
    """除权日缺失时退回"报告期次年中期"的惯例。

    (按除权日归属这件事本身由 test_a_pure_bonus_issue_is_not_dilution 守着: 那一例的送转
    报告期正好等于窗口起点, 改成按报告期归属就会漏算, 真实稀释从 0% 跳到 14.3%。)
    """
    rows = [
        {"报告期": "2020-12-31", "总股本": 1000000000, "送转股份-送转总比例": 3.0, "除权除息日": ""},
        {"报告期": "2025-12-31", "总股本": 1300000000, "送转股份-送转总比例": None, "除权除息日": ""},
    ]
    r = _fh(monkeypatch, rows)          # 惯例日 2021-06-30 落在窗口内 → 30% 全部由送转解释
    assert r["送转贡献%"] == 30.0 and r["真实稀释%"] == 0.0


def test_stock_for_stock_merger_is_not_called_dilution():
    """发股并购不是稀释: 多出来的股换回了别人的资产和收入。原规则写的是"股本膨胀>20%(非并购)"。

    实测中国船舶: 5年股本 44.72→75.26亿股(+68.3%, 吸收合并中国重工), 同期营收 552→1520亿
    (+175%)。不做这个区分就会把一次并购判成严重稀释, 而它是这个组合里唯一的个股。
    """
    r = evaluate(metrics(**{"真实稀释%": 68.3, "营收增幅%": 175.1}))
    row = by_name(r, "5年真实股本膨胀")
    assert row["结论"] == "判不了"
    assert "发股并购" in row["说明"] and "175.1" in row["说明"]
    assert "请自行核" in row["说明"]        # 代理量而非证据, 要让人去看公告


def test_dilution_without_revenue_growth_is_still_an_exclusion():
    """股本涨了营收没跟上 = 真的被摊薄。这一侧不能被并购豁免顺手带走。"""
    m = metrics(**{"真实稀释%": 40.0, "营收增幅%": 8.0,
                   "毛利率长期均%": 20.0, "ROE长期均%": 15.0})
    assert by_name(evaluate(m), "5年真实股本膨胀")["结论"] == "排除"


def test_merger_check_needs_revenue_data():
    """营收增幅取不到时不许默认成"疑似并购" —— 那等于用缺数据把一条硬指标关掉。"""
    m = metrics(**{"真实稀释%": 40.0, "营收增幅%": None,
                   "毛利率长期均%": 20.0, "ROE长期均%": 15.0})
    assert by_name(evaluate(m), "5年真实股本膨胀")["结论"] == "排除"


def test_revenue_growth_uses_the_same_window_as_the_share_comparison():
    """拿五年营收去对三年股本, 比出来的东西没有意义 —— 窗口必须同一个。"""
    from services.quality_screen import _rev_growth
    ab = {"年报期": ["20251231", "20241231", "20231231", "20221231", "20211231", "20201231"],
          "营收": [1519.8, 800.0, 700.0, 650.0, 600.0, 552.4]}
    assert _rev_growth(ab, "2020-12-31(44.72亿股)→2025-12-31(75.26亿股)") == 175.1
    assert _rev_growth(ab, "2022-12-31(1亿股)→2025-12-31(2亿股)") == 133.8   # 换窗口就换答案
    assert _rev_growth(ab, "") is None
    assert _rev_growth(ab, "2015-12-31(1亿股)→2025-12-31(2亿股)") is None    # 窗口外无数据


def test_share_history_needs_two_annual_points(monkeypatch):
    assert _fh(monkeypatch, [{"报告期": "2025-12-31", "总股本": 1e9,
                              "送转股份-送转总比例": None, "除权除息日": ""}]) == {}


# ── 5 年累计 FCF 的口径 ─────────────────────────────────

def test_fcf_uses_each_year_own_share_count():
    """每股自由现金流直接相加是错的: 期间股本变过, 相加的和不对应任何一笔真实的钱。"""
    ab = {"年报期": ["20251231", "20241231", "20231231"],
          "每股FCF": [1.0, 1.0, 1.0]}
    fh = {"逐年股本": {"2025": 4e9, "2024": 2e9, "2023": 2e9}}
    assert _fcf_total(ab, fh) == 80.0            # (4+2+2)e9 元 / 1e8 = 80 亿


def test_fcf_refuses_to_call_three_years_a_five_year_total():
    ab = {"年报期": ["20251231", "20241231"], "每股FCF": [1.0, 1.0]}
    fh = {"逐年股本": {"2025": 4e9, "2024": 4e9}}
    assert _fcf_total(ab, fh) is None


def test_fcf_skips_years_with_no_share_count():
    ab = {"年报期": ["20251231", "20241231", "20231231", "20221231"],
          "每股FCF": [1.0, 1.0, 1.0, 1.0]}
    fh = {"逐年股本": {"2025": 1e8, "2024": 1e8, "2023": 1e8}}   # 2022 缺
    assert _fcf_total(ab, fh) == 3.0


# ── 护栏 ────────────────────────────────────────────────

def test_output_never_scores_or_recommends():
    """排除法的结论只有三种。一旦出现评分/目标价/建议, 它就变成了投资建议。"""
    for m in (metrics(), metrics(**{"净利率长期均%": 2.0, "毛利率长期均%": 12.0, "ROE长期均%": 9.0}),
              metrics(**{"ROE10均%": None})):
        r = evaluate(m)
        assert r["结论"] in ("未被排除", "排除", "判不了")
        blob = str(r)
        for bad in ("评分", "分数", "目标价", "建议买", "建议卖", "推荐"):
            assert bad not in blob, (bad, r["结论"])


def test_the_note_says_passing_is_not_a_buy():
    """"未被排除"最容易被读成"可以买"。这句话必须跟着结论一起走。"""
    r = evaluate(metrics())
    assert "未被排除 ≠ 值得买" in r["note"]


def test_thresholds_are_labelled_as_borrowed_not_measured():
    """门限是 ai-berkshire 的约定, 不是我们回测出来的。不标注就是把别人的约定冒充自己的实测。"""
    r = evaluate(metrics())
    assert "ai-berkshire" in r["口径"] and "不是本项目实测" in r["口径"]


def test_dilution_caveat_is_stated():
    r = evaluate(metrics())
    assert "送转" in r["口径"]


def test_module_has_no_valuation_or_target_functions():
    """与 calc_rigor 同一条界: 这里只做质地排除, 不做估值。"""
    from services import quality_screen
    assert not [n for n in dir(quality_screen)
                if any(k in n.lower() for k in ("target", "fair", "intrinsic", "score", "rank"))]


# ── agent 接线 ──────────────────────────────────────────

def test_agent_tool_is_registered_and_warns_against_reading_it_as_a_buy():
    from services.stock_agent import _TOOLS, _EXECUTORS
    assert "screen_quality" in _EXECUTORS
    t = next(t for t in _TOOLS if t["name"] == "screen_quality")
    assert "未被排除≠值得买" in t["description"]
    assert "ai-berkshire" in t["description"]      # 门限出处要让模型转述时带上
    assert "判不了" in t["description"]


def test_agent_tool_rejects_non_a_shares():
    """多年财务摘要只对得上 A 股。港美股走进来会静默取空, 不如直接说不支持。"""
    from services.stock_agent import _tool_screen_quality
    out = asyncio.run(_tool_screen_quality("HK.00700"))
    assert "error" in out and "A 股" in out["error"]


def test_agent_tool_reports_fetch_failure_instead_of_raising(monkeypatch):
    import services.quality_screen as qs

    async def _boom(code, name=""):
        raise RuntimeError("财务源全挂")
    monkeypatch.setattr(qs, "screen", _boom)
    from services.stock_agent import _tool_screen_quality
    out = asyncio.run(_tool_screen_quality("600176"))
    assert "error" in out and "财务源全挂" in out["error"]
