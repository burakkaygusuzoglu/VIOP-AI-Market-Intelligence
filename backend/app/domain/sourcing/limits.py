"""Bounds and retry rules for external providers (Phase 15 Part 1).

The Phase 13 streaming bounds (``LiveLimits``) still apply to every stream: a
real provider goes through the same book, the same queue and the same limits
as the simulation. These bounds are the provider-facing ones - how many
connections and subscriptions, how much backfill, how often to retry, how long
metadata may be cached - so no adapter can poll without bound or hammer a
provider that has refused it.

## Retrying is not a way to ignore a refusal

Failures are classified. A transient failure (a dropped connection, a
timeout, a provider error) is retried with a capped exponential delay and a
bounded number of attempts. A failure that retrying cannot fix - rejected
credentials, a missing or lapsed licence, a request the provider says is not
permitted - is terminal immediately: retrying it would be a busy loop against
an account, not recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

__all__ = ["FailureClass", "ProviderLimits", "RetryPolicy"]


@unique
class FailureClass(StrEnum):
    TRANSIENT = "TRANSIENT"
    """Connection lost, timeout, temporary provider error. May be retried."""

    AUTHENTICATION = "AUTHENTICATION"
    """Credentials rejected. Terminal: a retry sends the same credentials."""

    NOT_LICENSED = "NOT_LICENSED"
    """The provider says this account may not have this data. Terminal."""

    RATE_LIMITED = "RATE_LIMITED"
    """The provider asked for less traffic. Retried, but never sooner than the
    policy's longest delay."""

    INVALID_REQUEST = "INVALID_REQUEST"
    """The request itself is wrong. Terminal: it would fail the same way."""


_TERMINAL = frozenset(
    {FailureClass.AUTHENTICATION, FailureClass.NOT_LICENSED, FailureClass.INVALID_REQUEST}
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if not 0 < self.base_delay_seconds <= self.max_delay_seconds:
            raise ValueError("delays must be positive and base must not exceed max")

    def next_delay(self, attempt: int, failure: FailureClass) -> float | None:
        """Seconds to wait before attempt ``attempt + 1``, or ``None`` to stop.

        ``attempt`` counts attempts already made, from 1. Deterministic: no
        jitter here, so a test and a log can say exactly what will happen; an
        adapter may add bounded jitter below the cap.
        """
        if failure in _TERMINAL or attempt >= self.max_attempts:
            return None
        if failure is FailureClass.RATE_LIMITED:
            return self.max_delay_seconds
        delay: float = self.base_delay_seconds * float(2 ** (attempt - 1))
        return min(delay, self.max_delay_seconds)


@dataclass(frozen=True, slots=True)
class ProviderLimits:
    """Provider-facing bounds. Every one is finite; none is absorbed silently."""

    max_connections: int = 1
    max_subscriptions: int = 16
    max_queued_events: int = 2_048
    """Events buffered from a provider before the stream is declared broken
    (Phase 13's overflow signal), never dropped quietly."""

    max_backfill_candles: int = 2_000
    max_historical_days: int = 366
    """The widest historical range one request may ask for."""

    metadata_cache_entries: int = 1_024
    metadata_cache_seconds: int = 3_600
    """How long a verified record may be served from cache before it is asked
    for again. Caching never extends a record's own verification age."""

    shutdown_seconds: float = 10.0
    retry: RetryPolicy = RetryPolicy()

    # Part 2A: the external-intelligence categories. A source that returns
    # more than its bound is refused, not truncated - a truncated list of
    # articles or constituents is silently a different list.
    max_fact_records: int = 64
    """Contract source records one source may return for one contract."""
    max_calendar_days: int = 400
    max_open_interest_points: int = 5_000
    max_news_articles: int = 500
    max_universe_constituents: int = 1_000

    def __post_init__(self) -> None:
        for name in (
            "max_connections",
            "max_subscriptions",
            "max_queued_events",
            "max_backfill_candles",
            "max_historical_days",
            "metadata_cache_entries",
            "metadata_cache_seconds",
            "max_fact_records",
            "max_calendar_days",
            "max_open_interest_points",
            "max_news_articles",
            "max_universe_constituents",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        if self.shutdown_seconds <= 0:
            raise ValueError("shutdown_seconds must be positive")
