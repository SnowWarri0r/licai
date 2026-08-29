"""一次决策被拆成几笔成交 —— 复盘之前先合回去。

起因: 8-25 买紫光股份, 账本里是两条 —— 200股@34.93 和 100股@34.94, 同一分钟(10:06)。
那不是两次决策, 是一次委托的两笔成交(券商分笔成交/分两次录入都会这样)。可 AI 复盘拿到的
是两行, 于是点评成「一次决策被拆成两下手」, 还煞有介事地说"间隔极小" —— 说的其实是它自己
把一笔看成了两笔。同一份重复还会让「当前轮 ≥3 笔买入 = 反复补仓」提前触发。

判据故意收窄: 同日 + 同标的 + 同方向 + 与簇内首笔价差 ≤0.3% + (两边都有时间时)间隔 ≤10 分钟。
价差再放宽就会把真正的追高(34.9 买完 36.5 又追)一起合掉 —— 而那恰恰是复盘该点出来的东西。
合并后保留 fills/fill_detail: 原始成交没有被抹掉, 只是不再被当成两次决策。
"""
from __future__ import annotations

_PRICE_TOL = 0.003        # 0.3%: 分笔成交的价差通常是一两个跳板
_TIME_GAP_MIN = 10        # 分钟; 只有两边都带时间时才用这一条


def _mins(hhmm) -> int | None:
    try:
        h, m = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _close_enough(first: dict, prev: dict, t: dict) -> bool:
    """跟簇里的**首笔**比价(不跟上一笔比, 否则 34.9→35.0→35.1 会一路串成一簇),
    跟**上一笔**比时间(连续成交是一串)。"""
    p0, p = float(first.get("price") or 0), float(t.get("price") or 0)
    if p0 <= 0 or p <= 0 or abs(p - p0) / p0 > _PRICE_TOL:
        return False
    a, b = _mins(prev.get("time")), _mins(t.get("time"))
    if a is not None and b is not None and abs(b - a) > _TIME_GAP_MIN:
        return False
    return True


def _fold(cluster: list[dict]) -> dict:
    if len(cluster) == 1:
        return cluster[0]
    shares = sum(float(t.get("shares") or 0) for t in cluster)
    cost = sum(float(t.get("price") or 0) * float(t.get("shares") or 0) for t in cluster)
    vwap = cost / shares if shares > 0 else float(cluster[0].get("price") or 0)
    nd = 3 if cluster[0].get("asset_class") == "stock" else 4
    out = {**cluster[0], "price": round(vwap, nd), "shares": shares,
           "fills": len(cluster),
           "fill_detail": [{"price": t.get("price"), "shares": t.get("shares")} for t in cluster]}
    cur = out.get("current")
    if cur and vwap > 0:                        # 均价变了, 至今% / 命中要跟着重算
        out["pct"] = round((cur - vwap) / vwap * 100, 2)
        out["hit"] = (cur > vwap) if out.get("kind") == "buy" else (cur < vwap)
    if all(t.get("amount") is not None for t in cluster):
        out["amount"] = round(sum(float(t.get("amount") or 0) for t in cluster), 2)
    else:
        out.pop("amount", None)          # 只有首笔带金额的话, 留着就是"三笔的量配一笔的钱"
    ends = [t.get("time") for t in cluster if t.get("time")]
    if ends:
        out["time"], out["time_end"] = ends[0], ends[-1]
    return out


def merge_fills(trades: list[dict]) -> list[dict]:
    """把同一次决策的分笔成交合成一行(均价×总量), 其余原样返回。顺序不保证, 调用方自己排。"""
    groups: dict = {}
    order: list = []
    for t in trades:
        k = (t.get("date"), t.get("code"), t.get("kind"))
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(t)
    out = []
    for k in order:
        g = groups[k]
        if len(g) == 1:
            out.append(g[0])
            continue
        # 有时间的按时间排(连续成交才是一串); 没时间的保持原顺序
        g = sorted(g, key=lambda t: (_mins(t.get("time")) if _mins(t.get("time")) is not None else 0))
        cluster: list[dict] = []
        for t in g:
            if cluster and _close_enough(cluster[0], cluster[-1], t):
                cluster.append(t)
            else:
                if cluster:
                    out.append(_fold(cluster))
                cluster = [t]
        if cluster:
            out.append(_fold(cluster))
    return out


def fills_note(t: dict) -> str:
    """给复盘/界面用的一句说明: 「1笔委托分2次成交: 200@34.93 + 100@34.94」。没合并就是空串。"""
    n = int(t.get("fills") or 1)
    if n < 2:
        return ""
    parts = []
    for f in (t.get("fill_detail") or [])[:4]:
        sh = float(f.get("shares") or 0)
        parts.append(f"{sh:.0f}@{f.get('price')}" if sh == int(sh) else f"{sh:.2f}@{f.get('price')}")
    return f"同一决策分{n}笔成交: " + " + ".join(parts)
