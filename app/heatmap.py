"""Zone heatmap — visit_count, avg dwell, normalised intensity [0,100].

Returns low-confidence flag if total sessions < 20.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, select

from .db import events_table, session_scope
from .metrics import _today_window


async def compute_heatmap(store_id: str, now: datetime | None = None) -> dict[str, Any]:
    start, end = _today_window(now)
    e = events_table.c

    async with session_scope() as s:
        # Entries for confidence denominator.
        entries_q = select(func.count(func.distinct(e.visitor_id))).where(
            and_(
                e.store_id == store_id,
                e.timestamp >= start,
                e.timestamp < end,
                e.event_type == "ENTRY",
                e.is_staff.is_(False),
            )
        )
        entries = int((await s.execute(entries_q)).scalar() or 0)

        # Per-zone visit counts from ZONE_ENTER.
        visits_q = (
            select(e.zone_id, func.count(), func.avg(e.dwell_ms))
            .where(
                and_(
                    e.store_id == store_id,
                    e.timestamp >= start,
                    e.timestamp < end,
                    e.event_type.in_(["ZONE_ENTER", "ZONE_DWELL"]),
                    e.is_staff.is_(False),
                    e.zone_id.isnot(None),
                )
            )
            .group_by(e.zone_id)
        )
        rows = (await s.execute(visits_q)).all()

    zones = [
        {
            "zone_id": r[0],
            "visit_count": int(r[1] or 0),
            "avg_dwell_ms": round(float(r[2] or 0), 2),
        }
        for r in rows
    ]

    max_visits = max((z["visit_count"] for z in zones), default=0)
    for z in zones:
        z["intensity"] = round((z["visit_count"] / max_visits) * 100, 2) if max_visits else 0.0

    return {
        "store_id": store_id,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "total_sessions": entries,
        "data_confidence": "low" if entries < 20 else "normal",
        "zones": zones,
    }
