"""LLM 摘要解析(截断/包装/前后多余文字) 与 ETF 透视在东财 clist 被限流时的兜底源。"""
from api.news_routes import _parse_digest
from services import etf_xray as X

FULL = '{"summary": "摘要", "highlights": [{"level": "good", "title": "A", "related": "600000", "impact": "x"}]}'


def test_parse_full_and_wrapped():
    assert _parse_digest(FULL) == ("摘要", [{"level": "good", "title": "A", "related": "600000", "impact": "x"}])
    assert _parse_digest("```json\n" + FULL + "\n```")[0] == "摘要"
    assert _parse_digest("以下是摘要:\n" + FULL + "\n以上。")[1][0]["title"] == "A"


def test_parse_truncated_salvages_summary_and_complete_items():
    raw = ('{ "summary": "福莱新材受机器人概念带动活跃", "highlights": [ { "level": "good", "title": "第一条", '
           '"related": "605488", "impact": "偏强" }, { "level": "good", "title": "第二条", "related": "60548')
    summary, hl = _parse_digest(raw)
    assert summary == "福莱新材受机器人概念带动活跃"
    assert [h["title"] for h in hl] == ["第一条"]


def test_parse_garbage():
    assert _parse_digest("") == ("", [])
    assert _parse_digest("模型没按格式回答") == ("", [])
    assert _parse_digest('{ "summ') == ("", [])


def test_xray_fallbacks_when_clist_empty(monkeypatch):
    X._cache.clear()
    monkeypatch.setattr(X, "_clist", lambda *a, **k: [])
    monkeypatch.setattr(X, "_datacenter_industry_map", lambda: {"688347": ("华虹宏力", "半导体", 3192.0)})
    monkeypatch.setattr(X, "_sina_etf_list", lambda: [{"code": "588810", "name": "科创芯片ETF", "规模亿": 17.6,
                                                       "成交额亿": 1.07, "今日%": 2.8}])
    assert X.industry_map()["688347"][1] == "半导体"
    assert X.etf_universe()[0]["code"] == "588810"
    X._cache.clear()
