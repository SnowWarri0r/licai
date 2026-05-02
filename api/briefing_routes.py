"""Morning briefing endpoints."""
from __future__ import annotations
import json
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter

from database import get_briefings_for_date

router = APIRouter(prefix="/api/briefing", tags=["briefing"])


def _today_cst() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=8)).strftime("%Y-%m-%d")


@router.get("")
async def get_today_briefings():
    """Return today's stored briefings (or latest available date if today is empty)."""
    today = _today_cst()
    rows = await get_briefings_for_date(today)
    used_date = today
    if not rows:
        # Fall back to most recent date with any briefing — useful on weekends
        from database import get_db
        db = await get_db()
        try:
            cursor = await db.execute(
                "SELECT MAX(briefing_date) FROM morning_briefings"
            )
            row = await cursor.fetchone()
            latest = row[0] if row else None
        finally:
            await db.close()
        if latest:
            rows = await get_briefings_for_date(latest)
            used_date = latest

    briefings = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except Exception:
            continue
        briefings.append(payload)

    return {
        "date": used_date,
        "is_today": used_date == today,
        "briefings": briefings,
    }


@router.post("/refresh")
async def refresh_briefings():
    """Manually trigger briefing regeneration (sync, may take 10-30s for 3 stocks)."""
    from services.morning_briefing import generate_all_briefings
    results = await generate_all_briefings()
    return {
        "date": _today_cst(),
        "count": len(results),
        "briefings": results,
    }
