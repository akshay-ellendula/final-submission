"""Zone geometry + line crossing + dwell timers.

Pure-Python, no CV/torch deps so it can be unit-tested cheaply.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


Point = tuple[float, float]


def point_in_polygon(pt: Point, polygon: list[Point]) -> bool:
    """Ray casting point-in-polygon. O(n) and tolerant of open polygons."""
    if len(polygon) < 3:
        return False
    x, y = pt
    inside = False
    n = len(polygon)
    j = n - 1
    for i in range(n):
        xi, yi = polygon[i]
        xj, yj = polygon[j]
        intersect = ((yi > y) != (yj > y)) and (
            x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
        )
        if intersect:
            inside = not inside
        j = i
    return inside


def line_side(pt: Point, a: Point, b: Point) -> float:
    """Signed scalar — positive on one side of the line, negative on the other."""
    return (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0])


@dataclass
class LineCrossing:
    """Track which side of a line each visitor was last seen on.

    Returns 'enter' / 'exit' / None when the signed side flips.
    Direction convention is set by `inside_normal` (dot product test).
    """

    a: Point
    b: Point
    inside_normal: tuple[float, float] = (0.0, 1.0)
    _last_side: dict[str, float] = field(default_factory=dict)

    def update(self, visitor_id: str, pt: Point) -> Optional[str]:
        side = line_side(pt, self.a, self.b)
        prev = self._last_side.get(visitor_id)
        self._last_side[visitor_id] = side
        if prev is None:
            return None
        if prev == 0 or side == 0:
            return None
        if (prev < 0) == (side < 0):
            return None  # same side, no crossing
        # crossing occurred — figure out direction
        # normal-aligned side is 'inside'
        # We treat going to inside_normal direction as ENTER.
        inside_score = side * (self.inside_normal[0] + self.inside_normal[1])
        return "enter" if inside_score > 0 else "exit"


@dataclass
class ZoneState:
    """Tracks per-(visitor, zone) presence and dwell accumulation."""

    in_zone_since_ms: dict[tuple[str, str], int] = field(default_factory=dict)
    last_dwell_emit_ms: dict[tuple[str, str], int] = field(default_factory=dict)

    def on_zone_event(
        self,
        visitor_id: str,
        zone_id: str,
        is_inside: bool,
        timestamp_ms: int,
    ) -> list[tuple[str, int]]:
        """Return a list of (event_type, dwell_ms) tuples to emit."""
        key = (visitor_id, zone_id)
        out: list[tuple[str, int]] = []
        currently_in = key in self.in_zone_since_ms

        if is_inside and not currently_in:
            self.in_zone_since_ms[key] = timestamp_ms
            self.last_dwell_emit_ms[key] = timestamp_ms
            out.append(("ZONE_ENTER", 0))
        elif is_inside and currently_in:
            start = self.in_zone_since_ms[key]
            last_emit = self.last_dwell_emit_ms[key]
            if timestamp_ms - last_emit >= 30_000:  # 30s cadence
                out.append(("ZONE_DWELL", timestamp_ms - start))
                self.last_dwell_emit_ms[key] = timestamp_ms
        elif not is_inside and currently_in:
            start = self.in_zone_since_ms.pop(key)
            self.last_dwell_emit_ms.pop(key, None)
            out.append(("ZONE_EXIT", timestamp_ms - start))
        return out


def bbox_center(bbox: tuple[float, float, float, float]) -> Point:
    """(x1,y1,x2,y2) → (cx, cy)."""
    x1, y1, x2, y2 = bbox
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)
