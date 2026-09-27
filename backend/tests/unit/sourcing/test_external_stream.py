"""An external provider's candles go through the Phase 13 machinery (Part 1).

The provider here is a labelled test double declaring a granted delayed feed.
Every candle it sends is a ``VendorCandle`` translated by the vendor-neutral
``to_raw_event`` and then validated by the real ``LiveSession`` and
``CandleBook`` - so these tests show that being external skips nothing:
forming candles stay unconfirmed, gaps are gaps, duplicates are duplicates,
conflicts are quarantined, and receive time is the session's own clock.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.adapters.live.mock_stream import ManualClock
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.live.records import StreamRecord
from app.application.live.session import LiveSession
from app.application.sourcing.candles import VendorCandle, to_raw_event
from app.domain.live.book import ApplyOutcome
from app.domain.live.events import MarketCurrency, ProviderSignal, SignalKind, StreamProvenance
from app.domain.sourcing.capability import Delivery
from tests.unit.live.support import FRESHNESS, M5, SYMBOL, at
from tests.unit.sourcing.support import NOW, GrantedTestProvider, fixture_grant

pytestmark = pytest.mark.unit


def vendor(
    index: int, *, closed: bool = True, close: str = "100.5", sequence: int | None = None
) -> VendorCandle:
    return VendorCandle(
        symbol=SYMBOL,
        timeframe=M5.value,
        open_time=at(5 * index),
        open="100",
        high="101",
        low="99",
        close=close,
        volume="1000",
        closed=closed,
        event_time=at(5 * index + 5) if closed else at(5 * index + 2),
        sequence=index if sequence is None else sequence,
        # The vendor published it much later than the market time - irrelevant
        # to market ordering, and not the receive time either.
        provider_time=at(5 * index) + timedelta(days=3),
    )


async def play(*items: object) -> tuple[LiveSession, list[StreamRecord]]:
    records: list[StreamRecord] = []
    provider = GrantedTestProvider(StreamProvenance.REAL_EXCHANGE_DELAYED)
    session = LiveSession(
        provider=provider,
        symbol=SYMBOL,
        timeframes=(M5,),
        clock=ManualClock(NOW),
        parser=CsvCandleTextParser(),
        freshness=FRESHNESS,
        observer=records.append,
        provider_id=GrantedTestProvider.provider_id,
        grant=fixture_grant(Delivery.DELAYED),
    )
    provider.put(ProviderSignal(SignalKind.CONNECTED))
    for item in items:
        provider.put(to_raw_event(item) if isinstance(item, VendorCandle) else item)  # type: ignore[arg-type]
    provider.put(ProviderSignal(SignalKind.END_OF_STREAM), None)
    await session.run()
    return session, records


def outcomes(records: list[StreamRecord]) -> list[str]:
    return [record.outcome for record in records if record.kind.value == "OBSERVATION"]


class TestTheTranslation:
    def test_provider_time_becomes_publication_time_never_market_time(self) -> None:
        raw = to_raw_event(vendor(0))

        assert raw.event_time == at(5)
        assert raw.published_at == at(0) + timedelta(days=3)
        assert not hasattr(raw, "provider_time")

    async def test_receive_time_is_the_sessions_clock_not_the_providers(self) -> None:
        _, records = await play(vendor(0))

        (record,) = [r for r in records if r.kind.value == "OBSERVATION"]
        assert record.received_at == NOW
        assert record.event_time == at(5)

    async def test_a_float_price_is_refused_not_rounded(self) -> None:
        floaty = VendorCandle(
            symbol=SYMBOL,
            timeframe=M5.value,
            open_time=at(0),
            open=100.1,
            high=101.0,
            low=99.0,
            close=100.5,
            volume="1000",
            closed=True,
            event_time=at(5),
            sequence=0,
        )

        session, records = await play(floaty)

        assert [r.outcome for r in records if r.kind.value == "REJECTED"] == ["NON_DECIMAL"]
        assert session.state().book(M5).confirmed() == ()


class TestNothingIsSkippedForBeingExternal:
    async def test_a_forming_candle_is_never_confirmed(self) -> None:
        session, _ = await play(vendor(0), vendor(1, closed=False))

        book = session.state().book(M5)
        assert [o.candle.open_time for o in book.confirmed()] == [at(0)]

    async def test_a_time_jump_is_a_gap(self) -> None:
        session, _ = await play(vendor(0), vendor(5, sequence=1))

        integrity = session.state().book(M5).integrity().value
        assert integrity != "COMPLETE"

    async def test_an_identical_repeat_is_a_duplicate(self) -> None:
        _, records = await play(vendor(0), vendor(0))

        assert outcomes(records) == [ApplyOutcome.ACCEPTED.value, ApplyOutcome.DUPLICATE.value]

    async def test_a_correction_is_quarantined_not_applied(self) -> None:
        session, records = await play(vendor(0), vendor(0, close="99.5"))

        assert outcomes(records)[-1] == ApplyOutcome.CONFLICT.value
        (held,) = session.state().book(M5).confirmed()
        assert str(held.candle.close) == "100.5"  # the stored candle was not rewritten

    async def test_a_sequence_that_disagrees_with_time_is_not_trusted(self) -> None:
        session, _ = await play(vendor(0), vendor(1, sequence=7))

        assert session.state().book(M5).integrity().value != "COMPLETE"

    async def test_a_disconnect_is_recorded_and_a_delayed_feed_stays_delayed(self) -> None:
        session, records = await play(
            vendor(0),
            ProviderSignal(SignalKind.DISCONNECTED),
            ProviderSignal(SignalKind.CONNECTED),
            vendor(1),
        )

        signals = [r.outcome for r in records if r.kind.value == "SIGNAL"]
        assert "DISCONNECTED" in signals
        assert session.snapshot().market_currency is MarketCurrency.DELAYED
