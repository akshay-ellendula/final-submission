"""Staff classification heuristics.

Primary signal: HSV colour match against store_layout.staff_uniform_hsv.
Secondary: dwell pattern — repeated crossing of >2 zones in <30s across a clip.
Fallback: CLIP zero-shot (not used in the default path because it pulls a
torch model download we'd rather keep optional).

Documented in docs/CHOICES.md.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class UniformHSV:
    h_range: tuple[int, int]
    s_range: tuple[int, int]
    v_range: tuple[int, int]

    def matches(self, h: float, s: float, v: float) -> bool:
        return (
            self.h_range[0] <= h <= self.h_range[1]
            and self.s_range[0] <= s <= self.s_range[1]
            and self.v_range[0] <= v <= self.v_range[1]
        )


@dataclass
class StaffClassifier:
    uniform: Optional[UniformHSV] = None
    force_is_staff: bool = False
    # dwell-pattern heuristic state
    _zone_history: dict[str, list[tuple[int, str]]] = field(default_factory=lambda: defaultdict(list))
    _flagged: set[str] = field(default_factory=set)

    def record_zone(self, visitor_id: str, zone_id: str, ts_ms: int) -> None:
        hist = self._zone_history[visitor_id]
        hist.append((ts_ms, zone_id))
        # Trim: keep only last 30s of history
        cutoff = ts_ms - 30_000
        while hist and hist[0][0] < cutoff:
            hist.pop(0)
        distinct_zones = {z for _, z in hist}
        if len(distinct_zones) > 2:
            self._flagged.add(visitor_id)

    def classify(
        self,
        visitor_id: str,
        avg_hsv: Optional[tuple[float, float, float]] = None,
    ) -> bool:
        if self.force_is_staff:
            return True
        if visitor_id in self._flagged:
            return True
        if self.uniform and avg_hsv and self.uniform.matches(*avg_hsv):
            return True
        return False
