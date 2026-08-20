"""公司简介的两条口径: UI 要全文, 喂模型的按句截。

起因: prof[:400] 写在取数层, 而那个函数同时喂 agent 工具和 /api/market/company
(K 线头部的公司简介)。于是前端点开「完整简介」看到的也是切一半的 ——
实测石药创新 647 字丢 247、工业富联 688 字丢 288, 且切在句子中间
("公司为百事可乐、可口可")。
"""
from services.stock_agent import clip_profile, _PROFILE_TOOL_CHARS


def test_short_profile_untouched():
    """短简介(茅台 265 字)不该被加任何标记。"""
    t = "贵州茅台酒股份有限公司主要从事茅台酒及系列酒的生产与销售。" * 2
    assert len(t) < _PROFILE_TOOL_CHARS
    assert clip_profile(t) == t


def test_clip_lands_on_sentence_end():
    """在 limit 之前的最后一个句号处断开, 不留残句。"""
    body = "第一句话内容比较长用来占位。" * 10          # 每句 14 字
    out = clip_profile(body, limit=100)
    head = out.split("…")[0]
    assert head.endswith("。"), head[-12:]
    assert len(head) <= 100


def test_clip_marks_total_length():
    """要显式告诉模型"这是截断的、原文多长", 否则它会当成全文来判断。"""
    body = "甲乙丙丁。" * 200
    out = clip_profile(body, limit=120)
    assert "已截断" in out
    assert str(len(body)) in out


def test_clip_falls_back_when_no_sentence_end():
    """整段没有句号(有些简介是一长串逗号)时退回硬切, 但不留悬空的逗号。"""
    body = "甲，乙，丙，" * 100
    out = clip_profile(body, limit=60)
    head = out.split("…")[0]
    assert len(head) <= 60
    assert not head.endswith("，")


def test_clip_ignores_too_early_sentence_end():
    """句号出现在很靠前的位置时不能用它 —— 否则 400 字的额度只用了几十字。"""
    body = "短。" + "后面是一整段没有句号的很长内容" * 20
    out = clip_profile(body, limit=200)
    head = out.split("…")[0]
    assert len(head) > 100, f"截得太短: {len(head)}"


def test_clip_handles_empty():
    assert clip_profile("") == ""
    assert clip_profile(None) == ""


def test_default_limit_is_the_agent_budget():
    """默认上限就是喂模型那一侧的预算; UI 侧不该走这个函数。"""
    assert _PROFILE_TOOL_CHARS == 400
    long_text = "一二三四五六七八九十。" * 60
    assert len(clip_profile(long_text)) < len(long_text)
