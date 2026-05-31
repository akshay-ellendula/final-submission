# Apex Retail Store Intelligence — System Design

## 1. Problem Statement

The system transforms raw CCTV footage (five 1080p clips from a single
Purplle retail store) into structured behavioural events and real-time
retail analytics. These are exposed via a containerised FastAPI service
with sub-second per-request latency under typical load.

---

## 2. System Architecture

```
┌─────────────────────┐    ┌───────────────────────────────┐    ┌──────────────────────┐
│  CCTV clips         │    │  pipeline/                    │    │  POST /events/ingest │
│  CAM 1..5.mp4       │───▶│    YOLOv8-m (Ultralytics)     │───▶│  Idempotent          │
│  1080p · ~20 min    │    │    ByteTrack (supervision)    │    │  Partial-success     │
│                     │    │    zones.py   (geometry)      │    │  207 Multi-Status    │
│                     │    │    staff.py   (HSV + dwell)   │    └──────────┬───────────┘
│                     │    │    reentry.py (5-min cache)   │               │
│                     │    │    emit.py    (JSONL + POST)  │               ▼
│                     │    └───────────────────────────────┘    ┌──────────────────────┐
│                     │                                        │  FastAPI  (app/)     │
└─────────────────────┘                                        │    Pydantic v2 Event │
                                                               │    Structured logs   │
                                                               │    Error middleware   │
                                                               └──────────┬───────────┘
                                                                          │
                                                                          ▼
                                                               ┌──────────────────────┐
                                                               │  PostgreSQL 16       │
                                                               │  (asyncpg)           │
                                                               │  events (PK=event_id)│
                                                               │  pos_transactions    │
                                                               └──────────┬───────────┘
                                                                          │
                            ┌──────────────┬──────────────┬───────────────┼───────────────┐
                            ▼              ▼              ▼               ▼               ▼
                       /metrics       /funnel        /heatmap        /anomalies       /health
                            │              │              │               │
                            └──────────────┴──────┬───────┴───────────────┘
                                                  │
                                       ┌──────────▼──────────┐
                                       │  TTL Cache (3s)     │
                                       └──────────┬──────────┘
                                                  │
                                                  ▼
                                       ┌─────────────────────┐
                                       │ /ws/stores/{id}     │
                                       │ WebSocket endpoint  │
                                       └──────────┬──────────┘
                                                  │
                                    ┌─────────────┴─────────────┐
                                    ▼                           ▼
                       ┌────────────────────────┐  ┌────────────────────────┐
                       │ Terminal Dashboard     │  │ Web Dashboard          │
                       │ (Rich via WebSocket)   │  │ (HTML/JS via WebSocket)│
                       └────────────────────────┘  └────────────────────────┘
```

---

## 3. Data Flow

The pipeline processes raw video into structured events through a
well-defined sequence of stages:

1. **Clip iteration** — `pipeline/run_windows.ps1` (or `run_linux.sh`) iterates clips in the order
   defined in `config/store_layout.json`. Each clip is assigned a
   `camera_id` and role (`entry` | `floor` | `stockroom`).

2. **Person detection** — For every sampled frame (5 fps, tunable),
   YOLOv8-m returns person bounding boxes at `imgsz=320` with a confidence
   threshold of `0.20`.

3. **Multi-object tracking** — ByteTrack (via the `supervision` library)
   assigns persistent `track_id` values per camera, converting per-frame
   detections into continuous visitor trajectories.

4. **Zone event generation** — `pipeline/zones.py` converts track
   positions into events:
   - **`ENTRY` / `EXIT`** via signed line crossing at the entry camera
   - **`ZONE_ENTER` / `ZONE_EXIT` / `ZONE_DWELL`** via point-in-polygon
     with a 30-second dwell cadence
   - **`BILLING_QUEUE_JOIN` / `LEAVE` / `ABANDON`** via the billing
     polygon with 5-second minimum residency

5. **Re-entry detection** — `pipeline/reentry.py` maintains a 5-minute
   appearance-histogram cache. Any `ENTRY` whose 3-bin HSV signature
   matches a recent `EXIT` (cosine similarity ≥ 0.90) is reclassified
   as `REENTRY`.

6. **Staff classification** — `pipeline/staff.py` classifies each visitor
   using HSV uniform match plus a dwell-pattern heuristic (>2 distinct
   zones crossed in <30 seconds).

7. **Event emission** — `pipeline/emit.py` writes every event to
   `data/events.jsonl` AND posts batches of up to 500 to
   `POST /events/ingest`. JSONL is the durable source of truth; the API
   is the query surface.

