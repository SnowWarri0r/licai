"""Portfolio news aggregation. 用 akshare 拉持仓个股的新闻 + 公告."""
from __future__ import annotations
import asyncio
import hashlib
import json as _json
import time
from fastapi import APIRouter
from pydantic import BaseModel

from typing import Optional
import services.llm_client as _llm
from database import get_all_holdings

router = APIRouter(prefix="/api/news", tags=["news"])

# 5 分钟缓存 (akshare stock_notice_report 拉全表慢)
_cache: dict[str, tuple[list, float]] = {}
_TTL = 300


def _is_a_share(code: str) -> bool:
    code = (code or "").upper()
    if code.startswith(("HK.", "US.")):
        return False
    return code.isdigit() and len(code) == 6


def _fetch_stock_news_em_sync(code: str) -> list[dict]:
    """同步拉一只票的最近 10 条新闻 (akshare EastMoney)."""
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    try:
        import akshare as ak
        df = ak.stock_news_em(symbol=code)
        if df is None or df.empty:
            return []
        out = []
        for _, r in df.iterrows():
            out.append({
                "kind": "news",
                "code": code,
                "title": str(r.get("新闻标题") or ""),
                "content": str(r.get("新闻内容") or "")[:200],
                "time": str(r.get("发布时间") or ""),
                "source": str(r.get("文章来源") or ""),
                "url": str(r.get("新闻链接") or ""),
            })
        return out
    except Exception:
        return []


def _fetch_all_notices_sync() -> dict[str, list[dict]]:
    """全 A 股公告 (akshare stock_notice_report). 一次拉 → 按 code 分桶."""
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    out: dict[str, list[dict]] = {}
    try:
        import akshare as ak
        df = ak.stock_notice_report(symbol="全部")
        if df is None or df.empty:
            return out
        for _, r in df.iterrows():
            code = str(r.get("代码") or "")
            if not code:
                continue
            out.setdefault(code, []).append({
                "kind": "notice",
                "code": code,
                "name": str(r.get("名称") or ""),
                "title": str(r.get("公告标题") or ""),
                "type": str(r.get("公告类型") or ""),
                "time": str(r.get("公告日期") or ""),
                "url": str(r.get("网址") or ""),
            })
        return out
    except Exception:
        return out


@router.get("/portfolio")
async def portfolio_news(limit_per_code: int = 5):
    """聚合所有 A 股持仓的新闻 + 公告. 5min 缓存.

    返回:
      { items: [{kind, code, name, title, time, ...}, ...], count: N }
    items 按 time 倒序, 跨持仓股合并.
    """
    cache_key = f"portfolio_news_{limit_per_code}"
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]

    holdings = await get_all_holdings()
    codes = [h["stock_code"] for h in holdings if _is_a_share(h["stock_code"])]
    name_by_code = {h["stock_code"]: h.get("stock_name") or "" for h in holdings}

    if not codes:
        result = {"items": [], "count": 0}
        _cache[cache_key] = (result, time.time())
        return result

    # 并发拉新闻 + 一次性拉公告表
    news_tasks = [asyncio.to_thread(_fetch_stock_news_em_sync, c) for c in codes]
    notices_task = asyncio.to_thread(_fetch_all_notices_sync)
    results = await asyncio.gather(*news_tasks, notices_task, return_exceptions=True)

    notices_by_code = results[-1] if not isinstance(results[-1], Exception) else {}
    news_per_code = [r if not isinstance(r, Exception) else [] for r in results[:-1]]

    items: list[dict] = []
    for code, news_list in zip(codes, news_per_code):
        name = name_by_code.get(code, "")
        for n in news_list[:limit_per_code]:
            n["name"] = name
            items.append(n)
        for notice in (notices_by_code.get(code, []))[:limit_per_code]:
            if not notice.get("name"):
                notice["name"] = name
            items.append(notice)

    # 按 time 倒序
    items.sort(key=lambda x: x.get("time") or "", reverse=True)

    result = {"items": items, "count": len(items), "tracked_codes": codes}
    _cache[cache_key] = (result, time.time())
    return result


def _strip_proxy_env():
    """akshare 这几个源走国内直连, 清掉 proxy 环境变量避免被代理拖慢/拦截."""
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)


def _item(src: str, title: str, content: str, t: str, url: str = "") -> dict | None:
    title = (title or "").strip()
    if not title:
        return None
    return {
        "kind": "market",
        "source": src,
        "title": title,
        "content": (content or "").strip()[:200],
        "time": (t or "").strip(),
        "url": url,
    }


def _fetch_global_em() -> list[dict]:
    """东财全球财经 (量最大 ~200 条)."""
    try:
        import akshare as ak
        df = ak.stock_info_global_em()
    except Exception:
        return []
    out = []
    for _, r in df.iterrows():
        it = _item("东财", str(r.get("标题") or ""), str(r.get("摘要") or ""),
                   str(r.get("发布时间") or ""), str(r.get("链接") or ""))
        if it:
            out.append(it)
    return out


