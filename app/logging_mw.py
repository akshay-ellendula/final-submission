"""Structured logging middleware.

Emits one JSON log line per request with trace_id, latency_ms, store_id,
endpoint, event_count, status_code. Uses structlog for minimal dependencies.
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Awaitable, Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from .config import SETTINGS


def configure_logging() -> None:
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, SETTINGS.log_level, logging.INFO),
    )
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, SETTINGS.log_level, logging.INFO),
        ),
    )


_STORE_PATTERN = re.compile(r"/stores/([^/]+)")


def _extract_store_id(path: str) -> str | None:
    m = _STORE_PATTERN.search(path)
    return m.group(1) if m else None


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_id = request.headers.get("x-trace-id") or str(uuid.uuid4())
        start = time.perf_counter()
        request.state.trace_id = trace_id
        request.state.event_count = 0

        log = structlog.get_logger().bind(
            trace_id=trace_id,
            endpoint=request.url.path,
            method=request.method,
            store_id=_extract_store_id(request.url.path),
        )

        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-trace-id"] = trace_id
            return response
        finally:
            latency_ms = int((time.perf_counter() - start) * 1000)
            log.info(
                "request",
                status_code=status_code,
                latency_ms=latency_ms,
                event_count=getattr(request.state, "event_count", 0),
            )
