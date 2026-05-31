# 🛍️ Apex · Purplle Store Intelligence System

**End-to-end CCTV → Analytics pipeline for the Purplle Engineering Hiring
Challenge.** Processes raw 1080p retail footage through YOLOv8-m + ByteTrack,
emits structured behavioural events, and exposes real-time metrics via a
containerised FastAPI service.

> **AI-Assisted.** This codebase was designed and implemented with LLM
> collaboration. All AI-influenced decisions are documented in
> [`docs/CHOICES.md`](docs/CHOICES.md) with what the AI suggested, what was
> overridden, and why.

---

## Key Capabilities

| Capability | Detail |
|---|---|
| **Person detection** | YOLOv8-m (Medium) at `imgsz=320`, `conf=0.20`, 5 fps subsampling — 19,515 frames in ~46 min on CPU |
| **Tracking** | ByteTrack via `supervision` — persistent `visitor_id` per camera |
| **Event types** | `ENTRY`, `EXIT`, `REENTRY`, `ZONE_ENTER`, `ZONE_EXIT`, `ZONE_DWELL`, `BILLING_QUEUE_JOIN`, `BILLING_QUEUE_LEAVE`, `BILLING_QUEUE_ABANDON`, `POS_TRANSACTION` |
| **Staff classification** | HSV uniform match + dwell-pattern heuristic (>2 zones in <30 s) |
| **Re-entry detection** | 5-minute HSV histogram cache with cosine similarity ≥ 0.90 |
| **Cross-camera dedup** | 3-second suppression window per `(visitor_id, zone_id)` pair |
| **API** | FastAPI + PostgreSQL (asyncpg) — idempotent ingest, partial-success 207, structured error envelopes |
| **Real-time streaming** | WebSocket (`/ws/stores/{id}`) with 3-second TTL cache — powers both web and terminal dashboards |
| **POS correlation** | Two-phase abandon classification — pipeline emits best-guess, API reclassifies after POS ingest |
| **Anomaly detection** | Queue spike, conversion drop, dead zone, stale camera |
| **Test suite** | **41/41 passing** — pure-Python pipeline unit tests + in-process HTTP tests via SQLite |

---

## Quick Start

### Prerequisites

- Docker & Docker Compose (for the API + PostgreSQL)
- Python 3.11+ with a virtual environment (for the CV pipeline)

### Step 1 — Start the Docker Backend

```bash
docker-compose down -v        # clean slate
docker-compose up --build     # starts FastAPI + PostgreSQL
```

The API is now live at **http://localhost:8000**. Keep this terminal open.

### Step 2 — Install Pipeline Dependencies

Open a **second** terminal in this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\activate            # Windows
# source .venv/bin/activate         # Linux / macOS
pip install -r requirements-pipeline.txt
```

### Step 3 — Open the Dashboards

- **Web Dashboard:** http://localhost:8000/
- **Terminal Dashboard (optional):**

  **Windows:**
  ```powershell
  python dashboard\terminal_dashboard.py
  ```
  **macOS / Linux:**
  ```bash
  python dashboard/terminal_dashboard.py
  ```

Both dashboards will show zeroes until the pipeline starts feeding events.

### Step 4 (Optional) — Preview YOLO Detections

To visually verify that YOLOv8 is detecting people correctly before running
the full pipeline, use the live preview tool. This opens a 3×2 grid window
showing all 5 cameras with bounding boxes drawn in real time:

```bash
python pipeline/preview.py
```

Press `q` in the preview window to quit. This uses YOLOv8-n (nano) for speed.

### Step 5 — Run the CV Pipeline

In the second terminal (virtual environment active):

**Windows (PowerShell):**
```powershell
.\pipeline\run_windows.ps1
```

**macOS / Linux (Bash):**
```bash
bash pipeline/run_linux.sh
```

The pipeline processes all 5 cameras sequentially, then ingests real POS
transaction data. Watch the dashboards update in real time.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/health` | Service health — DB status, per-store staleness |
| `POST` | `/events/ingest` | Batch event ingest (idempotent, partial-success) |
| `POST` | `/pos/ingest` | POS transaction ingest (triggers abandon reclassification) |
| `GET` | `/stores/{id}/metrics` | Store KPIs — visitors, conversion, queue depth, staff count |
| `GET` | `/stores/{id}/funnel` | Conversion funnel — Entry → ZoneVisit → BillingQueue → Purchase |
| `GET` | `/stores/{id}/heatmap` | Zone visit intensity with normalised [0,100] scores |
| `GET` | `/stores/{id}/anomalies` | Active anomalies with severity and suggested actions |
| `GET` | `/stores/{id}/events` | Paginated raw event list (`?limit=100&offset=0`) |
| `WS`  | `/ws/stores/{id}` | Real-time WebSocket stream — metrics, funnel, heatmap, anomalies, health every 2 s |

