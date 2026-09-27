"""A provider's publication time is kept, and is never market time (Part 2A, H)."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from app.application.live.records import RecordKind
from app.application.sourcing.candles import to_raw_event
from app.domain.live.limits import LiveLimits
from app.domain.live.validation import Rejection, RejectionCode, validate
from tests.unit.live.support import M5, SYMBOL, at
from tests.unit.sourcing.support import NOW
from tests.unit.sourcing.test_external_stream import play, vendor

pytestmark = pytest.mark.unit

LIMITS = LiveLimits()


def check(published_at: object, *, received_at: datetime = NOW) -> object:
    from app.domain.live.events import StreamKey

    raw = replace(to_raw_event(vendor(0)), published_at=published_at)
    return validate(raw, key=StreamKey(SYMBOL, M5), received_at=received_at, limits=LIMITS)


class TestHPublicationTimePreserved:
    async def test_it_reaches_the_stream_record_beside_market_and_receive_time(self) -> None:
        _, records = await play(vendor(0))

        (record,) = [r for r in records if r.kind is RecordKind.OBSERVATION]
        assert record.published_at == at(0) + timedelta(days=3)
        assert record.event_time == at(5)  # market time unchanged
        assert record.received_at == NOW  # receive time is still the session's

    async def test_it_never_changes_what_is_confirmed_or_how_it_is_ordered(self) -> None:
        with_time, _ = await play(vendor(0), vendor(1))
        without, _ = await play(
            replace(vendor(0), provider_time=None), replace(vendor(1), provider_time=None)
        )

        a = [o.candle for o in with_time.state().book(M5).confirmed()]
        b = [o.candle for o in without.state().book(M5).confirmed()]
        assert a == b

    async def test_a_candle_differing_only_in_publication_time_is_a_duplicate(self) -> None:
        later = replace(vendor(0), provider_time=at(0) + timedelta(days=4))
        _, records = await play(vendor(0), later)

        outcomes = [r.outcome for r in records if r.kind is RecordKind.OBSERVATION]
        assert outcomes == ["ACCEPTED", "DUPLICATE"]

    def test_absent_is_absent(self) -> None:
        observation = check(None)

        assert not isinstance(observation, Rejection)
        assert observation.published_at is None  # type: ignore[attr-defined]

    @pytest.mark.parametrize(
        ("published_at", "code"),
        [
            ("2026-03-02T09:05:00Z", RejectionCode.MALFORMED),
            (datetime(2026, 3, 2, 9, 30), RejectionCode.NAIVE_TIMESTAMP),  # noqa: DTZ001
            (at(5) - timedelta(minutes=1), RejectionCode.MALFORMED),  # before the event
            (NOW + timedelta(minutes=1), RejectionCode.FUTURE_EVENT),  # after receipt
        ],
    )
    def test_an_impossible_publication_time_refuses_the_event(
        self, published_at: object, code: RejectionCode
    ) -> None:
        outcome = check(published_at)

        assert isinstance(outcome, Rejection)
        assert outcome.code is code
