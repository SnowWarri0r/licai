"""追问时沿用上一轮解析出的代码(简称被重新解析成另一只同名票)。"""
import services.stock_agent as sa
from services.stock_agent import _harvest_entities, _seed_messages, _state_block


def test_harvest_keeps_user_alias_from_resolve():
    acc = {}
    tus = [{"name": "resolve_stock", "input": {"query": "医药"}},
           {"name": "get_quote", "input": {"code": "600519"}},
           {"name": "get_news", "input": {"code": "000001"}}]
    outs = [{"code": "512010", "name": "医药ETF华宝", "in_holdings": True},
            {"code": "600519", "name": "贵州茅台", "price": 370.48},
            {"error": "timeout"}]
    _harvest_entities(tus, outs, acc)
    assert acc["512010"] == {"code": "512010", "name": "医药ETF华宝", "q": "医药"}
    assert acc["600519"] == {"code": "600519", "name": "贵州茅台"}
    assert "000001" not in acc


def test_state_goes_before_question_in_user_message():
    st = {"entities": [{"code": "512010", "name": "医药ETF华宝", "q": "医药"}]}
    msgs = _seed_messages("医药今天分时怎么走的", [], None, st)
    blocks = msgs[-1]["content"]
    assert "医药ETF华宝(512010, 用户称'医药')" in blocks[0]["text"]
    assert blocks[-1]["text"] == "医药今天分时怎么走的"


def test_no_state_keeps_plain_string():
    assert _state_block(None) == ""
    assert _seed_messages("hi", [], None, None)[-1]["content"] == "hi"


def test_earlier_block_keeps_recent_turns_within_budget():
    turns = [{"q": f"问题{i}", "a": "答" * 2000} for i in range(10)]
    b = sa._state_block({"earlier": turns})
    assert "问题9" in b and "问题0" not in b          # 从最近的往前取
    assert "更早还有" in b
    assert len(b) < sa._EARLIER_BUDGET + 600
    assert b.index("问题8") < b.index("问题9")        # 列出时仍按时间顺序


def test_state_block_combines_earlier_and_entities():
    b = sa._state_block({"earlier": [{"q": "哪些少吸了", "a": "少吸 1200 股"}],
                         "entities": [{"code": "588080", "name": "科创50ETF"}]})
    assert "少吸 1200 股" in b and "588080" in b
    assert b.index("更早的对话") < b.index("已确认的标的")