---

## Testing

```bash
pytest tests/ -v
```

All **41 tests** pass, covering:

- API endpoints (ingest, metrics, funnel, heatmap, anomalies, events, health)
- Idempotency and partial-success semantics
- POS-correlated abandon reclassification
- Pipeline geometry (point-in-polygon, line crossing, zone state machine)
- Re-entry cache eviction and cosine similarity
- Cross-camera deduplication
- Emitter JSONL buffering and HTTP batching
- UUID uniqueness and Pydantic schema compliance

---

## Project Structure

```
├── app/                  # FastAPI application
│   ├── main.py           # Entrypoint, routes, WebSocket, TTL cache
│   ├── models.py         # Pydantic v2 Event + POS schemas
│   ├── db.py             # SQLAlchemy async schema + engine
│   ├── ingestion.py      # Batch ingest with partial-success
│   ├── metrics.py        # Store KPI computation
│   ├── funnel.py         # Session-based conversion funnel
│   ├── heatmap.py        # Zone visit intensity
│   ├── anomalies.py      # Anomaly detection rules
│   ├── health.py         # Health check + stale feed detection
│   ├── errors.py         # Global error handlers
│   ├── logging_mw.py     # Structured JSON logging middleware
│   └── config.py         # Environment-based settings
├── pipeline/             # CV pipeline (runs locally, not in Docker)
│   ├── detect.py         # YOLOv8-m inference + zone + emit loop
│   ├── tracker.py        # ByteTrack wrapper with identity fallback
│   ├── zones.py          # Geometry: point-in-polygon, line crossing, dwell
│   ├── staff.py          # HSV + dwell-pattern staff classification
│   ├── reentry.py        # 5-minute appearance cache for re-entry detection
│   ├── cross_camera.py   # Cross-camera deduplication filter
│   ├── emit.py           # JSONL writer + buffered HTTP POST
│   ├── post_pos.py       # POS CSV → API ingest
│   ├── run_windows.ps1   # Windows pipeline runner
│   └── run_linux.sh      # Linux/macOS pipeline runner
├── dashboard/
│   ├── web/              # HTML/JS/CSS web dashboard
│   └── terminal_dashboard.py  # Rich-powered terminal dashboard
├── config/
│   ├── store_layout.json # Camera → zone mapping (aligned to floor plan)
│   └── alembic.ini       # Alembic migration config
├── tests/                # 41 test cases
├── docs/
│   ├── DESIGN.md         # System architecture and data flow
│   └── CHOICES.md        # Engineering decisions with AI override rationale
├── Dockerfile            # Slim API image (~200 MB, no torch)
├── docker-compose.yml    # API + PostgreSQL 16
├── requirements.txt      # API-only deps (Docker)
└── requirements-pipeline.txt  # Full deps (local: API + torch + ultralytics)
```

---

## Documentation

- **[DESIGN.md](docs/DESIGN.md)** — System architecture, data flow, schema, storage, streaming, observability, error handling, camera alignment, testing strategy, scalability considerations
- **[CHOICES.md](docs/CHOICES.md)** — 8 key engineering decisions with alternatives evaluated, AI suggestions documented, trade-offs accepted, and measured results
