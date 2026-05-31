"""Batch event ingestion with idempotency and partial-success semantics.

- Max 500 events per batch (oversized → 413).
- Dedup on event_id via ON CONFLICT DO NOTHING / INSERT OR IGNORE.
- Individual validation failures are reported per-event; the batch is not rejected wholesale.
"""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .config import SETTINGS
from .db import events_table, session_scope
from .models import Event, IngestResponse, RejectedEvent


class BatchTooLarge(ValueError):
    pass


def _row_from_event(e: Event) -> dict[str, Any]:
    return {
        "event_id": str(e.event_id),
        "store_id": e.store_id,
        "camera_id": e.camera_id,
        "visitor_id": e.visitor_id,
        "event_type": e.event_type.value,
        "timestamp": e.timestamp,
        "zone_id": e.zone_id,
        "dwell_ms": e.dwell_ms,
        "is_staff": e.is_staff,
        "confidence": e.confidence,
        "metadata_json": e.metadata,
    }


async def _upsert_ignore(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Insert rows, skipping duplicates. Returns count of new rows inserted."""
    if not rows:
        return 0

    ids = [r["event_id"] for r in rows]
    existing_q = select(events_table.c.event_id).where(events_table.c.event_id.in_(ids))
    existing = {r[0] for r in (await session.execute(existing_q)).all()}
    new_rows = [r for r in rows if r["event_id"] not in existing]

    if not new_rows:
        return 0

    dialect = session.bind.dialect.name if session.bind else ""
    stmt: Any
    if dialect == "postgresql":
        stmt = pg_insert(events_table).values(new_rows).on_conflict_do_nothing(
            index_elements=[events_table.c.event_id]
        )
    elif dialect == "sqlite":
        stmt = sqlite_insert(events_table).values(new_rows).on_conflict_do_nothing(
            index_elements=[events_table.c.event_id]
        )
    else:
        stmt = events_table.insert().values(new_rows)
    await session.execute(stmt)
    return len(new_rows)


async def ingest_events(raw_events: list[dict[str, Any]]) -> IngestResponse:
    """Validate and persist a batch. Returns partial-success response."""
    if len(raw_events) > SETTINGS.batch_max_events:
        raise BatchTooLarge(
            f"batch size {len(raw_events)} exceeds max {SETTINGS.batch_max_events}"
        )
    if not raw_events:
        return IngestResponse(accepted=0, duplicates=0, rejected=[])

    validated: list[Event] = []
    rejected: list[RejectedEvent] = []
    for raw in raw_events:
        try:
            validated.append(Event.model_validate(raw))
        except ValidationError as ve:
            rejected.append(
                RejectedEvent(
                    event_id=str(raw.get("event_id")) if isinstance(raw, dict) else None,
                    error=_summarise_validation_error(ve),
                )
            )

    if not validated:
        return IngestResponse(accepted=0, duplicates=0, rejected=rejected)

    rows = [_row_from_event(e) for e in validated]
    async with session_scope() as s:
        inserted = await _upsert_ignore(s, rows)

    return IngestResponse(
        accepted=inserted,
        duplicates=len(validated) - inserted,
        rejected=rejected,
    )


def _summarise_validation_error(exc: ValidationError) -> str:
    first = exc.errors()[0] if exc.errors() else {"msg": "invalid"}
    loc = ".".join(str(p) for p in first.get("loc", ()))
    return f"{loc}: {first.get('msg', 'invalid')}" if loc else str(first.get("msg", "invalid"))
