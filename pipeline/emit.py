"""Event emission — JSONL writer + buffered POST to /events/ingest."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

import httpx


@dataclass
class EmitterConfig:
    api_url: Optional[str] = None           # e.g. "http://localhost:8000"
    jsonl_path: Optional[Path] = None
    batch_size: int = 500
    timeout_sec: float = 10.0


def make_event(
    *,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    event_type: str,
    timestamp: datetime,
    zone_id: Optional[str] = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.9,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "event_id": str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp.astimezone(timezone.utc).isoformat(),
        "zone_id": zone_id,
        "dwell_ms": int(dwell_ms),
        "is_staff": bool(is_staff),
        "confidence": float(confidence),
        "metadata": metadata or {},
    }


class Emitter:
    """Thread-safe-ish emitter. Buffers to batch_size, flushes JSONL + POST."""

    def __init__(self, config: EmitterConfig):
        self._cfg = config
        self._buffer: list[dict[str, Any]] = []
        self._jsonl_fh = None
        if config.jsonl_path:
            config.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            self._jsonl_fh = open(config.jsonl_path, "a", encoding="utf-8")
        self._http = httpx.Client(timeout=config.timeout_sec) if config.api_url else None
        self._posted = 0
        self._written = 0
        # Per-visitor event ordinal — injected into metadata.session_seq
        self._session_seq: dict[str, int] = {}

    def emit(self, event: dict[str, Any]) -> None:
        # Stamp session_seq (ordinal position of this event in the visitor session).
        vid = event.get("visitor_id")
        if vid:
            self._session_seq[vid] = self._session_seq.get(vid, 0) + 1
            meta = event.setdefault("metadata", {}) or {}
            if not isinstance(meta, dict):
                meta = {}
                event["metadata"] = meta
            meta.setdefault("session_seq", self._session_seq[vid])
            # On EXIT, drop the counter so a REENTRY restarts at 1.
            if event.get("event_type") == "EXIT":
                self._session_seq.pop(vid, None)
        self._buffer.append(event)
        if self._jsonl_fh:
            self._jsonl_fh.write(json.dumps(event) + "\n")
            self._written += 1
        if len(self._buffer) >= self._cfg.batch_size:
            self.flush()

    def emit_many(self, events: Iterable[dict[str, Any]]) -> None:
        for e in events:
            self.emit(e)

    def flush(self) -> None:
        if self._jsonl_fh:
            self._jsonl_fh.flush()
            os.fsync(self._jsonl_fh.fileno())
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        if self._http and self._cfg.api_url:
            try:
                r = self._http.post(
                    f"{self._cfg.api_url.rstrip('/')}/events/ingest",
                    json={"events": batch},
                )
                if r.status_code in (200, 207):
                    self._posted += len(batch)
                else:
                    print(f"[emit] POST failed {r.status_code}: {r.text[:200]}")
            except httpx.HTTPError as exc:
                print(f"[emit] POST error: {exc}")

    def close(self) -> None:
        self.flush()
        if self._jsonl_fh:
            self._jsonl_fh.close()
            self._jsonl_fh = None
        if self._http:
            self._http.close()

    @property
    def stats(self) -> dict[str, int]:
        return {"written": self._written, "posted": self._posted}

    def __enter__(self) -> "Emitter":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()
