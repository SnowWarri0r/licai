"""板块占全市场成交额份额: 资金往哪个板块聚拢/从哪撤离。

数据 = 同花顺行业板块汇总(90个, 互斥)。份额 = 板块总成交额 / 全部板块合计。
逐日档案(收盘后定格)攒出"较昨日/较5日前"的份额变化——涨跌幅会骗人,
成交占比的迁移更接近资金的真实走向。纯客观数据, 不构成任何买卖建议。
"""
from __future__ import annotations

import asyncio
import datetime
import time

_cache: dict = {}
_TTL = 600


def _fetch_share_sync() -> list:
    import os
    for k in list(os.environ):
        if "proxy" in k.lower():
            os.environ.pop(k, None)
    import akshare as ak
    df = ak.stock_board_industry_summary_ths()
    if df is None or not len(df):
        return []
    total = float(df["总成交额"].sum()) or 1.0
    rows = []
    for _, r in df.iterrows():
        amt = float(r.get("总成交额") or 0)
        rows.append({"board": str(r.get("板块") or ""),
                     "amount_yi": round(amt, 1),
                     "share_pct": round(amt / total * 100, 2),
                     "pct": float(r.get("涨跌幅") or 0),
                     "net_inflow": round(float(r.get("净流入") or 0), 1),
                     "leader": str(r.get("领涨股") or "")})
    rows.sort(key=lambda x: -x["share_pct"])
    return rows


async def sector_share_view() -> dict:
    """实时份额排行 + 与档案(昨日/5个交易日前)的份额变化。缓存10min。"""
    c = _cache.get("v")
    if c and time.time() - c[1] < _TTL:
        return c[0]
    from database import get_sector_shares_on, list_sector_share_dates
    rows = await asyncio.to_thread(_fetch_share_sync)
    if not rows:
        return {"rows": [], "note": "板块汇总源暂不可达(同花顺抖动)。"}

    today = datetime.date.today().isoformat()
    dates = [d for d in await list_sector_share_dates(12) if d < today]
    prev1 = await get_sector_shares_on(dates[0]) if dates else {}
    prev5 = await get_sector_shares_on(dates[4]) if len(dates) >= 5 else {}
    for r in rows:
        p1, p5 = prev1.get(r["board"]), prev5.get(r["board"])
        r["d1"] = round(r["share_pct"] - p1, 2) if p1 is not None else None
        r["d5"] = round(r["share_pct"] - p5, 2) if p5 is not None else None
    total = round(sum(x["amount_yi"] for x in rows))
    out = {"rows": rows, "total_yi": total,
           "baseline": {"d1": dates[0] if dates else None, "d5": dates[4] if len(dates) >= 5 else None},
           "note": ("份额=板块成交额/全部板块合计(同花顺90行业, 互斥)。份额升=资金聚拢, 跌=退潮; "
                    "与涨跌幅背离时(份额升但板块跌)通常是分歧放量。档案随每日收盘积累, "
                    "变化列在攒够历史后出现。纯客观数据, 不构成任何买卖建议。")}
    _cache["v"] = (out, time.time())
    return out


async def archive_today() -> int:
    """收盘后把今日份额定格进档案(eod loop 调用)。返回入档行数。"""
    from database import save_sector_shares
    rows = await asyncio.to_thread(_fetch_share_sync)
    if rows:
        await save_sector_shares(datetime.date.today().isoformat(), rows)
    return len(rows)
