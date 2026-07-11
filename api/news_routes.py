"""Portfolio news aggregation. 用 akshare 拉持仓个股的新闻 + 公告."""
from __future__ import annotations
import asyncio
import hashlib
import ipaddress
import json as _json
import re
import socket
import time
from urllib.parse import urlparse
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

    # 只拉当前在持(shares>0)的票的新闻; 已清仓的不算"持仓新闻"
    holdings = [h for h in await get_all_holdings() if float(h.get("shares") or 0) > 0]
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


def _item(src: str, title: str, content: str, t: str, url: str = "", image: str = "") -> dict | None:
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
        "image": (image or "").strip(),
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


def _fetch_global_jin10(pages: int = 6, want: int = 80) -> list[dict]:
    """金十数据全球快讯 (flash-api 直连, 偏全球宏观/地缘/央行/能源)。
    翻页(max_time)多拉几页凑够历史(单页仅 ~20 条 / 约 10 分钟), 按 id 去重,
    保留 important 标记, 正文不截断(此前 text[:60] 导致快讯显示不完整)。"""
    import re
    import requests
    s = requests.Session()
    s.trust_env = False  # 绕开 macOS 系统代理
    H = {"x-app-id": "bVBF4FyRTn5NJF5n", "x-version": "1.0.0",
         "User-Agent": "Mozilla/5.0", "Referer": "https://www.jin10.com/"}

    def _page(max_time=None):
        p = {"channel": "-8200", "vip": "1"}
        if max_time:
            p["max_time"] = max_time
        try:
            return s.get("https://flash-api.jin10.com/get_flash_list",
                         params=p, headers=H, timeout=7).json().get("data") or []
        except Exception:
            return []

    raw, seen, max_time = [], set(), None
    for _ in range(max(1, pages)):
        page = _page(max_time)
        if not page:
            break
        fresh = [x for x in page if x.get("id") not in seen]
        if not fresh:
            break
        for x in fresh:
            seen.add(x.get("id"))
        raw.extend(fresh)
        max_time = page[-1].get("time")
        if len(raw) >= want:
            break

    out = []
    for x in raw:
        typ = x.get("type")
        if typ not in (0, None, 2):   # 文本(0) + 图文分析(2); 跳过视频型
            continue
        d = x.get("data") or {}
        if typ == 2 and (d.get("tag") or "").strip() == "VIP":   # 纯 VIP 原文要会员, 跳过; 保留免费分析(精选分析/热点头条/地缘热点/市场要闻)
            continue
        content = d.get("content") or d.get("title") or ""
        text = re.sub(r"<[^>]+>", " ", content)      # 去 HTML 标签
        text = re.sub(r"\s+", " ", text).strip()
        title = (d.get("title") or "").strip() or text
        if not title:
            continue
        # pic 两种都可能有: type=2 是图文配图; type=0 的"金十图示/持仓报告"图就是正文本身。
        image = (d.get("pic") or "").strip()
        url = (d.get("link") or d.get("source_link") or "") if typ == 2 else (d.get("source_link") or "")
        it = _item("金十", title, text, str(x.get("time") or ""), url, image)
        if it:
            it["important"] = bool(x.get("important"))
            if typ == 2:
                it["tag"] = (d.get("tag") or "").strip()   # VIP / 精选分析 / 市场要闻
            out.append(it)
    return out


# 单源超时: 任一源(如同花顺/财联社)卡死也不拖累整体, 最坏冷启动 ≈ 该超时值
_SOURCE_TIMEOUT = 8.0


async def _fetch_source(fetcher) -> list[dict]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(fetcher), timeout=_SOURCE_TIMEOUT)
    except Exception:
        return []


# 小金属关键词 (跟有色/中钨持仓相关: 钨钼锑稀土锗镓钽铌钴 + 收储/供需信号)
_SMALL_METAL_KW = [
    "钨", "钼", "锑", "稀土", "镨钕", "镝", "铽", "锗", "镓", "钽", "铌", "钴",
    "小金属", "稀有金属", "永磁", "金属硅", "工业硅", "收储",
]


