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


def _parse_info(kind: str, raw: str) -> tuple:
    """→ (描述, 关键幅度%)。关键幅度按事件类型取最有信息量的那个数(速度类=急变速度,
    大单/封板/开板/60日=当日涨跌幅, 竞价类=竞价幅度), 前端右列大字上色显示。"""
    p = [x for x in str(raw or "").split(",") if x != ""]
    try:
        if kind in ("火箭发射", "快速反弹", "高台跳水", "加速下跌"):
            v = float(p[0])
            if kind in ("高台跳水", "加速下跌") and v > 0:
                v = -v                       # 跳水类东财给绝对值, 按方向补负号
            return f"价 {float(p[1]):g}", round(v * 100, 2)
        if kind in ("竞价上涨", "竞价下跌", "高开5日线", "低开5日线"):
            return f"价 {float(p[1]):g}", round(float(p[0]) * 100, 2)
        if kind in ("大笔买入", "大笔卖出", "有大买盘", "有大卖盘"):
            return f"金额 {_wan(p[3])} · 价 {float(p[1]):g}", round(float(p[2]) * 100, 2)
        if kind in ("封涨停板", "封跌停板"):
            return f"价 {float(p[0]):g} · 封单 {float(p[1]) / 100:.0f}手", round(float(p[3]) * 100, 2)
        if kind in ("打开涨停板", "打开跌停板"):
            return f"价 {float(p[0]):g}", round(float(p[1]) * 100, 2)
        if kind in ("60日新高", "60日新低", "60日大幅上涨", "60日大幅下跌"):
            return f"价 {float(p[0]):g}", round(float(p[-1]) * 100, 2)
        if kind in ("向上缺口", "向下缺口"):
            return f"缺口 · {' / '.join(f'{float(x):g}' for x in p[:2])}", None
    except (ValueError, IndexError, TypeError):
        pass
    return str(raw or "")[:24], None


def _fetch_kind_sync(kind: str) -> tuple:
    """→ (展示行[最新60条, 含解析后的描述/幅度], 全天统计元组[(时间,code,name), ...])。
    统计走整列向量化(全天不截断), 逐行解析只花在展示条上。东财返回按时间倒序。"""
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    try:
        df = ak.stock_changes_em(symbol=kind)
    except Exception:
        return [], []
    if df is None or not len(df):
        return [], []
    stats = list(zip(df["时间"].astype(str), df["代码"].astype(str), df["名称"].astype(str)))
    out = []
    for _, r in df.head(60).iterrows():
        code = str(r.get("代码") or "")
        desc, pct = _parse_info(kind, r.get("相关信息"))
        out.append({"时间": str(r.get("时间") or ""), "code": code,
                    "name": str(r.get("名称") or ""), "类型": kind,
                    "up": kind in _UP_KINDS, "pct": pct, "描述": desc})
    return out, stats


def _sec(t: str) -> int:
    try:
        h, m, s = (int(x) for x in t.split(":"))
        return h * 3600 + m * 60 + s
    except ValueError:
        return -1


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

    disp: list = []
    stat: list = []          # (时间, code, name, kind, up) 全天
    for kind, (part, st) in zip(GROUPS[group], await asyncio.gather(*(_one(k) for k in GROUPS[group]))):
        disp += part
        up = kind in _UP_KINDS
        stat += [(t, c, nm, kind, up) for t, c, nm in st]
    disp.sort(key=lambda x: x["时间"], reverse=True)

    # 统计一律基于全天全量, 展示流只保留最新 120 条
    from collections import Counter
    kind_cnt = Counter(s[3] for s in stat)
    kinds = [{"kind": k, "n": n, "up": k in _UP_KINDS}
             for k, n in sorted(kind_cnt.items(), key=lambda x: -x[1])]

    # 异动最活跃: 同一只股票今天反复触发(次数≥2 才有信息量)
    code_cnt = Counter(s[1] for s in stat)
    names = {s[1]: s[2] for s in stat}
    hot = [{"code": c, "name": names.get(c, c), "n": n}
           for c, n in code_cnt.most_common(6) if n >= 2]
    for r in disp:
        n = code_cnt[r["code"]]
        if n >= 3:
            r["n_today"] = n

    # 全天脉搏: 5 分钟一档的拉升/跳水事件数(前端画迷你分布条)。
    # 竞价类全部集中在 9:15-9:25 一档, 会把盘中档位压扁, 不计入分布(单独看竞价组)
    bucket: dict = {}
    auction = set(GROUPS["竞价"])
    for t, _c, _nm, kind, up in stat:
        s = _sec(t)
        if s < 0 or kind in auction:
            continue
        key = s // 300 * 300
        b = bucket.setdefault(key, [0, 0])
        b[0 if up else 1] += 1
    buckets = [{"t": f"{k // 3600:02d}:{k % 3600 // 60:02d}", "up": v[0], "down": v[1]}
               for k, v in sorted(bucket.items())]

    # 近30分钟计数(以全天流最新事件时间为锚, 收盘后看的是尾盘30分钟)
    n_up = n_down = 0
    if stat:
        lo_sec = max(_sec(s[0]) for s in stat) - 1800
        for t, _c, _nm, _k, up in stat:
            if _sec(t) >= lo_sec:
                n_up += 1 if up else 0
                n_down += 0 if up else 1

    # rows 不合并截断: 每类各带最新60条, 前端选具体类型时才不会被高频类挤没
    out = {"group": group, "rows": disp,
           "kinds": kinds, "hot": hot, "buckets": buckets,
           "pulse": {"近30分钟拉升类": n_up, "近30分钟跳水类": n_down},
           "note": ("交易所盘口异动事件流(东财), 盘中随时滚动、收盘后显示当日全程。"
                    "竞价类为 9:15-9:25 集合竞价产物。纯客观事件, 不构成任何买卖建议。")}
    if disp:
        _cache[group] = (out, time.time())
    return out
