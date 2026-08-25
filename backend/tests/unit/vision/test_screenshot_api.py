"""The screenshot endpoint (§16, §17).

Every test uses a fake analyser injected through the dependency seam, so the
API surface is exercised without an API key, a network call or a paid request.

Every image is TEST_FIXTURE data.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.adapters.vision.claude_analyzer import AnalyzerConfig, ClaudeScreenshotAnalyzer
from app.api.routes.screenshots import get_analyzer
from app.application.ports.screenshot import ScreenshotAnalyzer
from app.application.vision.errors import VisionFailure, VisionProviderError
from app.main import create_app
from tests.factories_vision import gif_bytes
from tests.unit.vision.test_adapter_and_workflow import FakeTransport, response_payload

MODEL = "fixture-vision-model"


def chart_png(width: int = 640, height: int = 480) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def chart_jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(buffer, format="JPEG")
    return buffer.getvalue()


def chart_webp() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), "white").save(buffer, format="WEBP")
    return buffer.getvalue()


def fake_analyzer(
    text: str | None = None, error: VisionProviderError | None = None
) -> ScreenshotAnalyzer:
    transport = FakeTransport(
        text=text if text is not None else json.dumps(response_payload()), error=error
    )
    return ClaudeScreenshotAnalyzer(transport, AnalyzerConfig(model=MODEL))


def client_with(analyzer: ScreenshotAnalyzer) -> Iterator[TestClient]:
    application = create_app()
    application.dependency_overrides[get_analyzer] = lambda: analyzer
    with TestClient(application) as client:
        yield client
    application.dependency_overrides.clear()


@pytest.fixture
def client() -> Iterator[TestClient]:
    yield from client_with(fake_analyzer())


def upload(client: TestClient, data: bytes, name: str = "chart.png", mime: str = "image/png"):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/screenshots/analyse",
        files={"file": (name, data, mime)},
        data={"slot": "1H", "expected_symbol": "FIXTURE_SYM"},
    )


# ----------------------------------------------------------------------
# Valid uploads
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("builder", "name", "mime"),
    (
        (chart_png, "chart.png", "image/png"),
        (chart_jpeg, "chart.jpg", "image/jpeg"),
        (chart_webp, "chart.webp", "image/webp"),
    ),
)
def test_each_accepted_format_analyses(
    client: TestClient, builder: object, name: str, mime: str
) -> None:
    response = upload(client, builder(), name, mime)  # type: ignore[operator]
    assert response.status_code == 200
    body = response.json()
    assert body["slot"] == "1H"
    assert body["screenshot_id"]
    assert body["observations"]


@pytest.mark.unit
def test_the_response_carries_quality_and_provenance(client: TestClient) -> None:
    body = upload(client, chart_png()).json()
    assert body["quality"]["method_version"].startswith("screenshot-quality/")
    assert body["prompt_version"].startswith("vision-extraction/")
    assert body["model"] == MODEL


@pytest.mark.unit
def test_a_confidence_is_returned_as_text_and_may_be_null(client: TestClient) -> None:
    body = upload(client, chart_png()).json()
    confidences = {item["confidence"] for item in body["observations"]}
    assert all(value is None or isinstance(value, str) for value in confidences)


@pytest.mark.unit
def test_the_response_offers_no_trade_action(client: TestClient) -> None:
    """§20: Phase 7 owns LONG / SHORT / WAIT, so the API has no field for one."""
    body = upload(client, chart_png()).json()
    forbidden = {"action", "decision", "recommendation", "direction", "signal", "verdict"}
    assert forbidden.isdisjoint(body)
    assert "LONG" not in json.dumps(body)
    assert "SHORT" not in json.dumps(body)


# ----------------------------------------------------------------------
# Upload rules are enforced at the edge
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_an_unsupported_type_is_refused(client: TestClient) -> None:
    response = upload(client, gif_bytes(), "chart.gif", "image/gif")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] in {"UNREADABLE_IMAGE", "TOO_SMALL"}


@pytest.mark.unit
def test_a_misleading_mime_type_does_not_get_the_file_accepted(client: TestClient) -> None:
    """Claiming to be a PNG does not make a script one."""
    response = upload(client, b"#!/bin/sh\necho hi\n" * 8, "chart.png", "image/png")
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "UNREADABLE_IMAGE"


@pytest.mark.unit
def test_an_oversize_upload_is_refused(client: TestClient) -> None:
    response = upload(client, chart_png(9000, 9000))
    assert response.status_code == 422
    assert response.json()["detail"]["code"] in {
        "TOO_LARGE",
        "EXCESSIVE_PIXELS",
        "EXCESSIVE_DIMENSIONS",
    }


@pytest.mark.unit
def test_a_malformed_image_is_refused(client: TestClient) -> None:
    good = chart_png()
    response = upload(client, good[: len(good) // 2])
    assert response.status_code == 422
    assert response.json()["detail"]["code"] in {"CORRUPT", "TRUNCATED", "UNREADABLE_IMAGE"}


@pytest.mark.unit
def test_a_path_traversal_filename_never_reaches_the_response(client: TestClient) -> None:
    response = upload(client, chart_png(), "../../etc/passwd.png")
    assert response.status_code == 200
    assert ".." not in json.dumps(response.json())
    assert "etc" not in response.json()["screenshot_id"]


@pytest.mark.unit
def test_an_invalid_slot_is_rejected_by_validation(client: TestClient) -> None:
    response = client.post(
        "/api/screenshots/analyse",
        files={"file": ("chart.png", chart_png(), "image/png")},
        data={"slot": "4H"},
    )
    assert response.status_code == 422


# ----------------------------------------------------------------------
# Provider failures become typed public errors (§17)
# ----------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    (
        (VisionFailure.TIMEOUT, 504),
        (VisionFailure.RATE_LIMITED, 429),
        (VisionFailure.AUTHENTICATION, 503),
        (VisionFailure.CONFIGURATION, 503),
        (VisionFailure.NETWORK, 502),
        (VisionFailure.PROVIDER_REJECTED, 502),
        (VisionFailure.UNAVAILABLE, 502),
    ),
)
def test_a_provider_failure_maps_to_a_typed_status(
    failure: VisionFailure, expected_status: int
) -> None:
    error = VisionProviderError(failure, "internal diagnostic text")
    for test_client in client_with(fake_analyzer(error=error)):
        response = upload(test_client, chart_png())
        assert response.status_code == expected_status
        assert response.json()["detail"]["code"] == failure.value


@pytest.mark.unit
def test_an_error_response_leaks_no_internal_detail() -> None:
    """No stack trace, no provider text, no credential."""
    error = VisionProviderError(
        VisionFailure.AUTHENTICATION,
        "x-api-key sk-ant-SECRET rejected by api.anthropic.com at line 42",
    )
    for test_client in client_with(fake_analyzer(error=error)):
        body = json.dumps(upload(test_client, chart_png()).json())
        assert "sk-ant" not in body
        assert "Traceback" not in body
        assert "anthropic.com" not in body
        assert "line 42" not in body


@pytest.mark.unit
def test_invalid_model_output_becomes_a_typed_error() -> None:
    for test_client in client_with(fake_analyzer(text="the chart looks bullish to me")):
        response = upload(test_client, chart_png())
        assert response.status_code == 502
        assert response.json()["detail"]["code"] == "INVALID_OUTPUT"


@pytest.mark.unit
def test_an_unconfigured_analyzer_returns_a_typed_unavailable() -> None:
    """The default dependency refuses rather than building a client."""
    application = create_app()
    with TestClient(application) as unconfigured:
        response = upload(unconfigured, chart_png())
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "VISION_NOT_CONFIGURED"


# ----------------------------------------------------------------------
# The provider is never reached after a failed upload check
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_the_provider_is_never_invoked_when_validation_fails() -> None:
    """Review E: decode failure must stop the request before the provider."""
    transport = FakeTransport(text=json.dumps(response_payload()))
    engine = ClaudeScreenshotAnalyzer(transport, AnalyzerConfig(model=MODEL))
    for test_client in client_with(engine):
        good = chart_png()
        assert upload(test_client, good[: len(good) // 2]).status_code == 422
        assert upload(test_client, gif_bytes(), "x.gif", "image/gif").status_code == 422
    assert transport.sent == [], "the provider was called despite a rejected upload"


@pytest.mark.unit
def test_the_provider_is_invoked_exactly_once_for_a_valid_upload() -> None:
    transport = FakeTransport(text=json.dumps(response_payload()))
    engine = ClaudeScreenshotAnalyzer(transport, AnalyzerConfig(model=MODEL))
    for test_client in client_with(engine):
        assert upload(test_client, chart_png()).status_code == 200
    assert len(transport.sent) == 1
