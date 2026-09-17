"""The HTTP ingest boundary (§1).

Phase 8 bounded an OHLCV dataset at 8 MiB and enforced it after FastAPI had
already read, decoded and validated the whole body. Measured against the
running container, a 64 MiB request was materialised in 0.5 s and only then
rejected. These tests exist so that cannot come back.

They generate bodies as bounded streams rather than as one large object: proving
a memory bound by first building the thing in the test runner would be its own
small version of the bug.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, MutableMapping
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.limits import RequestSizeLimitMiddleware
from app.core.config import Settings
from app.main import create_app

SMALL_LIMIT = 4096
"""Small enough to exercise the boundary without moving real megabytes."""


def settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "app_env": "test",
        "app_version": "0.0.0-test",
        "postgres_host": "localhost",
        "postgres_port": 5432,
        "postgres_user": "viop",
        "postgres_password": SecretStr("fixture-password"),  # TEST_FIXTURE value
        "postgres_db": "viop_test",
    }
    base.update(overrides)
    return Settings(**base)


class _Spy:
    """Records whether the wrapped application was ever entered, and with what."""

    def __init__(self) -> None:
        self.calls = 0
        self.body_bytes = 0
        self.saw_disconnect = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls += 1
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                self.saw_disconnect = True
                return
            self.body_bytes += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def drive(
    middleware: RequestSizeLimitMiddleware,
    *,
    chunks: Iterator[bytes],
    content_length: int | None,
) -> list[dict[str, Any]]:
    """Run one request through the middleware and collect what it sent."""
    headers: list[tuple[bytes, bytes]] = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))

    pending = list(chunks)

    async def receive() -> dict[str, Any]:
        if not pending:
            return {"type": "http.request", "body": b"", "more_body": False}
        body = pending.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(pending)}

    sent: list[dict[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    await middleware({"type": "http", "headers": headers, "method": "POST"}, receive, send)
    return sent


def status_of(sent: list[dict[str, Any]]) -> int | None:
    for message in sent:
        if message["type"] == "http.response.start":
            return int(message["status"])
    return None


def body_of(sent: list[dict[str, Any]]) -> bytes:
    return b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")


# ----------------------------------------------------------------------
# The middleware in isolation
# ----------------------------------------------------------------------


class TestIngestBoundary:
    @pytest.mark.asyncio
    async def test_oversized_content_length_is_rejected_before_any_body_is_read(self) -> None:
        """The fast path. Not one byte of body reaches the application."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent = await drive(
            middleware,
            chunks=iter([b"x" * 1024]),
            content_length=SMALL_LIMIT * 100,
        )

        assert status_of(sent) == 413
        assert spy.calls == 0, "the application ran for an over-declared request"
        assert spy.body_bytes == 0

    @pytest.mark.asyncio
    async def test_a_chunked_body_with_no_content_length_cannot_exceed_the_ceiling(self) -> None:
        """The check that actually holds.

        A chunked request declares no length, so the header fast path is blind
        to it. The received-byte counter is what bounds this.
        """
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        # Ten times the ceiling, arriving in pieces.
        sent = await drive(
            middleware,
            chunks=iter([b"x" * 1024 for _ in range(40)]),
            content_length=None,
        )

        assert status_of(sent) == 413
        assert spy.body_bytes <= SMALL_LIMIT + 1024, (
            f"application received {spy.body_bytes} bytes past a {SMALL_LIMIT} byte limit"
        )

    @pytest.mark.asyncio
    async def test_a_lying_content_length_does_not_get_past_the_counter(self) -> None:
        """A small declared length with a large body is still bounded."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent = await drive(
            middleware,
            chunks=iter([b"x" * 1024 for _ in range(40)]),
            content_length=16,
        )

        assert status_of(sent) == 413
        assert spy.body_bytes <= SMALL_LIMIT + 1024

    @pytest.mark.asyncio
    async def test_the_application_is_told_the_client_disconnected(self) -> None:
        """So a partially-read handler stops rather than waiting forever."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        await drive(
            middleware,
            chunks=iter([b"x" * 1024 for _ in range(40)]),
            content_length=None,
        )

        assert spy.saw_disconnect is True

    @pytest.mark.asyncio
    async def test_a_normal_body_passes_through_untouched(self) -> None:
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent = await drive(
            middleware,
            chunks=iter([b"a" * 100, b"b" * 100]),
            content_length=200,
        )

        assert status_of(sent) == 200
        assert spy.body_bytes == 200

    @pytest.mark.asyncio
    async def test_a_body_exactly_at_the_limit_is_accepted(self) -> None:
        """The boundary is inclusive. `> max` rejects, `== max` does not."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent = await drive(
            middleware,
            chunks=iter([b"x" * SMALL_LIMIT]),
            content_length=SMALL_LIMIT,
        )

        assert status_of(sent) == 200
        assert spy.body_bytes == SMALL_LIMIT

    @pytest.mark.asyncio
    async def test_one_byte_over_the_limit_is_rejected(self) -> None:
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent = await drive(
            middleware,
            chunks=iter([b"x" * (SMALL_LIMIT + 1)]),
            content_length=None,
        )

        assert status_of(sent) == 413

    @pytest.mark.asyncio
    async def test_the_rejection_names_the_limit_and_echoes_no_payload(self) -> None:
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent = await drive(
            middleware,
            chunks=iter([b"<script>alert(1)</script>" * 400]),
            content_length=None,
        )

        body = body_of(sent)
        assert b"REQUEST_TOO_LARGE" in body
        assert str(SMALL_LIMIT).encode() in body
        assert b"<script>" not in body

    @pytest.mark.asyncio
    async def test_a_malformed_content_length_falls_through_to_the_counter(self) -> None:
        """A header quirk must not become a rejection the body did not earn."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        headers = [(b"content-length", b"not-a-number")]
        pending = [b"ok"]

        async def receive() -> dict[str, Any]:
            body = pending.pop(0) if pending else b""
            return {"type": "http.request", "body": body, "more_body": bool(pending)}

        sent: list[dict[str, Any]] = []

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(dict(message))

        await middleware({"type": "http", "headers": headers, "method": "POST"}, receive, send)

        assert status_of(sent) == 200

    @pytest.mark.asyncio
    async def test_non_http_scopes_are_passed_straight_through(self) -> None:
        """Lifespan and websocket scopes carry no body to bound."""
        seen: list[str] = []

        async def app(scope: Any, receive: Any, send: Any) -> None:
            seen.append(scope["type"])

        middleware = RequestSizeLimitMiddleware(app, max_bytes=SMALL_LIMIT)

        async def receive() -> dict[str, Any]:
            return {"type": "lifespan.startup"}

        async def send(_: MutableMapping[str, Any]) -> None:
            return None

        await middleware({"type": "lifespan"}, receive, send)
        assert seen == ["lifespan"]

    def test_a_non_positive_limit_is_refused(self) -> None:
        with pytest.raises(ValueError):
            RequestSizeLimitMiddleware(_Spy(), max_bytes=0)


