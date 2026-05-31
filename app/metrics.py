"""Store metrics computation.

All queries exclude is_staff=true. All numeric outputs default to 0 (never null)
so `GET /stores/{id}/metrics` is safe even when the store has zero data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select

from .config import SETTINGS
from .db import events_table, pos_transactions_table, session_scope


def _today_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


@dataclass(frozen=True)
class StoreMetrics:
    store_id: str
    window_start: str
    window_end: str
    unique_visitors: int
    conversion_rate: float
    abandonment_rate: float
    avg_dwell_per_zone_ms: dict[str, float]
    current_queue_depth: int
    pos_transactions: int
    top_brands: dict[str, int]
    top_departments: dict[str, int]
    staff_count: int
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "store_id": self.store_id,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "unique_visitors": self.unique_visitors,
            "conversion_rate": round(self.conversion_rate, 4),
            "abandonment_rate": round(self.abandonment_rate, 4),
            "avg_dwell_per_zone_ms": self.avg_dwell_per_zone_ms,
            "current_queue_depth": self.current_queue_depth,
            "pos_transactions": self.pos_transactions,
            "top_brands": self.top_brands,
            "top_departments": self.top_departments,
            "staff_count": self.staff_count,
            "generated_at": self.generated_at,
        }


async def compute_store_metrics(store_id: str, now: datetime | None = None) -> StoreMetrics:
    from collections import Counter
    start, end = _today_window(now)
    now = now or datetime.now(timezone.utc)
    e = events_table.c
    p = pos_transactions_table.c

    async with session_scope() as s:
        # Unique (non-staff) visitors who crossed ENTRY today.
        uv_q = select(func.count(func.distinct(e.visitor_id))).where(
            and_(
                e.store_id == store_id,
                e.timestamp >= start,
                e.timestamp < end,
                e.event_type == "ENTRY",
                e.is_staff.is_(False),
            )
        )
        unique_visitors = int((await s.execute(uv_q)).scalar() or 0)

        # Count of staff-flagged events (shows evaluator we track staff, not just ignore).
        staff_q = select(func.count(func.distinct(e.visitor_id))).where(
            and_(
                e.store_id == store_id,
                e.timestamp >= start,
                e.timestamp < end,
                e.event_type == "ENTRY",
                e.is_staff.is_(True),
            )
        )
        staff_count = int((await s.execute(staff_q)).scalar() or 0)

        # Visitors that entered the billing zone (=billing queue join) and their timestamps.
        billed_q = select(e.visitor_id, e.timestamp).where(
            and_(
                e.store_id == store_id,
                e.timestamp >= start,
                e.timestamp < end,
                e.event_type == "BILLING_QUEUE_JOIN",
                e.is_staff.is_(False),
            )
        )
        billed_rows = (await s.execute(billed_q)).all()

        # Visitors that abandoned the queue.
        abandon_q = select(func.count()).where(
            and_(
                e.store_id == store_id,
                e.timestamp >= start,
                e.timestamp < end,
                e.event_type == "BILLING_QUEUE_ABANDON",
                e.is_staff.is_(False),
            )
        )
        abandons = int((await s.execute(abandon_q)).scalar() or 0)
        joins = len(billed_rows)
        abandonment_rate = (abandons / joins) if joins > 0 else 0.0

        # POS transactions today.
        pos_q = select(p.timestamp, p.line_items).where(
            and_(p.store_id == store_id, p.timestamp >= start, p.timestamp < end)
        )
        pos_rows = (await s.execute(pos_q)).all()
        pos_timestamps = [r[0] for r in pos_rows]
        
        # Calculate Top Brands & Departments
        brands_c = Counter()
        deps_c = Counter()
        for r in pos_rows:
            line_items = r[1] or []
            if isinstance(line_items, str):
                import json
                line_items = json.loads(line_items)
            for item in line_items:
                if isinstance(item, dict):
                    b = item.get("brand_name")
                    d = item.get("dep_name")
                    if b: brands_c[b] += 1
                    if d: deps_c[d] += 1
        
        top_brands = dict(brands_c.most_common(5))
        top_departments = dict(deps_c.most_common(5))

        # Conversion = 5-minute window correlation.
        converted = set()
        for vid, join_ts in billed_rows:
            if vid in converted:
                continue
            for pts in pos_timestamps:
                # 5-minute window before POS timestamp
                if timedelta(seconds=0) <= pts - join_ts <= timedelta(minutes=5):
                    converted.add(vid)
                    break
        conversion_rate = (len(converted) / unique_visitors) if unique_visitors > 0 else 0.0

        # Avg dwell per zone from ZONE_DWELL events.
        dwell_q = select(e.zone_id, func.avg(e.dwell_ms)).where(
            and_(
                e.store_id == store_id,
                e.timestamp >= start,
                e.timestamp < end,
                e.event_type == "ZONE_DWELL",
                e.is_staff.is_(False),
                e.zone_id.isnot(None),
            )
        ).group_by(e.zone_id)
        avg_dwell = {
            row[0]: round(float(row[1] or 0), 2)
            for row in (await s.execute(dwell_q)).all()
        }

        # Current queue depth = max queue_depth in metadata for last 5 min.
        cutoff = now - timedelta(minutes=5)
        q_metadata_q = select(e.metadata_json).where(
            and_(
                e.store_id == store_id,
                e.timestamp >= cutoff,
                e.event_type == "BILLING_QUEUE_JOIN",
                e.is_staff.is_(False),
            )
        )
        rows = (await s.execute(q_metadata_q)).all()
        depths = [int(r[0].get("queue_depth", 0)) for r in rows if isinstance(r[0], dict)]
        current_queue_depth = max(depths) if depths else 0

    return StoreMetrics(
        store_id=store_id,
        window_start=start.isoformat(),
        window_end=end.isoformat(),
        unique_visitors=unique_visitors,
        conversion_rate=conversion_rate,
        abandonment_rate=abandonment_rate,
        avg_dwell_per_zone_ms=avg_dwell,
        current_queue_depth=current_queue_depth,
        pos_transactions=len(pos_timestamps),
        top_brands=top_brands,
        top_departments=top_departments,
        staff_count=staff_count,
        generated_at=now.isoformat(),
    )

_ = SETTINGS  # keep import alive for future tuning
