"""Anomaly detection: billing queue spike, conversion drop, dead zone.

Severity: CRITICAL | WARN | INFO. Each anomaly has a suggested_action.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, select

from .config import SETTINGS
from .db import events_table, pos_transactions_table, session_scope
from .metrics import _today_window


async def detect_anomalies(store_id: str, now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    start, end = _today_window(now)
    e = events_table.c
    p = pos_transactions_table.c
    anomalies: list[dict[str, Any]] = []

    async with session_scope() as s:
        # --- queue spike --------------------------------------------------
        spike_cutoff = now - timedelta(seconds=SETTINGS.queue_spike_duration_sec)
        q_rows = (
            await s.execute(
                select(e.timestamp, e.metadata_json).where(
                    and_(
                        e.store_id == store_id,
                        e.event_type == "BILLING_QUEUE_JOIN",
                        e.timestamp >= spike_cutoff,
                        e.is_staff.is_(False),
                    )
                )
            )
        ).all()
        sustained = [
            (ts, int(md.get("queue_depth", 0)))
            for ts, md in q_rows
            if isinstance(md, dict) and int(md.get("queue_depth", 0)) > SETTINGS.queue_spike_threshold
        ]
        if len(sustained) >= 2:  # at least 2 samples above threshold in the window
            max_depth = max(d for _, d in sustained)
            anomalies.append(
                {
                    "type": "BILLING_QUEUE_SPIKE",
                    "severity": "CRITICAL",
                    "store_id": store_id,
                    "detected_at": now.isoformat(),
                    "detail": {
                        "max_queue_depth": max_depth,
                        "threshold": SETTINGS.queue_spike_threshold,
                        "window_seconds": SETTINGS.queue_spike_duration_sec,
                    },
                    "suggested_action": "Open a second billing counter immediately.",
                }
            )

        # --- conversion drop ---------------------------------------------
        today_conv = await _conversion_rate(s, store_id, start, end)
        trailing_start = start - timedelta(days=SETTINGS.trailing_days)
        trailing_conv = await _conversion_rate(s, store_id, trailing_start, start)
        trailing_has_data = trailing_conv is not None
        if trailing_has_data and trailing_conv and today_conv is not None:
            if today_conv < 0.70 * trailing_conv:
                anomalies.append(
                    {
                        "type": "CONVERSION_DROP",
                        "severity": "WARN",
                        "store_id": store_id,
                        "detected_at": now.isoformat(),
                        "detail": {
                            "today_conversion": round(today_conv, 4),
                            "trailing_avg": round(trailing_conv, 4),
                            "trailing_days": SETTINGS.trailing_days,
                        },
                        "suggested_action": "Review staffing and promotions for today.",
                    }
                )
        elif today_conv is not None and not trailing_has_data:
            anomalies.append(
                {
                    "type": "CONVERSION_DROP",
                    "severity": "INFO",
                    "store_id": store_id,
                    "detected_at": now.isoformat(),
                    "detail": {
                        "today_conversion": round(today_conv or 0, 4),
                        "note": "Insufficient trailing history to benchmark.",
                    },
                    "suggested_action": "Collect more data before triggering alerts.",
                }
            )

        # --- dead zone ----------------------------------------------------
        dead_cutoff = now - timedelta(seconds=SETTINGS.dead_zone_window_sec)
        active_zones_q = (
            select(func.distinct(e.zone_id))
            .where(
                and_(
                    e.store_id == store_id,
                    e.event_type == "ZONE_ENTER",
                    e.timestamp >= dead_cutoff,
                )
            )
        )
        active = {r[0] for r in (await s.execute(active_zones_q)).all() if r[0]}

        all_zones_q = (
            select(func.distinct(e.zone_id))
            .where(
                and_(
                    e.store_id == store_id,
                    e.timestamp >= start,
                    e.zone_id.isnot(None),
                )
            )
        )
        all_zones = {r[0] for r in (await s.execute(all_zones_q)).all()}
        open_hours = SETTINGS  # readability
        dead = all_zones - active
        if _is_open_now(now) and dead:
            for z in sorted(dead):
                anomalies.append(
                    {
                        "type": "DEAD_ZONE",
                        "severity": "INFO",
                        "store_id": store_id,
                        "detected_at": now.isoformat(),
                        "detail": {
                            "zone_id": z,
                            "silent_seconds": SETTINGS.dead_zone_window_sec,
                        },
                        "suggested_action": f"Inspect zone {z} for obstructions or camera failure.",
                    }
                )

        _ = open_hours  # avoid unused warning
        _ = p  # reserved for future POS-based anomalies

        # --- stale camera -------------------------------------------------
        stale_cutoff = now - timedelta(minutes=10)
        stale_q = (
            select(e.camera_id, func.max(e.timestamp).label("last_ts"))
            .where(e.store_id == store_id)
            .group_by(e.camera_id)
        )
        for row in (await s.execute(stale_q)).all():
            cam_id, last_ts = row.camera_id, row.last_ts
            if last_ts:
                if last_ts.tzinfo is None:
                    last_ts = last_ts.replace(tzinfo=timezone.utc)
                if last_ts < stale_cutoff:
                    anomalies.append(
                        {
                            "type": "STALE_CAMERA",
                            "severity": "CRITICAL",
                            "store_id": store_id,
                            "detected_at": now.isoformat(),
                            "detail": {
                                "camera_id": cam_id,
                                "last_event_at": last_ts.isoformat(),
                                "silent_minutes": round((now - last_ts).total_seconds() / 60),
                            },
                            "suggested_action": f"Check connectivity for {cam_id}; restart edge agent.",
                        }
                    )

    return anomalies


async def _conversion_rate(session, store_id: str, start: datetime, end: datetime) -> float | None:
    e = events_table.c
    p = pos_transactions_table.c
    uv = (
        await session.execute(
            select(func.count(func.distinct(e.visitor_id))).where(
                and_(
                    e.store_id == store_id,
                    e.timestamp >= start,
                    e.timestamp < end,
                    e.event_type == "ENTRY",
                    e.is_staff.is_(False),
                )
            )
        )
    ).scalar() or 0
    if uv == 0:
        return None
    pos_q = select(p.timestamp).where(
        and_(p.store_id == store_id, p.timestamp >= start, p.timestamp < end)
    )
    pos_timestamps = [r[0] for r in (await session.execute(pos_q)).all()]
    
    bq_q = select(e.visitor_id, e.timestamp).where(
        and_(
            e.store_id == store_id,
            e.timestamp >= start,
            e.timestamp < end,
            e.event_type == "BILLING_QUEUE_JOIN",
            e.is_staff.is_(False),
        )
    )
    bq_rows = (await session.execute(bq_q)).all()
    
    converted = set()
    for vid, join_ts in bq_rows:
        if vid in converted:
            continue
        for pts in pos_timestamps:
            if timedelta(seconds=0) <= pts - join_ts <= timedelta(minutes=5):
                converted.add(vid)
                break
                
    return len(converted) / uv


def _is_open_now(now: datetime) -> bool:
    # We don't carry store open/close hours in the DB — default to always-open for
    # the demo. Production would join against a stores table.
    _ = now
    return True
