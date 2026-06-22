"""问股票为什么涨/跌 — 挂工具的小 agent。

LLM 自己决定调哪些工具(查行情/走势/新闻/持仓/大盘情绪)拿数据, 再总结涨跌原因。
自由问答。硬规则: 只做客观解读, 严禁任何买卖/操作建议。
"""
from __future__ import annotations
import asyncio
import json as _json
import re as _re
import time as _time

import services.llm_client as _llm

_MODEL = "claude-opus-4-8"
_MAX_ROUNDS = 8

# A 股 代码↔名称 表 (akshare, 缓存 12h, 供按名字解析)
_code_name_cache: tuple[dict, dict, float] | None = None


def _load_a_code_name_sync():
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_info_a_code_name()
    name2code, code2name = {}, {}
    for _, r in df.iterrows():
        code = str(r.get("code") or "").strip()
        name = str(r.get("name") or "").strip()
        if code and name:
            name2code[name] = code
            code2name[code] = name
    return name2code, code2name


async def _code_name_maps():
    global _code_name_cache
    if _code_name_cache and _time.time() - _code_name_cache[2] < 43200:
        return _code_name_cache[0], _code_name_cache[1]
    try:
        n2c, c2n = await asyncio.to_thread(_load_a_code_name_sync)
        _code_name_cache = (n2c, c2n, _time.time())
        return n2c, c2n
    except Exception:
        return ({}, {}) if not _code_name_cache else (_code_name_cache[0], _code_name_cache[1])


# ---------------------------------------------------------------------------
# 工具执行器 (都返回可 JSON 序列化的 dict/list)
# ---------------------------------------------------------------------------

def _norm_code(code: str) -> str:
    """LLM 常给 A 股带 sh/sz 前缀(如 sh600667), 但行情接口要裸 6 位代码; 这里剥掉前缀。
    HK./US. 这类保持原样。"""
    c = (code or "").strip()
    m = _re.match(r"^(?:sh|sz|SH|SZ)(\d{6})$", c)
    return m.group(1) if m else c

async def _tool_resolve_stock(query: str) -> dict:
    """名字或代码 → 标准代码 + 名称。先查持仓, 再查 A 股全表。"""
    from database import get_all_holdings
    q = (query or "").strip()
    if not q:
        return {"error": "空查询"}
    # 1) 持仓里匹配 (名字/代码 子串)
    try:
        for h in await get_all_holdings():
            nm = h.get("stock_name") or ""
            cd = h.get("stock_code") or ""
            if q == nm or q == cd or (q in nm) or (q in cd):
                return {"code": cd, "name": nm, "in_holdings": True}
    except Exception:
        pass
    # 2) A 股全表
    n2c, c2n = await _code_name_maps()
    if q in c2n:
        return {"code": q, "name": c2n[q], "in_holdings": False}
    if q in n2c:
        return {"code": n2c[q], "name": q, "in_holdings": False}
    hits = [(nm, cd) for nm, cd in n2c.items() if q in nm][:5]
    if hits:
        return {"candidates": [{"name": nm, "code": cd} for nm, cd in hits]}
    return {"error": f"找不到 {q}"}


async def _tool_get_quote(code: str) -> dict:
    from services.market_data import get_realtime_quotes, normalize_stock_code, get_stock_name
    code = normalize_stock_code(_norm_code(code))
    q = (await get_realtime_quotes([code])).get(code)
    if not q:
        return {"error": f"{code} 无行情"}
    name = ""
    try:
        name = await get_stock_name(code)
    except Exception:
        pass
    return {
        "code": code, "name": name,
        "price": q.get("price"), "change_pct": q.get("change_pct"),
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "prev_close": q.get("prev_close"), "amount": q.get("amount"),
        "turnover_rate": q.get("turnover_rate"),
    }


