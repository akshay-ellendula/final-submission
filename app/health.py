"""Service health — per-store last_event_timestamp and STALE_FEED warning."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select

from .config import SETTINGS
from .db import events_table, get_engine, session_scope


async def health_snapshot(now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    stale_cutoff = now - timedelta(minutes=SETTINGS.stale_feed_minutes)
    db_ok = True
    stores: list[dict[str, Any]] = []
    try:
        async with session_scope() as s:
            e = events_table.c
            rows = (
                await s.execute(
                    select(e.store_id, func.max(e.timestamp)).group_by(e.store_id)
                )
            ).all()
        for store_id, last_ts in rows:
            if last_ts is not None and last_ts.tzinfo is None:
                last_ts = last_ts.replace(tzinfo=timezone.utc)
            last_iso = last_ts.isoformat() if last_ts else None
            stale = bool(last_ts and last_ts < stale_cutoff)
            stores.append(
                {
                    "store_id": store_id,
                    "last_event_timestamp": last_iso,
                    "stale": stale,
                }
            )
    except Exception as exc:  # noqa: BLE001 — surface as degraded, not 500
        db_ok = False
        import structlog
        structlog.get_logger().warning("health_db_check_failed", error_class=type(exc).__name__, error=str(exc))

    warnings = [f"STALE_FEED: {s['store_id']}" for s in stores if s.get("stale")]
    status = "ok" if db_ok and not warnings else ("degraded" if db_ok else "db_unavailable")

    return {
        "status": status,
        "database": "ok" if db_ok else "unavailable",
        "engine": str(get_engine().url.drivername) if db_ok else None,
        "checked_at": now.isoformat(),
        "stores": stores,
        "warnings": warnings,
    }
