"""Sector radar endpoints."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter

from database import get_all_holdings
from services.sector_compare import get_sector_compare
from services.sector_scanner import scan_sectors
from services.market_data import is_a_share

router = APIRouter(prefix="/api/sector", tags=["sector"])


@router.get("/compare/{stock_code}")
async def compare_one(stock_code: str, force: bool = False):
    return await get_sector_compare(stock_code, force=force)


@router.get("/compare-all")
async def compare_all(force: bool = False):
    holdings = await get_all_holdings()
    holdings = [h for h in holdings if is_a_share(h["stock_code"])]
    if not holdings:
        return {"holdings": []}
    results = await asyncio.gather(
        *(get_sector_compare(h["stock_code"], force=force) for h in holdings),
        return_exceptions=True,
    )
    out = []
    for h, r in zip(holdings, results):
        if isinstance(r, Exception):
            continue
        r["stock_name"] = h.get("stock_name", "")
        out.append(r)
    return {"holdings": out}


@router.get("/scan")
async def scan(force: bool = False):
    """全板块扫描: 90 个 THS 板块的 1d/5d/30d 涨幅 + 持仓标记 + 兜底 ETF.

    用于发现"未持仓但有动量"的板块. 5d/30d 仅 top 30 (按 1d) 拉 K 线.
    """
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings if is_a_share(h["stock_code"])] if holdings else []
    return await scan_sectors(held_codes, force=force)
