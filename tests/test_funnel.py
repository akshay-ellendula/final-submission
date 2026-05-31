"""Funnel tests — session dedup, re-entry collapse, stage monotonicity.

# PROMPT: "Tests for /stores/{id}/funnel. Must cover: re-entry doesn't
#   double-count a visitor at the Entry stage; funnel stages are monotonic
#   (each <= prev); a session that reaches Purchase also counts at every
#   earlier stage."
# CHANGES MADE: Added explicit monotonicity assertion (len-descending chain)
#   and a drop-off sanity check to ensure the final drop_off_from_prev_pct
#   falls within [0, 100].
"""
from __future__ import annotations

import pytest

from .conftest import make_event_payload
from .test_metrics import _today_iso_hour


@pytest.mark.asyncio
async def test_funnel_reentry_does_not_double_count(client):
    ts = _today_iso_hour(9)
    events = [
        make_event_payload(event_type="ENTRY", visitor_id="V_RE", timestamp=ts),
        make_event_payload(event_type="EXIT", visitor_id="V_RE", timestamp=_today_iso_hour(9, 10)),
        make_event_payload(event_type="REENTRY", visitor_id="V_RE", timestamp=_today_iso_hour(9, 30)),
    ]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    r = await client.get("/stores/STORE_001/funnel")
    stages = r.json()["stages"]
    entry = next(s for s in stages if s["stage"] == "Entry")
    assert entry["count"] == 1  # collapsed across REENTRY


@pytest.mark.asyncio
async def test_funnel_is_monotonic(client):
    ts = _today_iso_hour(10)
    events = []
    for i in range(5):
        vid = f"VIS_{i:06d}"
        events.append(make_event_payload(event_type="ENTRY", visitor_id=vid, timestamp=ts))
        if i < 4:
            events.append(make_event_payload(event_type="ZONE_ENTER", visitor_id=vid, zone_id="ZONE_MAKEUP", timestamp=_today_iso_hour(10, 3)))
        if i < 3:
            events.append(make_event_payload(
                event_type="BILLING_QUEUE_JOIN", visitor_id=vid,
                zone_id="ZONE_BILLING", timestamp=_today_iso_hour(10, 6),
                metadata={"queue_depth": 1},
            ))
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200

    r = await client.get("/stores/STORE_001/funnel")
    stages = r.json()["stages"]
    counts = [s["count"] for s in stages]
    assert all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1))
    assert counts[0] == 5
    assert counts[1] == 4
    assert counts[2] == 3
    for s in stages:
        assert 0.0 <= s["drop_off_from_prev_pct"] <= 100.0
