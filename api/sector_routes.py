"""Sector radar endpoints."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter

from database import get_all_holdings
from services.sector_compare import get_sector_compare
from services.sector_scanner import scan_sectors
from services.sector_us import scan_us_sectors
from services.sector_hk import scan_hk_sectors
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
    """A 股全板块扫描: 90 个 THS 板块的 1d/5d/30d 涨幅 + 持仓标记 + 兜底 ETF."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings if is_a_share(h["stock_code"])] if holdings else []
    return await scan_sectors(held_codes, force=force)


@router.get("/scan-us")
async def scan_us(force: bool = False):
    """美股板块扫描: 11 个 GICS 板块 (SPDR Sector ETFs)."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings if str(h.get("stock_code", "")).upper().startswith("US.")] if holdings else []
    return await scan_us_sectors(held_codes, force=force)


@router.get("/scan-hk")
async def scan_hk(force: bool = False):
    """港股板块扫描: 12 个恒生综合行业指数."""
    holdings = await get_all_holdings()
    held_codes = [h["stock_code"] for h in holdings if str(h.get("stock_code", "")).upper().startswith("HK.")] if holdings else []
    return await scan_hk_sectors(held_codes, force=force)
