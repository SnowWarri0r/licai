"""R6 数字溯源: [实测] 行里的数字要能回溯到本轮工具返回。"""
from services.answer_guard import unsourced_numbers as U

OUTS = {"get_quote": [{"price": 370.48, "change_pct": 2.29, "amount": 3284000000, "cost": 362.15,
                       "chg": 0.0229}],
        "calc": [{"result": "0.38"}], "get_trend": [{"daily": [{"date": "2026-10-12", "pct": -3.42}]}]}


def test_sourced_numbers_pass_with_rounding_and_units():
    ans = ("- 现价 **370.48**, 涨 2.29% [实测]\n"
           "- 成交额约 32.8 亿, 浮盈 +0.38%, 成本 362.15, 10-12 跌 3.42% [实测]")
    assert U(ans, OUTS) == []


def test_unsourced_flagged():
    assert U("- 总市值约 2.83 万亿, 量比 1.87 [实测]", OUTS) == ["2.83万亿", "1.87"]


def test_untagged_lines_and_dates_ignored():
    assert U("市值 999.99 亿\n- 2026-10-13 收盘 370.48 [实测]", OUTS) == []


def test_no_tools_no_flags():
    assert U("- 价 1.23 [实测]", {}) == []
