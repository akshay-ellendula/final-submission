#!/usr/bin/env bash
# Runs YOLOv8-m+ByteTrack over all 5 CCTV clips, then ingests POS data.
#
# Usage:
#   bash pipeline/run_linux.sh                  # process all clips → POST to localhost
#   API_URL=http://localhost:8000 bash pipeline/run_linux.sh
#
# Requires: pip install -r requirements-pipeline.txt (includes torch, ultralytics, supervision)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_URL="${API_URL:-http://localhost:8000}"
CCTV_DIR="${CCTV_DIR:-../data-provided/CCTV Footage}"
LAYOUT="${LAYOUT:-config/store_layout.json}"
FPS="${FPS:-5}"
STORE="${STORE_ID:-ST1008}"

# Reset events file so re-runs are idempotent (DB itself is still idempotent on event_id).
mkdir -p data
: > data/events.jsonl

# Camera → clip mapping derived from Phase 0 recon (see store_layout.json).
declare -a MAPPING=(
    "CAM_ENTRY_01|CAM 3.mp4"
    "CAM_FLOOR_SKIN|CAM 1.mp4"
    "CAM_FLOOR_MAKEUP|CAM 2.mp4"
    "CAM_CASH_COUNTER|CAM 5.mp4"
    "CAM_STOCKROOM|CAM 4.mp4"
)

START_TS="${START_TS:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}"
echo "[run_linux.sh] start_ts=$START_TS  api=$API_URL  fps=$FPS  store=$STORE"

for entry in "${MAPPING[@]}"; do
    IFS='|' read -r cam clip <<< "$entry"
    clip_path="$CCTV_DIR/$clip"
    if [[ ! -f "$clip_path" ]]; then
        echo "[run_linux.sh] SKIP $cam — missing $clip_path"
        continue
    fi
    echo "[run_linux.sh] processing $cam <- $clip"
    python -m pipeline.detect \
        --clip "$clip_path" \
        --camera-id "$cam" \
        --store-id "$STORE" \
        --layout "$LAYOUT" \
        --api-url "$API_URL" \
        --jsonl "data/events.jsonl" \
        --start-ts "$START_TS" \
        --fps "$FPS"
done

echo "[run_linux.sh] posting real POS transactions"
python -m pipeline.post_pos \
    --csv "data-provided/Brigade_Bangalore_10_April_26 (1)bc6219c.csv" \
    --api-url "$API_URL"

echo "[run_linux.sh] done. Events in data/events.jsonl, POS ingested from real data."