def _fetch_global_cls() -> list[dict]:
    """财联社全球财经."""
    try:
        import akshare as ak
        df = ak.stock_info_global_cls()
    except Exception:
        return []
    out = []
    for _, r in df.iterrows():
        t = (str(r.get("发布日期") or "") + " " + str(r.get("发布时间") or "")).strip()
        it = _item("财联社", str(r.get("标题") or ""), str(r.get("内容") or ""), t)
        if it:
            out.append(it)
    return out


def _fetch_global_ths() -> list[dict]:
    """同花顺全球财经."""
    try:
        import akshare as ak
        df = ak.stock_info_global_ths()
    except Exception:
        return []
    out = []
    for _, r in df.iterrows():
        it = _item("同花顺", str(r.get("标题") or ""), str(r.get("内容") or ""),
                   str(r.get("发布时间") or ""), str(r.get("链接") or ""))
        if it:
            out.append(it)
    return out


# 单源超时: 任一源(如同花顺/财联社)卡死也不拖累整体, 最坏冷启动 ≈ 该超时值
_SOURCE_TIMEOUT = 8.0


async def _fetch_source(fetcher) -> list[dict]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(fetcher), timeout=_SOURCE_TIMEOUT)
    except Exception:
        return []


@router.get("/market")
async def market_news():
    """全市场要闻 (东财 + 财联社 + 同花顺). 三源并发 + 单源超时, 5min 缓存."""
    cache_key = "market_news"
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]
    _strip_proxy_env()
    # 三源并发拉取, 任一源超时/失败只丢自己, 不阻塞其余
    results = await asyncio.gather(
        _fetch_source(_fetch_global_em),
        _fetch_source(_fetch_global_cls),
        _fetch_source(_fetch_global_ths),
    )
    # 合并去重 (按 title; 顺序 em→cls→ths 决定保留优先级), 时间倒序
    out, seen = [], set()
    for lst in results:
        for it in lst:
            title = it.get("title")
            if not title or title in seen:
                continue
            seen.add(title)
            out.append(it)
    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    result = {"items": out, "count": len(out)}
    # 三源全空(全超时)不写缓存, 避免把空结果钉死 5 分钟; 有数据才缓存
    if out:
        _cache[cache_key] = (result, time.time())
    return result


async def news_prewarm_loop():
    """后台预热 market_news 缓存, 让 sector/why、digest 等读到的永远是热缓存,
    用户点击不再吃冷启动的几秒。每 4 分钟刷一次 (< 5min TTL, 始终保鲜)。"""
    await asyncio.sleep(5)  # 让 app 先起来
    while True:
        try:
            await market_news()
        except Exception as e:
            print(f"[news-prewarm] failed: {e}")
        await asyncio.sleep(240)


_DIGEST_TTL = 1800  # LLM 摘要 30 分钟缓存 (调用贵 + 慢)


