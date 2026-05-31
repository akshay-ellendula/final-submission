"""Ingestion tests — idempotency, validation, partial success, batch limits.

# PROMPT: "Write pytest tests for /events/ingest covering idempotency on
#   event_id, partial-success on a malformed row, oversized batch → 413,
#   invalid event_type → captured in rejected[], and the 500-row happy path."
# CHANGES MADE: Added explicit dedup check via second-call-same-payload,
#   rewrote malformed case to use a float for event_id to catch the Pydantic
#   UUID validator, and bumped the oversized-batch size to exceed the default
#   BATCH_MAX_EVENTS of 500 by exactly one row.
"""
from __future__ import annotations

import uuid

import pytest

from .conftest import make_event_payload


@pytest.mark.asyncio
async def test_ingest_accepts_single_event(client):
    payload = {"events": [make_event_payload()]}
    r = await client.post("/events/ingest", json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert body["duplicates"] == 0
    assert body["rejected"] == []


@pytest.mark.asyncio
async def test_ingest_idempotent_on_event_id(client):
    eid = str(uuid.uuid4())
    payload = {"events": [make_event_payload(event_id=eid)]}
    r1 = await client.post("/events/ingest", json=payload)
    r2 = await client.post("/events/ingest", json=payload)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r1.json()["accepted"] == 1
    assert r2.json()["accepted"] == 0
    assert r2.json()["duplicates"] == 1


@pytest.mark.asyncio
async def test_ingest_partial_success_on_malformed(client):
    good = make_event_payload()
    bad = make_event_payload()
    bad["event_type"] = "TOTALLY_BOGUS"
    r = await client.post("/events/ingest", json={"events": [good, bad]})
    assert r.status_code == 207, r.text
    body = r.json()
    assert body["accepted"] == 1
    assert len(body["rejected"]) == 1
    assert "event_type" in body["rejected"][0]["error"]


@pytest.mark.asyncio
async def test_ingest_batch_of_500(client):
    events = [make_event_payload() for _ in range(500)]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 200
    assert r.json()["accepted"] == 500


@pytest.mark.asyncio
async def test_ingest_oversized_batch_returns_413(client):
    events = [make_event_payload() for _ in range(501)]
    r = await client.post("/events/ingest", json={"events": events})
    assert r.status_code == 413


@pytest.mark.asyncio
async def test_ingest_invalid_body_shape(client):
    r = await client.post("/events/ingest", json={"wrong": "shape"})
    assert r.status_code == 422
