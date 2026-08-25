"""Structured logging (master spec sections 97 and 98).

Logs are emitted as one JSON object per line and are scrubbed of every known
secret before they leave the process. Redaction is applied at the formatter,
which is the last point every log record passes through, rather than trusted
to individual call sites.

Structured logging is implemented on the standard library on purpose: it is a
handful of lines and adds no dependency (master spec section 106).
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable, MutableMapping
from datetime import UTC, datetime
from typing import Any

from app.core.context import get_request_id

REDACTED = "***REDACTED***"

_RESERVED_RECORD_FIELDS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }
)


def redact(text: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of a known secret with a placeholder."""
    for secret in secrets:
        if secret and secret in text:
            text = text.replace(secret, REDACTED)
    return text


class JsonLogFormatter(logging.Formatter):
    """Formats records as a single redacted JSON line."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = get_request_id()
        if request_id is not None:
            payload["request_id"] = request_id
        for key, value in record.__dict__.items():
            if key not in _RESERVED_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        rendered = json.dumps(payload, default=str, ensure_ascii=False)
        return redact(rendered, self._secrets)


class ConsoleLogFormatter(logging.Formatter):
    """Human-readable formatter for local development, redacted identically."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__("%(asctime)s %(levelname)-8s %(name)s | %(message)s")
        self._secrets = tuple(secrets)

    def format(self, record: logging.LogRecord) -> str:
        request_id = get_request_id()
        rendered = super().format(record)
        if request_id is not None:
            rendered = f"{rendered} [request_id={request_id}]"
        return redact(rendered, self._secrets)


def configure_logging(
    level: str = "INFO",
    log_format: str = "json",
    secrets: Iterable[str] = (),
) -> None:
    """Install the root logging configuration for the process."""
    formatter: logging.Formatter = (
        JsonLogFormatter(secrets) if log_format == "json" else ConsoleLogFormatter(secrets)
    )
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(level.upper())

    # uvicorn installs its own handlers; route them through ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True

    # Phase 6: the imaging library must never narrate an uploaded image.
    #
    # Pillow logs at DEBUG while parsing - chunk names, offsets, lengths, and
    # for some formats the metadata values themselves. None of that belongs in
    # a log that may hold a user's chart, and the Phase 6B review is explicit
    # that image content does not get logged. Raising the root level to DEBUG
    # to chase an unrelated bug must not quietly turn that guarantee off, so
    # the floor is set here rather than left to whatever level is configured.
    for name in ("PIL", "PIL.Image", "PIL.PngImagePlugin", "PIL.TiffImagePlugin"):
        logging.getLogger(name).setLevel(logging.WARNING)


class ContextLoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """Merges fixed context with per-call ``extra`` fields.

    The stock ``LoggerAdapter`` replaces ``extra`` outright, which silently
    discards the fields passed at the call site. Structured logs are only
    useful if both survive.
    """

    def process(
        self, msg: Any, kwargs: MutableMapping[str, Any]
    ) -> tuple[Any, MutableMapping[str, Any]]:
        merged: dict[str, Any] = dict(self.extra or {})
        merged.update(kwargs.get("extra") or {})
        kwargs["extra"] = merged
        return msg, kwargs


def get_logger(name: str, **context: Any) -> ContextLoggerAdapter:
    """Return a logger that attaches fixed structured context to every record.

    Context and per-call ``extra`` fields are both flattened into the emitted
    JSON object by the formatters above.
    """
    return ContextLoggerAdapter(logging.getLogger(name), context)
