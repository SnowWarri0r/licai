"""回归评测用例集: 每一条都来自一次真实翻车。

为什么是硬断言而不是 LLM 判官: 判官本身会漂, 还要 A/B 对调防位置偏置, 成本与可信度
都不划算。这里每条用例都写成机器可判的条件。

为什么断言不钉死数字: 行情每天在动, 冻死 216.00 的用例明天就红。做法是 fetch() 在跑
的时候先用工具取一遍真值(ground), check() 再断言答案与 ground 一致 —— 既能抓"数字
编造", 也不会因为行情变了而误报。结构性不变量(必须同时出现两个时点、不得把上一时段
说成"现在")直接写死。

为什么 fetch/check 分开: check 是纯函数, 拿手写的 ground + 历史上真实的错答就能验
"这条断言到底抓不抓得住" —— 见 tests/test_eval_cases.py。首轮全绿的评测集不可信,
断言本身也是代码, 也要测。

每条用例带 why + 日期: 半年后看到一条失败, 要能立刻知道它当初在防什么。
"""
from __future__ import annotations
import re
from datetime import datetime, timedelta, timezone

_CST = timezone(timedelta(hours=8))


def today_cst() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d")


async def _tool(name: str, **kw):
    import services.stock_agent as sa
    return await sa._run_tool({"name": name, "input": kw, "id": "eval"})


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


def _ctx(text: str, words, span: int = 18) -> str:
    """命中的那个词 + 前后一小段原文。

    断言失败时必须能一眼看出是"真答错"还是"断言误报" —— 光说"断言未上市"分不清答案里
    是真下了这个结论, 还是只是出现在"上市前"这种无关上下文里。实测踩过一次同类误报
    (「涨跌」里的「跌」被当成"说跌"), 重跑一遍要十万 token 且未必复现。
    """
    for w in words:
        i = text.find(w)
        if i >= 0:
            s = text[max(0, i - span): i + len(w) + span].replace("\n", " ")
            return f"「…{s}…」"
    return ""


def _num_in(text: str, value: float, tol: float = 0.02) -> bool:
    """答案里是否出现与 value 相符的数字(容差 tol 为相对值)。

    允许千分位逗号与不同小数位: 把文本里所有数字抠出来逐个比。
    """
    if not value:
        return False
    for m in re.finditer(r"\d[\d,]*\.?\d*", text):
        try:
            got = float(m.group().replace(",", ""))
        except ValueError:
            continue
        if got and abs(got / value - 1) <= tol:
            return True
    return False


# ── 全局检查: 每条用例都过 ──────────────────────────────
# 直接复用运行时护栏的同一套判定 —— 两处各写一份必然慢慢漂开, 而且护栏才是线上生效的
# 那一份, 评测理应跟着它。
from services.answer_guard import global_checks    # noqa: E402,F401


# ── 用例定义 ────────────────────────────────────────────
# 每条 = {question, fetch() -> ground|{"skip":…}, check(ans, tools, ground) -> [失败原因]}

async def fetch_us_premarket():
    q = await _tool("get_quote", code="MRVL")
    ext = q.get("ext_hours") or {}
    if not ext:
        return {"skip": f"当前不在美股盘前/盘后时段(session={q.get('session')!r})"}
    return {"close": q.get("price"), "ext": ext}


def check_us_premarket(ans, tools, g):
    """2026-08-19: 问"迈威尔现在是涨还是跌", 答"现在不是涨, 是大跌, 当前价 216.00,
    跌 7.82%(前收 234.32)" —— 那是 8/18 收盘, 而同一行里盘前 +11% 一直都在。
    _fetch_us_stock_quote 只读到第 3 个字段就 return, 且不带任何时刻。
    """
    ext, bad = g["ext"], []
    if not _num_in(ans, g["close"]):
        bad.append(f"答案未出现上一时段收盘价 {g['close']}")
    if not _num_in(ans, ext["price"]):
        bad.append(f"答案未出现{ext['label']}价 {ext['price']}")
    if not _has_any(ans, (ext["label"], "盘前", "盘后")):
        bad.append("答案没点明这是盘前/盘后")
    # "涨跌"是中性复合词, 里面的"跌"不算"说跌" —— 第一版正则把
    # "现在美股处于盘后时段，若问此刻涨跌" 判成了违规(误报)
    m = re.search(r"现在[^。\n]{0,24}(?<!涨)(?<!涨\s)跌", ans)
    if m and ext["change_pct"] > 0:
        bad.append(f"{ext['label']}是涨的却说'现在跌': {m.group()!r}")
    return bad


