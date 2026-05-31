"""ByteTrack wrapper — thin façade over supervision.ByteTrack for testability.

Exposes a `Tracker` with `update(detections) -> list[Track]` — in production
this wraps `supervision.ByteTrack.update_with_detections`, and in tests we
fall back to an identity tracker keyed by detection index.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol


@dataclass
class Track:
    track_id: int
    bbox: tuple[float, float, float, float]  # (x1, y1, x2, y2)
    confidence: float


class DetectionProvider(Protocol):
    def __iter__(self): ...  # pragma: no cover


class Tracker:
    """Wraps supervision.ByteTrack, with an identity fallback."""

    def __init__(self, min_confidence: float = 0.25):
        self.min_confidence = min_confidence
        self._bt = None
        self._next_id = 1
        self._sv: Any = None
        try:
            import supervision as sv  # type: ignore
            self._bt = sv.ByteTrack()
            self._sv = sv
        except Exception:
            # Identity fallback — still produces deterministic track ids.
            self._sv = None

    def update(self, detections: list[tuple[tuple[float, float, float, float], float]]) -> list[Track]:
        """detections: list of ((x1,y1,x2,y2), confidence)."""
        filtered = [(b, c) for b, c in detections if c >= self.min_confidence]
        if not filtered:
            return []

        if self._bt is not None and self._sv is not None:
            import numpy as np  # type: ignore
            xyxy = np.array([list(b) for b, _ in filtered], dtype=float)
            conf = np.array([c for _, c in filtered], dtype=float)
            cls = np.zeros(len(filtered), dtype=int)
            det = self._sv.Detections(xyxy=xyxy, confidence=conf, class_id=cls)
            det = self._bt.update_with_detections(det)
            out: list[Track] = []
            for i in range(len(det)):
                tid = int(det.tracker_id[i]) if det.tracker_id is not None else -1
                if tid < 0:
                    continue
                xs = det.xyxy[i]
                bbox: tuple[float, float, float, float] = (
                    float(xs[0]), float(xs[1]), float(xs[2]), float(xs[3])
                )
                conf_val = float(det.confidence[i]) if det.confidence is not None else 0.0
                out.append(
                    Track(
                        track_id=tid,
                        bbox=bbox,
                        confidence=conf_val,
                    )
                )
            return out

        # Identity fallback — each detection gets a stable id in call order.
        out = []
        for b, c in filtered:
            out.append(Track(track_id=self._next_id, bbox=b, confidence=c))
            self._next_id += 1
        return out


def track_id_to_visitor_id(track_id: int) -> str:
    return f"VIS_{track_id:06x}"
