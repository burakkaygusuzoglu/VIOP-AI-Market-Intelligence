"""Request correlation context.

Every log line carries the id of the request that produced it, so that the
question 'what exactly did the application know at this moment' can be
answered from the logs (master spec section 67).
"""

from __future__ import annotations

from contextvars import ContextVar

_request_id: ContextVar[str | None] = ContextVar("viop_request_id", default=None)


def set_request_id(request_id: str) -> None:
    """Bind a request id to the current execution context."""
    _request_id.set(request_id)


def get_request_id() -> str | None:
    """Return the request id bound to the current context, if any."""
    return _request_id.get()


def clear_request_id() -> None:
    """Detach any request id from the current context."""
    _request_id.set(None)
