"""Centralised runtime configuration — reads from env with safe defaults."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _normalise_db_url(url: str) -> str:
    """Ensure the DATABASE_URL uses the asyncpg driver for PostgreSQL.

    Cloud platforms (Railway, Render, Heroku) provide DATABASE_URL as
    ``postgresql://...`` or ``postgres://...``, which makes SQLAlchemy pick
    the *psycopg2* sync driver.  We only ship *asyncpg*, so rewrite the
    scheme to ``postgresql+asyncpg://`` automatically.
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+asyncpg://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


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
        _project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        _data_dir = os.path.join(_project_root, "data")
        os.makedirs(_data_dir, exist_ok=True)
        default_db = f"sqlite+aiosqlite:///{os.path.join(_data_dir, 'apex.db')}"
        raw_url = os.getenv("DATABASE_URL", default_db)
        return cls(
            database_url=_normalise_db_url(raw_url),
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
