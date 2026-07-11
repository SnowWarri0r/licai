from fastapi.testclient import TestClient
import services.llm_client as llm
from run import app

client = TestClient(app)


def test_interpret_returns_three_parts(monkeypatch):
    calls = {"n": 0}
    def fake(user_prompt, system=None, model=None, max_tokens=600):
        calls["n"] += 1
        return '{"what":"讲了降准","why":"利好流动性","relation":"你持有银行股受益"}'
    monkeypatch.setattr(llm, "call_claude", fake)
    r = client.post("/api/news/interpret", json={"title": "央行降准", "content": "全文片段", "code": "601398", "name": "工商银行"})
    assert r.status_code == 200
    d = r.json()
    assert d["what"] and d["why"] and d["relation"]
    assert calls["n"] == 1
    r2 = client.post("/api/news/interpret", json={"title": "央行降准", "content": "全文片段", "code": "601398", "name": "工商银行"})
    assert r2.status_code == 200 and r2.json().get("cached") is True
    assert calls["n"] == 1


def test_interpret_llm_error_graceful(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no creds")
    monkeypatch.setattr(llm, "call_claude", boom)
    r = client.post("/api/news/interpret", json={"title": "另一条新闻xyz", "content": "x"})
    assert r.status_code == 200
    assert r.json().get("error")


def test_interpret_non_json_fallback(monkeypatch):
    monkeypatch.setattr(llm, "call_claude", lambda *a, **k: "这不是JSON只是一段话")
    r = client.post("/api/news/interpret", json={"title": "标题abc", "content": "y"})
    assert r.status_code == 200
    d = r.json()
    assert d["what"]


def test_skip_page_head_kills_site_nav():
    """东财风格页头: 栏目菜单(长行短词并排)/数据中心菜单/面包屑 全部砍掉, 正文保留。"""
    from api.news_routes import _skip_page_head, _trim_article_tail
    md = (
        "东方财富网\n\n"
        "指数 期指 期权 个股 板块 排行 新股 基金 港股 美股 期货 外汇 黄金 自选股 自选基金\n\n"
        "数据中心\n\n"
        "资金流向 主力排名 板块资金 个股研报 新股申购 转债申购 北交所申购 AH股比价 年报大全 融资融券 龙虎榜 限售解禁 IPO审核 大宗交易 估值分析\n\n"
        "首页 >\n\n财经频道 >\n\n正文\n\n"
        "近期,人民币对美元汇率持续走强,多家外资机构表示看好中国资产的长期配置价值,认为估值仍处低位。\n\n"
        "业内人士认为,随着政策面持续发力,市场信心正在逐步恢复,北向资金近一个月净流入超过500亿元,后续配置窗口仍在。\n\n"
        "相关阅读\n\n外资加速流入A股\n"
    )
    out = _trim_article_tail(_skip_page_head(md).strip())
    assert "指数 期指" not in out
    assert "资金流向 主力排名" not in out
    assert "首页 >" not in out
    assert "相关阅读" not in out
    assert out.startswith("近期,人民币对美元汇率持续走强")


def test_skip_page_head_h1_anchor_and_prose_safety():
    from api.news_routes import _skip_page_head, _is_nav_line
    # H1 锚点优先
    assert _skip_page_head("字体\n分享\n# 正文标题\n正文内容").startswith("# 正文标题")
    # 短但带句读的正文行不算导航
    assert not _is_nav_line("央行今日宣布:降准0.5个百分点。")
    assert not _is_nav_line("特朗普表示,关税将于下周生效")


def test_is_nav_line_short_punct_noise():
    """东财页头的'方便，快捷/提示：/小中大'带句读也是噪声; 极短行一律按噪声跳。"""
    from api.news_routes import _is_nav_line
    assert _is_nav_line("方便，快捷")
    assert _is_nav_line("提示：")
    assert _is_nav_line("小中大")
    assert _is_nav_line("朋友圈")
    # 长度够的真句子仍放行
    assert not _is_nav_line("央行今日宣布:降准0.5个百分点。")


def test_trim_tail_line_anchored_no_midbody_cut():
    """正文句中提到'微信公众号/热门排行'不截断; 自成一行的尾部区块照截。"""
    from api.news_routes import _trim_article_tail
    body = (
        "某公司宣布，其微信公众号粉丝突破千万，并登上平台热门排行榜首，"
        "带动相关概念股走强。分析人士认为，私域流量价值正在被市场重新定价，"
        "后续需观察其商业化兑现节奏与广告加载率的平衡。\n\n"
        "公司还表示，将在三季度推出新的会员体系。\n\n"
        "热门排行\n\n1. 某某股票大涨\n2. 另一只票跌停\n"
    )
    out = _trim_article_tail(body)
    assert "微信公众号粉丝突破千万" in out          # 句中提及保留
    assert "会员体系" in out                        # 正文完整
    assert "某某股票大涨" not in out                 # 行首'热门排行'区块截掉
