"""工具缺口探测: agent 调工具时取不到数就记一笔。

为什么做这个: 实测 agent 的错误质量集中在取数层, 而不是推理层。2026-08-19 一天里
四次翻车, 三次是取数 ——
  · get_news 对美股返回 10 条(模型因此以为消息面已覆盖), 但最新一条比异动早三天
  · 美股行情把 开/高/低/成交量 全返 0, 而源里同一行就有
  · 上游 529 一次不重试
这类"看着成功、其实没数"的情况最难被发现: 工具没报错, 模型也就照着空数据往下答。
不记账就只能等用户看出来。

四类缺口:
  error       工具直接报错
  empty       返回了但没内容(列表全空 / note 里写着暂无)
  stale       有数, 但最新时点早于今天 —— 附上早几天
  zero_fields 关键字段被填成 0(价格有值而 开/高/低 全 0 就是典型的解析漏读)

只按 (工具, 类型, 市场) 聚合计数, 不存每次调用: 目的是"哪类数据长期取不到", 不是审计。
"""
from __future__ import annotations
import json
import re
from datetime import datetime, timedelta, timezone

_CST = timezone(timedelta(hours=8))

# 有数但"早于今天"才算 stale 的工具: 只对时效性强的记, 基本面/公司简介本来就不天天变
_TIME_SENSITIVE = {"get_news", "get_market_news", "get_announcements", "get_lhb",
                   "get_fund_flow", "get_inst_flow", "get_hot_rank", "get_hot_concepts"}
# 从返回里找"最新时点"的键
_TIME_KEYS = ("latest_time", "last_date", "as_of", "date", "time")
# 价格类工具: price 有值而这些全 0 → 解析漏读
_PRICE_FIELDS = ("open", "high", "low")
# 隔几个交易日算过期 —— 2 才能滤掉"隔夜"与"周末"这两类正常情况
_STALE_SESSIONS = 2
# 这些工具"返回空"是正常结果而不是缺口: 每天只有几十只票上龙虎榜, 绝大多数票查了就是没有。
# 不排除的话台账顶部会被它们长期占满, 真正的覆盖漏洞反而看不见。
_EMPTY_IS_NORMAL = {"get_lhb", "get_inst_flow", "get_announcements", "get_red_flags",
                    "get_seat_history"}


def _market_of(args: dict) -> str:
    code = str((args or {}).get("code") or (args or {}).get("query") or "").upper()
    if code.startswith("US.") or re.fullmatch(r"[A-Z]{1,5}", code):
        return "US"
    if code.startswith("HK.") or re.fullmatch(r"\d{5}", code):
        return "HK"
    if re.fullmatch(r"\d{6}", code):
        return "A"
    return ""


def _today() -> str:
    return datetime.now(_CST).strftime("%Y-%m-%d")


def _is_session(d, market: str) -> bool:
    mkt = {"A": "CN", "HK": "HK", "US": "US"}.get(market, "CN")
    try:
        from services.dca import _is_market_trading_day
        return _is_market_trading_day(d, mkt)
    except Exception:
        return d.weekday() < 5


def _stale_cutoff(market: str = ""):
    """新鲜度的基准日 = 最近一个交易日(可能就是今天)。"""
    d = datetime.now(_CST).date()
    for _ in range(10):
        if _is_session(d, market):
            return d
        d -= timedelta(days=1)
    return datetime.now(_CST).date()


def _sessions_between(d, base, market: str) -> int:
    """d 之后到 base 之间隔了几个交易日。

    为什么不用日历天数: 周五的新闻到周一差 3 个日历天, 但只隔 1 个交易日 —— 按日历算
    每个周一早上都会误报。凌晨看到前一天的新闻同理(隔 0~1 个交易日, 完全正常)。
    实测阈值取 2: 隔 2 个交易日以上才算真的没覆盖(MRVL 那次是 8-16 对 8-19)。
    """
    n, cur = 0, d + timedelta(days=1)
    while cur <= base and n < 40:
        if _is_session(cur, market):
            n += 1
        cur += timedelta(days=1)
    return n