async def _tool_get_trend(code: str, days: int = 20) -> dict:
    """近 N 日走势: 每日涨跌幅 + 累计。仅 A 股(走新浪历史)。"""
    from services.market_data import get_historical_data, normalize_stock_code, is_a_share
    code = normalize_stock_code(_norm_code(code))
    if not is_a_share(code):
        return {"error": "走势仅支持 A 股"}
    days = max(5, min(int(days or 20), 60))
    df = await get_historical_data(code, days + 5)
    if df is None or df.empty:
        return {"error": "无历史数据"}
    closes = [float(x) for x in df["收盘"].tolist()][-(days + 1):]
    if len(closes) < 2:
        return {"error": "数据不足"}
    daily = [round((closes[i] / closes[i - 1] - 1) * 100, 2) for i in range(1, len(closes))]
    cum = round((closes[-1] / closes[0] - 1) * 100, 2)
    up = sum(1 for d in daily if d > 0)
    return {
        "code": code, "days": len(daily),
        "cum_pct": cum, "up_days": up, "down_days": len(daily) - up,
        "last_close": round(closes[-1], 3),
        "daily_pct": daily[-min(10, len(daily)):],  # 最近 10 日逐日涨跌
    }


async def _tool_get_news(code: str) -> dict:
    """个股最近新闻 (akshare 东财, A 股)。"""
    from api.news_routes import _fetch_stock_news_em_sync
    from services.market_data import normalize_stock_code
    raw = normalize_stock_code(_norm_code(code))
    bare = raw.split(".")[-1] if "." in raw else raw
    items = await asyncio.to_thread(_fetch_stock_news_em_sync, bare)
    if not items:
        return {"news": [], "note": "无个股新闻 (东财仅 A 股)"}
    return {"news": [{"title": it["title"], "summary": it["content"][:140],
                      "time": it["time"], "source": it["source"]} for it in items[:10]]}


async def _tool_get_holdings() -> dict:
    from database import get_all_holdings
    try:
        hs = await get_all_holdings()
        return {"holdings": [{"code": h.get("stock_code"), "name": h.get("stock_name"),
                              "shares": h.get("shares")} for h in hs if float(h.get("shares") or 0) > 0]}
    except Exception as e:
        return {"error": str(e)}


async def _tool_sector_momentum(days: int = 10) -> dict:
    """板块趋势矩阵: 各行业近 N 日累计涨跌/连涨动能/净流入 → 看动量是否延续(动量风格) 还是冲高回落(退潮/反转)。"""
    try:
        from services.sector_matrix import get_sector_matrix
        m = await get_sector_matrix(days=int(days or 10))
        rows = m.get("rows") or []
        if not rows:
            return {"error": "板块矩阵暂无数据"}
        def brief(r):
            return {"板块": r["name"], "今日": r.get("today_pct"), f"近{m.get('days')}日累计": r.get("cum_pct"),
                    "连涨天": r.get("streak"), "净流入亿": r.get("net_inflow")}
        return {"days": m.get("days"), "intraday": m.get("intraday"),
                "走强top": [brief(r) for r in rows[:8]],
                "退潮bottom": [brief(r) for r in rows[-5:]]}
    except Exception as e:
        return {"error": str(e)}


_concept_cache: dict = {}


def _fetch_hot_concepts_sync(top: int = 15) -> list[dict]:
    """今日东财概念板块涨幅榜(带主力净流入)。这是 量化/游资正在冲的'概念'粒度
    (如 CPO/HBM/先进封装/玻璃基板…), 比行业级更细。
    akshare 走死分片 79.push2 被墙 → 直连可达 host(push2delay 优先)+ 重试轮换。"""
    import requests as _rq
    import time as _t
    ck = f"concepts_{top}"
    c = _concept_cache.get(ck)
    if c and _t.time() - c[1] < 300:
        return c[0]
    hosts = ["push2delay.eastmoney.com", "push2.eastmoney.com",
             "1.push2.eastmoney.com", "50.push2.eastmoney.com"]
    params = {"pn": "1", "pz": str(max(top, 30)), "po": "1", "np": "1", "fltt": "2",
              "invt": "2", "fid": "f3", "fs": "m:90 t:3",
              "fields": "f12,f14,f3,f62,f104,f105"}
    for i in range(12):
        host = hosts[i % len(hosts)]
        try:
            r = _rq.get(f"https://{host}/api/qt/clist/get", params=params, timeout=7)
            diff = (r.json().get("data") or {}).get("diff")
            if diff:
                out = []
                for x in diff[:top]:
                    try:
                        out.append({"概念": x.get("f14"), "涨跌幅": float(x.get("f3")),
                                    "主力净流入亿": round(float(x.get("f62") or 0) / 1e8, 2),
                                    "涨家": x.get("f104"), "跌家": x.get("f105")})
                    except (ValueError, TypeError):
                        continue
                if out:
                    _concept_cache[ck] = (out, _t.time())
                    return out
        except Exception:
            _t.sleep(0.3)
    return []


