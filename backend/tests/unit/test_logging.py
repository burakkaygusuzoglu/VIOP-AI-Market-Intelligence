"""Structured logging and secret redaction."""

from __future__ import annotations

import json
import logging
from io import StringIO

import pytest

from app.core.context import clear_request_id, set_request_id
from app.core.logging import (
    REDACTED,
    ConsoleLogFormatter,
    JsonLogFormatter,
    configure_logging,
    get_logger,
    redact,
)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="app.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@pytest.fixture(autouse=True)
def _reset_request_id() -> None:
    clear_request_id()


@pytest.mark.unit
def test_log_line_is_valid_json_with_core_fields() -> None:
    payload = json.loads(JsonLogFormatter().format(_record("hello")))
    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert "timestamp" in payload


@pytest.mark.unit
def test_structured_extras_are_included() -> None:
    payload = json.loads(JsonLogFormatter().format(_record("request completed", status_code=200)))
    assert payload["status_code"] == 200


@pytest.mark.unit
def test_request_id_is_attached_when_bound() -> None:
    set_request_id("abc123")
    payload = json.loads(JsonLogFormatter().format(_record("in request")))
    assert payload["request_id"] == "abc123"


@pytest.mark.unit
def test_api_key_never_reaches_json_log_output() -> None:
    """Master spec section 98: keys, credentials and tokens are never logged."""
    secret = "sk-ant-fixture-000111"
    formatter = JsonLogFormatter(secrets=[secret])
    line = formatter.format(_record(f"calling provider with {secret}", token=secret))
    assert secret not in line
    assert line.count(REDACTED) == 2


@pytest.mark.unit
def test_api_key_never_reaches_console_log_output() -> None:
    secret = "sk-ant-fixture-000111"
    line = ConsoleLogFormatter(secrets=[secret]).format(_record(f"key={secret}"))
    assert secret not in line
    assert REDACTED in line


@pytest.mark.unit
def test_redact_is_a_no_op_without_secrets() -> None:
    assert redact("nothing to hide", []) == "nothing to hide"


@pytest.mark.unit
def test_exception_details_are_captured() -> None:
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("app.test.exception")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    try:
        raise ValueError("boom")
    except ValueError:
        logger.exception("failed")
    payload = json.loads(stream.getvalue())
    assert "ValueError: boom" in payload["exception"]


@pytest.mark.unit
def test_call_site_extras_survive_the_logger_adapter() -> None:
    """Per-call structured fields must not be discarded by fixed context."""
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    base = logging.getLogger("app.test.adapter")
    base.handlers = [handler]
    base.propagate = False
    base.setLevel(logging.INFO)

    logger = get_logger("app.test.adapter", component="access")
    logger.info("request completed", extra={"status_code": 503, "path": "/api/health"})

    payload = json.loads(stream.getvalue())
    assert payload["component"] == "access"
    assert payload["status_code"] == 503
    assert payload["path"] == "/api/health"


@pytest.mark.unit
def test_the_imaging_library_is_never_allowed_to_narrate_an_image() -> None:
    """Phase 6: image content must not reach a log, at any level.

    Pillow logs while parsing - chunk names, offsets, lengths, and for some
    formats the metadata values themselves. Turning the root logger up to
    DEBUG to chase an unrelated bug must not silently start writing a user's
    chart into the log, so the floor is part of the logging configuration
    rather than a property of the level that happens to be set.
    """
    root = logging.getLogger()
    previous = root.level
    try:
        configure_logging(level="DEBUG", log_format="json")
        root.setLevel(logging.DEBUG)
        for name in ("PIL", "PIL.Image", "PIL.PngImagePlugin", "PIL.TiffImagePlugin"):
            logger = logging.getLogger(name)
            assert logger.getEffectiveLevel() >= logging.WARNING, (
                f"{name} would emit image internals at DEBUG"
            )
    finally:
        root.setLevel(previous)


@pytest.mark.unit
def test_no_image_chunk_chatter_survives_a_real_decode_at_debug_level() -> None:
    """The floor above, proven against an actual decode rather than asserted."""
    import io

    from PIL import Image
    from PIL.PngImagePlugin import PngInfo

    from app.application.vision.decode import verify_and_normalise

    marker = "MARKER-IMAGE-CONTENT-9f3a"
    metadata = PngInfo()
    metadata.add_text("Comment", marker)
    buffer = io.BytesIO()
    Image.new("RGB", (120, 90), "white").save(buffer, format="PNG", pnginfo=metadata)

    sink = io.StringIO()
    handler = logging.StreamHandler(sink)
    root = logging.getLogger()
    previous = root.level
    configure_logging(level="DEBUG", log_format="json")
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        verify_and_normalise(buffer.getvalue())
    finally:
        root.removeHandler(handler)
        root.setLevel(previous)

    written = sink.getvalue()
    assert marker not in written, "image metadata reached the log"
    assert "STREAM" not in written, "Pillow chunk chatter reached the log"
