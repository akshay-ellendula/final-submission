"""Pydantic v2 models for the event schema.

The schema matches the brief's Event specification exactly. Event is the
single canonical type emitted by the CV pipeline and consumed by the API.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class EventType(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"
    REENTRY = "REENTRY"
    ZONE_ENTER = "ZONE_ENTER"
    ZONE_EXIT = "ZONE_EXIT"
    ZONE_DWELL = "ZONE_DWELL"
    BILLING_QUEUE_JOIN = "BILLING_QUEUE_JOIN"
    BILLING_QUEUE_LEAVE = "BILLING_QUEUE_LEAVE"
    BILLING_QUEUE_ABANDON = "BILLING_QUEUE_ABANDON"
    POS_TRANSACTION = "POS_TRANSACTION"


class Event(BaseModel):
    """Canonical behavioural event.

    Immutable by construction (frozen). `metadata` is an open dict for
    type-specific extras like queue_depth, sku_zone, session_seq.
    """

    model_config = {"frozen": True, "extra": "forbid"}

    event_id: UUID
    store_id: str = Field(..., min_length=1, max_length=64)
    camera_id: str = Field(..., min_length=1, max_length=64)
    visitor_id: str = Field(..., min_length=1, max_length=64)
    event_type: EventType
    timestamp: datetime
    zone_id: Optional[str] = Field(default=None, max_length=64)
    dwell_ms: int = Field(default=0, ge=0)
    is_staff: bool = False
    confidence: float = Field(..., ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _ensure_tz(cls, v: datetime) -> datetime:
        # ISO8601 with TZ is required. Naive datetimes are coerced UTC for safety.
        if v.tzinfo is None:
            from datetime import timezone
            return v.replace(tzinfo=timezone.utc)
        return v


class IngestBatch(BaseModel):
    model_config = {"extra": "forbid"}
    events: list[Event] = Field(..., min_length=1, max_length=500)


class RejectedEvent(BaseModel):
    event_id: Optional[str] = None
    error: str


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int = 0
    rejected: list[RejectedEvent] = Field(default_factory=list)


class POSTransaction(BaseModel):
    """POS row used for conversion calculation."""
    model_config = {"extra": "forbid"}
    transaction_id: str
    store_id: str
    visitor_id: Optional[str] = None
    timestamp: datetime
    basket_value: float = Field(ge=0)
    items_count: int = Field(ge=0, default=0)
    line_items: list[dict[str, Any]] = Field(default_factory=list)