8. **Ingest validation** — `POST /events/ingest` validates each event
   against the Pydantic `Event` schema, deduplicates on `event_id` via
   `ON CONFLICT DO NOTHING` (PK-enforced), and returns
   `{accepted, duplicates, rejected}` for partial-success semantics.

9. **POS integration** — `pipeline/post_pos.py` reads POS transaction
   data and posts it to `POST /pos/ingest` for correlation with store
   visitor events.

---

## 4. Event Schema

The Pydantic `Event` model in `app/models.py` is the single canonical type
emitted by the CV pipeline and consumed by the API. Its shape is optimised
for the four endpoint families:

| Endpoint Family | Fields Used |
|---|---|
| **`/metrics`** | `event_type=ENTRY`, `is_staff`, `zone_id` + `dwell_ms` for `ZONE_DWELL`, `metadata.queue_depth` |
| **`/funnel`** | `event_type ∈ {ENTRY, REENTRY, ZONE_ENTER, BILLING_QUEUE_JOIN}` + POS join |
| **`/heatmap`** | `zone_id`, `event_type ∈ {ZONE_ENTER, ZONE_DWELL}`, `dwell_ms` |
| **`/anomalies`** | `metadata.queue_depth`, `event_type=BILLING_QUEUE_JOIN` timestamps, missing `ZONE_ENTER` |

The open-ended `metadata: dict[str, Any]` field carries type-specific
extras (`queue_depth`, `sku_zone`, `session_seq`) without schema churn.
Pydantic still enforces the outer contract. `event_id` is a `UUID` so the
pipeline can mint IDs offline and the API can deduplicate without
coordination.

---

## 5. Storage & Idempotency

**PostgreSQL 16** is the primary data store, chosen over Redis and flat files
because:

- **Primary-key idempotency is free** — `PRIMARY KEY (event_id)` plus
  `ON CONFLICT DO NOTHING` gives bit-exact re-run safety. A re-runnable
  pipeline is essential for a demo environment.
- **Secondary indexes** on `(store_id, timestamp)` and
  `(event_type, store_id, timestamp)` make the analytics endpoints
  O(log n) rather than full scans.
- **Asyncpg + SQLAlchemy async** keeps the FastAPI event loop responsive
  under concurrent ingest and read operations.
- **Tests run against SQLite** (`aiosqlite`) for zero-setup isolation. The
  schema is defined once in `app/db.py` and replayed on both dialects.

---

## 6. Real-time Streaming

The system uses a **FastAPI WebSocket endpoint** (`/ws/stores/{store_id}`)
backed by an in-memory TTL cache with a 3-second time-to-live. This
architecture provides:

- **Single DB query set per cache interval** — whether 1 or 100 clients
  connect, PostgreSQL is queried at most once every 3 seconds.
- **Unified payload** — each WebSocket push contains metrics, funnel,
  heatmap, anomalies, and health data in a single JSON frame.
- **Dual dashboard support** — both the web dashboard (`dashboard/web/`)
  and the Rich-powered terminal dashboard (`dashboard/terminal_dashboard.py`)
  consume the same WebSocket endpoint.

---

## 7. Observability

Every request emits one JSON log line with `trace_id`, `endpoint`,
`store_id`, `latency_ms`, `event_count`, and `status_code`. The
`x-trace-id` header is propagated on response headers for end-to-end
correlation.

`/health` reports per-store last-event timestamps and flags `STALE_FEED`
for any store whose feed is >10 minutes silent.

---

## 8. Error Handling

Four global exception handlers in `app/errors.py` ensure no stack trace
ever leaks to the client:

| Exception | Response | Behaviour |
|---|---|---|
| `RequestValidationError` | **422** | Per-field `detail` array |
| `SQLAlchemyError` | **503** | Returns `request_id` — API stays up even if the DB blips |
| `HTTPException` | Variable | Safe `{error, request_id}` envelope |
| Catch-all `Exception` | **500** | Safe `{error, request_id}` envelope |

---

## 9. Camera & Zone Configuration

The zone configuration in `config/store_layout.json` is explicitly aligned
to the physical store blueprint (`Brigade Road - Store layout.xlsx`):

