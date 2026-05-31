"""Pipeline / schema tests — UUID uniqueness, zone + line utilities.

# PROMPT: "Tests for the CV pipeline that don't require torch/ultralytics.
#   Should cover: point-in-polygon, line crossing direction, ReentryCache
#   lookup+eviction after window, Emitter writes JSONL and flushes to an
#   HTTP stub, and UUID uniqueness across a synthetic run."
# CHANGES MADE: Inlined a tiny httpx MockTransport stub instead of using
#   respx so test dep surface stays minimal. Added polygon fixture with a
#   concave point to guard against bbox-shortcut regressions.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx
import pytest

from pipeline.emit import Emitter, EmitterConfig, make_event
from pipeline.reentry import ReentryCache, cosine_sim
from pipeline.zones import (
    LineCrossing,
    ZoneState,
    bbox_center,
    line_side,
    point_in_polygon,
)


def test_point_in_polygon_convex():
    square = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert point_in_polygon((5, 5), square) is True
    assert point_in_polygon((11, 11), square) is False


def test_point_in_polygon_concave():
    concave = [(0, 0), (10, 0), (10, 10), (5, 5), (0, 10)]
    assert point_in_polygon((5, 9), concave) is False
    assert point_in_polygon((5, 2), concave) is True


def test_line_crossing_detects_direction_change():
    lc = LineCrossing(a=(0, 5), b=(10, 5), inside_normal=(0, 1))
    assert lc.update("v1", (5, 1)) is None  # first observation
    evt = lc.update("v1", (5, 9))
    assert evt in ("enter", "exit")
    evt2 = lc.update("v1", (5, 1))
    assert evt2 in ("enter", "exit") and evt2 != evt


def test_zone_state_emits_enter_dwell_exit():
    zs = ZoneState()
    out1 = zs.on_zone_event("v1", "z1", True, 0)
    assert out1 == [("ZONE_ENTER", 0)]
    # no re-enter within 30s should produce no event
    assert zs.on_zone_event("v1", "z1", True, 1_000) == []
    # after 30s we get a DWELL
    dwell = zs.on_zone_event("v1", "z1", True, 31_000)
    assert dwell and dwell[0][0] == "ZONE_DWELL"
    exit_out = zs.on_zone_event("v1", "z1", False, 32_000)
    assert exit_out == [("ZONE_EXIT", 32_000)]


def test_reentry_cache_evicts_after_window():
    cache = ReentryCache(window_ms=1000, similarity_threshold=0.5)
    cache.record_exit("V1", (1.0, 0.0, 0.0), ts_ms=0)
    assert cache.lookup((1.0, 0.0, 0.0), ts_ms=500) == "V1"
    # past the window
    assert cache.lookup((1.0, 0.0, 0.0), ts_ms=2000) is None


def test_cosine_sim_extremes():
    assert cosine_sim((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)
    assert cosine_sim((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)


def test_bbox_center():
    assert bbox_center((0, 0, 10, 20)) == (5.0, 10.0)


def test_line_side_signs():
    # Line from (0,0) to (10,0). The signed formula produces opposite signs
    # for points above vs below the line — we assert the opposition, not the
    # specific sign convention.
    above = line_side((0, 10), (0, 0), (10, 0))
    below = line_side((0, -10), (0, 0), (10, 0))
    assert (above > 0) != (below > 0)
    assert above != 0 and below != 0


def test_emitter_writes_jsonl_and_posts(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(len(body["events"]))
        return httpx.Response(200, json={"accepted": len(body["events"]), "duplicates": 0, "rejected": []})

    # We want emit.py to post via its httpx.Client — so monkeypatch by swapping
    # the client's transport post-init. Emitter exposes `_http`.
    cfg = EmitterConfig(api_url="http://api", jsonl_path=path, batch_size=2)
    em = Emitter(cfg)
    em._http = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        from datetime import datetime, timezone
        for i in range(5):
            em.emit(make_event(
                store_id="S", camera_id="C", visitor_id=f"V_{i}",
                event_type="ENTRY", timestamp=datetime.now(timezone.utc),
            ))
        em.flush()
    finally:
        em.close()

    # JSONL should have all 5 lines.
    lines = path.read_text().strip().splitlines()
    assert len(lines) == 5
    # All UUIDs unique.
    ids = [json.loads(ln)["event_id"] for ln in lines]
    assert len(set(ids)) == 5
    # Two full batches of 2 and one final flush of 1.
    assert sum(seen) == 5


def test_make_event_uuid_uniqueness():
    from datetime import datetime, timezone
    ids = {
        make_event(
            store_id="S", camera_id="C", visitor_id="V",
            event_type="ENTRY", timestamp=datetime.now(timezone.utc),
        )["event_id"]
        for _ in range(1000)
    }
    assert len(ids) == 1000


def test_make_event_schema_matches_pydantic():
    from datetime import datetime, timezone
    from app.models import Event
    raw = make_event(
        store_id="S", camera_id="C", visitor_id="V",
        event_type="ENTRY", timestamp=datetime.now(timezone.utc),
        confidence=0.7,
    )
    # Force a dummy UUID into the required shape.
    raw["event_id"] = str(uuid.UUID(raw["event_id"]))
    Event.model_validate(raw)  # must not raise


def test_cross_camera_dedup_suppresses_duplicate():
    from pipeline.cross_camera import CrossCameraDedup
    dedup = CrossCameraDedup(window_ms=3000)
    # First emission — should pass
    assert dedup.should_emit("V1", "ZONE_SKIN", 1000) is True
    # Same visitor+zone within 3s — should be suppressed
    assert dedup.should_emit("V1", "ZONE_SKIN", 2000) is False
    # Different zone — should pass
    assert dedup.should_emit("V1", "ZONE_MAKEUP", 2000) is True
    # Same zone after window expires — should pass
    assert dedup.should_emit("V1", "ZONE_SKIN", 5000) is True


def test_cross_camera_dedup_prune():
    from pipeline.cross_camera import CrossCameraDedup
    dedup = CrossCameraDedup(window_ms=1000)
    dedup.should_emit("V1", "Z1", 100)
    dedup.should_emit("V2", "Z2", 200)
    assert len(dedup._seen) == 2
    # Prune with a timestamp far in the future
    dedup.prune(50_000)
    assert len(dedup._seen) == 0