async def _tool_hot_concepts(top: int = 15) -> dict:
    """今日热门概念榜(概念粒度, 比行业细): 涨幅 + 主力净流入。看量化/资金在冲哪个具体概念。"""
    out = await asyncio.to_thread(_fetch_hot_concepts_sync, int(top or 15))
    if not out:
        return {"error": "概念榜暂不可达(东财源抖动), 请改用行业级 get_sector_momentum"}
    return {"top_concepts": out, "note": "按今日涨幅排序; 主力净流入正=资金流入"}


async def _tool_hot_rank() -> dict:
    """资金人气榜(东财): 资金/散户关注度最高的个股, 标出哪些在用户持仓里。看资金主线/抱团方向。"""
    try:
        from api.market_routes import hot_rank
        r = await hot_rank(top=20)
        items = [{"name": x.get("name"), "code": x.get("code"), "rank": x.get("rank"),
                  "mine": x.get("mine")} for x in (r.get("items") or [])]
        return {"top": items, "mine": [x.get("name") for x in (r.get("mine") or [])]}
    except Exception as e:
        return {"error": str(e)}


_POLICY_KW = [
    "央行", "降准", "降息", "逆回购", "MLF", "LPR", "国债", "专项债", "财政", "货币政策",
    "证监会", "银保监", "金融监管", "国常会", "国务院", "政治局", "发改委", "工信部", "部委",
    "政策", "监管", "调控", "刺激", "新政", "出口管制", "关税", "制裁", "实体清单",
    "反垄断", "反内卷", "供给侧", "去产能", "收储", "汇率", "稳增长", "会议", "规划", "意见",
]


async def _tool_market_news(limit: int = 40) -> dict:
    """全市场财经快讯(东财+财联社+同花顺), 含政策面/国家调控。用来把宏观政策、监管动向、
    产业政策、央行财政、重要会议等市场背景因素纳入分析。policy_news 是按关键词筛出的政策相关条目。"""
    try:
        from api.news_routes import market_news
        mn = await market_news()
        items = (mn.get("items") or [])[:max(limit, 40)]
        def is_pol(t):
            return any(k in (t or "") for k in _POLICY_KW)
        heads = [{"title": it.get("title"), "time": it.get("time"), "source": it.get("source")} for it in items]
        policy = [h for h in heads if is_pol(h["title"])]
        return {"policy_news": policy[:18], "headlines": heads[:limit], "note": "policy_news=政策/调控相关筛选; headlines=全部要闻(时间倒序)"}
    except Exception as e:
        return {"error": str(e)}


async def _tool_market_sentiment() -> dict:
    try:
        from api.market_routes import market_sentiment
        s = await market_sentiment()
        return {"mood": s.get("mood"), "mood_desc": s.get("mood_desc"),
                "n_zt": s.get("n_zt"), "n_dt": s.get("n_dt"), "zbl_rate": s.get("zbl_rate"),
                "max_lianban": s.get("max_lianban"), "money_effect": s.get("money_effect"),
                "hot_sectors": [h.get("name") for h in (s.get("hot_sectors") or [])[:6]]}
    except Exception as e:
        return {"error": str(e)}