# ----------------------------------------------------------------------
# Through the real application
# ----------------------------------------------------------------------


class TestThroughTheApp:
    @pytest.fixture
    def client(self) -> Iterator[TestClient]:
        app = create_app(settings(max_request_bytes=SMALL_LIMIT))
        with TestClient(app) as test_client:
            yield test_client

    def test_an_oversized_analysis_request_never_reaches_the_engines(
        self, client: TestClient
    ) -> None:
        """413 from the ingest boundary, not `CSV_TOO_LARGE` from the analysis.

        The distinction is the whole point: `CSV_TOO_LARGE` means the body was
        read and understood first.
        """
        payload = {
            "symbol": "TEST_FIXTURE_FUT",
            "datasets": [
                {"timeframe": "1H", "content": "x" * (SMALL_LIMIT * 4), "source_name": "a"}
            ],
        }
        response = client.post("/api/analysis", json=payload)

        assert response.status_code == 413
        assert response.json()["detail"]["code"] == "REQUEST_TOO_LARGE"

    def test_a_normal_request_still_works(self, client: TestClient) -> None:
        payload = {
            "symbol": "TEST_FIXTURE_FUT",
            "datasets": [
                {
                    "timeframe": "1H",
                    "content": "open_time,open,high,low,close,volume\n",
                    "source_name": "a",
                }
            ],
        }
        response = client.post("/api/analysis", json=payload)

        assert response.status_code == 200

    def test_a_malformed_body_below_the_limit_stays_typed(self, client: TestClient) -> None:
        """The size limiter must not swallow ordinary validation."""
        response = client.post("/api/analysis", json={"symbol": "X"})

        assert response.status_code == 422
        assert response.status_code != 413

    def test_the_limit_is_configuration_not_a_constant(self) -> None:
        assert settings().max_request_bytes == 48 * 1024 * 1024
        assert settings(max_request_bytes=123).max_request_bytes == 123


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _unused() -> AsyncIterator[None]:  # pragma: no cover - import shape only
    yield None