@router.get("/digest")
async def news_digest(force: bool = False, max_items: int = 80):
    """LLM 摘要: 把市场要闻 + 持仓相关新闻喂给 Claude, 生成结构化要点.
    30min 缓存; force=true 强制重算.
    """
    cache_key = "news_digest"
    if not force:
        cached = _cache.get(cache_key)
        if cached and time.time() - cached[1] < _DIGEST_TTL:
            return cached[0]

    # 1) 准备素材: 持仓新闻 + 市场要闻 合并 (持仓优先)
    portfolio = await portfolio_news()
    market = await market_news()
    holdings = await get_all_holdings()
    codes = [h["stock_code"] for h in holdings if _is_a_share(h["stock_code"])]
    name_by_code = {h["stock_code"]: h.get("stock_name") or "" for h in holdings}

    # 取前 max_items 条 (持仓相关 30 + 市场 50)
    p_items = (portfolio.get("items") or [])[:30]
    m_items = (market.get("items") or [])[: max(0, max_items - len(p_items))]
    all_items = p_items + m_items
    if not all_items:
        return {"summary": "", "highlights": [], "generated_at": "", "model": "", "input_count": 0}

    # 2) 拼 prompt
    lines = []
    for it in all_items:
        prefix = ""
        if it.get("code"):
            prefix = f"[{it['code']}{('-' + it['name']) if it.get('name') else ''}] "
        kind = "公告" if it.get("kind") == "notice" else "新闻"
        src = it.get("source") or ""
        t = (it.get("time") or "")[:16]
        title = it.get("title") or ""
        lines.append(f"({t} {kind} {src}) {prefix}{title}")

    holdings_desc = ", ".join(f"{c}({name_by_code.get(c,'')})" for c in codes) or "(无 A 股持仓)"
    user_prompt = (
        f"用户 A 股持仓: {holdings_desc}\n\n"
        f"近期市场新闻和公告 ({len(all_items)} 条, 按时间倒序):\n"
        + "\n".join(lines)
        + "\n\n请按要求输出。"
    )
    system_prompt = (
        "你是 A 股市场资讯摘要助手. 工作原则:\n"
        "1. 抽取 5-8 条最值得关注的要点, 优先级:\n"
        "   (a) 直接涉及用户持仓的公告/新闻 — 必选\n"
        "   (b) 涉及用户持仓行业的政策/资金/事件 — 高优\n"
        "   (c) 大盘级别转向信号 (央行/汇率/外资/重大政策) — 中优\n"
        "2. 每条要点格式:\n"
        "   🟢 [利好] / 🔴 [利空] / 🟡 [关注] - 一句话核心事件 (≤30字)\n"
        "   涉及: 代码或行业 | 可能影响方向\n"
        "3. 不给 '该买/该卖' 建议, 只描述事件和市场可能的反应方向\n"
        "4. 输出严格 JSON: {summary: '一段 60 字以内总结', highlights: [{level, title, related, impact}, ...]}\n"
        "   level: 'good'/'bad'/'watch'; title: 核心事件; related: 代码/行业; impact: 可能反应方向\n"
        "5. 只输出 JSON, 不要其他文字或 markdown 包装"
    )

    # 3) 调 LLM
    from services import llm_client
    import json as _json
    try:
        raw = await asyncio.to_thread(
            llm_client.call_claude, user_prompt, system_prompt,
            "claude-sonnet-4-5", 1800,
        )
    except Exception as e:
        return {"summary": "", "highlights": [], "generated_at": "", "model": "", "error": str(e)[:200], "input_count": len(all_items)}

    # 4) 解析 JSON (LLM 偶尔会带 ```json 包装, 兼容)
    raw = (raw or "").strip()
    if raw.startswith("```"):
        # 去掉 fence
        raw = raw.strip("`")
        # 可能有 'json\n' 前缀
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip("\n").lstrip()
        # 去掉尾部 ```
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()
    try:
        parsed = _json.loads(raw)
        summary = parsed.get("summary", "")
        highlights = parsed.get("highlights", [])
    except Exception:
        summary = raw[:200] if raw else ""
        highlights = []

    from datetime import datetime, timezone, timedelta
    now_cst = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    result = {
        "summary": summary,
        "highlights": highlights,
        "generated_at": now_cst,
        "model": "claude-sonnet-4-5",
        "input_count": len(all_items),
    }
    _cache[cache_key] = (result, time.time())
    return result


# ---------------------------------------------------------------------------
# POST /api/news/interpret — LLM 单条新闻解读 (三段式 + 缓存 + 降级)
# ---------------------------------------------------------------------------

_INTERPRET_CACHE: dict[str, dict] = {}


class InterpretIn(BaseModel):
    title: str
    content: Optional[str] = ""
    code: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None
    time: Optional[str] = None


_INTERPRET_SYS = (
    "你是 A 股资讯解读助手。只解释新闻, 严禁任何操作建议(买入/卖出/加仓/减仓/目标价/仓位都不许出)。"
    "用简体中文输出严格 JSON, 三个键:\n"
    '{"what":"这条新闻讲了什么(1-2句)","why":"为什么重要/影响面(1-2句)",'
    '"relation":"跟用户持仓或关注板块什么关系(没有就写\'与你当前持仓无直接关系\')"}'
    "\n只输出 JSON, 不要多余文字。"
)


@router.post("/interpret")
async def interpret_news(data: InterpretIn):
    key = hashlib.sha1(f"{data.title}|{data.content}|{data.code or ''}".encode("utf-8")).hexdigest()
    if key in _INTERPRET_CACHE:
        return {**_INTERPRET_CACHE[key], "cached": True}
    try:
        holdings = await get_all_holdings()
        hold_desc = ", ".join(f"{h['stock_code']}({h.get('stock_name','')})" for h in holdings) or "(无持仓信息)"
    except Exception:
        hold_desc = "(无持仓信息)"
    rel = f"[{data.code}{('-'+data.name) if data.name else ''}] " if data.code else ""
    user_prompt = (
        f"用户持仓: {hold_desc}\n\n"
        f"新闻标题: {rel}{data.title}\n"
        f"新闻正文: {data.content or '(无正文, 仅标题)'}\n\n请按要求输出 JSON。"
    )
    try:
        raw = await asyncio.to_thread(_llm.call_claude, user_prompt, _INTERPRET_SYS, "claude-sonnet-4-20250514", 600)
    except Exception:
        return {"what": "", "why": "", "relation": "", "error": "解读暂不可用", "cached": False}
    parsed = None
    try:
        s = raw.strip()
        i, j = s.find("{"), s.rfind("}")
        if i >= 0 and j > i:
            parsed = _json.loads(s[i:j+1])
    except Exception:
        parsed = None
    if not isinstance(parsed, dict):
        parsed = {"what": raw.strip()[:300], "why": "", "relation": ""}
    out = {
        "what": str(parsed.get("what") or "").strip(),
        "why": str(parsed.get("why") or "").strip(),
        "relation": str(parsed.get("relation") or "").strip(),
    }
    _INTERPRET_CACHE[key] = out
    return {**out, "cached": False}
