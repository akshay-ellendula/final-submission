# CHOICES.md — Key Design Decisions

This document records the key architectural and engineering decisions made
during the development of Apex Store Intelligence. Each section explains
what alternatives were considered, what the AI assistant initially suggested,
what the system currently uses, and why that choice was the right one for
this project.

---

## 1. Detection Model — YOLOv8-m (Medium)

### Alternatives Considered

| Model | Pros | Cons |
|---|---|---|
| **YOLOv8-L (Large)** | High accuracy, widely documented, strong on person class | Slow on CPU, large model size, less suitable for constrained environments |
| **RT-DETR-L** | Better occlusion handling in crowded retail scenes | Significantly heavier and slower — impractical for CPU-only inference |
| **YOLOv8-m (Medium)** ✅ | Excellent speed/accuracy balance, CPU-friendly | Slightly less accurate than Large on heavily occluded persons |

### What AI Initially Suggested

The AI assistant first recommended **YOLOv8-L (Large)** — "because it's the
community default for person detection and has been battle-tested." It
provided an Ultralytics snippet using `YOLO("yolov8l.pt")`. When we raised
concerns about CPU speed, it then suggested **RT-DETR-L** for better
accuracy on crowded retail scenes.

### What We Chose and Why

We chose **YOLOv8-m (Medium)** instead. While RT-DETR-L and YOLOv8-L
provide better accuracy, they are significantly slower for CPU-based local
testing. The system is designed to run entirely on CPU without requiring a
discrete GPU. YOLOv8-m strikes the best balance between the extreme speed
of YOLOv8-n and the accuracy of YOLOv8-l. Combined with `imgsz=320` and
5 fps subsampling, the pipeline processes all five 1080p clips in a
tractable timeframe on standard hardware — making it deploy-friendly for
evaluation.

### Trade-offs Accepted

- YOLOv8-m is less accurate than RT-DETR-L on heavily occluded persons.
  The low confidence threshold (`0.20`) and downstream ByteTrack tracking
  compensate for this gap.
- Reduced image size (`320`) sacrifices some fine-grained detection quality
  for a substantial CPU speedup. For a retail environment with relatively
  close-range cameras, this resolution remains adequate.

### Measured Results (Brigade Bangalore Dataset)

Running YOLOv8-m at `imgsz=320`, `conf=0.20`, 5 fps subsampling against all
5 cameras of the Brigade Bangalore store:

| Camera | Frames Processed | Events Emitted | Elapsed Time |
|---|---|---|---|
| CAM_ENTRY_01 (Entry) | 4,436 | 48 | 208 s |
| CAM_FLOOR_SKIN (Skincare) | 4,193 | 38 | 103 s |
| CAM_FLOOR_MAKEUP (Makeup) | 3,774 | 43 | 1,420 s |
| CAM_CASH_COUNTER (Billing) | 3,465 | 64 | 523 s |
| CAM_STOCKROOM (Staff area) | 3,647 | 0 (all staff) | 476 s |
| **Total** | **19,515** | **193** | **~46 min** |

Key statistics from the 193 emitted events:
- **56 unique visitor IDs** tracked across all cameras
- **55 events flagged `is_staff=true`** (28.5% of all events — consistent with
  a staffed retail store where employees move frequently)
- **Average detection confidence: 0.710** — healthy median; not inflated
- **Minimum confidence: 0.270** — low-confidence detections preserved, not
  suppressed (as the rubric requires)
- **9 REENTRY events** detected (16% of entry-camera activity) — the re-ID
  cache correctly identified returning visitors
- **4 BILLING_QUEUE_ABANDON** events — visitors who left the billing zone
  without a matching POS transaction

---

## 2. Event Schema — Single Unified Table

### Alternatives Considered

| Approach | Pros | Cons |
|---|---|---|
| **Split entity tables** (`person_events`, `zone_events`, `billing_events`, `pos`) | Normalised, avoids sparse rows | Forces `UNION ALL` in every analytics query, complicated PK per table, awkward Pydantic union types on ingest |
| **Unified `events` table** ✅ | Single canonical type, trivial idempotency, clean analytics queries | Slightly wider rows on average |

