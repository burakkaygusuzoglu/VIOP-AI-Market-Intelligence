"""HTTP middleware: request correlation and access logging."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.core.context import clear_request_id, set_request_id
from app.core.logging import get_logger

REQUEST_ID_HEADER = "X-Request-ID"

_logger = get_logger("app.api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, logs the outcome, and returns the id to the caller."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex
        set_request_id(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            _logger.exception(
                "request failed",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "duration_ms": duration_ms,
                },
            )
            clear_request_id()
            raise
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        _logger.info(
            "request completed",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers[REQUEST_ID_HEADER] = request_id
        clear_request_id()
        return response