@router.get("/small-metal")
async def small_metal_news(limit: int = 30):
    """小金属资讯: 从全市场要闻按关键词过滤出钨钼锑稀土等的政策/收储/供需/价格消息。
    现货价无源, 用资讯补宏观。复用 market_news 缓存的全量, 没有就现拉三源。"""
    ck = "small_metal_news"
    cached = _cache.get(ck)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]

    mc = _cache.get("market_news")
    if mc and time.time() - mc[1] < _TTL:
        items = mc[0].get("items", [])
    else:
        _strip_proxy_env()
        results = await asyncio.gather(
            _fetch_source(_fetch_global_em),
            _fetch_source(_fetch_global_cls),
            _fetch_source(_fetch_global_ths),
        )
        items, seen = [], set()
        for lst in results:
            for it in lst:
                t = it.get("title")
                if t and t not in seen:
                    seen.add(t)
                    items.append(it)

    def _hit(it):
        s = (it.get("title") or "") + (it.get("content") or "")
        return any(k in s for k in _SMALL_METAL_KW)

    out = [it for it in items if _hit(it)]
    out.sort(key=lambda x: x.get("time") or "", reverse=True)
    result = {"items": out[:limit], "count": len(out)}
    if out:
        _cache[ck] = (result, time.time())
    return result


@router.get("/market")
async def market_news():
    """全市场要闻 (东财 + 财联社 + 同花顺 + 金十). 四源并发 + 单源超时, 5min 缓存."""
    cache_key = "market_news"
    cached = _cache.get(cache_key)
    if cached and time.time() - cached[1] < _TTL:
        return cached[0]
    _strip_proxy_env()
    # 四源并发拉取, 任一源超时/失败只丢自己, 不阻塞其余
    results = await asyncio.gather(
        _fetch_source(_fetch_global_em),
        _fetch_source(_fetch_global_cls),
        _fetch_source(_fetch_global_ths),
        _fetch_source(_fetch_global_jin10),
    )
    # 合并去重 (按 title; 顺序 em→cls→ths→金十 决定保留优先级), 时间倒序
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


# 持仓相关板块关键词(命中即标"关联我持仓"): 小金属 + 有色/半导体/电子/新能源/海外
_PORTFOLIO_SECTOR_KW = _SMALL_METAL_KW + [
    "有色", "铜", "铝", "锌", "铅", "镍", "贵金属", "黄金", "白银", "金属",
    "半导体", "芯片", "晶圆", "存储芯片", "电子", "PCB", "覆铜板", "面板", "光刻",
    "科技", "人工智能", "算力", "新能源", "锂", "光伏", "储能", "风电", "稀土",
    "纳斯达克", "纳指", "标普", "美股", "日经", "费城半导体",
]


async def _portfolio_keywords() -> set:
    """关联判定关键词 = 固定板块词 + 当前在持股票名(让点名个股的快讯也命中)。"""
    kws = set(_PORTFOLIO_SECTOR_KW)
    try:
        for h in await get_all_holdings():
            if float(h.get("shares") or 0) > 0:
                nm = (h.get("stock_name") or "").strip()
                if len(nm) >= 2:
                    kws.add(nm)
    except Exception:
        pass
    return kws


@router.get("/jin10")
async def jin10_flash(limit: int = 30):
    """金十快讯独立流: 全球宏观/地缘/央行实时快讯, important 重要标记 + related 关联持仓。60s 缓存。"""
    ck = "jin10_flash"
    cached = _cache.get(ck)
    items = cached[0] if (cached and time.time() - cached[1] < 60) else None
    if items is None:
        _strip_proxy_env()
        items = await _fetch_source(_fetch_global_jin10)
        if items:
            _cache[ck] = (items, time.time())
    # 关联持仓标记(每次按当前持仓判, 持仓变动即时反映; 字符串命中很轻量)
    kws = await _portfolio_keywords()
    for it in items:
        t = (it.get("title") or "") + " " + (it.get("summary") or "")
        it["related"] = any(k in t for k in kws)
    return {"items": items[:limit], "count": len(items),
            "related_count": sum(1 for it in items if it.get("related"))}


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
    holdings = [h for h in await get_all_holdings() if float(h.get("shares") or 0) > 0]
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

    holdings_desc = await _all_holdings_desc()   # 全资产(A股+基金/ETF+加密+机器人), 不再只 A股
    user_prompt = (
        f"用户持仓: {holdings_desc}\n\n"
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
            "claude-sonnet-5", 1800,
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
        "model": "claude-sonnet-5",
        "input_count": len(all_items),
    }
    _cache[cache_key] = (result, time.time())
    return result


