"""Nothing that had not finished may be seen (Phase 11).

This is the invariant the whole phase exists for:

    at replay market time T, no consumer may see market information whose
    coverage ends after T.

Each test below picks a different consumer - the store, the chart window, the
analysis prefix, the HTTP payload - and asks the same question of it. The
adversarial ones put a candle in the dataset that no market would print, so a
leak cannot pass for a plausible number.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.application.analysis.orchestrator import AnalysisOutcome
from app.application.replay.service import ReplayServiceError, SessionView
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from tests.factories_replay import (
    BASE,
    aggregate,
    csv_of,
    dataset,
    five_minute,
    with_absurd_future_candle,
)
from tests.integration.replay_support import create_command, replay_service

pytestmark = pytest.mark.integration

ABSURD = Decimal("999999999")
START = BASE + timedelta(hours=1)
"""Twelve 5M candles of warm-up, one finished 1H candle."""


def absurd_dataset(index: int = 200) -> dict[Timeframe, str]:
    """An aligned dataset whose ``index``-th 5M candle is unmistakable."""
    rows = with_absurd_future_candle(five_minute(288), index)
    return {
        Timeframe.M5: csv_of(rows),
        Timeframe.M15: csv_of(aggregate(rows, Timeframe.M15)),
        Timeframe.H1: csv_of(aggregate(rows, Timeframe.H1)),
    }


class TestTheStoreNeverMaterialisesAnUnrevealedCandle:
    async def test_a_bounded_read_stops_at_coverage_end(self, database: Database) -> None:
        """Not "fetched then filtered" - the bound is in the SQL."""
        service = replay_service(database)
        session, _ = await service.create(create_command("leak-store-0000001"))
        store = service._store  # noqa: SLF001 - asserting on the seam itself
        candles = await store.candles(
            session.dataset.dataset_id, Timeframe.M5, until=session.cursor.as_of
        )
        assert len(candles) == 12
        assert max(candle.open_time for candle in candles) + timedelta(minutes=5) <= START

    async def test_an_absurd_future_candle_is_not_read(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("leak-store-0000002", content=absurd_dataset())
        )
        store = service._store  # noqa: SLF001
        candles = await store.candles(
            session.dataset.dataset_id, Timeframe.M5, until=session.cursor.as_of
        )
        assert all(candle.high < ABSURD for candle in candles)

    async def test_it_is_in_the_dataset_all_along(self, database: Database) -> None:
        """The control: an unbounded read finds it, so absence above is the rule
        working rather than the fixture failing to store it."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("leak-store-0000003", content=absurd_dataset())
        )
        store = service._store  # noqa: SLF001
        every = await store.candles(session.dataset.dataset_id, Timeframe.M5)
        assert any(candle.high == ABSURD for candle in every)


class TestTheChartWindow:
    async def test_the_window_holds_only_finished_candles(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("leak-chart-0000001"))
        view = await service.get(session.session_id, chart=Timeframe.M5)
        window = next(item for item in view.availability if item.timeframe is Timeframe.M5)
        assert window.revealed_total == 12
        assert window.dataset_total == 288
        assert all(
            candle.open_time + timedelta(minutes=5) <= session.cursor.as_of
            for candle in window.candles
        )

    async def test_a_bounded_window_says_it_is_bounded(self, database: Database) -> None:
        """Displayed N of revealed M, never a quiet truncation."""
        service = replay_service(database)
        long_history = {Timeframe.M5: csv_of(five_minute(1000))}
        session, _ = await service.create(
            create_command(
                "leak-chart-0000002",
                content=long_history,
                replay_start=BASE + timedelta(minutes=5 * 900),
            )
        )
        view = await service.get(session.session_id, chart=Timeframe.M5)
        window = next(item for item in view.availability if item.timeframe is Timeframe.M5)
        assert window.revealed_total == 900
        assert len(window.candles) == service.limits.max_chart_candles
        assert window.truncated is True
        newest = max(candle.open_time for candle in window.candles)
        assert newest + timedelta(minutes=5) == view.session.cursor.as_of

    async def test_an_unrequested_timeframe_is_not_called_truncated(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("leak-chart-0000003"))
        view = await service.get(session.session_id, chart=Timeframe.M5)
        hourly = next(item for item in view.availability if item.timeframe is Timeframe.H1)
        assert hourly.candles == ()
        assert hourly.truncated is False
        assert hourly.revealed_total == 1