def _looks_empty(out: dict) -> bool:
    """有键但全是空容器 —— 典型是 {"news": [], "note": "暂无个股新闻"}。"""
    meaningful = False
    for k, v in out.items():
        if k.startswith("_") or k in ("note", "code", "name", "error"):
            continue
        if isinstance(v, (list, dict, str)):
            if v:
                meaningful = True
        elif v not in (None, 0, 0.0):
            meaningful = True
    return not meaningful


def classify(tool: str, args: dict, out, cutoff=None) -> tuple[str, str] | None:
    """判定这次调用是不是一个缺口。返回 (kind, detail) 或 None。

    cutoff: 新鲜度基准日, 默认取该市场的上一个交易日(测试里可以直接传)。
    """
    if not isinstance(out, dict):
        return None if out else ("empty", "返回空")

    if out.get("error"):
        return ("error", str(out["error"])[:120])

    note = str(out.get("note") or "")
    if tool not in _EMPTY_IS_NORMAL:
        if _looks_empty(out):
            return ("empty", note[:120] or "所有字段都为空")
        if "暂无" in note or "无数据" in note:
            return ("empty", note[:120])

    # 价格类: price 有值而 开/高/低 全 0 → 源里大概率有, 是解析漏读
    if out.get("price") and all(
            (out.get(k) in (0, 0.0, None)) for k in _PRICE_FIELDS if k in out):
        if any(k in out for k in _PRICE_FIELDS):
            return ("zero_fields", f"price={out['price']} 而 {'/'.join(_PRICE_FIELDS)} 全 0")

    if tool in _TIME_SENSITIVE:
        market = _market_of(args)
        base = cutoff or _stale_cutoff(market)
        for k in _TIME_KEYS:
            v = str(out.get(k) or "")
            m = re.match(r"(\d{4}-\d{2}-\d{2})", v)
            if not m:
                continue
            try:
                d = datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except ValueError:
                continue
            gap = _sessions_between(d, base, market)
            if gap >= _STALE_SESSIONS:
                return ("stale", f"{k}={m.group(1)}, 距 {base} 隔了 {gap} 个交易日")
            break
    return None


async def record(tool: str, args: dict, out) -> None:
    """best-effort 记账。任何异常都不能连累工具调用本身。"""
    try:
        hit = classify(tool, args, out)
        if not hit:
            return
        kind, detail = hit
        market = _market_of(args)
        sample = json.dumps(args or {}, ensure_ascii=False)[:160]
        import aiosqlite
        # 用单例 config, 不是 Config 类 —— 类属性只是 dataclass 的默认值, 改它不生效,
        # 会静默连到默认的 portfolio.db(真实账本)。这个坑今天踩了三次。
        from config import config as _cfg
        async with aiosqlite.connect(_cfg.db_path) as db:
            await db.execute(
                """INSERT INTO tool_gap (tool, kind, market, hits, detail, sample)
                   VALUES (?, ?, ?, 1, ?, ?)
                   ON CONFLICT(tool, kind, market) DO UPDATE SET
                     hits = hits + 1, detail = excluded.detail,
                     sample = excluded.sample, last_seen = CURRENT_TIMESTAMP""",
                (tool, kind, market, detail, sample))
            await db.commit()
    except Exception:
        pass          # 记账失败不该影响回答


async def report(min_hits: int = 1) -> list[dict]:
    """读台账, 按命中次数倒序。"""
    import aiosqlite
    from config import config as _cfg
    async with aiosqlite.connect(_cfg.db_path) as db:
        cur = await db.execute(
            """SELECT tool, kind, market, hits, detail, sample, first_seen, last_seen
               FROM tool_gap WHERE hits >= ? ORDER BY hits DESC, tool""", (min_hits,))
        rows = await cur.fetchall()
    cols = ("tool", "kind", "market", "hits", "detail", "sample", "first_seen", "last_seen")
    return [dict(zip(cols, r)) for r in rows]
