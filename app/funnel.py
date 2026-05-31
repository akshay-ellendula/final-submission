"""Session-based conversion funnel.

Stages: Entry → ZoneVisit → BillingQueue → Purchase.
A session is keyed by (store_id, visitor_id) across the day. REENTRY events
collapse into the same session (no double count).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import and_, select

from .db import events_table, pos_transactions_table, session_scope
from .metrics import _today_window


@dataclass(frozen=True)
class FunnelStage:
    name: str
    count: int
    drop_off_pct_from_prev: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.name,
            "count": self.count,
            "drop_off_from_prev_pct": round(self.drop_off_pct_from_prev, 2),
        }


async def compute_funnel(store_id: str, now: datetime | None = None) -> dict[str, Any]:
    start, end = _today_window(now)
    e = events_table.c
    p = pos_transactions_table.c

    async with session_scope() as s:
        # All non-staff events today for this store.
        rows = (await s.execute(
            select(e.visitor_id, e.event_type, e.timestamp).where(
                and_(
                    e.store_id == store_id,
                    e.timestamp >= start,
                    e.timestamp < end,
                    e.is_staff.is_(False),
                )
            )
        )).all()
        pos_rows = (await s.execute(
            select(p.timestamp).where(
                and_(p.store_id == store_id, p.timestamp >= start, p.timestamp < end)
            )
        )).all()

    entries: set[str] = set()
    zone_visitors: set[str] = set()
    billing_visitors: set[str] = set()
    billing_joins: list[tuple[str, datetime]] = []
    
    for vid, etype, ts in rows:
        if etype in ("ENTRY", "REENTRY"):
            entries.add(vid)
        if etype in ("ZONE_ENTER",):
            zone_visitors.add(vid)
        if etype == "BILLING_QUEUE_JOIN":
            billing_visitors.add(vid)
            billing_joins.append((vid, ts))

    pos_timestamps = [r[0] for r in pos_rows]
    purchasers = set()
    from datetime import timedelta
    for vid, join_ts in billing_joins:
        if vid in purchasers:
            continue
        for pts in pos_timestamps:
            if timedelta(seconds=0) <= pts - join_ts <= timedelta(minutes=5):
                purchasers.add(vid)
                break

    # Funnel monotonicity: a visitor in a later stage must have entered the store.
    # ZoneVisit and BillingQueue are siblings (ZONE_ENTER isn't a hard prerequisite
    # for joining a billing queue — a customer may head straight to checkout), so
    # each is clamped to the Entry set independently rather than chained.
    zone_visitors &= entries
    billing_visitors &= entries
    purchasers &= billing_visitors

    counts = [
        ("Entry", len(entries)),
        ("ZoneVisit", len(zone_visitors)),
        ("BillingQueue", len(billing_visitors)),
        ("Purchase", len(purchasers)),
    ]
    stages: list[FunnelStage] = []
    prev_count = 0
    for idx, (name, cnt) in enumerate(counts):
        if idx == 0:
            drop = 0.0
        else:
            drop = ((prev_count - cnt) / prev_count * 100.0) if prev_count > 0 else 0.0
        stages.append(FunnelStage(name=name, count=cnt, drop_off_pct_from_prev=drop))
        prev_count = cnt

    conversion_rate = (len(purchasers) / len(entries)) if entries else 0.0

    return {
        "store_id": store_id,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "total_sessions": len(entries),
        "conversion_rate": round(conversion_rate, 4),
        "stages": [s.to_dict() for s in stages],
    }