class TestMultipleTimeframesAreRevealedIndependently:
    async def test_a_forming_hour_is_invisible_while_its_minutes_are_not(
        self, database: Database
    ) -> None:
        """The leak this phase is most likely to have: at 10:05 the 1H bar that
        opened at 10:00 has not happened, however many 5M bars have."""
        service = replay_service(database)
        session, _ = await service.create(create_command("leak-mtf-000000001"))
        stepped = await service.step(session.session_id, command_key=None, expected_version=None)
        as_of = stepped.view.session.cursor.as_of
        assert as_of == START + timedelta(minutes=5)

        revealed = {item.timeframe: item.revealed_total for item in stepped.view.availability}
        assert revealed[Timeframe.M5] == 13
        assert revealed[Timeframe.M15] == 4
        assert revealed[Timeframe.H1] == 1

    async def test_the_hour_appears_exactly_when_it_closes(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("leak-mtf-000000002"))
        before = await service.step(
            session.session_id, steps=11, command_key=None, expected_version=None
        )
        assert before.view.session.cursor.as_of == START + timedelta(minutes=55)
        assert _revealed(before.view, Timeframe.H1) == 1

        at_close = await service.step(session.session_id, command_key=None, expected_version=None)
        assert at_close.view.session.cursor.as_of == START + timedelta(hours=1)
        assert _revealed(at_close.view, Timeframe.H1) == 2

    async def test_a_quarter_hour_appears_exactly_when_it_closes(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("leak-mtf-000000003"))
        before = await service.step(
            session.session_id, steps=2, command_key=None, expected_version=None
        )
        assert _revealed(before.view, Timeframe.M15) == 4
        at_close = await service.step(session.session_id, command_key=None, expected_version=None)
        assert at_close.view.session.cursor.as_of == START + timedelta(minutes=15)
        assert _revealed(at_close.view, Timeframe.M15) == 5


class TestWarmUpHistoryIsLegitimate:
    async def test_everything_that_had_finished_by_the_start_is_available(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(create_command("leak-warmup-000001"))
        view = await service.get(session.session_id, chart=Timeframe.M5)
        assert _revealed(view, Timeframe.M5) == 12
        assert _revealed(view, Timeframe.M15) == 4
        assert _revealed(view, Timeframe.H1) == 1

    async def test_warm_up_reaches_the_analysis(self, database: Database) -> None:
        """History before the start is past information, not lookahead."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("leak-warmup-000002", replay_start=BASE + timedelta(hours=6))
        )
        _session, outcome = await service.analyse(session.session_id)
        usable = [item for item in outcome.timeframes if item.usable]
        assert usable, "six hours of warm-up should be enough to analyse"


class TestTheFutureCannotChangeThePresent:
    async def test_an_absurd_unrevealed_candle_changes_no_current_number(
        self, database: Database
    ) -> None:
        """Two datasets, identical up to the replay moment, different after it.

        Every current answer must be byte-identical: the analysis, the revealed
        counts and the replay clock. If an unrevealed candle could move any of
        them, the replay would be reading the future.
        """
        service = replay_service(database)
        plain, _ = await service.create(create_command("leak-future-000001"))
        absurd, _ = await service.create(
            create_command("leak-future-000002", content=absurd_dataset(200))
        )
        elsewhere, _ = await service.create(
            create_command("leak-future-000003", content=absurd_dataset(250))
        )
        assert plain.dataset.dataset_id != absurd.dataset.dataset_id

        results = []
        for session in (plain, absurd, elsewhere):
            _s, outcome = await service.analyse(session.session_id)
            results.append(_fingerprint(outcome))
        assert results[0] == results[1] == results[2]

    async def test_the_same_holds_after_stepping_short_of_it(self, database: Database) -> None:
        service = replay_service(database)
        plain, _ = await service.create(create_command("leak-future-000004"))
        absurd, _ = await service.create(
            create_command("leak-future-000005", content=absurd_dataset(200))
        )
        for session in (plain, absurd):
            await service.step(
                session.session_id, steps=20, command_key=None, expected_version=None
            )
        _a, first = await service.analyse(plain.session_id)
        _b, second = await service.analyse(absurd.session_id)
        assert _fingerprint(first) == _fingerprint(second)

    async def test_it_does_appear_once_the_replay_reaches_it(self, database: Database) -> None:
        """The other half of the proof: the candle is real and does arrive."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("leak-future-000006", content=absurd_dataset(13))
        )
        before = await service.get(session.session_id, chart=Timeframe.M5)
        assert all(candle.high < ABSURD for candle in _candles(before, Timeframe.M5))

        after = await service.step(
            session.session_id, steps=2, command_key=None, expected_version=None
        )
        assert any(candle.high == ABSURD for candle in _candles(after.view, Timeframe.M5))