| Camera | Logical ID | Physical Zone | Role |
|---|---|---|---|
| CAM 3 | `CAM_ENTRY_01` | Glass doorway entrance | Entry line crossing (ENTRY / EXIT) |
| CAM 1 | `CAM_FLOOR_SKIN` | Skincare section | Product floor (ZONE_ENTER / DWELL / EXIT) |
| CAM 2 | `CAM_FLOOR_MAKEUP` | Makeup section | Product floor (ZONE_ENTER / DWELL / EXIT) |
| CAM 5 | `CAM_CASH_COUNTER` | Cash Counter | Billing zone (QUEUE_JOIN / LEAVE / ABANDON) |
| CAM 4 | `CAM_STOCKROOM` | Stockroom | Staff-only (`force_is_staff: true`) |

The pipeline dynamically resolves which camera handles billing queue
detection based on the `type: "billing"` flag in the layout config —
no camera IDs are hardcoded in the billing logic.

---

## 10. Testing Strategy

| Test Layer | Scope | Dependencies |
|---|---|---|
| **Pure-Python unit tests** | Geometry, line crossing, zone state machines, reentry cache, emitter buffering | None (no torch / OpenCV) — runs in <1 second |
| **In-process HTTP tests** | Every API endpoint via `httpx.AsyncClient` over `ASGITransport` | Fresh SQLite DB per test for isolation |
| **Named test cases** | Partial-success, 413, 422, idempotency, staff-exclusion, WebSocket connections | — |

**41/41 tests pass.** CV-runtime modules (`detect.py`, `tracker.py`,
`post_pos.py`, `staff.py`) are excluded from the unit-test line count
because they depend on heavy ML wheels and are exercised by the end-to-end
demo pipeline, not unit tests.

---

## 11. AI-Assisted Decisions

This codebase was designed and implemented with AI (LLM) collaboration.
Below are three places where the AI's initial suggestion was evaluated,
and where the final design either agreed with or overrode it.

### 11.1 Event Schema — Override

The AI proposed a **split-table schema** with separate `PersonEvent`,
`ZoneEvent`, and `BillingEvent` tables — arguing that each entity family
has different columns and that separate tables avoid sparse rows.

We **overrode this** and collapsed everything into a single `events` table
keyed by `event_id` with an open `metadata` dict. The reason: every
analytics query (`/metrics`, `/funnel`, `/heatmap`, `/anomalies`) joins
across event types for the same visitor in the same time window. A split
schema would force `UNION ALL` in every endpoint handler, complicate
idempotency (which PK per table?), and require awkward Pydantic union
types on ingest. The unified table eliminated all three problems.

### 11.2 Staff Detection — Override

The AI defaulted to **CLIP zero-shot classification** on each bounding
box — "run OpenAI's CLIP model to classify whether a person is wearing a
retail uniform." At 5 fps × 5 cameras, this would dominate pipeline
runtime and require downloading a second GPU-sized model.

We **overrode this** with a two-signal heuristic: HSV uniform colour
match (primary) plus a dwell-pattern check (>2 zones in <30 seconds =
staff). This runs at near-zero cost on CPU and produces accurate results
for this store's dark/black uniform. CLIP remains available as an optional
fallback (noted in `pipeline/staff.py`) but is not used in the default
path.

### 11.3 Re-entry Handling — Override

The AI proposed **per-frame cosine similarity against every active track**
in full history — essentially an unbounded Re-ID lookup on every new
ENTRY event.

We **overrode this** by bounding the search to a 5-minute sliding cache
(`pipeline/reentry.py`) so the cost is O(live_candidates), not
O(all_history). This also matches the business intent: "if a shopper
re-enters within a short window, don't double-count them." Exits older
than 5 minutes represent genuinely new visits, not re-entries.

---

## 12. Cross-Camera Deduplication

The entry camera (CAM 3) and the floor cameras partially overlap in their
field of view. Without deduplication, a person walking from the entry
threshold into the skincare zone is visible to both cameras simultaneously,
producing duplicate `ZONE_ENTER` events.

The `pipeline/cross_camera.py` module implements a `CrossCameraDedup` filter:

1. Events are keyed by `(visitor_id, zone_id)`.
2. If the same key is seen within a 3-second window, the second emission is
   suppressed.
3. The cache self-prunes every 50 frames to keep memory bounded.

This approach works because zone events are emitted per-camera independently.
A 3-second window is generous enough to catch the overlap without being so
long that it suppresses legitimate zone re-visits.

---

## 13. Scalability Considerations (40-Store Deployment)

The current architecture is built for the evaluation window (1 store,
5 cameras, ≤500 events per batch). At 40 live stores with real-time feeds,
the following bottlenecks would appear — and here is how we would address them:

| Bottleneck | Current | At 40 Stores |
|---|---|---|
| **Database writes** | Single Postgres, ~50 events/s | Connection pool exhaustion at 40 × 50 = 2,000 events/s. Fix: increase `pool_size`, add pgBouncer, partition `events` table by `store_id`. |
| **TTL cache** | In-memory dict, keyed by `store_id` | 40 keys × 5 endpoints = 200 cache entries. Still fits in memory, but cache churn increases. Fix: per-store TTL tuning, LRU eviction. |
| **WebSocket connections** | One WS per dashboard client | 40 stores × N clients could exhaust event loop. Fix: move WebSocket fan-out to Redis Pub/Sub so API pods are stateless. |
| **Pipeline compute** | Sequential per-camera YOLO inference | 40 × 5 cameras = 200 concurrent inference streams. Fix: GPU batch inference, horizontal pipeline workers with message queue (RabbitMQ / Kafka). |
| **Cross-camera dedup** | In-memory per pipeline process | At 40 stores, each pipeline instance handles one store — dedup is still in-memory per-process. No change needed. |

The key architectural decision that enables this scaling path is that **every
record is `store_id`-keyed**. The events table, POS table, and all queries
filter by `store_id` first, making horizontal partitioning (one DB partition
per store) straightforward.

---

## 14. Known Limitations

The system works end-to-end but has several limitations that would need to
be addressed before a production deployment:

### 14.1 Re-entry False Matches on Similar Clothing

The re-entry cache (`pipeline/reentry.py`) compares 3-bin HSV histogram
signatures via cosine similarity (threshold ≥ 0.90). Two distinct visitors
wearing similar-coloured clothing who exit and enter within the 5-minute
window may be falsely matched as a re-entry, under-counting unique visitors.

**Production fix:** Replace the HSV histogram with a learned person Re-ID
embedding (e.g., `torchreid` OSNet) that encodes body shape, texture, and
colour jointly. This was not used here to avoid a second large model download.

### 14.2 Zone Polygon Imprecision at Edges

Zone polygons in `config/store_layout.json` are manually defined rectangular
approximations of irregular floor regions. Visitors standing at the boundary
of a zone may flicker between inside/outside across consecutive frames,
generating spurious `ZONE_ENTER` / `ZONE_EXIT` pairs.

**Mitigation in place:** The 30-second dwell cadence for `ZONE_DWELL` events
means brief flickering does not inflate dwell metrics. However, `ZONE_ENTER`
counts may be slightly inflated for boundary cases.

**Production fix:** Add a hysteresis buffer (e.g., require 3 consecutive
inside-frames before emitting `ZONE_ENTER`) or use camera calibration to
project floor coordinates more precisely.

### 14.3 Queue Depth Jitter from Occlusion

The billing queue depth is computed by counting tracked persons inside the
billing zone polygon per frame. When two people in the queue stand close
together, YOLOv8 may merge them into a single detection (at `imgsz=320`),
causing a momentary depth drop. When they separate, the depth jumps back up.

**Observable symptom:** `current_queue_depth` in `/metrics` may fluctuate by
±1 during heavy occlusion periods. The `BILLING_QUEUE_SPIKE` anomaly
requires ≥ 2 sustained samples above threshold, which mitigates
false-positive spike alerts from single-frame jitter.

**Production fix:** Increase `imgsz` to 640 on GPU hardware, or apply a
3-frame moving-average filter on queue depth before emitting events.

### 14.4 HSV Staff Classification under Variable Lighting

The staff uniform classifier matches against a broad `V < 70` (dark clothing)
range. Under strong overhead lighting or near bright display shelves, a staff
member's uniform may appear lighter than `V = 70`, causing a false negative
(staff counted as customer). Conversely, a customer wearing dark clothing
(e.g., a black jacket) may be falsely flagged as staff.

**Mitigation in place:** The secondary dwell-pattern heuristic (>2 zones in
<30 seconds) catches some false negatives. The `force_is_staff` flag on the
stockroom camera provides a hard backstop for that camera.

**Production fix:** Calibrate HSV thresholds per-camera using sample frames,
or use a lightweight appearance classifier (fine-tuned MobileNet on uniform
vs. non-uniform patches).

---

## 15. What is Intentionally Out of Scope

- **Cross-store federation** — the brief focuses on one store. The schema
  scales (every event is `store_id`-keyed), but there is no store-to-store
  reconciliation logic.
- **Authentication** — the challenge did not require it; `/health` is open.
- **Training a custom detector** — pretrained YOLOv8-m is sufficient for
  person-only detection in a retail environment.
- **Kubernetes / tracing / Prometheus** — observability stops at structured
  JSON logs and the `/health` endpoint.

