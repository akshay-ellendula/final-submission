"""Global error handlers — 5xx become graceful 503 with request_id, never stack traces."""
from __future__ import annotations

import uuid

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException


def _request_id(request: Request) -> str:
    return getattr(request.state, "trace_id", None) or str(uuid.uuid4())


def register_error_handlers(app: FastAPI) -> None:
    log = structlog.get_logger()

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": exc.errors(),
                "request_id": _request_id(request),
            },
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_handler(request: Request, exc: SQLAlchemyError):
        rid = _request_id(request)
        log.error("database_error", request_id=rid, error_class=type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "error": "service_unavailable",
                "message": "Database temporarily unavailable. Retry shortly.",
                "request_id": rid,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": "http_error",
                "message": exc.detail,
                "request_id": _request_id(request),
            },
        )

    @app.exception_handler(Exception)
    async def generic_handler(request: Request, exc: Exception):
        rid = _request_id(request)
        log.error("unhandled_exception", request_id=rid, error_class=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An internal error occurred.",
                "request_id": rid,
            },
        )