def test_the_default_ceiling_exceeds_the_sum_of_dataset_limits() -> None:
    """The outer bound must not be tighter than what an analysis may accept.

    Four timeframes at 8 MiB each is 32 MiB of CSV before JSON encoding. A
    ceiling below that would reject legitimate requests at the wrong layer,
    with the wrong error.
    """
    from app.application.analysis.limits import InputLimits

    four_timeframes = InputLimits().max_csv_bytes * 4
    assert Settings.model_fields["max_request_bytes"].default > four_timeframes


def test_the_app_and_the_proxy_agree_on_who_is_authoritative() -> None:
    """nginx must sit *above* the application ceiling, not below it.

    Its 1 MiB default silently undercut the 8 MiB the analysis endpoint
    documents per timeframe, so a legitimate upload died on an HTML error page
    instead of the application's typed JSON. The proxy is the coarse backstop;
    the application is the authority.
    """
    from pathlib import Path

    conf = (Path(__file__).resolve().parents[3] / ".." / "frontend" / "nginx.conf").resolve()
    text = conf.read_text(encoding="utf-8")

    assert "client_max_body_size" in text, "nginx would fall back to its 1 MiB default"
    megabytes = int(text.split("client_max_body_size")[1].split("m;")[0].strip())
    assert megabytes * 1024 * 1024 > Settings.model_fields["max_request_bytes"].default


# ----------------------------------------------------------------------
# Does the fast path actually skip `receive`?
# ----------------------------------------------------------------------


class _CountingReceive:
    """A `receive` callable that records how many times it was awaited.

    `_Spy` above proves the *application* never ran. That is a weaker claim than
    the one the fast path makes: an ASGI layer can reject a request and still
    have pulled body events off the transport first. Only counting the awaits
    distinguishes "nothing reached the app" from "nothing was consumed at all".
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self.pending = list(chunks)
        self.calls = 0

    async def __call__(self) -> dict[str, Any]:
        self.calls += 1
        if not self.pending:
            return {"type": "http.request", "body": b"", "more_body": False}
        body = self.pending.pop(0)
        return {"type": "http.request", "body": body, "more_body": bool(self.pending)}


async def drive_counted(
    middleware: RequestSizeLimitMiddleware,
    *,
    chunks: list[bytes],
    content_length: int | None,
) -> tuple[list[dict[str, Any]], _CountingReceive]:
    headers: list[tuple[bytes, bytes]] = []
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))

    receive = _CountingReceive(chunks)
    sent: list[dict[str, Any]] = []

    async def send(message: MutableMapping[str, Any]) -> None:
        sent.append(dict(message))

    await middleware({"type": "http", "headers": headers, "method": "POST"}, receive, send)
    return sent, receive


class TestTheHeaderDecidesWithoutReading:
    """§1 micro-closeout: prove the Content-Length fast path is a real fast path.

    The final closeout report said a 500 MB body was "refused after 1 MiB
    received", which was the wrong description of a correct behaviour. That
    figure was measured from the *client* side: the probe wrote a megabyte into
    the socket before pausing to look, by which time the 413 was already waiting.
    The server had answered from the header alone.

    These tests measure the server side instead, by counting awaits of the ASGI
    `receive` callable.
    """

    @pytest.mark.asyncio
    async def test_a_declared_oversize_costs_zero_receive_calls(self) -> None:
        """A. The header alone settles it."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent, receive = await drive_counted(
            middleware,
            chunks=[b"x" * 1024] * 64,
            content_length=500_000_000,
        )

        assert status_of(sent) == 413
        assert receive.calls == 0, f"{receive.calls} body events were consumed"
        assert spy.calls == 0, "the application ran"
        assert len(receive.pending) == 64, "chunks were drained from the transport"

    @pytest.mark.asyncio
    async def test_a_legal_declared_length_receives_normally(self) -> None:
        """B. The fast path must not become a general refusal."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent, receive = await drive_counted(
            middleware,
            chunks=[b"x" * 512, b"y" * 512],
            content_length=1024,
        )

        assert status_of(sent) == 200
        assert receive.calls >= 2, "the body was not read"
        assert spy.body_bytes == 1024

    @pytest.mark.asyncio
    async def test_an_undeclared_oversize_is_stopped_by_the_counter(self) -> None:
        """C. No Content-Length: the counter is the only thing that can hold.

        It must also stop *at* the ceiling rather than after the whole stream,
        so the number of chunks consumed is part of the claim.
        """
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)
        chunk = SMALL_LIMIT // 2

        sent, receive = await drive_counted(
            middleware,
            chunks=[b"z" * chunk] * 100,
            content_length=None,
        )

        assert status_of(sent) == 413
        # Two chunks reach the limit, the third crosses it: nothing beyond.
        assert receive.calls == 3, f"consumed {receive.calls} chunks before stopping"
        assert len(receive.pending) == 97

    @pytest.mark.asyncio
    async def test_a_lying_declared_length_is_stopped_by_the_counter(self) -> None:
        """D. A small declared length buys no extra bytes."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)
        chunk = SMALL_LIMIT // 2

        sent, receive = await drive_counted(
            middleware,
            chunks=[b"z" * chunk] * 100,
            content_length=16,
        )

        assert status_of(sent) == 413
        assert receive.calls == 3
        assert spy.saw_disconnect is True

    @pytest.mark.asyncio
    async def test_an_unparsable_declared_length_is_treated_as_absent(self) -> None:
        """E. A header quirk must not decide anything on its own.

        Refusing here would turn a malformed header into a rejection the body
        size did not justify; trusting it would let `Content-Length: banana`
        skip the check. It is ignored, and the counter does the work.
        """
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent: list[dict[str, Any]] = []

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(dict(message))

        receive = _CountingReceive([b"z" * SMALL_LIMIT] * 4)
        await middleware(
            {
                "type": "http",
                "method": "POST",
                "headers": [(b"content-length", b"not-a-number")],
            },
            receive,
            send,
        )

        assert status_of(sent) == 413
        assert receive.calls == 2, "the counter did not run"

    @pytest.mark.asyncio
    async def test_a_small_body_with_an_unparsable_length_still_succeeds(self) -> None:
        """The same quirk must not fail a legitimate request either."""
        spy = _Spy()
        middleware = RequestSizeLimitMiddleware(spy, max_bytes=SMALL_LIMIT)

        sent: list[dict[str, Any]] = []

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(dict(message))

        await middleware(
            {
                "type": "http",
                "method": "POST",
                "headers": [(b"content-length", b"")],
            },
            _CountingReceive([b"ok"]),
            send,
        )

        assert status_of(sent) == 200
        assert spy.body_bytes == 2