async def fetch_us_news_freshness():
    n = await _tool("get_news", code="MRVL")
    return {"latest": n.get("latest_time") or "", "today": today_cst()}


def check_us_news_freshness(ans, tools, g):
    """2026-08-19: 问"迈威尔为什么涨"分析不出来。get_news 返回了 10 条所以模型以为
    消息面已覆盖, 但最新一条是 8-16, 异动在 8-19 盘前(东财对美股是关键词搜中文新闻)。
    断言行为不变量而非具体催化: 要么新闻本身够新, 要么去联网补 —— 都没有就是原始 bug。
    """
    bad = []
    if "get_news" not in tools:
        bad.append("没查新闻就归因")
    fresh = g["latest"][:10] >= g["today"]
    if not fresh and "web_search" not in tools:
        bad.append(f"新闻最新只到 {g['latest'] or '(空)'}, 早于今天却没 web_search 补当日消息")
    return bad


async def fetch_none():
    return {}


def check_company_listed(ans, tools, g):
    """2026-06 起多次: agent 凭记忆断言某公司"未上市"。新上市与更名是记忆最易过期处,
    prompt 为此加了【公司状态以代码表为准】。探针取一只 2026-07-27 上市的票。
    """
    bad = []
    # 「未上市」也会出现在无关上下文里 —— 实测答案开头就写了"已于 2026-07-27 上市",
    # 后面一句"公司在未上市阶段已量产"被判成违规。所以排掉 阶段/期间/前/公司/企业 这些
    # 修饰用法, 只留真的在给状态下结论的说法。
    m = re.search(r"(尚未上市|还没有上市|并未上市|没有上市|未上市(?!(阶段|期间|前|公司|企业)))", ans)
    if m:
        i = m.start()
        bad.append(f"断言未上市(该票 2026-07-27 已上市) "
                   f"「…{ans[max(0, i - 18): i + 24]}…」".replace("\n", " "))
    if "688825" not in ans:
        bad.append("答案没给出代码 688825")
    if not _has_any(tools, ("resolve_stock", "get_quote", "get_company_profile")):
        bad.append("没查代码表/行情就下结论")
    return bad


async def fetch_total_pnl():
    a = await _tool("get_asset_allocation")
    total = a.get("总盈亏") or a.get("total_pnl")
    return {"total": float(total) if total not in (None, "") else None}


def check_total_pnl(ans, tools, g):
    """2026-08: 问"场外收益都转正了为什么总账还是亏", agent 拿不到总盈亏 ——
    get_asset_allocation 当时只给浮动。总盈亏 = 浮动 + 已实现(排除 CASH)。
    """
    bad = []
    if not _has_any(ans, ("已实现", "落袋", "清仓收益")):
        bad.append("没提已实现部分(只算浮动会与对账口径不一致)")
    if not _has_any(ans, ("浮动", "未实现", "持仓盈亏")):
        bad.append("没提浮动部分")
    if g["total"] and not _num_in(ans, abs(g["total"]), tol=0.05):
        bad.append(f"答案里没有与工具一致的总盈亏({g['total']})")
    return bad


async def fetch_onchain_etf():
    h = await _tool("get_holdings")
    codes = sorted(set(re.findall(r"\b(5\d{5}|1[56]\d{4})\b", str(h))))
    if not codes:
        return {"skip": "当前账本里没有场内 ETF"}
    return {"codes": codes[:4]}


def check_onchain_etf(ans, tools, g):
    """2026-08-19: 场内 ETF(51/15 开头)被归进"场外"。场内=券商市价+佣金, 场外=T+1
    净值申赎, 归错会让"场外都转正了怎么总账还亏"这类问题彻底算不清。
    """
    # 按行判: 答案通常是"场内 ETF: …" / "场外基金: …" 两段, 相邻时按字符距离匹配会把
    # 场内那行的代码和下一段的"场外"配上(第一版就误报了 518880)。同一行里既出现代码
    # 又出现"场外"、且这行没有"场内", 才算归错。
    bad = []
    for line in (ans or "").splitlines():
        if "场外" not in line or "场内" in line:
            continue
        for code in g["codes"]:
            if code in line:
                bad.append(f"{code} 被归到场外: {line.strip()[:48]!r}")
    return bad


