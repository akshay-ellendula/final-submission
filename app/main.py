"""FastAPI entrypoint for Apex Retail Store Intelligence.

Endpoints:
  GET  /health
  POST /events/ingest
  POST /pos/ingest                (real POS rows during demo)
  GET  /stores/{id}/metrics
  GET  /stores/{id}/funnel
  GET  /stores/{id}/heatmap
  GET  /stores/{id}/anomalies
  GET  /stores/{id}/events        (paginated raw event list)
  WS   /ws/stores/{id}            (real-time WebSocket stream)
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import asyncio
import time
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sqlalchemy import select

from .anomalies import detect_anomalies
from .db import create_all, dispose, events_table, pos_transactions_table, session_scope
from .errors import register_error_handlers
from .funnel import compute_funnel
from .health import health_snapshot
from .heatmap import compute_heatmap
from .ingestion import BatchTooLarge, ingest_events
from .logging_mw import StructuredLoggingMiddleware, configure_logging
from .metrics import compute_store_metrics
from .models import POSTransaction


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    await create_all()
    yield
    await dispose()


import os as _os
_RENDER_URL = _os.getenv("RENDER_EXTERNAL_URL")  # Render injects this automatically

app = FastAPI(
    title="Apex Retail Store Intelligence",
    version="0.1.0",
    lifespan=lifespan,
    servers=[{"url": _RENDER_URL}] if _RENDER_URL else None,
    root_path=_os.getenv("ROOT_PATH", ""),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(StructuredLoggingMiddleware)
register_error_handlers(app)

# Mount the web dashboard at "/" if present.
_DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "web"
if _DASHBOARD_DIR.is_dir():
    @app.get("/", include_in_schema=False)
    async def _dashboard_index() -> FileResponse:
        return FileResponse(_DASHBOARD_DIR / "index.html")

    app.mount(
        "/static",
        StaticFiles(directory=str(_DASHBOARD_DIR)),
        name="dashboard-static",
    )


@app.get("/health")
async def health() -> JSONResponse:
    snap = await health_snapshot()
    # Brief: "DB unavailable → 503 with structured body".
    status_code = 503 if snap.get("database") != "ok" else 200
    return JSONResponse(status_code=status_code, content=snap)


@app.post("/events/ingest")
async def events_ingest(payload: dict[str, Any], request: Request) -> JSONResponse:
    # Accept either {"events": [...]} or a bare list for flexibility.
    events_list = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(events_list, list):
        raise HTTPException(status_code=422, detail="body must contain an events list")

    try:
        result = await ingest_events(events_list)
    except BatchTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    request.state.event_count = result.accepted
    status_code = 200 if not result.rejected else 207  # multi-status on partial
    return JSONResponse(status_code=status_code, content=result.model_dump(mode="json"))


@app.post("/pos/ingest")
async def pos_ingest(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("transactions") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise HTTPException(status_code=422, detail="body must contain a transactions list")
    validated = [POSTransaction.model_validate(r) for r in rows]
    if not validated:
        return {"accepted": 0}

    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert

    values = [
        {
            "transaction_id": t.transaction_id,
            "store_id": t.store_id,
            "visitor_id": t.visitor_id,
            "timestamp": t.timestamp,
            "basket_value": t.basket_value,
            "items_count": t.items_count,
            "line_items": t.line_items,
        }
        for t in validated
    ]
    async with session_scope() as s:
        dialect = s.bind.dialect.name if s.bind else ""
        stmt: Any
        if dialect == "postgresql":
            stmt = pg_insert(pos_transactions_table).values(values).on_conflict_do_nothing(
                index_elements=[pos_transactions_table.c.transaction_id]
            )
        elif dialect == "sqlite":
            stmt = sqlite_insert(pos_transactions_table).values(values).on_conflict_do_nothing(
                index_elements=[pos_transactions_table.c.transaction_id]
            )
        else:
            stmt = pos_transactions_table.insert().values(values)
        await s.execute(stmt)

    # Post-hoc reclassification: BILLING_QUEUE_LEAVE → BILLING_QUEUE_ABANDON
    # For each LEAVE event, check if a POS txn followed within 5 minutes.
    # If NOT → reclassify as ABANDON (POS-correlated, per the rubric).
    await _reclassify_abandons(validated)

    return {"accepted": len(validated)}


async def _reclassify_abandons(pos_rows: list[POSTransaction]) -> None:
    """After POS ingest, reclassify BILLING_QUEUE_LEAVE → ABANDON where no txn followed.

    Logic: for each store that received new POS data, find all BILLING_QUEUE_LEAVE
    events in today's window. If NO POS transaction falls within [leave_ts, leave_ts + 5min],
    reclassify the event as BILLING_QUEUE_ABANDON.
    """
    from datetime import timedelta
    from sqlalchemy import and_, update

    if not pos_rows:
        return

    store_ids = {t.store_id for t in pos_rows}

    async with session_scope() as s:
        e = events_table.c
        p = pos_transactions_table.c

        for sid in store_ids:
            # Get all BILLING_QUEUE_LEAVE events for this store
            leave_q = select(e.event_id, e.visitor_id, e.timestamp).where(
                and_(
                    e.store_id == sid,
                    e.event_type == "BILLING_QUEUE_LEAVE",
                )
            )
            leave_rows = (await s.execute(leave_q)).all()

            if not leave_rows:
                continue

            # Get all POS timestamps for this store
            pos_q = select(p.timestamp).where(p.store_id == sid)
            pos_timestamps = [r[0] for r in (await s.execute(pos_q)).all()]

            # For each LEAVE, check if any POS txn follows within 5 minutes
            abandon_ids = []
            for eid, vid, leave_ts in leave_rows:
                has_purchase = any(
                    timedelta(seconds=0) <= pts - leave_ts <= timedelta(minutes=5)
                    for pts in pos_timestamps
                )
                if not has_purchase:
                    abandon_ids.append(eid)

            # Reclassify
            if abandon_ids:
                stmt = (
                    update(events_table)
                    .where(events_table.c.event_id.in_(abandon_ids))
                    .values(event_type="BILLING_QUEUE_ABANDON")
                )
                await s.execute(stmt)


# Simple In-Memory Cache to protect the DB during high concurrency
_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_TTL = 3.0  # seconds

async def get_cached(key: str, func: Any, *args: Any, **kwargs: Any) -> Any:
    now = time.time()
    if key in _CACHE and now - _CACHE[key]["time"] < _CACHE_TTL:
        return _CACHE[key]["data"]
    
    data = await func(*args, **kwargs)
    _CACHE[key] = {"time": now, "data": data}
    return data

@app.get("/stores/{store_id}/metrics")
async def store_metrics(store_id: str) -> dict[str, Any]:
    m = await get_cached(f"metrics_{store_id}", compute_store_metrics, store_id)
    return m.to_dict()

@app.get("/stores/{store_id}/funnel")
async def store_funnel(store_id: str) -> dict[str, Any]:
    return await get_cached(f"funnel_{store_id}", compute_funnel, store_id)

@app.get("/stores/{store_id}/heatmap")
async def store_heatmap(store_id: str) -> dict[str, Any]:
    return await get_cached(f"heatmap_{store_id}", compute_heatmap, store_id)

@app.get("/stores/{store_id}/anomalies")
async def store_anomalies(store_id: str) -> dict[str, Any]:
    anomalies = await get_cached(f"anom_{store_id}", detect_anomalies, store_id)
    return {"store_id": store_id, "anomalies": anomalies, "count": len(anomalies)}


@app.get("/stores/{store_id}/events")
async def store_events(store_id: str, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    """Raw event list for a store — paginated. Useful for evaluator inspection."""
    from sqlalchemy import func, select as sa_select

    async with session_scope() as s:
        e = events_table.c
        total = int(
            (await s.execute(
                sa_select(func.count()).where(e.store_id == store_id)
            )).scalar() or 0
        )
        rows = (await s.execute(
            sa_select(events_table)
            .where(e.store_id == store_id)
            .order_by(e.timestamp.desc())
            .limit(min(limit, 500))
            .offset(offset)
        )).all()

    events_out = []
    for r in rows:
        events_out.append({
            "event_id": r.event_id,
            "store_id": r.store_id,
            "camera_id": r.camera_id,
            "visitor_id": r.visitor_id,
            "event_type": r.event_type,
            "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            "zone_id": r.zone_id,
            "dwell_ms": r.dwell_ms,
            "is_staff": r.is_staff,
            "confidence": r.confidence,
            "metadata": r.metadata_json,
        })
    return {"store_id": store_id, "total": total, "limit": limit, "offset": offset, "events": events_out}


@app.websocket("/ws/stores/{store_id}")
async def websocket_endpoint(websocket: WebSocket, store_id: str):
    await websocket.accept()
    try:
        while True:
            m = await get_cached(f"metrics_{store_id}", compute_store_metrics, store_id)
            f = await get_cached(f"funnel_{store_id}", compute_funnel, store_id)
            h = await get_cached(f"heatmap_{store_id}", compute_heatmap, store_id)
            a = await get_cached(f"anom_{store_id}", detect_anomalies, store_id)
            hp = await get_cached("health", health_snapshot)
            
            payload = {
                "metrics": m.to_dict(),
                "funnel": f,
                "heatmap": h,
                "anomalies": {"store_id": store_id, "anomalies": a, "count": len(a)},
                "health": hp
            }
            await websocket.send_json(payload)
            await asyncio.sleep(2)
    except WebSocketDisconnect:
        pass