class TestTheFastPathThroughTheRealApp:
    """The same claim, through the whole composed application.

    Driven as raw ASGI rather than through `TestClient`, because the question is
    precisely what happens to the `receive` callable - and a test client owns
    that callable itself.
    """

    @staticmethod
    def _scope(content_length: int | None) -> dict[str, Any]:
        headers = [(b"content-type", b"application/json")]
        if content_length is not None:
            headers.append((b"content-length", str(content_length).encode()))
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.1"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/analysis",
            "raw_path": b"/api/analysis",
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("127.0.0.1", 8000),
        }

    @pytest.mark.asyncio
    async def test_an_over_declared_request_consumes_nothing_and_runs_nothing(self) -> None:
        app = create_app(settings())
        receive = _CountingReceive([b"{"] + [b"x" * 4096] * 32)
        sent: list[dict[str, Any]] = []

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(dict(message))

        await app(self._scope(500_000_000), receive, send)

        assert status_of(sent) == 413
        assert receive.calls == 0, f"{receive.calls} body events were consumed"
        assert b"REQUEST_TOO_LARGE" in body_of(sent)
        # The route never ran, so nothing parsed a request model and no engine
        # or provider was constructed for it.
        assert b"analysis_id" not in body_of(sent)

    @staticmethod
    async def _run_lifespan(app: Any, event: str) -> None:
        """Start or stop the app over raw ASGI.

        `create_app` alone does not populate `app.state`; the lifespan does, and
        driving the request without it fails on a missing dependency rather than
        on anything these tests are about.
        """
        messages = [{"type": f"lifespan.{event}"}]

        async def receive() -> dict[str, Any]:
            return messages.pop(0) if messages else {"type": "lifespan.shutdown"}

        async def send(message: MutableMapping[str, Any]) -> None:
            assert not message["type"].endswith(".failed"), message

        await app({"type": "lifespan", "asgi": {"version": "3.0"}}, receive, send)

    @pytest.mark.asyncio
    async def test_a_legal_request_is_still_read_and_answered(self) -> None:
        app = create_app(settings())
        payload = b'{"symbol":"X","datasets":[]}'
        receive = _CountingReceive([payload])
        sent: list[dict[str, Any]] = []

        async def send(message: MutableMapping[str, Any]) -> None:
            sent.append(dict(message))

        await self._run_lifespan(app, "startup")
        try:
            await app(self._scope(len(payload)), receive, send)
        finally:
            await self._run_lifespan(app, "shutdown")

        # 422 because `datasets` is empty - which proves the body was read and
        # validated rather than refused on its size.
        assert status_of(sent) == 422
        assert receive.calls >= 1
