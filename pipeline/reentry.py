"""Re-entry detection via short-term appearance cache.

Uses a 3-bin HSV histogram signature per visitor. On new ENTRY, checks cache
within a 5-minute window; returns the prior visitor_id if similarity passes
the threshold so we emit REENTRY instead of a fresh ENTRY.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional

Signature = tuple[float, ...]


def cosine_sim(a: Signature, b: Signature) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return 0.0 if na == 0 or nb == 0 else dot / (na * nb)


@dataclass
class ReentryCache:
    window_ms: int = 5 * 60 * 1000
    similarity_threshold: float = 0.90
    _entries: deque = field(default_factory=deque)

    def record_exit(self, visitor_id: str, signature: Signature, ts_ms: int) -> None:
        self._entries.append((ts_ms, visitor_id, signature))
        self._prune(ts_ms)

    def lookup(self, signature: Signature, ts_ms: int) -> Optional[str]:
        self._prune(ts_ms)
        best_vid = None
        best_sim = 0.0
        for _ts, vid, sig in self._entries:
            sim = cosine_sim(signature, sig)
            if sim > best_sim:
                best_sim = sim
                best_vid = vid
        return best_vid if best_sim >= self.similarity_threshold else None

    def _prune(self, now_ms: int) -> None:
        cutoff = now_ms - self.window_ms
        while self._entries and self._entries[0][0] < cutoff:
            self._entries.popleft()