# ---------------------------------------------------------------------------
# GET /api/news/daily-review — 收盘 AI 复盘日报 (持仓今日归因 + 全球大事 + 明日关注)
# ---------------------------------------------------------------------------

_REVIEW_TTL = 1800  # 30min; force=true 强制重算


@router.get("/daily-review")
async def daily_review(force: bool = False):
    """收盘复盘日报: A 股持仓今日涨跌 + 板块异动 + 全球快讯 → LLM 归因复盘。
    输出 {summary, holdings[], sectors[], global[], tomorrow[]}。不给买卖建议。"""
    from datetime import date as _date
    today = _date.today().isoformat()
    cache_key = f"daily_review_{today}"
    if not force:
        c = _cache.get(cache_key)
        if c and time.time() - c[1] < _REVIEW_TTL:
            return {**c[0], "cached": True}

    from services.market_data import get_realtime_quotes
    from services.sector_scanner import scan_sectors

    # 1) 全组合今日表现: A股/港美股(holdings 表, 实时) + 基金/加密/机器人(external_assets,
    #    复用 assets 口径: 场内ETF实时价、场外基金净值或底层代理估、加密24h、机器人累计)
    holdings = await get_all_holdings()
    live = [h for h in holdings if float(h.get("shares") or 0) > 0]
    quotes = await get_realtime_quotes([h["stock_code"] for h in live]) if live else {}
    a_codes = [h["stock_code"] for h in live if _is_a_share(h["stock_code"])]
    hold_moves = []    # A股(板块扫描/涨跌统计沿用)
    other_moves = []   # 港美股 + 基金/ETF + 加密 + 机器人
    for h in live:
        c = h["stock_code"]
        q = quotes.get(c) or {}
        row = {"name": h.get("stock_name", ""), "code": c,
               "change_pct": q.get("change_pct"), "suffix": "",
               "mv": round((q.get("price") or 0) * float(h.get("shares") or 0)
                           * float(q.get("fx_rate") or 1), 0)}
        if _is_a_share(c):
            hold_moves.append(row)
        else:
            row["cls"] = "港股" if str(c).upper().startswith("HK") else "美股"
            other_moves.append(row)
    hold_moves.sort(key=lambda x: (x["change_pct"] if x["change_pct"] is not None else 0))
    try:
        from api.assets_routes import list_assets as _list_assets
        ext = (await _list_assets()).get("assets") or []
    except Exception as e:
        print(f"[daily-review] assets enrich failed: {e}")
        ext = []
    for a in ext:
        t, nm = a.get("asset_type"), (a.get("name") or "").strip()
        mv = float(a.get("current_value") or 0)
        if not nm or mv <= 0:
            continue
        q = a.get("quote") or {}
        if t == "FUND":
            if float(a.get("shares") or 0) <= 0:
                continue
            tc = q.get("today_change_pct")
            est = (q.get("nav_date") or "") != today and q.get("proxy_change_pct") is not None
            other_moves.append({"name": nm, "cls": "基金/ETF", "change_pct": tc,
                                "suffix": "(按底层估)" if est else "", "mv": round(mv)})
        elif t == "CRYPTO":
            if float(a.get("shares") or 0) <= 0:
                continue
            other_moves.append({"name": nm, "cls": "加密", "change_pct": q.get("change_pct"),
                                "suffix": "(24h)", "mv": round(mv)})
        elif t == "BOT":
            other_moves.append({"name": nm, "cls": "机器人", "change_pct": a.get("pnl_pct"),
                                "suffix": "(累计)", "mv": round(mv)})
        # WEALTH/CASH 无"今日行情", 不进复盘
    other_moves.sort(key=lambda x: -abs(x["change_pct"] if x["change_pct"] is not None else 0))

    # 2) 持仓所属板块今日 (复用板块扫描的 held 标记)
    held_sectors = []
    try:
        sec = await scan_sectors(a_codes)
        held_sectors = [{"name": s.get("name"), "d1": s.get("change_1d"), "d5": s.get("change_5d")}
                        for s in (sec.get("sectors") or []) if s.get("held")]
    except Exception as e:
        print(f"[daily-review] sector scan failed: {e}")

    # 3) 资讯素材: 全球快讯 + 持仓相关新闻
    market = await market_news()
    g_heads = [it.get("title", "") for it in (market.get("items") or []) if it.get("title")][:45]
    try:
        pnews = await portfolio_news()
        p_heads = [f"[{it.get('name') or it.get('code')}] {it.get('title','')}"
                   for it in (pnews.get("items") or [])][:20]
    except Exception:
        p_heads = []

    if not hold_moves and not other_moves and not g_heads:
        return {"summary": "今日无可复盘数据", "holdings": [], "sectors": [], "global": [], "tomorrow": [], "cached": False}

    # 4) 拼 prompt
    holds_txt = "\n".join(
        f"  {m['name']}({m['code']}) {('%+.2f%%' % m['change_pct']) if m['change_pct'] is not None else '—'} 市值≈{m['mv']:.0f}"
        for m in hold_moves) or "  (无 A 股持仓)"
    others_txt = "\n".join(
        f"  {m['name']} [{m['cls']}] {('%+.2f%%' % m['change_pct']) if m['change_pct'] is not None else '—'}{m['suffix']} 市值≈{m['mv']:.0f}"
        for m in other_moves) or "  (无)"
    secs_txt = "\n".join(
        f"  {s['name']} 今日{('%+.2f%%' % s['d1']) if s['d1'] is not None else '—'} 5日{('%+.2f%%' % s['d5']) if s['d5'] is not None else '—'}"
        for s in held_sectors) or "  (无)"
    user_prompt = (
        f"今天是 {today}, 收盘后做组合复盘。\n\n"
        f"【我的 A 股持仓今日涨跌】\n{holds_txt}\n\n"
        f"【我的其余持仓今日】(港美股为实时; 场外基金为净值或按底层持仓估; 加密为24h; 机器人为累计收益)\n{others_txt}\n\n"
        f"【A股持仓所属板块今日】\n{secs_txt}\n\n"
        f"【持仓相关新闻】\n" + ("\n".join(f"  - {h}" for h in p_heads) or "  (无)") + "\n\n"
        f"【全球财经快讯(标题)】\n" + "\n".join(f"  - {h}" for h in g_heads if h) + "\n\n"
        "请据此生成今日复盘 JSON。"
    )
    allowed_names = "、".join([m["name"] for m in hold_moves] + [m["name"] for m in other_moves]) or "(无)"
    allowed_sectors = "、".join(s["name"] for s in held_sectors) or "(无)"
    system_prompt = (
        "你是组合收盘复盘助手。基于给定数据复盘今天, 严禁任何买卖/加减仓/目标价/仓位建议, "
        "只做客观归因和资讯梳理。用简体中文输出严格 JSON (只输出 JSON, 无 markdown):\n"
        "{\n"
        '  "summary": "一句话总览【我的持仓】今天整体表现(≤40字)",\n'
        '  "holdings": [{"name":"股票名","change":"+5.5%","why":"为什么涨跌, 结合板块/新闻, ≤25字"}],\n'
        '  "sectors": [{"name":"板块","change":"-1.8%","note":"一句话(≤20字)"}],\n'
        '  "global": ["与持仓/市场相关的全球大事, 每条≤25字, 2-4条"],\n'
        '  "tomorrow": ["明日值得关注的点(财报/数据/事件), 据快讯推断, 没有就空数组, ≤3条"]\n'
        "}\n"
        "【硬性约束·必须遵守】\n"
        f"- holdings 的 name 只能取自我的持仓: {allowed_names}。绝对不许出现持仓外的股票(如建设银行/银行股等热点新闻里的票)。\n"
        f"- sectors 的 name 只能取自我的持仓板块: {allowed_sectors}。不许新增其它板块。\n"
        "- summary 只讲我上面这几只持仓的整体表现, 不要扯无关的大盘/银行热点。\n"
        "- 新闻只用来给【我的持仓】做归因和填 global/tomorrow, 不能把新闻里的别家公司写进 holdings/sectors。\n"
        "holdings 从全部持仓(含基金/加密/机器人)里挑今天动得明显的 4-8 只; 基金的归因可结合其名称指向的行业/底层板块。"
        "why/note 要具体, 料不足写'暂无明确催化'不编造。"
    )

    from services import llm_client
    import json as _json
    try:
        raw = await asyncio.to_thread(
            llm_client.call_claude, user_prompt, system_prompt, "claude-opus-4-8", 1500)
    except Exception as e:
        return {"summary": "复盘生成失败", "holdings": [], "sectors": [], "global": [], "tomorrow": [],
                "error": str(e)[:160], "cached": False}

    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].lstrip("\n").lstrip()
        if raw.endswith("```"):
            raw = raw[:-3].rstrip()
    try:
        i, j = raw.find("{"), raw.rfind("}")
        parsed = _json.loads(raw[i:j + 1]) if i >= 0 and j > i else {}
    except Exception:
        parsed = {}

    # 硬过滤: holdings/sectors 只保留真实持仓/板块, % 用实测覆盖 (LLM 只贡献 why/note),
    # 彻底杜绝 LLM 把新闻里的别家公司(如建设银行)塞进来。
    move_by_name = {m["name"]: m for m in hold_moves}
    move_by_name.update({m["name"]: m for m in other_moves})
    holdings_out = []
    for h in (parsed.get("holdings") or []):
        nm = (h.get("name") or "").strip()
        if nm not in move_by_name:
            continue
        m = move_by_name[nm]
        cp = m["change_pct"]
        holdings_out.append({"name": nm,
                             "change": (f"{cp:+.2f}%{m.get('suffix') or ''}" if cp is not None else "—"),
                             "why": str(h.get("why") or "").strip()})
    sec_by_name = {s["name"]: s for s in held_sectors}
    sectors_out = []
    for s in (parsed.get("sectors") or []):
        nm = (s.get("name") or "").strip()
        if nm not in sec_by_name:
            continue
        d1 = sec_by_name[nm]["d1"]
        sectors_out.append({"name": nm, "change": (f"{d1:+.2f}%" if d1 is not None else "—"),
                            "note": str(s.get("note") or "").strip()})

    # summary 用实测确定性生成 (LLM 总览总爱扯无关热点), 叙事交给每只的 why
    vals = [m for m in (hold_moves + other_moves) if m["change_pct"] is not None
            and m.get("cls") != "机器人"]     # 机器人是累计口径, 不进"今日"涨跌统计
    ups = sum(1 for m in vals if m["change_pct"] > 0)
    downs = sum(1 for m in vals if m["change_pct"] < 0)
    det = f"{len(vals)} 项持仓有今日数据 · {ups} 涨 {downs} 跌"
    if vals:
        top = max(vals, key=lambda m: m["change_pct"])
        bot = min(vals, key=lambda m: m["change_pct"])
        if top["change_pct"] > 0:
            det += f" · 领涨 {top['name']}{top['change_pct']:+.1f}%{top.get('suffix') or ''}"
        if bot["change_pct"] < 0:
            det += f" · 领跌 {bot['name']}{bot['change_pct']:+.1f}%{bot.get('suffix') or ''}"

    from datetime import datetime, timezone, timedelta
    now_cst = (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
    result = {
        "date": today,
        "summary": det,
        "holdings": holdings_out,
        "sectors": sectors_out,
        "global": parsed.get("global") or [],
        "tomorrow": parsed.get("tomorrow") or [],
        "generated_at": now_cst,
        "cached": False,
    }
    if holdings_out or result["global"]:
        _cache[cache_key] = (result, time.time())
    return result


# ---------------------------------------------------------------------------
# POST /api/news/interpret — LLM 单条新闻解读 (三段式 + 缓存 + 降级)
# ---------------------------------------------------------------------------

_INTERPRET_CACHE: dict[str, dict] = {}


# 文章正文之后的页面尾部噪声标志(版权/分享/评论/推荐/下载), 命中最早的一个即截断
_END_MARKERS = (
    "未经正式授权", "未经授权", "侵权必究", "版权声明", "免责声明", "郑重声明",
    "责任编辑", "本文首发", "扫描二维码", "下载界面新闻", "微信公众号",
    "热门排行", "发布评论", "暂无评论", "下一篇", "上一篇", "分享至",
    "相关阅读", "推荐阅读", "点击排行", "精彩推荐", "热门推荐",
)


def _trim_article_tail(text: str) -> str:
    """从正文里砍掉尾部页面噪声(版权/点赞收藏/分享/评论/热门排行/下载App 等)。
    取最早命中的尾标志处截断; 标志出现在极靠前(<80)时不截, 避免误伤短正文。"""
    cut = len(text)
    for mk in _END_MARKERS:
        i = text.find(mk)
        if 80 <= i < cut:
            cut = i
    return text[:cut].strip()


def _is_nav_line(ln: str) -> bool:
    """站点头部噪声行判定: 栏目菜单(一串短词条空格并排, 如'指数 期指 期权 个股…')、
    面包屑('首页 > 财经频道 > 正文')、孤立短词(字体/分享/登录/数据中心)。
    中文正文行带句读且几乎不用空格分词, 形态与菜单行区分稳定。"""
    s = ln.strip().lstrip("#-*>| ").strip()
    if not s:
        return True                       # 头部区的空行一并跳过
    if len(s) < 8:
        return True                       # 极短行(小中大/提示：/方便，快捷)带不带句读都是页头噪声
    if re.search(r"[。；！？：，,]", s):
        return False                      # 有句读 = 正文(抓取正文常用半角逗号, 一并算)
    toks = s.split()
    if len(toks) >= 4 and sum(len(t) for t in toks) / len(toks) <= 5:
        return True                       # 栏目菜单: ≥4 个短词条并排
    if ">" in s and len(s) <= 30:
        return True                       # 面包屑: 首页 > 财经频道 > 正文
    return len(s) < 16                    # 孤立短词/短行(正文标题行通常更长或带句读)


def _skip_page_head(full: str) -> str:
    """跳过站点导航头: 优先从正文首个 H1 起; 无 H1 则逐行跳过菜单/面包屑/短行噪声。"""
    m = re.search(r"(?m)^#\s", full)
    if m and m.start() < 2000:
        return full[m.start():]
    lines = full.split("\n")
    for i, ln in enumerate(lines):
        if not _is_nav_line(ln):
            return "\n".join(lines[i:])
    return full


def _url_is_safe_public(url: str) -> bool:
    """SSRF 防护: 只放行 http(s) 且解析到公网 IP 的 URL, 挡环回/内网/链路本地(含云元数据 169.254.x)/组播/保留地址。
    抓取虽由外部 Firecrawl/Jina 代抓(不直连内网), 仍做纵深防御, 避免本端点被当开放抓取代理指向内部目标。"""
    try:
        u = urlparse(url)
        if u.scheme not in ("http", "https"):
            return False
        host = (u.hostname or "").strip().rstrip(".").lower()
        if not host:
            return False
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
                return False
        return True
    except Exception:
        return False


class InterpretIn(BaseModel):
    title: str
    content: Optional[str] = ""
    code: Optional[str] = None
    name: Optional[str] = None
    source: Optional[str] = None
    time: Optional[str] = None
    url: Optional[str] = None      # 带原文链接的快讯: 正文薄时抓全文补足


_INTERPRET_SYS = (
    "你是 A 股资讯解读助手。只解释新闻, 严禁任何操作建议(买入/卖出/加仓/减仓/目标价/仓位都不许出)。"
    "用简体中文输出严格 JSON, 三个键:\n"
    '{"what":"这条新闻讲了什么(1-2句)","why":"为什么重要/影响面(1-2句)",'
    '"relation":"跟用户持仓或关注板块什么关系(没有就写\'与你当前持仓无直接关系\')"}'
    "\n只输出 JSON, 不要多余文字。"
)


async def _all_holdings_desc() -> str:
    """全部在持资产描述(给 LLM 当上下文): A股 + 场外基金/ETF/加密/机器人/理财。
    此前只取 A股 holdings 表, 解读只认 A股; 这里补上 external_assets。已清仓(份额 0)过滤。"""
    parts = []
    try:
        a = [h for h in await get_all_holdings() if float(h.get("shares") or 0) > 0]
        if a:
            parts.append("A股: " + ", ".join(f"{h.get('stock_name','')}({h['stock_code']})" for h in a))
    except Exception:
        pass
    try:
        from database import list_external_assets
        label = {"FUND": "基金/ETF", "CRYPTO": "加密", "BOT": "量化机器人", "WEALTH": "理财", "CASH": "现金"}
        byt: dict[str, list] = {}
        for x in await list_external_assets():
            t = x.get("asset_type")
            nm = (x.get("name") or "").strip()
            if not nm:
                continue
            # 基金/ETF/加密 看份额>0; 机器人/理财/现金是余额型, 有成本即算在持
            if t in ("FUND", "CRYPTO"):
                if float(x.get("shares") or 0) <= 0:
                    continue
            elif float(x.get("cost_amount") or 0) <= 0 and not x.get("manual_value"):
                continue
            lst = byt.setdefault(label.get(t, t), [])
            if nm not in lst:
                lst.append(nm)
        for lbl in ("基金/ETF", "加密", "量化机器人", "理财"):   # 现金对新闻无关, 略
            if byt.get(lbl):
                parts.append(f"{lbl}: " + ", ".join(byt[lbl][:20]))
    except Exception:
        pass
    return " | ".join(parts) or "(无持仓信息)"


@router.post("/interpret")
async def interpret_news(data: InterpretIn):
    content = (data.content or "").strip()
    body_excerpt = ""
    # 正文只是摘要/teaser(金十快讯多为一行, 图文分析带"点击查看"链接指向全文)且带原文链接时,
    # 抓全文补足 → 解读基于全文而非一句话摘要。阈值放宽到 600, 覆盖带 teaser 的图文分析;
    # 已是长正文(≥600)就不再抓。只对解析到公网地址的 URL 抓取(SSRF 防护)。
    if data.url and len(content) < 600 and await asyncio.to_thread(_url_is_safe_public, data.url):
        try:
            full = ""
            # 首选 DOM 级提取(bs4 按 <p> 密度定位正文容器, 站点导航/分享条整块剔除)
            from services.news import extract_article_text
            art = await asyncio.to_thread(extract_article_text, data.url)
            if art and len(art.get("text") or "") >= 200:
                full = art["text"]
            else:
                # 回退: Firecrawl/Jina 整页 markdown + 行级启发式清洗(JS 渲染页/海外站)
                from services.stock_agent import _fetch_url_markdown_sync
                md = await asyncio.to_thread(_fetch_url_markdown_sync, data.url)
                full = (md or {}).get("markdown") or ""
                if full and not md.get("error"):
                    full = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", full)        # 去图片 markdown
                    full = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", full)     # 链接只留文字
                    full = re.sub(r"\n{3,}", "\n\n", full).strip()
                    full = _skip_page_head(full)          # 跳过站点导航头(H1锚点/菜单/面包屑/短行)
                else:
                    full = ""
            if full:
                full = _trim_article_tail(full.strip())   # 砍尾部版权/分享/评论/热门排行等噪声
                if len(full) > len(content):       # 抓到的比原摘要更全才替换
                    content = full[:3000]          # 喂给 LLM 的正文(限长控 token)
                body_excerpt = full[:7000]         # 展示给前端的原文全文
        except Exception:
            pass
    key = hashlib.sha1(f"{data.title}|{content}|{data.code or ''}|{data.url or ''}".encode("utf-8")).hexdigest()
    if key in _INTERPRET_CACHE:
        return {**_INTERPRET_CACHE[key], "cached": True}
    hold_desc = await _all_holdings_desc()
    rel = f"[{data.code}{('-'+data.name) if data.name else ''}] " if data.code else ""
    user_prompt = (
        f"用户持仓: {hold_desc}\n\n"
        f"新闻标题: {rel}{data.title}\n"
        f"新闻正文: {content or '(无正文, 仅标题)'}\n\n请按要求输出 JSON。"
    )
    try:
        raw = await asyncio.to_thread(_llm.call_claude, user_prompt, _INTERPRET_SYS, "claude-sonnet-5", 600)
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
        "body": body_excerpt,      # 抓到的原文摘录(仅带链接且成功时非空), 供前端展示
    }
    _INTERPRET_CACHE[key] = out
    return {**out, "cached": False}
