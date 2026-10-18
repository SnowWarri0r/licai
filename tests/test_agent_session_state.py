"""追问时沿用上一轮解析出的代码(简称被重新解析成另一只同名票)。"""
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