_TOOLS = [
    {"name": "resolve_stock", "description": "把股票名字或代码解析成标准代码+名称。用户报名字(如'中钨高新')时先调它拿代码。",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "股票名字或代码"}}, "required": ["query"]}},
    {"name": "get_quote", "description": "查个股实时行情: 现价/当日涨跌幅/开高低/成交额/换手。code 直接用 resolve_stock 返回的 code 原样传(A股是裸6位如 600667 / 000657; 港美股 HK.00700 / US.AAPL), 不要自己加 sh/sz 前缀。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_trend", "description": "查个股近 N 个交易日走势: 累计涨跌/逐日涨跌/上涨天数。仅 A 股。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}, "days": {"type": "integer", "description": "默认20"}}, "required": ["code"]}},
    {"name": "get_news", "description": "查个股最近新闻(标题+摘要+时间), 用来找涨跌的消息面原因。仅 A 股(东财)。",
     "input_schema": {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]}},
    {"name": "get_holdings", "description": "查用户当前持仓列表(代码/名称/股数), 用于回答跟用户持仓的关系。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_market_sentiment", "description": "查大盘打板情绪(涨停数/连板高度/炸板率/赚钱效应/热点板块), 判断是个股原因还是大盘普涨普跌; 也用于判断市场风格(打板赚钱效应高=追涨/动量有效; 炸板率高+亏钱效应=高位分歧/反转)。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_sector_momentum", "description": "板块趋势矩阵: 各行业近N日累计涨跌/连涨动能/净流入。看哪些板块在持续走强(动量延续)、哪些冲高回落(退潮), 判断市场是动量风格还是高低切/轮动, 资金主线在哪。days 默认10。",
     "input_schema": {"type": "object", "properties": {"days": {"type": "integer"}}}},
    {"name": "get_hot_rank", "description": "资金人气榜(东财): 关注度最高的个股, 标出哪些在用户持仓。看资金主线/抱团方向。",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_hot_concepts", "description": "今日热门概念板块榜(概念粒度, 比行业更细, 如 CPO/HBM/先进封装/玻璃基板/固态电池等): 涨幅+主力净流入。回答'量化/资金这几天在冲哪个具体概念、概念怎么切'时用它。",
     "input_schema": {"type": "object", "properties": {"top": {"type": "integer", "description": "默认15"}}}},
    {"name": "get_market_news", "description": "全市场财经快讯(含政策面/国家调控: 货币财政、央行、证监会/部委监管、产业政策、行业调控、出口管制/关税、国常会/政治局等重要会议)。分析市场背景、判断政策驱动/调控影响时必看; policy_news 是政策相关筛选。",
     "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "description": "默认40"}}}},
    # Anthropic 服务端联网搜索: 碰到本地工具查不到/可能过期的事实(海外公司是否上市/IPO/代码/政策/最新消息)用它核实, 不要凭记忆嘴硬。
    {"type": "web_search_20250305", "name": "web_search", "max_uses": 4},
]

_EXECUTORS = {
    "resolve_stock": lambda a: _tool_resolve_stock(a.get("query", "")),
    "get_quote": lambda a: _tool_get_quote(a.get("code", "")),
    "get_trend": lambda a: _tool_get_trend(a.get("code", ""), a.get("days", 20)),
    "get_news": lambda a: _tool_get_news(a.get("code", "")),
    "get_holdings": lambda a: _tool_get_holdings(),
    "get_market_sentiment": lambda a: _tool_market_sentiment(),
    "get_sector_momentum": lambda a: _tool_sector_momentum(a.get("days", 10)),
    "get_hot_rank": lambda a: _tool_hot_rank(),
    "get_hot_concepts": lambda a: _tool_hot_concepts(a.get("top", 15)),
    "get_market_news": lambda a: _tool_market_news(a.get("limit", 40)),
}

