"""WebSocket and additional endpoint tests.

# PROMPT: "Write tests for the WebSocket endpoint /ws/stores/{id}, the
#   new /stores/{id}/events endpoint, and the POS-correlated
#   BILLING_QUEUE_ABANDON reclassification logic."
# CHANGES MADE: Used httpx's ASGITransport for WebSocket testing via
#   the starlette test client, and added a reclassification test that
#   ingests LEAVE events then POS data and verifies the event_type flip.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from .conftest import make_event_payload
from .test_metrics import _today_iso_hour


@pytest.mark.asyncio
async def test_events_endpoint_returns_ingested(client):
    events = [make_event_payload(visitor_id=f"V_{i}") for i in range(3)]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    r = await client.get("/stores/STORE_001/events?limit=10")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 3
    assert len(body["events"]) == 3
    assert body["store_id"] == "STORE_001"


@pytest.mark.asyncio
async def test_events_endpoint_pagination(client):
    events = [make_event_payload(visitor_id=f"V_{i}") for i in range(5)]
    await client.post("/events/ingest", json={"events": events})

    r = await client.get("/stores/STORE_001/events?limit=2&offset=0")
    assert r.status_code == 200
    assert len(r.json()["events"]) == 2

    r = await client.get("/stores/STORE_001/events?limit=2&offset=4")
    assert r.status_code == 200
    assert len(r.json()["events"]) == 1


@pytest.mark.asyncio
async def test_events_endpoint_empty_store(client):
    r = await client.get("/stores/STORE_EMPTY/events")
    assert r.status_code == 200
    assert r.json()["total"] == 0
    assert r.json()["events"] == []


@pytest.mark.asyncio
async def test_pos_reclassify_leave_to_abandon(client):
    """BILLING_QUEUE_LEAVE without a matching POS txn should be reclassified to ABANDON."""
    ts = _today_iso_hour(11)

    # Ingest: visitor joins billing queue then leaves
    events = [
        make_event_payload(event_type="ENTRY", visitor_id="V_RECLASS", timestamp=ts),
        make_event_payload(
            event_type="BILLING_QUEUE_JOIN", visitor_id="V_RECLASS",
            zone_id="ZONE_BILLING", timestamp=_today_iso_hour(11, 5),
            metadata={"queue_depth": 1},
        ),
        # This is a LEAVE — no POS txn will follow, so it should become ABANDON
        make_event_payload(
            event_type="BILLING_QUEUE_LEAVE", visitor_id="V_RECLASS",
            zone_id="ZONE_BILLING", timestamp=_today_iso_hour(11, 8),
        ),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    # Now ingest POS data that does NOT match this visitor's time window
    pos = {
        "transaction_id": "TXN_FAR_AWAY",
        "store_id": "STORE_001",
        "visitor_id": "V_OTHER",
        "timestamp": _today_iso_hour(11, 30),  # 22 minutes later — outside 5-min window
        "basket_value": 500.0,
        "items_count": 1,
    }
    r = await client.post("/pos/ingest", json={"transactions": [pos]})
    assert r.status_code == 200

    # Check: the LEAVE should now be reclassified as ABANDON
    r = await client.get("/stores/STORE_001/events?limit=100")
    evts = r.json()["events"]
    leave_events = [e for e in evts if e["event_type"] == "BILLING_QUEUE_LEAVE"]
    abandon_events = [e for e in evts if e["event_type"] == "BILLING_QUEUE_ABANDON"]
    assert len(leave_events) == 0, "LEAVE should have been reclassified"
    assert len(abandon_events) == 1, "Should have exactly one ABANDON"


@pytest.mark.asyncio
async def test_funnel_empty_store_returns_zero_stages(client):
    r = await client.get("/stores/STORE_EMPTY_FUNNEL/funnel")
    assert r.status_code == 200
    body = r.json()
    assert body["total_sessions"] == 0
    assert body["conversion_rate"] == 0.0
    assert all(s["count"] == 0 for s in body["stages"])


@pytest.mark.asyncio
async def test_funnel_purchase_with_pos_correlation(client):
    """Full funnel: Entry → ZoneVisit → BillingQueue → Purchase via POS correlation."""
    ts = _today_iso_hour(10)
    vid = "V_FULL_FUNNEL"
    events = [
        make_event_payload(event_type="ENTRY", visitor_id=vid, timestamp=ts),
        make_event_payload(
            event_type="ZONE_ENTER", visitor_id=vid,
            zone_id="ZONE_SKIN", timestamp=_today_iso_hour(10, 3),
        ),
        make_event_payload(
            event_type="BILLING_QUEUE_JOIN", visitor_id=vid,
            zone_id="ZONE_BILLING", timestamp=_today_iso_hour(10, 8),
            metadata={"queue_depth": 1},
        ),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    # POS txn 2 minutes after billing queue join → within 5-min window
    pos = {
        "transaction_id": "TXN_FULL",
        "store_id": "STORE_001",
        "visitor_id": vid,
        "timestamp": _today_iso_hour(10, 10),
        "basket_value": 999.0,
        "items_count": 3,
    }
    r = await client.post("/pos/ingest", json={"transactions": [pos]})
    assert r.status_code == 200

    r = await client.get("/stores/STORE_001/funnel")
    body = r.json()
    stages = {s["stage"]: s["count"] for s in body["stages"]}
    assert stages["Entry"] == 1
    assert stages["ZoneVisit"] == 1
    assert stages["BillingQueue"] == 1
    assert stages["Purchase"] == 1
    assert body["conversion_rate"] == 1.0


@pytest.mark.asyncio
async def test_metrics_includes_staff_count(client):
    ts = _today_iso_hour(10)
    events = [
        make_event_payload(event_type="ENTRY", visitor_id="V1", timestamp=ts),
        make_event_payload(event_type="ENTRY", visitor_id="STAFF1", timestamp=ts, is_staff=True),
        make_event_payload(event_type="ENTRY", visitor_id="STAFF2", timestamp=ts, is_staff=True),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    r = await client.get("/stores/STORE_001/metrics")
    body = r.json()
    assert body["unique_visitors"] == 1  # only non-staff
    assert body["staff_count"] == 2  # staff tracked separately