class TestTheAnalysisPrefixStopsAtTheReplayMoment:
    async def test_no_analysed_candle_ends_after_the_replay_clock(self, database: Database) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("leak-prefix-000001", replay_start=BASE + timedelta(hours=8))
        )
        stored, outcome = await service.analyse(session.session_id)
        as_of = stored.cursor.as_of
        for item in outcome.timeframes:
            if item.series is None:
                continue
            last = item.series.candles[-1]
            assert last.open_time + timedelta(minutes=last.timeframe.minutes) <= as_of

    async def test_a_timeframe_with_nothing_finished_is_simply_absent(
        self, database: Database
    ) -> None:
        """Absent, not empty and not built from a finer timeframe."""
        service = replay_service(database)
        content = dataset(288, timeframes=(Timeframe.M5, Timeframe.H1))
        session, _ = await service.create(
            create_command(
                "leak-prefix-000002",
                content=content,
                replay_start=BASE + timedelta(minutes=30),
            )
        )
        _stored, outcome = await service.analyse(session.session_id)
        analysed = {item.timeframe for item in outcome.timeframes}
        assert Timeframe.M5 in analysed
        assert Timeframe.H1 not in analysed

    async def test_a_session_always_begins_with_something_revealed(
        self, database: Database
    ) -> None:
        """The earliest legal start is the first driver candle's close.

        This is why an analysis inside a replay is never asked to work from
        nothing: a start before that is refused when the session is created,
        not discovered when someone presses Analyse.
        """
        service = replay_service(database)
        content = {Timeframe.H1: dataset(288, timeframes=(Timeframe.H1,))[Timeframe.H1]}
        session, _ = await service.create(
            create_command(
                "leak-prefix-000003",
                content=content,
                driver=Timeframe.H1,
                replay_start=BASE + timedelta(hours=1),
            )
        )
        assert session.cursor.revealed_driver_candles == 1

        with pytest.raises(ReplayServiceError) as error:
            await service.create(
                create_command(
                    "leak-prefix-000004",
                    content=content,
                    driver=Timeframe.H1,
                    replay_start=BASE + timedelta(minutes=59),
                )
            )
        assert error.value.code == "REPLAY_START_BEFORE_DATA"


# ----------------------------------------------------------------------


def _revealed(view: SessionView, timeframe: Timeframe) -> int:
    return next(item.revealed_total for item in view.availability if item.timeframe is timeframe)


def _candles(view: SessionView, timeframe: Timeframe) -> tuple[Candle, ...]:
    return next(item.candles for item in view.availability if item.timeframe is timeframe)


def _fingerprint(outcome: AnalysisOutcome) -> str:
    """Everything an analysis says, as one comparable string."""
    parts: list[str] = []
    for item in outcome.timeframes:
        parts.append(f"{item.timeframe.value}:{item.usable}")
        if item.series is not None:
            parts.append(str(len(item.series.candles)))
            parts.append(format(item.series.candles[-1].close, "f"))
        if item.technicals is not None:
            parts.append(repr(item.technicals))
        if item.structure is not None:
            parts.append(repr(item.structure))
    analysis = outcome.analysis
    if analysis is not None:
        parts.append(repr(analysis.evidence))
        parts.append(repr(analysis.scenarios))
    return "|".join(parts)