_SYSTEM = (
    "你是市场&个股解读助手。用户自由提问: 个股为什么涨跌/消息面/跟持仓关系, 以及【市场风格】类问题"
    "(这周市场在奖励什么打法、是动量追涨还是低吸反转、是题材轮动还是抱团、高低切迹象、资金主线在哪、情绪处在什么周期)。\n"
    "工具: resolve_stock(名字转代码)、get_quote(个股实时行情)、get_trend(个股近N日走势)、get_news(个股新闻)、"
    "get_holdings(用户持仓)、get_market_sentiment(大盘打板情绪)、get_sector_momentum(板块趋势矩阵:动量/退潮/资金流)、get_hot_rank(资金人气榜)。\n"
    "【个股问题】先 resolve_stock 拿代码, 再 get_quote+get_trend+get_news, 需要时 get_market_sentiment 判断个股事件还是大盘普涨跌。\n"
    "【市场风格问题】用 get_market_sentiment(打板赚钱效应高=追涨/动量有效; 炸板率高+亏钱效应=高位分歧/反转占优) + "
    "get_sector_momentum(连涨板块多=动量延续; 普遍冲高回落=退潮/高低切) + get_hot_concepts(概念主攻) + get_hot_rank(资金主线/抱团) 综合判断, "
    "用具体数字描述'市场这周在奖励什么行为、惩罚什么行为、资金往哪走'。这是客观的市场逻辑分析, 不是策略推荐。\n"
    "【政策面/国家调控——市场背景必看】分析市场背景、或个股/板块异动疑似政策驱动时, 必须调 get_market_news 看政策面"
    "(货币/财政: 降准降息/LPR/逆回购/专项债; 监管: 证监会/部委/反垄断/平台经济; 产业政策与行业调控: 收储/去产能/反内卷/限价/补贴; "
    "地缘: 出口管制/关税/制裁/实体清单; 重要会议: 国常会/政治局/发改委部署), 必要时再 web_search 补最新政策细节。"
    "要点出'这波行情/这个板块背后有没有政策催化或调控压制'(如 收储拉动有色、AI/算力产业政策、地产/化债/反内卷、关税扰动出口链), 用快讯标题/日期举证。\n"
    "【分析框架·一线打板资金视角】(客观套用, 不点名出处, 不据此给操作建议):\n"
    "  · 量化/游资以【板块/概念】为维度运作, 不是单票。判断市场=判断资金这几天在冲哪个板块概念、节奏多快"
    "(概念可能一两天就切, 如从 A 概念直接换到 B 概念)。要找出资金主线板块 + 有没有概念轮动切换。\n"
    "  · 概念粒度优先用 get_hot_concepts(能拿到 CPO/HBM/先进封装/玻璃基板 这种具体概念名 + 主力净流入), "
    "它比 get_sector_momentum 的行业级更细, 正是判断'量化在冲哪个概念'的关键; 两个结合看(概念找主攻方向, 行业动量看延续性)。\n"
    "  · 个股位置分层看'看逻辑 vs 纯资金博弈': 短线打板股 3板以下看逻辑(题材/催化/空间扎不扎实)、3板以上逻辑让位转纯资金接力; "
    "趋势股 涨幅1倍(100%)以内看逻辑、超1倍转纯资金博弈。即低位看逻辑、高位看资金, 点出领涨标的当前在哪一段。\n"
    "  · 据此描述: 资金的板块主线、概念切换的轮动节奏、领涨票在'看逻辑'还是'资金博弈'区。\n"
    "  · 数据粒度: get_hot_concepts 给到概念级(今日榜), get_sector_momentum 给行业级近N日动量, 配合用。"
    "概念榜是当日快照, '这几天怎么切'的多日轨迹要结合行业动量推断; 概念榜偶发不可达(东财抖动)时就退回行业级, 并说明。绝不硬编榜上没有的概念名。\n"
    "每个结论都要有工具数据支撑。\n"
    "【硬规则】只做客观解读与市场逻辑分析(市场在奖励什么/为什么动/什么消息), 严禁任何面向用户的操作建议: "
    "不许出现 你该买/该卖/该用XX策略去操作/加仓/减仓/能不能追/还能不能拿/目标价/止损/现在适合。"
    "描述'市场在奖励动量'可以, 但不许说'所以你该追涨'。料不足就直说不确定, 绝不编造新闻或数字。\n"
    "【知识边界·先搜再答, 别嘴硬】你的训练知识有截止日、可能已过期(尤其海外公司是否上市/最新IPO/重组/政策/某公司近况)。"
    "碰到本地工具(行情/板块/概念)查不到、或时效性强、或你不确定的事实, 不许凭记忆下肯定结论——先用 web_search 联网核实; "
    "搜到结果就以搜到的为准(并可在文中标明来源/日期), 若搜到该标的有代码就再用 get_quote 查实时行情。"
    "只有 web_search 也查不到时, 才说'查不到/无法确认, 建议你自行核实'。宁可去搜或说不知道, 绝不编一个确定的答案。\n"
    "回答用简体中文, 简洁直给, 分点列证据(数字), 该下的客观结论就下——但只对工具数据支撑的结论自信。"
)


