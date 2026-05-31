"""Cross-camera deduplication filter.

Prevents the same visitor from generating duplicate ZONE_ENTER events
when two cameras have overlapping fields of view. Keyed by
(visitor_id, zone_id) with a configurable suppression window (default 3 s).
"""
from __future__ import annotations


class CrossCameraDedup:
    """Suppresses duplicate zone events for the same visitor within a time window."""

    def __init__(self, window_ms: int = 3_000):
        self._window_ms = window_ms
        # (visitor_id, zone_id) → last_emit_ts_ms
        self._seen: dict[tuple[str, str], int] = {}

    def should_emit(self, visitor_id: str, zone_id: str, ts_ms: int) -> bool:
        """Returns True if this zone event should be emitted (not a dup)."""
        key = (visitor_id, zone_id)
        last = self._seen.get(key)
        if last is not None and (ts_ms - last) < self._window_ms:
            return False  # suppress — duplicate within window
        self._seen[key] = ts_ms
        return True

    def prune(self, now_ms: int) -> None:
        """Remove entries older than 2× the window to keep memory bounded."""
        cutoff = now_ms - (self._window_ms * 2)
        self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
