"""流通股本变动表(K 线筹码分布算换手率用)。

来源: 东财股本结构(akshare.stock_zh_a_gbjg_em)的「已上市流通A股」, 每次变动一行(解禁、增发上市、
送转等)。筹码分布只要 换手率 = 成交量(手)×100 / 当日流通股本, 所以只取这一列。
⚠️ K 线的成交量不随送转调整(前复权只调价格), 与这里的真实股数是同一标度, 可以直接相除。
只支持 A 股个股; ETF/LOF 没有这张表(份额天天变), 返回空。打开个股时按需拉一次, 缓存 24h。
"""
from __future__ import annotations

import asyncio
import time

_cache: dict = {}
_TTL = 24 * 3600
_lock = asyncio.Lock()


def _symbol(code: str) -> str | None:
    c = code[-6:]
    if not (len(c) == 6 and c.isdigit()):
        return None
    if c[0] == "6":
        return f"{c}.SH"
    if c[0] in "03":
        return f"{c}.SZ"
    if c[0] in "48" or c[:3] == "920":
        return f"{c}.BJ"
    return None                                   # 5x/1x = 场内基金


def _fetch_sync(sym: str) -> list[list]:
    import os
    for k in list(os.environ):                    # 东财直连, 不走代理(与 exright 同)
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_zh_a_gbjg_em(symbol=sym)
    out = []
    if df is None or not len(df):
        return out
    for _, r in df.iterrows():
        d = str(r.get("变更日期") or "")[:10]
        try:
            v = float(r.get("已上市流通A股"))
        except (TypeError, ValueError):
            continue
        if len(d) == 10 and v > 0:
            out.append([d, v])
    return sorted(out)


async def schedule(code: str) -> list[list]:
    """[[变更日期 YYYY-MM-DD, 已上市流通A股(股)], ...] 升序; 不支持或拉不到返回 []。"""
    sym = _symbol(code)
    if not sym:
        return []
    hit = _cache.get(sym)
    if hit and time.time() - hit[1] < _TTL:
        return hit[0]
    async with _lock:                             # 同一时刻只打一次东财, 免得弹窗连开时并发
        hit = _cache.get(sym)
        if hit and time.time() - hit[1] < _TTL:
            return hit[0]
        try:
            rows = await asyncio.to_thread(_fetch_sync, sym)
        except Exception as e:  # noqa: BLE001
            print(f"[float_shares] {sym} 拉取失败: {e}")
            return []
        if rows:
            _cache[sym] = (rows, time.time())
        return rows
