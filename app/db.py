"""Database layer — SQLAlchemy async engine + schema definitions.

Supports Postgres (prod) and SQLite (tests) transparently.
Idempotency is enforced at the schema level: PRIMARY KEY (event_id).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Float,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from .config import SETTINGS

metadata = MetaData()

events_table = Table(
    "events",
    metadata,
    Column("event_id", String(64), primary_key=True),
    Column("store_id", String(64), nullable=False),
    Column("camera_id", String(64), nullable=False),
    Column("visitor_id", String(64), nullable=False),
    Column("event_type", String(32), nullable=False),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("zone_id", String(64), nullable=True),
    Column("dwell_ms", Integer, nullable=False, default=0),
    Column("is_staff", Boolean, nullable=False, default=False),
    Column("confidence", Float, nullable=False),
    Column("metadata_json", JSON, nullable=False, default=dict),
    Index("ix_events_store_time", "store_id", "timestamp"),
    Index("ix_events_visitor", "visitor_id"),
    Index("ix_events_type_store_time", "event_type", "store_id", "timestamp"),
)

pos_transactions_table = Table(
    "pos_transactions",
    metadata,
    Column("transaction_id", String(64), primary_key=True),
    Column("store_id", String(64), nullable=False),
    Column("visitor_id", String(64), nullable=True),
    Column("timestamp", DateTime(timezone=True), nullable=False),
    Column("basket_value", Float, nullable=False),
    Column("items_count", Integer, nullable=False, default=0),
    Column("line_items", JSON, nullable=False, default=list),
    Index("ix_pos_store_time", "store_id", "timestamp"),
)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _make_engine(url: str) -> AsyncEngine:
    # sqlite-async needs the aiosqlite driver and no pool sizing.
    if url.startswith("sqlite"):
        return create_async_engine(url, future=True)
    return create_async_engine(url, future=True, pool_size=10, max_overflow=5)


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = _make_engine(SETTINGS.database_url)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    sm = get_sessionmaker()
    async with sm() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise


async def create_all() -> None:
    """Create tables if not present. Used for tests + first-boot safety net.

    In production, alembic drives migrations; this is an additional guard so
    the acceptance gate never trips on missing tables.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def dispose() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _sessionmaker = None


def override_database_url(url: str) -> None:
    """Test hook — replace the global URL and force engine rebuild on next use."""
    global _engine, _sessionmaker
    _engine = None
    _sessionmaker = None
    # SETTINGS is frozen; we mutate via object.__setattr__ for the test path only.
    object.__setattr__(SETTINGS, "database_url", url)
