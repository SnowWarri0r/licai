"""评测断言本身的测试。

首轮全绿的评测集不可信 —— 断言可能压根抓不住它声称防的 bug。这里把**历史上真实出现
过的错答**喂给 check(), 要求它红; 把修好之后的正确答法喂进去, 要求它绿。

错答文本尽量取自当时的实际输出(截图/日志), 不是我事后编的。
"""
from evals import cases as C

NO_TOOLS: list = []


# ── 美股盘前: 把上一时段收盘说成"现在" ──────────────────

GROUND_PRE = {"close": 216.0,
              "ext": {"label": "盘前", "price": 240.56, "change_pct": 11.37,
                      "as_of": "Aug 19 08:43AM EDT"}}

# 2026-08-19 界面上的实际输出(用户截图)
BAD_PRE = ('注意，你问"为什么涨"，但迈威尔现在不是涨，是大跌——当前价 216.00 美元，'
           "跌 7.82%（前收 234.32）。我先确认一下走势和近几天路径。")

GOOD_PRE = ("分两个时点看，方向正好相反：8/18 正式收盘 216.00 美元，跌 7.82%（相对前收 "
            "234.33）；8/19 盘前 240.56 美元，涨 11.37%（相对 8/18 收盘）。"
            "当下（盘前）是涨的。")


def test_premarket_check_catches_the_real_bad_answer():
    bad = C.check_us_premarket(BAD_PRE, ["get_quote"], GROUND_PRE)
    assert bad, "这条断言没抓住当初的错答"
    joined = " ".join(bad)
    assert "盘前是涨的却说'现在跌'" in joined
    assert "240.56" in joined          # 盘前价整个没出现


def test_premarket_check_passes_the_fixed_answer():
    assert C.check_us_premarket(GOOD_PRE, ["get_quote"], GROUND_PRE) == []


def test_premarket_check_catches_missing_session_label():
    """两个价都报了但不说哪个是盘前, 读者照样分不清。"""
    ans = "216.00 美元，跌 7.82%；另有 240.56 美元的报价。"
    bad = C.check_us_premarket(ans, ["get_quote"], GROUND_PRE)
    assert any("没点明这是盘前" in b for b in bad)


def test_premarket_number_tolerance_allows_rounding():
    """答案写 240.6 或带千分位都算命中, 不能因为小数位不同就误报。"""
    ans = "8/18 收盘 216.00 跌 7.82%；8/19 盘前 240.6，涨 11.4%。"
    assert C.check_us_premarket(ans, ["get_quote"], GROUND_PRE) == []


# ── 新闻新鲜度: 有 10 条就以为够了 ──────────────────────

def test_news_check_catches_stale_without_websearch():
    """2026-08-19 的原始形态: 查了新闻(最新 8-16), 没联网, 直接归因。"""
    g = {"latest": "2026-08-16 20:29:59", "today": "2026-08-19"}
    bad = C.check_us_news_freshness("涨是因为板块回暖。", ["get_quote", "get_news"], g)
    assert any("web_search" in b for b in bad)


def test_news_check_ok_when_news_itself_is_fresh():
    g = {"latest": "2026-08-19 20:38:12", "today": "2026-08-19"}
    assert C.check_us_news_freshness("催化是谷歌认股权证。", ["get_news"], g) == []


def test_news_check_ok_when_stale_but_searched():
    g = {"latest": "2026-08-16 20:29:59", "today": "2026-08-19"}
    assert C.check_us_news_freshness("催化是…", ["get_news", "web_search"], g) == []


def test_news_check_catches_no_news_at_all():
    g = {"latest": "", "today": "2026-08-19"}
    bad = C.check_us_news_freshness("我觉得是板块带动。", ["get_quote"], g)
    assert any("没查新闻" in b for b in bad)


# ── 公司状态凭记忆 ──────────────────────────────────────

def test_listed_check_catches_memory_assertion():
    bad = C.check_company_listed("长鑫科技目前尚未上市，属于一级市场标的。", NO_TOOLS, {})
    assert any("断言未上市" in b for b in bad)
    assert any("688825" in b for b in bad)
    assert any("没查代码表" in b for b in bad)


def test_listed_check_passes_when_grounded():
    ans = "长鑫科技(688825)已于 2026-07-27 上市，当前日成交额上百亿。"
    assert C.check_company_listed(ans, ["resolve_stock", "get_quote"], {}) == []


# ── 总盈亏口径 ──────────────────────────────────────────

def test_total_pnl_check_catches_float_only():
    """只报浮动盈亏 = 当初"场外收益都转正了为什么总账还是亏"那次算不清的原因。

    数字是合成的: 这是字符串匹配的单测, 真实盈亏没有理由进公开仓库。
    """
    bad = C.check_total_pnl("你当前持仓浮动亏损 2,000 元。", ["get_asset_allocation"],
                            {"total": -12000.0})
    assert any("已实现" in b for b in bad)
    assert any("总盈亏" in b for b in bad)


def test_total_pnl_check_passes_full_scope():
    ans = "总盈亏 -12,000 元 = 浮动 -2,000 + 已实现 -10,000（合成数据）。"
    assert C.check_total_pnl(ans, ["get_asset_allocation"], {"total": -12000.0}) == []


# ── 场内 ETF 归类 ───────────────────────────────────────

def test_onchain_check_catches_otc_mislabel():
    g = {"codes": ["512480", "512000"]}
    bad = C.check_onchain_etf("场外基金：512480 半导体ETF、512000 券商ETF。", NO_TOOLS, g)
    assert len(bad) == 2


def test_onchain_check_passes_correct_split():
    g = {"codes": ["512480"]}
    ans = "场内 ETF：512480 半导体ETF（券商买卖）。场外基金：某全球股票基金（T+1 申赎）。"
    assert C.check_onchain_etf(ans, NO_TOOLS, g) == []


# ── 指数成交额币种 ──────────────────────────────────────

def test_amount_check_catches_bare_number_and_fabricated_us():
    bad = C.check_index_amount_currency(
        "恒生今日成交额 2531.7 亿；纳斯达克成交额 22.8 万亿。", NO_TOOLS, {})
    assert any("港元" in b for b in bad)
    assert any("编了成交额" in b for b in bad)


def test_amount_check_passes_correct():
    ans = ("恒生成交额 2531.7 亿港元；KOSPI 成交额 23.12 万亿韩元；"
           "纳斯达克只披露成交股数 64.61 亿股，没有成交额。")
    assert C.check_index_amount_currency(ans, NO_TOOLS, {}) == []


# ── 全局检查 ────────────────────────────────────────────

def test_global_catches_raw_html_tag():
    """2026-08-19: 模型改用 HTML 强调, 界面上直接显示出 <mark>。"""
    bad = C.global_checks("<mark>为全球科技品牌客户提供端到端服务</mark>", NO_TOOLS)
    assert any("HTML" in b for b in bad)


def test_global_catches_trade_instruction():
    assert C.global_checks("这个位置建议买入，可以加仓。", NO_TOOLS)


def test_global_passes_clean_markdown():
    assert C.global_checks("**要点**：成交额 1.22 万亿，较昨日放量。", NO_TOOLS) == []
