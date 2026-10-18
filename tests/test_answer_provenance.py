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
    assert U("- 总市值约 2.83 万亿[实测], 量比 1.87 [实测]", OUTS) == ["2.83万亿", "1.87"]


def test_untagged_lines_and_dates_ignored():
    assert U("市值 999.99 亿\n- 2026-10-13 收盘 370.48 [实测]", OUTS) == []


def test_no_tools_no_flags():
    assert U("- 价 1.23 [实测]", {}) == []


def test_required_lookups_by_loaded_guide():
    from services.answer_guard import missing_lookups
    outs = {"load_guide": [{"guides": {"intraday": "...", "stock": "..."}}]}
    bad = missing_lookups(["load_guide", "get_quote", "get_trend"], outs)
    assert len(bad) == 1 and "get_intraday" in bad[0]
    assert missing_lookups(["load_guide", "get_quote", "get_trend", "get_intraday"], outs) == []
    assert missing_lookups(["get_quote"], {}) == []


def test_only_the_clause_before_tag_is_checked():
    """2026-10-13 评测里 4 份被标记的回答全是同一行别的分句的数(联网/规则常数/复述上一轮)。"""
    ans = ("- 行权价是 206.58 美元,迈威尔科技昨收 370.48 美元[实测],目前行权价低于股价。\n"
           "- 盘口印证 [实测]：按 20% 算, 涨停价 1.038\n"
           "你现在涨了 **2.29%**[实测], 上一轮给的 +2,058 元只是 A 股")
    assert U(ans, OUTS) == []
    assert U("今天量比 1.87[实测]", OUTS) == ["1.87"]