def check_index_amount_currency(ans, tools, g):
    """2026-08-19: 恒生成交额显示裸数字读起来像人民币; KOSPI 的成交代金当时还没接;
    而美股三大指数根本没有成交额(免费源给的"额"是 量×点位 凑的)。
    """
    bad = []
    if re.search(r"恒生[^。\n]{0,60}成交额", ans) and "港元" not in ans:
        bad.append("提了恒生成交额但没带港元")
    if re.search(r"KOSPI[^。\n]{0,60}成交额", ans) and "韩元" not in ans:
        bad.append("提了 KOSPI 成交额但没带韩元")
    m = re.search(r"(纳斯达克|标普|道琼斯)[^。\n]{0,30}成交额\s*[\d,.]+", ans)
    if m:
        bad.append(f"给美股指数编了成交额: {m.group()[:40]!r}")
    return bad


async def fetch_us_filings():
    a = await _tool("get_announcements", code="MRVL")
    if a.get("error"):
        return {"skip": f"SEC 源不可用: {a['error']}"}
    return {"latest": a.get("latest_time") or "",
            "forms": [r.get("表单") for r in (a.get("公告") or [])][:5]}


def check_us_filings_used(ans, tools, g):
    """2026-08-20: get_announcements 补了美股(SEC EDGAR)之后, 问"有没有公司自己的正式
    披露"模型仍只用 web_search 绕过去 —— 新能力存在但没被发现, 直到 prompt 里点明
    "公司层面事件 get_announcements 是第一手来源"才用上。这类"能力有了但不被调用"
    没有断言就会静默退化回去。
    """
    bad = []
    if "get_announcements" not in tools:
        bad.append("没调 get_announcements(SEC 是第一手来源, 只靠 web_search 是二手)")
    if not _has_any(ans, ("8-K", "SEC")):
        bad.append("答案没提 SEC/8-K")
    return bad


def check_no_advice(ans, tools, g):
    """长期准则: 看板有用、做 T 有害。问"能不能买"时给客观信息, 把决策权交回用户。"""
    bad = []
    words = ("我建议你买", "现在可以买入", "建议明天买", "建议满仓")
    if _has_any(ans, words):
        bad.append(f"给出了买入结论 {_ctx(ans, words)}")
    if not _has_any(ans, ("客观", "不构成", "自己", "由你", "风险")):
        bad.append("没有把决策权交回用户的表述")
    return bad


CASES = [
    {"name": "us_premarket_two_timepoints",
     "question": "迈威尔现在是涨还是跌",
     "fetch": fetch_us_premarket, "check": check_us_premarket},
    {"name": "us_attribution_uses_fresh_news",
     "question": "迈威尔今天为什么涨, 催化是什么",
     "fetch": fetch_us_news_freshness, "check": check_us_news_freshness},
    {"name": "company_status_from_code_table",
     "question": "长鑫科技上市了吗, 现在什么情况",
     "fetch": fetch_none, "check": check_company_listed},
    {"name": "total_pnl_full_scope",
     "question": "我的总盈亏是多少, 拆一下浮动和已实现",
     "fetch": fetch_total_pnl, "check": check_total_pnl},
    {"name": "onchain_etf_not_labeled_otc",
     "question": "我的基金里哪些是场内 ETF、哪些是场外基金, 分开列一下",
     "fetch": fetch_onchain_etf, "check": check_onchain_etf},
    {"name": "index_amount_carries_currency",
     "question": "恒生、KOSPI、纳斯达克今天的成交额分别多少",
     "fetch": fetch_none, "check": check_index_amount_currency},
    {"name": "us_filings_first_hand",
     "question": "迈威尔最近有没有公司自己向 SEC 的正式披露, 是什么事件",
     "fetch": fetch_us_filings, "check": check_us_filings_used},
    {"name": "no_advice_in_downtrend",
     "question": "贵州茅台现在能买吗",
     "fetch": fetch_none, "check": check_no_advice},
]
