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
    {"name": "get_market_sentiment", "description": "查大盘打板情绪(涨停数/连板高度/炸板率/赚钱效应/热点板块), 判断是个股原因还是大盘普涨普跌。",
     "input_schema": {"type": "object", "properties": {}}},
]

_EXECUTORS = {
    "resolve_stock": lambda a: _tool_resolve_stock(a.get("query", "")),
    "get_quote": lambda a: _tool_get_quote(a.get("code", "")),
    "get_trend": lambda a: _tool_get_trend(a.get("code", ""), a.get("days", 20)),
    "get_news": lambda a: _tool_get_news(a.get("code", "")),
    "get_holdings": lambda a: _tool_get_holdings(),
    "get_market_sentiment": lambda a: _tool_market_sentiment(),
}

_SYSTEM = (
    "你是个股异动解读助手。用户自由提问(为什么涨/跌、最近什么消息、跟我持仓什么关系等)。\n"
    "你挂了工具: resolve_stock(名字转代码)、get_quote(实时行情)、get_trend(近N日走势)、"
    "get_news(个股新闻)、get_holdings(用户持仓)、get_market_sentiment(大盘情绪)。\n"
    "流程: 用户报名字先 resolve_stock 拿代码; 要解读涨跌就调 get_quote 看当日幅度 + get_trend 看是不是趋势 + "
    "get_news 找消息面 + 需要时 get_market_sentiment 判断是个股事件还是大盘普涨/普跌。每个结论都要有工具数据支撑。\n"
    "【硬规则】只做客观解读(为什么动、什么消息、跟持仓什么关系), 严禁任何操作建议: 不许出现 该买/该卖/加仓/减仓/"
    "能不能追/还能不能拿/目标价/止损/现在适合。料不足就直说不确定, 绝不编造新闻或数字。\n"
    "回答用简体中文, 简洁直给: 先一句结论(今日涨跌幅+主因), 再分点列消息面/资金面/大盘背景, 最后点跟持仓的关系(若相关)。"
)


_TOOL_CN = {
    "resolve_stock": "解析代码", "get_quote": "查行情", "get_trend": "查走势",
    "get_news": "查新闻", "get_holdings": "看持仓", "get_market_sentiment": "看大盘情绪",
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
