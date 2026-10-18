"""工具轮里写出的成段正文要接进最终答案(2026-10-13 评测: 答案从半句话开始)。"""
from services.stock_agent import _join_answer


def test_carried_text_prefixed():
    head = "已上市, 科创板。" + "主营 DRAM。" * 30
    out = _join_answer([head], "(总股本按约602亿股估算…)")
    assert out.startswith("已上市") and out.endswith("估算…)")


def test_no_duplicate_when_final_restates():
    head = "已上市, 科创板。" + "主营 DRAM。" * 30
    assert _join_answer([head], head + "\n补充: 市值核算无误。").count("已上市") == 1


def test_no_carry():
    assert _join_answer([], "答案") == "答案"
