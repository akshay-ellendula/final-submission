"""Centralised runtime configuration — reads from env with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    log_level: str
    stale_feed_minutes: int
    queue_spike_threshold: int
    queue_spike_duration_sec: int
    dead_zone_window_sec: int
    conversion_window_sec: int
    batch_max_events: int
    trailing_days: int

    @classmethod
    def from_env(cls) -> "Settings":
        # No credentialed default: if DATABASE_URL is unset, fall back to a
        # local SQLite file so tests and ad-hoc runs work without exposing
        # a password in source. docker-compose always injects DATABASE_URL.
        default_db = "sqlite+aiosqlite:///./data/apex.db"
        return cls(
            database_url=os.getenv("DATABASE_URL", default_db),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            stale_feed_minutes=int(os.getenv("STALE_FEED_MINUTES", "10")),
            queue_spike_threshold=int(os.getenv("QUEUE_SPIKE_THRESHOLD", "7")),
            queue_spike_duration_sec=int(os.getenv("QUEUE_SPIKE_DURATION_SEC", "120")),
            dead_zone_window_sec=int(os.getenv("DEAD_ZONE_WINDOW_SEC", "1800")),
            conversion_window_sec=int(os.getenv("CONVERSION_WINDOW_SEC", "300")),
            batch_max_events=int(os.getenv("BATCH_MAX_EVENTS", "500")),
            trailing_days=int(os.getenv("TRAILING_DAYS", "7")),
        )


SETTINGS = Settings.from_env()
