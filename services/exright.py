"""除权精确还原: 把前复权价折回某历史日的真实(不复权)标度。

历史分时(TDX)是当日真实成交价, K线链路是前复权价——除权日之后回看历史分时,
昨收基准若直接用前复权值会错位。这里用分红送配明细(东财, 每次除权的
每股派息/送转比例/除权除息日)做逐事件逆变换, 数学上精确:

  前复权单事件: qfq = (raw - 每股派息) / (1 + 每股送转率)      [配股极罕见, 不在表内, 不处理]
  逆变换(从最近事件回到最早):  raw = qfq × (1 + r) + d

对目标日 D: 取 (D, 今天] 内已实施的除权事件, 从晚到早依次逆变换前复权昨收。
事件表缓存24h; 拉不到返回 None, 调用方回退近似口径。
"""
from __future__ import annotations

import asyncio
import time

_ev_cache: dict = {}
_EV_TTL = 24 * 3600


def _events_sync(code: str) -> list:
    """[(除权除息日 YYYY-MM-DD, 每股派息元, 每股送转率)], 已实施, 按日期升序。"""
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_fhps_detail_em(symbol=code)
    out = []
    if df is None or not len(df):
        return out
    for _, r in df.iterrows():
        day = str(r.get("除权除息日") or "")[:10]
        prog = str(r.get("方案进度") or "")
        if len(day) != 10 or day == "NaT" or "实施" not in prog:
            continue

        def _f(v):
            try:
                x = float(v)
                return x if x == x else 0.0
            except (TypeError, ValueError):
                return 0.0

        d = _f(r.get("现金分红-现金分红比例")) / 10.0     # 每10股派X元 → 每股
        b = _f(r.get("送转股份-送转总比例")) / 10.0       # 每10股送转X股 → 每股率
        if d > 0 or b > 0:
            out.append((day, d, b))
    return sorted(out)


async def _events(code: str) -> list | None:
    c = _ev_cache.get(code)
    if c and time.time() - c[1] < _EV_TTL:
        return c[0]
    try:
        ev = await asyncio.to_thread(_events_sync, code)
    except Exception:
        return None
    _ev_cache[code] = (ev, time.time())
    return ev


async def raw_price_on(code: str, qfq_price: float, date: str) -> tuple:
    """前复权价 → date 当日真实标度。返回 (raw_price, 事件数); 事件表拉不到 → (None, 0)。"""
    if not qfq_price or not date:
        return None, 0
    ev = await _events(code)
    if ev is None:
        return None, 0
    import datetime
    today = datetime.date.today().isoformat()
    hits = [(d, cash, bonus) for d, cash, bonus in ev if date < d <= today]
    px = float(qfq_price)
    for _, cash, bonus in sorted(hits, reverse=True):    # 从最近事件往最早逆回
        px = px * (1 + bonus) + cash
    return round(px, 4), len(hits)