_TOOL_CN = {
    "resolve_stock": "解析代码", "get_quote": "查行情", "get_trend": "查走势",
    "get_news": "查新闻", "get_holdings": "看持仓", "get_market_sentiment": "看大盘情绪",
    "get_sector_momentum": "看板块动量", "get_hot_rank": "看资金热度",
    "get_hot_concepts": "看热门概念", "get_market_news": "看政策快讯", "web_search": "联网搜索",
}


async def ask_stock_stream(question: str):
    """流式版: 边跑边 yield 事件 (step/answer/done/error), 供 SSE 推给前端。
    每轮 LLM 调用之间 yield 工具步骤, 步骤实时出现; 末轮文本作为答案。"""
    question = (question or "").strip()
    if not question:
        yield {"type": "error", "error": "空问题"}
        return
    messages = [{"role": "user", "content": question}]
    for rnd in range(_MAX_ROUNDS):
        try:
            resp = await asyncio.to_thread(
                _llm.call_claude_messages, messages, _SYSTEM, _MODEL, 2048, _TOOLS)
        except Exception as e:
            yield {"type": "error", "error": str(e)}
            return
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        # 服务端联网搜索(web_search)已由 API 执行完, 这里只把"联网搜索"作为步骤推给前端
        for b in content:
            if b.get("type") == "server_tool_use" and b.get("name") == "web_search":
                yield {"type": "step", "tool": "web_search", "label": "联网搜索",
                       "arg": (b.get("input") or {}).get("query", "")}
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            yield {"type": "answer", "text": text.strip()}
            yield {"type": "done"}
            return
        # 先把这一轮模型的简短思考文本(若有)推出去当“正在做什么”的旁白
        think = "".join(b.get("text", "") for b in content if b.get("type") == "text").strip()
        if think:
            yield {"type": "thought", "text": think[:120]}
        for tu in tus:
            yield {"type": "step", "tool": tu.get("name"),
                   "label": _TOOL_CN.get(tu.get("name"), tu.get("name")),
                   "arg": (tu.get("input") or {}).get("query") or (tu.get("input") or {}).get("code") or ""}
        results = []
        for tu in tus:
            try:
                fn = _EXECUTORS.get(tu.get("name"))
                out = await fn(tu.get("input") or {}) if fn else {"error": "未知工具"}
            except Exception as e:
                out = {"error": str(e)}
            results.append({"type": "tool_result", "tool_use_id": tu.get("id"),
                            "content": _json.dumps(out, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})
    yield {"type": "answer", "text": "（分析步数超限, 请换个问法或更具体）"}
    yield {"type": "done"}


async def ask_stock(question: str) -> dict:
    """跑 agent loop, 返回 {answer, tools_used, rounds}。"""
    question = (question or "").strip()
    if not question:
        return {"answer": "", "error": "空问题"}
    messages = [{"role": "user", "content": question}]
    tools_used: list[str] = []
    for rnd in range(_MAX_ROUNDS):
        try:
            resp = await asyncio.to_thread(
                _llm.call_claude_messages, messages, _SYSTEM, _MODEL, 2048, _TOOLS)
        except Exception as e:
            return {"answer": "", "error": str(e), "tools_used": tools_used, "rounds": rnd}
        content = resp.get("content", [])
        messages.append({"role": "assistant", "content": content})
        tus = [b for b in content if b.get("type") == "tool_use"]
        if not tus:
            text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
            return {"answer": text.strip(), "tools_used": tools_used, "rounds": rnd + 1}
        results = []
        for tu in tus:
            name = tu.get("name", "")
            tools_used.append(name)
            try:
                fn = _EXECUTORS.get(name)
                out = await fn(tu.get("input") or {}) if fn else {"error": f"未知工具 {name}"}
            except Exception as e:
                out = {"error": str(e)}
            results.append({"type": "tool_result", "tool_use_id": tu.get("id"),
                            "content": _json.dumps(out, ensure_ascii=False)})
        messages.append({"role": "user", "content": results})
    return {"answer": "（分析步数超限, 请换个问法或更具体）", "tools_used": tools_used, "rounds": _MAX_ROUNDS}
