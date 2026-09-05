"""一次决策被拆成几笔成交 —— 复盘之前先合回去。

起因: 8-25 买紫光股份, 账本里是两条 —— 200股@34.93 和 100股@34.94, 同一分钟(10:06)。
那不是两次决策, 是一次委托的两笔成交(券商分笔成交/分两次录入都会这样)。可 AI 复盘拿到的
是两行, 于是点评成「一次决策被拆成两下手」, 还煞有介事地说"间隔极小" —— 说的其实是它自己
把一笔看成了两笔。同一份重复还会让「当前轮 ≥3 笔买入 = 反复补仓」提前触发。

判据故意收窄: 同日 + 同标的 + 同方向 + 与簇内首笔价差 ≤4 个跳板 + (两边都有时间时)同一分钟。

**价差为什么按跳板不按百分比** (9-01 生益科技那次的教训): 原来用 ≤0.3%, 于是
148.62 + 148.95 同在 09:32 被合成一笔 —— 用户说那是两次下单, 不是一次委托的分笔。
一次委托的分笔成交差的是"吃掉盘口几档", 是**跳板数**, 而 A 股跳板固定 0.01: 同一个 0.3%
在 34.9 元的票上是 10 个跳板, 在 148 元的票上是 44 个 —— 票价越高白拿的窗口越宽, 单位错了。
按跳板重算全部 33 组同日同向记录: 紫光那次(1 跳板)照旧合, 生益那次(33 跳板)分开, 合并
27 处降到 21 处, 剩下的全是同一分钟内的。

**方向性选择**: 少合 > 多合。少合了, 那个行为你在复盘里看得见, 自己驳回一句就行; 多合了,
行为被悄悄抹掉, 镜子就照不出来了。所以模糊地带(隔了两三分钟、差十几个跳板)一律按两次决策算。

**与佣金那套的关系**: services.position_ledger._order_key 也在判"同一张委托"(整张单只收一次
5 元最低佣金), 键是 代码+日期+方向+**成交时刻**、完全不看价格。本模块要求同一分钟 + 跳板收窄,
所以合并结果必定是它的**子集**: 凡被当成一次决策的必定是同一张委托, 反之不然(一张大单扫穿
好几档, 佣金只收一次, 但复盘会当成两次决策 —— 宁可如此)。tests 里焊了这条包含关系。

合并后保留 fills/fill_detail: 原始成交没有被抹掉, 只是不再被当成两次决策。
"""
from __future__ import annotations

_MAX_TICKS = 4            # 跳板: 分笔成交通常只吃掉盘口一两档
_TICK = {"stock": 0.01}   # A股最小变动价位; 场内ETF/场外基金按 0.001(净值 4 位小数)
_TICK_DEFAULT = 0.001


def _mins(hhmm) -> int | None:
    try:
        h, m = str(hhmm).split(":")[:2]
        return int(h) * 60 + int(m)
    except Exception:
        return None


def _tick(t: dict) -> float:
    return _TICK.get(t.get("asset_class"), _TICK_DEFAULT)


def tick_for_code(code: str) -> float:
    """按代码定最小变动价位: A股 0.01; 场内 ETF/LOF 报价到 0.001。

    佣金那侧只有 stock_code(没有 asset_class), 所以单独给一个入口 —— 但用的还是同一套跳板数。
    """
    from services.market_data import _is_etf_lof
    return _TICK_DEFAULT if _is_etf_lof(code or "") else 0.01


def _near(first: dict, prev: dict, t: dict, price_of, time_of, tick: float) -> bool:
    """跟簇里的**首笔**比价(不跟上一笔比, 否则 34.90→34.93→34.96 会一路串成一簇),
    跟**上一笔**比时间(连续成交是一串)。"""
    p0, p = float(price_of(first) or 0), float(price_of(t) or 0)
    if p0 <= 0 or p <= 0:
        return False
    if round(abs(p - p0) / tick) > _MAX_TICKS:
        return False
    a, b = _mins(time_of(prev)), _mins(time_of(t))
    if a is not None and b is not None and a != b:   # 跨了分钟就不算一串
        return False
    return True


def one_order_clusters(rows: list, *, price_of, time_of, tick: float) -> list[list[int]]:
    """把「同一张委托的多笔成交」聚成簇, 返回**输入下标**的分组。

    这是"同一张委托"在本项目里的**唯一定义**。复盘(merge_fills)和佣金
    (position_ledger.allocate_trade_fees)都调它 —— 两边各自演化过一次, 结果对同一件事给出
    过两个答案(8-13 沪电 09:43+09:45: 复盘算一次决策、佣金算两张委托), 所以收成一份。

    调用方负责先切到同标的 / 同日 / 同方向; 这里只管价和时间。
    """
    def _t(i):
        m = _mins(time_of(rows[i]))
        return (0 if m is None else m, i)
    clusters: list[list[int]] = []
    cur: list[int] = []
    for i in sorted(range(len(rows)), key=_t):
        if cur and _near(rows[cur[0]], rows[cur[-1]], rows[i], price_of, time_of, tick):
            cur.append(i)
        else:
            if cur:
                clusters.append(cur)
            cur = [i]
    if cur:
        clusters.append(cur)
    return clusters


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
        for cl in one_order_clusters(g, price_of=lambda t: t.get("price"),
                                     time_of=lambda t: t.get("time"), tick=_tick(g[0])):
            out.append(_fold([g[i] for i in cl]))
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
