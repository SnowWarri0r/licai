"""Sector radar endpoints."""
from __future__ import annotations
import asyncio
from fastapi import APIRouter

from database import get_all_holdings
from services.sector_compare import get_sector_compare

router = APIRouter(prefix="/api/sector", tags=["sector"])


@router.get("/compare/{stock_code}")
async def compare_one(stock_code: str, force: bool = False):
    return await get_sector_compare(stock_code, force=force)


@router.get("/compare-all")
async def compare_all(force: bool = False):
    holdings = await get_all_holdings()
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
