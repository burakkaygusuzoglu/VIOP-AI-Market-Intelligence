"""A bounded ingest path for untrusted request bodies (§1).

## The gap this closes

Phase 8 bounded an OHLCV upload at 8 MiB per timeframe — and enforced it in
`InputLimits.check_csv_size`, which runs *after* FastAPI has read the whole
body, decoded it as JSON and validated it with pydantic. Measured against the
running container, a 64 MiB body was accepted in 0.5 s, fully materialised, and
only then rejected with `CSV_TOO_LARGE`. The limit described what the analysis
would *accept*; it was never a bound on what the process would *hold*.

nginx returned 413 for the same request at its 1 MiB default, which is exactly
the reverse-proxy assumption that must not be mistaken for application
security: the backend port is reachable without it, and a proxy's default is
not a decision this application made.

## The invariant

    UNTRUSTED INPUT HAS A BOUNDED HTTP INGEST PATH BEFORE THE BODY IS
    MATERIALISED.

Enforced here, as pure ASGI, because that is the only layer that can see the
`receive` callable before anything assembles a body from it. A
`BaseHTTPMiddleware` cannot: by the time it has a `Request`, the machinery to
buffer the body already exists.

## Two checks, because one is not enough

**Content-Length**, when present, rejects before a single body byte is read.
It is a fast path and nothing more — a chunked request carries no
Content-Length, and a dishonest one carries the wrong number.

**Received bytes**, always. Every `http.request` chunk is counted, and the
ceiling is enforced against what actually arrived. This is the check that
holds; the header check only makes the common case cheap.

## Refusing mid-stream

Once the app has begun consuming the body there is no clean way to "return" a
response from inside `receive`. So the middleware sends the 413 itself, then
hands the application an `http.disconnect` — the message an ASGI app already
has to handle for a client that hung up — and drops anything the application
tries to send afterwards. The route's dependencies never resolve, so no
analysis engine and no provider runs for a rejected request.

The response body names the limit and never echoes any part of the payload.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

DEFAULT_MAX_REQUEST_BYTES = 48 * 1024 * 1024
"""48 MiB.

Derived, not guessed: four timeframes at the 8 MiB per-dataset limit is 32 MiB
of CSV, and JSON string encoding inflates that slightly (a newline becomes two
characters). 48 MiB leaves headroom for a legitimate four-timeframe request
while keeping the worst case a bounded, survivable allocation.

It is deliberately **larger** than any single application limit. This is the
outer ceiling on what the process will hold; `InputLimits` remains the
authority on what an analysis will accept, and its typed errors are what a user
should normally see.
"""


class RequestSizeLimitMiddleware:
    """Reject a request body larger than ``max_bytes`` before materialising it."""

    def __init__(self, app: Any, *, max_bytes: int = DEFAULT_MAX_REQUEST_BYTES) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared is not None and declared > self.max_bytes:
            # Rejected before reading one byte of body.
            await self._reject(send)
            return

        state = {"received": 0, "rejected": False}

        async def limited_receive() -> Message:
            if state["rejected"]:
                return {"type": "http.disconnect"}
            message = await receive()
            if message.get("type") == "http.request":
                state["received"] += len(message.get("body", b"") or b"")
                if state["received"] > self.max_bytes:
                    state["rejected"] = True
                    await self._reject(send)
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message: Message) -> None:
            # After answering 413 the exchange is over; a late response from the
            # application would be a second set of headers on one request.
            if state["rejected"]:
                return
            await send(message)

        await self.app(scope, limited_receive, guarded_send)

    async def _reject(self, send: Send) -> None:
        body = json.dumps(
            {
                "detail": {
                    "code": "REQUEST_TOO_LARGE",
                    "detail": f"request body exceeds the {self.max_bytes} byte limit",
                }
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"connection", b"close"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body, "more_body": False})


def _declared_length(scope: Scope) -> int | None:
    """The Content-Length header as an int, or ``None`` when absent or unusable.

    A malformed header is treated as absent rather than as an error: the
    received-byte counter is the real check, and refusing here would turn a
    header quirk into a rejection the body size did not justify.
    """
    for name, value in scope.get("headers", []):
        if name.lower() == b"content-length":
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


# ----------------------------------------------------------------------
# Bounded validation errors
# ----------------------------------------------------------------------

MAX_VALIDATION_ERRORS = 20
"""How many field errors one 422 will list.

A request can be malformed in as many ways as it has fields, and a body with
one bad dataset per timeframe is a realistic four. Twenty leaves room for a
genuinely messy request while keeping the ceiling a constant rather than
something the sender chooses.
"""


def bounded_validation_response(exc: Any) -> tuple[int, dict[str, Any]]:
    """Project a pydantic validation failure into a response of bounded size.

    ## The gap this closes

    FastAPI's default 422 handler returns pydantic's own error list, and each
    entry carries an `input` key holding **the rejected value itself**. Measured
    against the running stack, a request with one 2 MB unexpected field produced
    a 2 000 113 byte error body: the response grew byte-for-byte with the input.

    That is the same class of defect as an unbounded findings list (§13) and it
    sits on the cheaper path - a rejected request never reaches an engine, so
    an attacker gets the amplification without paying for any analysis.

    ## What is returned instead

    `loc`, `type` and `msg`: where the problem is, what kind it is, and how to
    fix it. That is everything a client needs, because the client is the party
    that *sent* the value - it does not need to be told what it just typed.

    `input` is dropped rather than truncated, which also keeps this consistent
    with the 413 above: a refusal names the limit and never echoes any part of
    the payload. Echoing an excerpt of an unparsed CSV would put unvalidated
    bytes back on the wire for no diagnostic gain.

    The error count is capped and the number dropped is reported, so a client
    is never told a list is complete when it is not.
    """
    errors = list(getattr(exc, "errors", lambda: [])())
    shown = [
        {
            "loc": [str(part) for part in error.get("loc", ())],
            "type": str(error.get("type", "validation_error")),
            "msg": str(error.get("msg", ""))[:200],
        }
        for error in errors[:MAX_VALIDATION_ERRORS]
    ]
    return 422, {
        "detail": shown,
        "omitted_error_count": max(len(errors) - len(shown), 0),
    }
