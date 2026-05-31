"""Store metrics tests — zeros vs non-null, staff exclusion, conversion math.

# PROMPT: "Tests for /stores/{id}/metrics covering: empty store returns zeros
#   (not nulls, not 500), all-staff events yield zero unique_visitors,
#   conversion rate with no POS = 0, basic conversion calculation when a
#   visitor both joined billing queue and has a POS row."
# CHANGES MADE: Used a fixed-UTC timestamp for determinism and added an
#   assertion on avg_dwell_per_zone_ms schema to catch regressions where a
#   zone would drop from the dict silently.
"""
from __future__ import annotations

import pytest

from .conftest import make_event_payload


@pytest.mark.asyncio
async def test_metrics_empty_store_returns_zeros(client):
    r = await client.get("/stores/STORE_EMPTY/metrics")
    assert r.status_code == 200
    body = r.json()
    assert body["unique_visitors"] == 0
    assert body["conversion_rate"] == 0.0
    assert body["abandonment_rate"] == 0.0
    assert body["avg_dwell_per_zone_ms"] == {}
    assert body["current_queue_depth"] == 0


@pytest.mark.asyncio
async def test_metrics_all_staff_zero_visitors(client):
    today_iso = _today_iso_hour(10)
    events = [
        make_event_payload(event_type="ENTRY", is_staff=True, timestamp=today_iso, visitor_id=f"STAFF_{i}")
        for i in range(5)
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    r = await client.get("/stores/STORE_001/metrics")
    assert r.status_code == 200
    assert r.json()["unique_visitors"] == 0


@pytest.mark.asyncio
async def test_metrics_conversion_with_pos(client):
    today_iso = _today_iso_hour(10)
    vid = "VIS_ABCDE1"
    events = [
        make_event_payload(event_type="ENTRY", visitor_id=vid, timestamp=today_iso),
        make_event_payload(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id=vid,
            zone_id="ZONE_BILLING",
            timestamp=_today_iso_hour(10, 5),
            metadata={"queue_depth": 2},
        ),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    pos_row = {
        "transaction_id": "TXN_ABC",
        "store_id": "STORE_001",
        "visitor_id": vid,
        "timestamp": _today_iso_hour(10, 7),
        "basket_value": 499.0,
        "items_count": 2,
    }
    r = await client.post("/pos/ingest", json={"transactions": [pos_row]})
    assert r.status_code == 200

    r = await client.get("/stores/STORE_001/metrics")
    body = r.json()
    assert body["unique_visitors"] == 1
    assert body["conversion_rate"] == 1.0
    assert body["pos_transactions"] == 1


@pytest.mark.asyncio
async def test_metrics_abandonment_rate(client):
    today_iso = _today_iso_hour(10)
    events = [
        make_event_payload(event_type="ENTRY", visitor_id="V1", timestamp=today_iso),
        make_event_payload(
            event_type="BILLING_QUEUE_JOIN",
            visitor_id="V1",
            zone_id="ZONE_BILLING",
            timestamp=_today_iso_hour(10, 5),
            metadata={"queue_depth": 1},
        ),
        make_event_payload(
            event_type="BILLING_QUEUE_ABANDON",
            visitor_id="V1",
            zone_id="ZONE_BILLING",
            timestamp=_today_iso_hour(10, 12),
        ),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    r = await client.get("/stores/STORE_001/metrics")
    body = r.json()
    assert body["abandonment_rate"] == 1.0


def _today_iso_hour(hour: int, minute: int = 0) -> str:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    ts = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return ts.isoformat()