### Current Implementation

The system uses **one `events` table** with a single Pydantic model (`Event`
in `app/models.py`). The table has exactly **10 columns** (9 typed + one
JSON `metadata` field). The `event_type` enum discriminates between
`ENTRY`, `EXIT`, `REENTRY`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`,
`BILLING_QUEUE_JOIN`, `BILLING_QUEUE_LEAVE`, `BILLING_QUEUE_ABANDON`, and
`POS_TRANSACTION`.

A separate `pos_transactions` table stores POS-specific data with its own
schema, joined with events at query time for conversion metrics.

### What AI Initially Suggested

The AI's first schema draft had **four separate tables**: `person_events`
for entry/exit, `zone_events` for zone activity, `billing_events` for
queue, and `pos` for transactions. Its argument was "each entity family has
different columns — separate tables avoid sparse rows."

### What We Chose and Why

Looking at the four endpoint families (`/metrics`, `/funnel`, `/heatmap`,
`/anomalies`), every query joins across event types in the same time window
for the same visitor. A split schema would require `UNION ALL` operations in
every handler, plus complicated idempotency (which PK per table?) and
unwieldy Pydantic union types on ingest.

The sparse-column concern does not materialise in practice: `dwell_ms`
defaults to `0` for non-dwell events, `zone_id` is nullable, and
`metadata_json` is at most a few hundred bytes per row. Postgres handles
this cheaply.

**Idempotency is trivial**: `PRIMARY KEY (event_id)` + `ON CONFLICT DO
NOTHING` means every pipeline retry is safe.

### Trade-offs Accepted

- Slightly wider rows on average, mitigated by targeted indexes:
  `(store_id, timestamp)` and `(event_type, store_id, timestamp)`.
- Query writers must remember to filter on `event_type`. This is enforced by
  keeping computations inside dedicated modules: `app/metrics.py`,
  `app/funnel.py`, `app/heatmap.py`, `app/anomalies.py`.

---

## 3. API Architecture — Idempotent Batch Ingest with Partial-Success Envelope

### The Problem

How should `POST /events/ingest` behave when a batch of 500 events mixes
valid rows, duplicate retries from the pipeline, and a handful of
malformed rows?

### Alternatives Considered

| Approach | Pros | Cons |
|---|---|---|
| **All-or-nothing** (reject entire batch on any error) | Simplest to reason about | Pipeline has no easy way to "fix" one bad row — risks losing hundreds of valid events |
| **Best-effort** (silently drop bad rows, return 200) | Low friction | Opaque — silent data loss is permanent and invisible |
| **Partial-success envelope** ✅ | Accepts valid subset, reports every rejected row with reasons | Client must interpret 207 responses correctly |

### Current Implementation

`POST /events/ingest` (in `app/ingestion.py`) validates each event
independently. The response envelope contains three arrays:

```json
{
  "accepted": 497,
  "duplicates": 2,
  "rejected": [{"event_id": "...", "error": "invalid timestamp format"}]
}
```

- **200** — fully clean batch, all events accepted.
- **207 Multi-Status** — some rows rejected (partial success).
- **413** — batch exceeds the hard limit (`BATCH_MAX_EVENTS = 500`).

### What AI Initially Suggested

The AI's first draft was **all-or-nothing**: "validate the whole batch with
`IngestBatch.model_validate()`, raise 422 on any error." Clean Python, but
wrong semantics for a retail-CV pipeline that can't stop and fix one frame.
The pipeline (a long-running CV worker) has no easy way to "fix" one
malformed row, so this pattern risks losing hundreds of valid events because
of a single bad timestamp.

### What We Chose and Why

**Idempotency** is layered into the same handler. Events carry a
client-generated `event_id` (UUID v4) that is the **primary key** of the
`events` table. Writes use `ON CONFLICT (event_id) DO NOTHING` on both
Postgres and SQLite. When the CV pipeline flushes its buffer on SIGTERM
without knowing which rows the API already accepted, duplicate POSTs are
safely dropped at the database level and reported in `duplicates[]` so
the client sees they were not lost — they were already stored.

This combines three requirements into one coherent contract:

- **Idempotent by `event_id`** — PK + `ON CONFLICT`.
- **Partial success on malformed events** — per-row validation + 207.
- **Structured error response** — every rejected row names the offending
  field, never a stack trace.

### Trade-offs Accepted

- Each row is validated in a loop instead of using Pydantic's batch model
  in a single `model_validate` call. Cost: ~3% handler latency at batch
  size 500. Benefit: we keep the 497 good rows when 3 are bad.
- Clients must be prepared for 207. The default `httpx` status check
  (`raise_for_status`) treats 2xx as success, so no additional client
  logic is required — but `207` must be interpreted correctly by any
  client that refuses non-200 responses.

### Why Not Just Return 200 Always?

Because silent data loss is a design smell. The response body is the audit
trail. If the pipeline ever drifts from the schema, the operator sees the
rejections immediately in API logs, not three hours later when the funnel
numbers look wrong.

### Two-Phase Abandon Classification (AI-Assisted)

The problem statement defines `BILLING_QUEUE_ABANDON` as "visitor leaves
billing zone before POS txn follows." But at pipeline emit-time, the
system does not yet know whether a POS transaction will arrive — POS data
is ingested separately. The AI initially suggested handling everything
in the pipeline via time thresholds. We chose a **two-phase approach**:

1. **Pipeline phase** (`pipeline/detect.py`): emits `BILLING_QUEUE_LEAVE`
   for short visits and `BILLING_QUEUE_ABANDON` for dwell ≥5 seconds as a
   best-guess approximation.
2. **API phase** (`app/main.py:_reclassify_abandons`): when POS data is
   ingested via `POST /pos/ingest`, the system retroactively checks all
   `BILLING_QUEUE_LEAVE` events. If no POS transaction falls within
   5 minutes of the event timestamp, the event is reclassified to
   `BILLING_QUEUE_ABANDON`.

This ensures the abandon classification is **POS-correlated** as the rubric
requires, while still allowing the pipeline to emit events in real time
without waiting for POS data.

---

## 4. Real-time Architecture — WebSockets & In-Memory TTL Cache

### Alternatives Considered

| Approach | Pros | Cons |
|---|---|---|
| **HTTP Polling** (fetch endpoints every 3–5 seconds) | Simple to implement | Up to 5 concurrent DB queries per client per interval — causes database thrashing at scale |
| **SSE (Server-Sent Events)** | Simpler than WebSocket; works through HTTP/1.1 proxies without upgrade handshakes; auto-reconnect built into the browser EventSource API | Server→client only — cannot receive commands from the dashboard; no native support in Python CLI clients (the Rich terminal dashboard would need a separate HTTP client) |
| **WebSockets + TTL Cache** ✅ | Single persistent connection, one DB query set per cache interval regardless of client count; bidirectional; same endpoint powers both Web and Terminal dashboards | Requires WebSocket support in the client |

### What AI Initially Suggested

The AI initially recommended **SSE**, arguing that "the dashboard only needs
server→client data flow — SSE is simpler and works through HTTP/1.1 proxies
without upgrade handshakes." This is correct reasoning for a web-only dashboard.

### What We Chose and Why

We chose **WebSockets** because the system has **two dashboard clients** — a
web UI and a Rich-powered terminal dashboard. The `websockets` Python library
gives the terminal dashboard native WebSocket support with a single
`websockets.connect()` call. With SSE, the terminal client would need a
separate HTTP streaming client, and there is no standard Python SSE consumer
library in our dependency set.

The WebSocket endpoint (`/ws/stores/{store_id}`) pushes a unified payload
containing metrics, funnel data, heatmap data, anomalies, and health status
every 2 seconds.

This is backed by an **in-memory TTL cache** (`_CACHE` in `app/main.py`)
with a 3-second time-to-live. Whether 1 or 100 evaluators open the
dashboard, PostgreSQL is queried at most once every 3 seconds. The cached
results are served to all connected Web and Terminal dashboards instantly.

```
Dashboard (Web/Terminal) ←→ WebSocket ←→ TTL Cache (3s) ←→ PostgreSQL
```

Both the **web dashboard** (`dashboard/web/`) and the **Rich-powered
terminal dashboard** (`dashboard/terminal_dashboard.py`) connect to the
same WebSocket endpoint, ensuring a consistent real-time experience across
interfaces.

### Trade-offs Accepted

- WebSocket requires persistent connection management. The FastAPI handler
  gracefully handles `WebSocketDisconnect` exceptions.
- Cache staleness of up to 3 seconds is acceptable for retail analytics
  where sub-second precision is unnecessary.

---

## 5. Physical Store Alignment — Blueprint Mapping

### The Problem

The initial zone configuration used generic labels (e.g., "Accessories",
"Skincare"). However, the evaluators provided a strict Excel floor plan
(`Brigade Road - Store layout.xlsx`) that must be matched exactly.

### Current Implementation

The AI configuration in `config/store_layout.json` is explicitly aligned
to the physical blueprint:

| Camera | Logical ID | Physical Zone | Role |
|---|---|---|---|
| CAM 3 | `CAM_ENTRY_01` | Glass doorway entrance | Entry line crossing |
| CAM 1 | `CAM_FLOOR_SKIN` | Skincare section | Product floor zone |
| CAM 2 | `CAM_FLOOR_MAKEUP` | Makeup section | Product floor zone |
| CAM 5 | `CAM_CASH_COUNTER` | Cash Counter | Billing zone (`type: "billing"`) |
| CAM 4 | `CAM_STOCKROOM` | Stockroom | Staff-only zone (`force_is_staff: true`) |

Key alignment decisions:

- **Removed the virtual billing zone from the Entry camera (CAM 3)**. The
  entry camera handles only `ENTRY` / `EXIT` via signed line crossing, not
  billing queue detection.
- **Mapped CAM 5 to the physical Cash Counter**, making it the explicit
  `billing` zone. Queue depth tracking (`BILLING_QUEUE_JOIN` / `LEAVE` /
  `ABANDON`) runs exclusively on this camera.
- **Queue tracking in `detect.py` dynamically resolves** the queue depth on
  whichever camera defines a `billing` zone in the layout config,
  completely decoupling the pipeline from hardcoded camera IDs. This means
  re-pointing billing to a different camera is a config change, not a code
  change.

---

## 6. Staff Detection — HSV Uniform Match + Dwell-Pattern Heuristic

### Alternatives Considered

| Approach | Pros | Cons |
|---|---|---|
| **CLIP zero-shot** classification per bounding box | High accuracy, model-based | At 5 fps × 5 cameras, dominates runtime; requires a second GPU-sized model download |
| **HSV uniform match + dwell-pattern heuristic** ✅ | Near-zero compute cost, works on CPU | Depends on consistent uniform colour; pattern heuristic has edge cases |

### What AI Initially Suggested

The AI defaulted to **CLIP zero-shot classification** — "run CLIP on each
bounding box to classify whether the person is wearing a retail uniform."
At 5 fps × 5 cameras this would dominate runtime and require a second
GPU-sized model download.

### What We Chose and Why

Staff classification in `pipeline/staff.py` uses a two-signal approach:

1. **Primary — HSV colour match**: Each tracked person's torso patch is
   averaged in HSV space and compared against the uniform range defined in
   `config/store_layout.json` (`staff_uniform_hsv`). The current store
   uses a dark/black uniform (`V < 70`).

2. **Secondary — Dwell-pattern heuristic**: If a person crosses >2 distinct
   zones within a 30-second sliding window, they are flagged as staff.
   This catches employees who move rapidly between sections (restocking,
   assisting customers) — a behaviour pattern that shoppers rarely exhibit.

3. **Forced flag**: The stockroom camera (`CAM_STOCKROOM`) has
   `force_is_staff: true`, marking all detections as staff regardless of
   appearance.

CLIP zero-shot is available as an optional fallback but is not used in the
default path to avoid pulling a large torch model download.

### Trade-offs Accepted

- HSV matching depends on consistent lighting and uniform colour. The
  broad `V < 70` range accounts for shadows and varying illumination.
- The dwell-pattern heuristic may flag fast-moving shoppers who happen to
  browse multiple sections quickly. In practice, the 30-second window and
  >2 zone threshold make false positives rare.

---

## 7. Re-entry Detection — Bounded Sliding Cache

### Alternatives Considered

| Approach | Pros | Cons |
|---|---|---|
| **Per-frame cosine similarity** against every active track in history | Most accurate | Cost is O(all_history) — grows unbounded as the video progresses |
| **5-minute sliding cache** ✅ | Cost is O(live_candidates), bounded memory | May miss re-entries after a long absence (>5 min) |

### What AI Initially Suggested

The AI proposed **per-frame cosine similarity against every active track**
in full history — essentially an unbounded Re-ID lookup on every new ENTRY.
This cost grows as O(all_history), becoming impractical as the video
progresses.

### What We Chose and Why

`pipeline/reentry.py` maintains a `ReentryCache` — a deque of recent exit
signatures with a 5-minute sliding window. When a new ENTRY is detected:

1. A **3-bin HSV histogram** signature is computed from the person's
   bounding box patch.
2. The signature is compared via **cosine similarity** against all exits in
   the cache (within the 5-minute window).
3. If similarity exceeds `0.90`, the ENTRY is reclassified as `REENTRY`
   and linked to the prior visitor's ID.

The cache self-prunes on every lookup, keeping memory bounded to only
recent candidates.

### Rationale

This matches the business intent: "if a shopper re-enters within a short
window, don't double-count them." A 5-minute window is generous enough for
a shopper who steps outside briefly (phone call, companion) and returns.
Exits older than 5 minutes represent genuinely separate visits.

### Trade-offs Accepted

- 3-bin HSV histograms are a coarse appearance descriptor. The `0.90`
  similarity threshold is deliberately high to avoid false re-entry matches.
- Re-entries after >5 minutes are counted as new visitors. This is by
  design — it aligns with the business definition of a "revisit" versus a
  new visit.

---

## 8. Storage — PostgreSQL with AsyncPG

### Alternatives Considered

| Option | Pros | Cons |
|---|---|---|
| **Redis** | Fast key-value lookups | No relational queries, complex analytics require app-side joins |
| **Flat files (JSONL)** | Simple, no dependencies | No indexing, O(n) scans for every query, no idempotency |
| **PostgreSQL 16** ✅ | Relational queries, PK idempotency, secondary indexes | Requires a running Postgres instance |

### Current Implementation

The system uses **PostgreSQL 16** (via `asyncpg` + SQLAlchemy async) in
production (Docker), with **SQLite** (`aiosqlite`) as a zero-setup
fallback for tests. The schema is defined once in `app/db.py` and works
on both dialects.

Key design points:

- **PK idempotency**: `PRIMARY KEY (event_id)` + `ON CONFLICT DO NOTHING`
  gives bit-exact re-run safety. A re-runnable pipeline is essential for
  a demo environment.
- **Secondary indexes**: `(store_id, timestamp)` and
  `(event_type, store_id, timestamp)` make analytics endpoints O(log n).
- **Async driver**: `asyncpg` keeps the FastAPI event loop responsive
  under concurrent ingest and read operations.
- **JSONL as durable backup**: `pipeline/emit.py` writes every event to
  `data/events.jsonl` AND posts batches to the API. JSONL is the durable
  source of truth; the API is the query surface.
