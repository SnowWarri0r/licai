"""盘口异动(同花顺式): 东财异动事件流——火箭发射/高台跳水/大笔买卖/封板开板/
竞价异动/缺口/60日新高低。纯客观事件呈现, 不构成任何买卖建议。

'相关信息'字段按事件类型编码不同, 逐类解析成人话; 未知类型兜底显示原始首值。
"""
from __future__ import annotations

import asyncio
import time

_cache: dict = {}
_TTL = 45

# 组 → 事件类型(东财盘口异动口径)
GROUPS = {
    "拉升": ["火箭发射", "快速反弹", "大笔买入", "有大买盘", "封涨停板", "打开跌停板",
             "60日新高", "向上缺口", "60日大幅上涨"],
    "跳水": ["高台跳水", "加速下跌", "大笔卖出", "有大卖盘", "封跌停板", "打开涨停板",
             "60日新低", "向下缺口", "60日大幅下跌"],
    "竞价": ["竞价上涨", "竞价下跌", "高开5日线", "低开5日线"],
}
# 全部 = 高频精选(轮询期请求数可控)
GROUPS["全部"] = ["火箭发射", "高台跳水", "大笔买入", "大笔卖出", "封涨停板", "封跌停板",
                  "打开涨停板", "打开跌停板", "竞价上涨", "竞价下跌"]

_UP_KINDS = set(GROUPS["拉升"]) | {"竞价上涨", "高开5日线"}


def _pct(v) -> str:
    return f"{float(v) * 100:+.2f}%"


def _wan(v) -> str:
    x = float(v)
    return f"{x / 1e8:.2f}亿" if abs(x) >= 1e8 else f"{x / 1e4:.0f}万"


def _fmt_info(kind: str, raw: str) -> str:
    p = [x for x in str(raw or "").split(",") if x != ""]
    try:
        if kind in ("火箭发射", "快速反弹", "高台跳水", "加速下跌"):
            v = float(p[0])
            if kind in ("高台跳水", "加速下跌") and v > 0:
                v = -v                       # 跳水类东财给绝对值, 按方向补负号
            return f"急变速度 {_pct(v)} · 价 {float(p[1]):g}"
        if kind in ("竞价上涨", "竞价下跌", "高开5日线", "低开5日线"):
            return f"竞价 {_pct(p[0])} · 价 {float(p[1]):g}"
        if kind in ("大笔买入", "大笔卖出", "有大买盘", "有大卖盘"):
            return f"金额 {_wan(p[3])} · 价 {float(p[1]):g} ({_pct(p[2])})"
        if kind in ("封涨停板", "封跌停板"):
            return f"价 {float(p[0]):g} · 封单 {float(p[1]) / 100:.0f}手 · {_pct(p[3])}"
        if kind in ("打开涨停板", "打开跌停板"):
            return f"价 {float(p[0]):g} · {_pct(p[1])}"
        if kind in ("60日新高", "60日新低", "60日大幅上涨", "60日大幅下跌"):
            return f"价 {float(p[0]):g} · {_pct(p[-1])}"
        if kind in ("向上缺口", "向下缺口"):
            return f"缺口 · {' / '.join(f'{float(x):g}' for x in p[:2])}"
    except (ValueError, IndexError, TypeError):
        pass
    return str(raw or "")[:24]


def _fetch_kind_sync(kind: str) -> list:
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    try:
        df = ak.stock_changes_em(symbol=kind)
    except Exception:
        return []
    if df is None or not len(df):
        return []
    out = []
    for _, r in df.head(80).iterrows():
        code = str(r.get("代码") or "")
        out.append({"时间": str(r.get("时间") or ""), "code": code,
                    "name": str(r.get("名称") or ""), "类型": kind,
                    "up": kind in _UP_KINDS,
                    "描述": _fmt_info(kind, r.get("相关信息"))})
    return out


async def market_changes(group: str = "全部") -> dict:
    """异动事件流(按时间倒序, 最多 120 条) + 近30分钟拉升/跳水事件计数。缓存45s。"""
    group = group if group in GROUPS else "全部"
    c = _cache.get(group)
    if c and time.time() - c[1] < _TTL:
        return c[0]
    sem = asyncio.Semaphore(6)

    async def _one(kind):
        async with sem:
            return await asyncio.to_thread(_fetch_kind_sync, kind)

    rows: list = []
    for part in await asyncio.gather(*(_one(k) for k in GROUPS[group])):
        rows += part
    rows.sort(key=lambda x: x["时间"], reverse=True)
    rows = rows[:120]

    # 近30分钟市场脉搏(以流内最新事件时间为锚, 收盘后看的是尾盘30分钟)
    n_up = n_down = 0
    if rows:
        anchor = rows[0]["时间"]
        try:
            h, m, s = (int(x) for x in anchor.split(":"))
            lo_sec = h * 3600 + m * 60 + s - 1800
            for r in rows:
                hh, mm, ss = (int(x) for x in r["时间"].split(":"))
                if hh * 3600 + mm * 60 + ss >= lo_sec:
                    n_up += 1 if r["up"] else 0
                    n_down += 0 if r["up"] else 1
        except ValueError:
            pass
    out = {"group": group, "rows": rows,
           "pulse": {"近30分钟拉升类": n_up, "近30分钟跳水类": n_down},
           "note": ("交易所盘口异动事件流(东财), 盘中随时滚动、收盘后显示当日全程。"
                    "竞价类为 9:15-9:25 集合竞价产物。纯客观事件, 不构成任何买卖建议。")}
    if rows:
        _cache[group] = (out, time.time())
    return out
