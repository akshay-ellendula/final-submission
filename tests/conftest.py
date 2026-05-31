"""Shared pytest fixtures — SQLite-async for isolation.

Each test gets a fresh in-memory DB via a per-test URL + engine reset.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from app import db as app_db  # noqa: E402
from app.main import _CACHE, app  # noqa: E402

@pytest.fixture(autouse=True)
def flush_cache():
    _CACHE.clear()


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def clean_db():
    """Use a fresh in-memory sqlite per test."""
    url = f"sqlite+aiosqlite:///file:mem_{uuid.uuid4().hex}?mode=memory&cache=shared&uri=true"
    app_db.override_database_url(url)
    await app_db.create_all()
    yield
    await app_db.dispose()


@pytest_asyncio.fixture
async def client(clean_db) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


def make_event_payload(
    *,
    event_id: str | None = None,
    store_id: str = "STORE_001",
    camera_id: str = "CAM_ENTRY_01",
    visitor_id: str = "VIS_000001",
    event_type: str = "ENTRY",
    timestamp: str = "2026-04-19T10:00:00+00:00",
    zone_id: str | None = None,
    dwell_ms: int = 0,
    is_staff: bool = False,
    confidence: float = 0.9,
    metadata: dict | None = None,
) -> dict:
    return {
        "event_id": event_id or str(uuid.uuid4()),
        "store_id": store_id,
        "camera_id": camera_id,
        "visitor_id": visitor_id,
        "event_type": event_type,
        "timestamp": timestamp,
        "zone_id": zone_id,
        "dwell_ms": dwell_ms,
        "is_staff": is_staff,
        "confidence": confidence,
        "metadata": metadata or {},
    }
