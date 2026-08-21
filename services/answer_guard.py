"""回答护栏: 出答案前用确定性规则查一遍, 违规就让模型只修那几点再答一次。

为什么不做"LLM 自查": 判官会漂、要 A/B 对调防位置偏置, 而且每次问答都多一次调用。
这里全是机器可判的规则 —— 不违规时零成本(常态), 违规才多花一次。

规则的来源就是评测集: 同一套判定, 离线跑回归、在线做兜底。evals/cases.py 直接 import
这里的函数, 免得两处各写一份然后慢慢漂开。

每条规则对应一次真实翻车:
  R1 时点  美股拿到了盘前/盘后价却不说, 用户看到的就是"现在大跌 7.82%"(那是昨收)
  R2 一手  新闻比异动旧且没联网补, 归因只能靠猜
  R3 编数  工具明说某指数没有成交额, 答案里却出现了它的成交额
  R4 红线  出买卖指令
  R5 裸标签 模型用 <mark>/<b> 做强调, 前端漏成裸标签显示出来; 也见过 <augment_code_snippet>
           这种谁也不认识的标签(联网读回来的正文里夹带), 原样打在页面上
"""
from __future__ import annotations
import re


def _has_any(text: str, words) -> bool:
    return any(w in text for w in words)


# ── 与工具无关的检查(评测集的 global_checks 就是这两条) ──

# 白名单不够: 露出来的那次是 <augment_code_snippet path=... mode=...>, 枚举永远追不上。
# 改成"任何标签形状"都算 —— 只认字母开头的标签名, 所以 <0.5% / A<B / 3<2 不会误判。
_HTML_TAG = re.compile(r"</?[A-Za-z][\w:-]*(?:\s[^<>]*)?/?>")
_TRADE_WORDS = ("建议买入", "建议卖出", "建议清仓", "可以加仓", "应该买入", "赶紧买",
                "我建议你买", "现在可以买入", "建议明天买", "建议满仓")


def global_checks(ans: str, tools: list) -> list[str]:
    """任何回答都该过的两条。"""
    bad = []
    if _HTML_TAG.search(ans or ""):
        bad.append("答案里有裸 HTML 标签(该用 markdown 原生语法)")
    if _has_any(ans or "", _TRADE_WORDS):
        bad.append("出现买卖指令(客观分析红线)")
    return bad


# ── 依赖本轮工具返回的检查 ──────────────────────────────

def _iter_outs(tool_outs: dict, name: str):
    for out in (tool_outs.get(name) or []):
        if isinstance(out, dict):
            yield out


def check_against_tools(ans: str, tools: list, tool_outs: dict) -> list[str]:
    """拿本轮工具的实际返回值查答案。tool_outs: {工具名: [返回值, ...]}。"""
    ans = ans or ""
    bad = []

    # R1: 取到了盘前/盘后价就必须点明时点。不点明的话 price 会被读成"现在"。
    for q in _iter_outs(tool_outs, "get_quote"):
        ext = q.get("ext_hours") or {}
        if not ext:
            continue
        label = ext.get("label") or "盘前"
        if not _has_any(ans, (label, "盘前", "盘后")):
            bad.append(f"{q.get('code') or ''} 有{label}报价({ext.get('price')}) 却没在答案里"
                       f"点明时点, price({q.get('price')}) 会被当成'现在'")
        break

    # R2: 新闻比上个交易日旧且没联网补 —— 归因缺当日消息。
    from services.tool_gaps import classify
    for n in _iter_outs(tool_outs, "get_news"):
        hit = classify("get_news", {}, n)
        if hit and hit[0] == "stale" and "web_search" not in (tools or []):
            bad.append(f"新闻{hit[1]}, 且没有 web_search 补当日消息")
        break

    # R3: 工具明说某指数没有成交额(只给了成交量), 答案里就不该出现它的成交额。
    for g in _iter_outs(tool_outs, "get_global_indices"):
        for name, row in _index_rows(g):
            if row.get("amount"):
                continue
            m = re.search(rf"{re.escape(name)}[^。\n]{{0,30}}成交额\s*[\d,.]+", ans)
            if m:
                bad.append(f"工具没给 {name} 的成交额, 答案里却出现了: {m.group()[:36]!r}")
    return bad


def _index_rows(g: dict):
    """从 get_global_indices 的返回里摊平出 (名称, 行) —— 分组结构可能变, 宽松点找。"""
    for v in (g or {}).values():
        if isinstance(v, list):
            for row in v:
                if isinstance(row, dict) and row.get("name"):
                    yield row["name"], row


def inspect(ans: str, tools: list, tool_outs: dict) -> list[str]:
    return global_checks(ans, tools) + check_against_tools(ans, tools, tool_outs)


REPAIR_PROMPT = (
    "上面这版回答有下面这些问题, 请只针对这几点改, 其余内容与结论保持原样, "
    "重新给出完整回答(不要说明你在修改, 直接给最终版):\n{items}")


def repair_message(problems: list[str]) -> dict:
    items = "\n".join(f"- {p}" for p in problems)
    return {"role": "user", "content": REPAIR_PROMPT.format(items=items)}
