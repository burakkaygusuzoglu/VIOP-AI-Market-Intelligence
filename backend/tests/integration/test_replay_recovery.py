"""What a half-finished step leaves behind, and what a retry does with it.

A replay step is not one transaction. It writes to the Phase 9 ledger - one
position at a time, each in its own unit of work - and then writes the cursor.
Claiming atomicity here would be a lie, so the design is stated instead and
these tests are the proof of it:

* **deliver, then commit.** A cursor that moved before delivery would leave a
  bar undeliverable for ever. A cursor that lags a delivered bar is corrected by
  the retry, because an identical bar is a no-op in the Phase 9 engine.
* **one boundary, one commit.** An advance of ten is ten committed steps, so a
  failure leaves the session at the last *completed* step - never somewhere in
  the middle of one.
* **the command key marks the whole command done**, so only the final boundary
  writes it, and a retry after a partial advance resumes rather than reporting
  success.

The failure is injected at every boundary that exists: before the first
position, after the first, in the middle of several, and after the last
position but before the cursor is written.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from app.adapters.persistence.database import Database
from app.application.paper.service import (
    PaperErrorKind,
    PaperServiceError,
    PaperTradingService,
    PositionView,
)
from app.application.replay.service import (
    ReplayService,
    ReplayServiceError,
    SessionView,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.paper import SimulationPolicy, TargetSpec
from app.domain.replay import ReplayCursor
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy
from tests.factories_replay import BASE
from tests.integration.replay_support import create_command, replay_service

pytestmark = pytest.mark.integration

START = BASE + timedelta(hours=8)
ACCOUNT = AccountState(equity=Decimal("100000"))
RISK = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("1000"))


class InjectedFaultError(RuntimeError):
    """A transient fault, standing in for a dropped connection."""


def typed_fault(position_id: str) -> Exception:
    """What a Phase 9 store outage looks like to the replay service."""
    return PaperServiceError(
        PaperErrorKind.UNAVAILABLE,
        "PAPER_STORE_UNAVAILABLE",
        f"the ledger could not be written for {position_id}",
    )


def untyped_fault(position_id: str) -> Exception:
    """What a dropped connection looks like: no type the caller knows."""
    return InjectedFaultError(f"connection lost while feeding {position_id}")


class CountingPaper:
    """The real Phase 9 service, with one observation made to fail.

    Delegation rather than a stub: every accepted bar goes through the real
    engine and the real ledger, so what the retry finds is exactly what a real
    partial failure would have left behind.
    """

    def __init__(self, inner: PaperTradingService, budget: dict[str, Any]) -> None:
        self._inner = inner
        self._budget = budget

    async def observe(self, position_id: str, content: str, source_name: str) -> PositionView:
        self._budget["calls"] += 1
        if self._budget["armed"] and self._budget["calls"] > self._budget["after"]:
            self._budget["armed"] = 0
            raise self._budget["fault"](position_id)
        return await self._inner.observe(position_id, content, source_name)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


def arm_paper_failure(
    service: ReplayService,
    *,
    after: int,
    fault: Callable[[str], Exception] = typed_fault,
) -> dict[str, Any]:
    """Make the next step fail after ``after`` successful observations."""
    budget: dict[str, Any] = {"calls": 0, "after": after, "armed": 1, "fault": fault}
    original = service._paper_for  # noqa: SLF001

    def failing(as_of: Any) -> Any:
        return CountingPaper(original(as_of), budget)

    service._paper_for = failing  # type: ignore[method-assign]  # noqa: SLF001
    return budget


def arm_cursor_failure(service: ReplayService) -> list[bool]:
    """Make the next cursor write fail, after every observation succeeded."""
    armed = [True]
    store = service._store  # noqa: SLF001
    original = store.advance_session

    async def failing(*args: Any, **kwargs: Any) -> Any:
        if armed[0]:
            armed[0] = False
            raise InjectedFaultError("connection lost while committing the cursor")
        return await original(*args, **kwargs)

    store.advance_session = failing  # type: ignore[method-assign]
    return armed


async def open_positions(service: ReplayService, session_id: str, count: int) -> list[str]:
    ids = []
    for index in range(count):
        _s, stored = await service.open_paper_position(
            session_id,
            idempotency_key=f"recovery-position-{index:06d}-{session_id[-6:]}",
            direction=Direction.LONG if index % 2 == 0 else Direction.SHORT,
            quantity=2,
            intended_entry=Decimal("100"),
            stop=Decimal("80") if index % 2 == 0 else Decimal("130"),
            targets=(TargetSpec(Decimal("130") if index % 2 == 0 else Decimal("80"), 2),),
            account=ACCOUNT,
            risk=RISK,
            policy=SimulationPolicy(),
        )
        ids.append(stored.position_id)
    return ids


async def bars_of(service: ReplayService, session_id: str) -> dict[str, list[str]]:
    """Each position's applied bar times, from its own ledger."""
    applied: dict[str, list[str]] = {}
    for stored in await service.positions(session_id):
        applied[stored.position_id] = [
            event.market_time.isoformat()
            for event in stored.events
            if event.type.value == "OBSERVATION_APPLIED" and event.market_time is not None
        ]
    return applied


def cursor_of(view: SessionView) -> ReplayCursor:
    return view.session.cursor


def bar_opening_at(boundary: datetime) -> str:
    """The bar a boundary revealed, as the ledger records it.

    A boundary is a coverage *end*; the ledger stamps an observation with the
    bar's open time. Mixing the two silently compares a bar with its successor.
    """
    return (boundary - timedelta(minutes=Timeframe.M5.minutes)).isoformat()


class TestAStepThatFailsPartWayThrough:
    @pytest.mark.parametrize("after", [0, 1, 3, 6])
    async def test_the_cursor_does_not_move_past_an_undelivered_bar(
        self, database: Database, after: int
    ) -> None:
        """Injected before the first position, after the first, and mid-way."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command(f"recovery-partial-{after:03d}", replay_start=START)
        )
        await open_positions(service, session.session_id, 7)
        before = (await service.get(session.session_id)).session.cursor

        arm_paper_failure(service, after=after)
        with pytest.raises(ReplayServiceError) as error:
            await service.step(session.session_id, command_key=None, expected_version=None)
        assert error.value.code == "OBSERVATION_NOT_DELIVERED"

        after_failure = (await service.get(session.session_id)).session.cursor
        assert after_failure == before, "the cursor moved past a bar nobody received"

    async def test_an_untyped_infrastructure_fault_also_stops_the_cursor(
        self, database: Database
    ) -> None:
        """A dropped connection has no code; it must still not be swallowed."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-untyped-0001", replay_start=START)
        )
        await open_positions(service, session.session_id, 4)
        before = (await service.get(session.session_id)).session.cursor

        arm_paper_failure(service, after=2, fault=untyped_fault)
        with pytest.raises(InjectedFaultError):
            await service.step(session.session_id, command_key=None, expected_version=None)

        assert (await service.get(session.session_id)).session.cursor == before

    @pytest.mark.parametrize("after", [0, 1, 3, 6])
    async def test_a_retry_leaves_every_position_with_the_bar_exactly_once(
        self, database: Database, after: int
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command(f"recovery-retry-{after:03d}", replay_start=START)
        )
        ids = await open_positions(service, session.session_id, 7)

        arm_paper_failure(service, after=after)
        with pytest.raises(ReplayServiceError):
            await service.step(session.session_id, command_key=None, expected_version=None)

        # The retry runs the same step again. Positions that already have the
        # bar must not take it twice; positions that missed it must get it.
        result = await service.step(session.session_id, command_key=None, expected_version=None)
        assert len(result.revealed) == 1
        bar = bar_opening_at(result.revealed[0])

        applied = await bars_of(service, session.session_id)
        for position_id in ids:
            times = applied[position_id]
            assert times.count(bar) == 1, f"{position_id} has {times.count(bar)} copies of {bar}"
            assert len(times) == len(set(times)), f"{position_id} has a duplicate bar"
        assert len({tuple(applied[position_id]) for position_id in ids}) == 1
        assert cursor_of(result.view).as_of == result.revealed[0]

    async def test_a_cursor_write_that_fails_after_delivery_is_recovered(
        self, database: Database
    ) -> None:
        """The last boundary: every position took the bar, the commit did not."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-cursorfail-1", replay_start=START)
        )
        ids = await open_positions(service, session.session_id, 3)
        before = (await service.get(session.session_id)).session.cursor

        arm_cursor_failure(service)
        with pytest.raises(InjectedFaultError):
            await service.step(session.session_id, command_key=None, expected_version=None)

        stalled = (await service.get(session.session_id)).session.cursor
        assert stalled == before

        result = await service.step(session.session_id, command_key=None, expected_version=None)
        bar = bar_opening_at(result.revealed[0])
        applied = await bars_of(service, session.session_id)
        for position_id in ids:
            assert applied[position_id].count(bar) == 1
        assert cursor_of(result.view).version == before.version + 1

    async def test_no_position_is_left_holding_a_bar_the_session_never_reaches(
        self, database: Database
    ) -> None:
        """The residual window, measured.

        Deliver-then-commit means a failure can leave positions holding the
        boundary the cursor has not yet reached. That window is exactly one
        bar wide, and the retry closes it.
        """
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-window-00001", replay_start=START)
        )
        ids = await open_positions(service, session.session_id, 4)

        arm_paper_failure(service, after=2)
        with pytest.raises(ReplayServiceError):
            await service.step(session.session_id, steps=5, command_key=None, expected_version=None)

        cursor = (await service.get(session.session_id)).session.cursor
        applied = await bars_of(service, session.session_id)
        for position_id in ids:
            ahead = [moment for moment in applied[position_id] if moment > cursor.as_of.isoformat()]
            assert len(ahead) <= 1, f"{position_id} is {len(ahead)} bars ahead of the cursor"

        await service.step(session.session_id, steps=5, command_key=None, expected_version=None)
        closed = (await service.get(session.session_id)).session.cursor
        reapplied = await bars_of(service, session.session_id)
        for position_id in ids:
            assert all(moment <= closed.as_of.isoformat() for moment in reapplied[position_id])
            assert len(reapplied[position_id]) == len(set(reapplied[position_id]))


class TestAdvanceFailureSemantics:
    async def test_an_advance_is_committed_step_by_step(self, database: Database) -> None:
        """Stated, not implied: the cursor reports the last completed step."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-advance-0001", replay_start=START)
        )
        await open_positions(service, session.session_id, 3)
        before = (await service.get(session.session_id)).session.cursor

        # Three positions per boundary; failing after eight observations puts
        # the fault inside the third boundary.
        arm_paper_failure(service, after=8)
        with pytest.raises(ReplayServiceError):
            await service.step(
                session.session_id, steps=10, command_key=None, expected_version=None
            )

        after = (await service.get(session.session_id)).session.cursor
        assert after.version == before.version + 2, "the cursor is not at the last completed step"
        assert after.as_of == before.as_of + timedelta(minutes=10)
        assert after.revealed_driver_candles == before.revealed_driver_candles + 2

    async def test_the_whole_advance_is_validated_before_any_of_it_commits(
        self, database: Database
    ) -> None:
        """An advance past the end moves nothing at all."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-advance-0002", replay_start=START)
        )
        before = (await service.get(session.session_id)).session.cursor
        with pytest.raises(ReplayServiceError) as error:
            await service.step(
                session.session_id, steps=9999, command_key=None, expected_version=None
            )
        assert error.value.code == "RESOURCE_LIMIT"
        assert (await service.get(session.session_id)).session.cursor == before

    async def test_a_retry_after_a_partial_advance_finishes_it_without_overshooting(
        self, database: Database
    ) -> None:
        """The command knows where it was going, so the retry finishes it.

        An earlier version advanced ``steps`` more from wherever the session
        now stood, so retrying ``advance(6)`` after two committed boundaries
        left the session eight candles on from a request for six. The command
        target is now stored with the key, and the retry completes only the
        remainder.
        """
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-advance-0003", replay_start=START)
        )
        await open_positions(service, session.session_id, 2)
        before = (await service.get(session.session_id)).session.cursor
        intended = before.revealed_driver_candles + 6

        arm_paper_failure(service, after=5)
        with pytest.raises(ReplayServiceError):
            await service.step(
                session.session_id,
                steps=6,
                command_key="recovery-advance-key-01",
                expected_version=None,
            )
        stalled = (await service.get(session.session_id)).session.cursor
        assert stalled.version > before.version
        assert stalled.revealed_driver_candles < intended

        resumed = await service.step(
            session.session_id,
            steps=6,
            command_key="recovery-advance-key-01",
            expected_version=None,
        )
        assert resumed.replayed is False, "a partial advance must not report itself complete"
        assert cursor_of(resumed.view).revealed_driver_candles == intended, (
            "the retry overshot the cursor the original command was going to reach"
        )
        assert cursor_of(resumed.view).version == before.version + 6

        again = await service.step(
            session.session_id,
            steps=6,
            command_key="recovery-advance-key-01",
            expected_version=None,
        )
        assert again.replayed is True
        assert cursor_of(again.view) == cursor_of(resumed.view)

    @pytest.mark.parametrize(("requested", "fail_after"), [(10, 4), (10, 8), (4, 2), (2, 1)])
    async def test_a_retry_never_passes_the_cursor_the_command_aimed_at(
        self, database: Database, requested: int, fail_after: int
    ) -> None:
        """One position, so the failure lands on a boundary exactly."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command(f"recovery-noovershoot-{requested}{fail_after}", replay_start=START)
        )
        await open_positions(service, session.session_id, 1)
        before = (await service.get(session.session_id)).session.cursor
        intended = before.revealed_driver_candles + requested

        arm_paper_failure(service, after=fail_after)
        with pytest.raises(ReplayServiceError):
            await service.step(
                session.session_id,
                steps=requested,
                command_key=f"recovery-key-{requested:03d}{fail_after:03d}",
                expected_version=None,
            )
        partial = (await service.get(session.session_id)).session.cursor
        assert partial.revealed_driver_candles == before.revealed_driver_candles + fail_after

        result = await service.step(
            session.session_id,
            steps=requested,
            command_key=f"recovery-key-{requested:03d}{fail_after:03d}",
            expected_version=None,
        )
        assert cursor_of(result.view).revealed_driver_candles == intended
        assert len(result.revealed) == requested - fail_after

        # And the ledger agrees: every bar once, none past the intended cursor.
        applied = await bars_of(service, session.session_id)
        for times in applied.values():
            assert len(times) == len(set(times))
            assert all(moment < cursor_of(result.view).as_of.isoformat() for moment in times)

    async def test_a_fresh_key_after_a_partial_advance_starts_a_new_command(
        self, database: Database
    ) -> None:
        """A different key is a different request, and advances its own count."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-freshkey-0001", replay_start=START)
        )
        await open_positions(service, session.session_id, 1)
        before = (await service.get(session.session_id)).session.cursor

        arm_paper_failure(service, after=3)
        with pytest.raises(ReplayServiceError):
            await service.step(
                session.session_id,
                steps=8,
                command_key="recovery-partial-key-1",
                expected_version=None,
            )
        partial = (await service.get(session.session_id)).session.cursor

        fresh = await service.step(
            session.session_id,
            steps=2,
            command_key="recovery-fresh-key-001",
            expected_version=None,
        )
        assert cursor_of(fresh.view).revealed_driver_candles == partial.revealed_driver_candles + 2
        assert before.revealed_driver_candles < partial.revealed_driver_candles


class TestStepIdempotencyAfterFailure:
    async def test_the_same_key_after_a_transient_failure_applies_one_step(
        self, database: Database
    ) -> None:
        service = replay_service(database)
        session, _ = await service.create(
            create_command("recovery-idem-000001", replay_start=START)
        )
        ids = await open_positions(service, session.session_id, 3)
        before = (await service.get(session.session_id)).session.cursor

        arm_paper_failure(service, after=1)
        with pytest.raises(ReplayServiceError):
            await service.step(
                session.session_id, command_key="recovery-step-key-001", expected_version=None
            )

        first = await service.step(
            session.session_id, command_key="recovery-step-key-001", expected_version=None
        )
        second = await service.step(
            session.session_id, command_key="recovery-step-key-001", expected_version=None
        )

        assert cursor_of(first.view).version == before.version + 1
        assert second.replayed is True
        assert cursor_of(second.view) == cursor_of(first.view)

        applied = await bars_of(service, session.session_id)
        bar = bar_opening_at(first.revealed[0])
        for position_id in ids:
            assert applied[position_id].count(bar) == 1

    async def test_the_same_key_with_a_different_payload_is_a_conflict(
        self, database: Database
    ) -> None:
        """Create keys carry a fingerprint; a changed request is refused."""
        service = replay_service(database)
        await service.create(create_command("recovery-idem-000002", replay_start=START))
        with pytest.raises(ReplayServiceError) as error:
            await service.create(
                create_command("recovery-idem-000002", replay_start=START + timedelta(hours=1))
            )
        assert error.value.code == "IDEMPOTENCY_CONFLICT"


class TestLongAndShortRecoverIdentically:
    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    async def test_a_recovered_session_reaches_the_same_ledger_as_a_clean_one(
        self, database: Database, direction: Direction
    ) -> None:
        """Driven to the same replay moment, the two ledgers must be identical.

        The recovered session takes an extra round trip to get there, which is
        the whole point: the *path* differs, the market state does not. Both are
        advanced to the same cursor before comparing, because a retry of
        "advance 8" advances eight more from wherever the session now stands -
        it does not re-run the original request.
        """
        target = 8
        ledgers = []
        cursors = []
        for index, fail in enumerate((False, True)):
            service = replay_service(database)
            session, _ = await service.create(
                create_command(f"recovery-parity-{direction.value[:1]}{index}0", replay_start=START)
            )
            await service.open_paper_position(
                session.session_id,
                idempotency_key=f"recovery-parity-pos-{direction.value[:1]}{index}0",
                direction=direction,
                quantity=2,
                intended_entry=Decimal("100"),
                stop=Decimal("80") if direction is Direction.LONG else Decimal("130"),
                targets=(
                    TargetSpec(Decimal("130") if direction is Direction.LONG else Decimal("80"), 2),
                ),
                account=ACCOUNT,
                risk=RISK,
                policy=SimulationPolicy(),
            )
            start_version = (await service.get(session.session_id)).session.cursor.version
            if fail:
                arm_paper_failure(service, after=2)
                with pytest.raises(ReplayServiceError):
                    await service.step(
                        session.session_id, steps=target, command_key=None, expected_version=None
                    )
            done = (await service.get(session.session_id)).session.cursor.version - start_version
            if done < target:
                await service.step(
                    session.session_id,
                    steps=target - done,
                    command_key=None,
                    expected_version=None,
                )
            stored = (await service.positions(session.session_id))[0]
            cursors.append((await service.get(session.session_id)).session.cursor.as_of)
            ledgers.append(
                [
                    (
                        event.type.value,
                        None if event.market_time is None else event.market_time.isoformat(),
                        tuple(sorted(event.data.items())),
                    )
                    for event in stored.events
                ]
            )
        assert cursors[0] == cursors[1]
        assert ledgers[0] == ledgers[1]


class TestAFailedAdvanceSaysWhereItGotTo:
    @pytest.mark.parametrize(("positions", "after", "expected"), [(2, 5, 2), (3, 8, 2), (1, 0, 0)])
    async def test_the_refusal_names_the_number_of_completed_steps(
        self, database: Database, positions: int, after: int, expected: int
    ) -> None:
        """A client that asked for six steps must be able to tell whether it
        moved none of them or five, without re-reading the session first."""
        service = replay_service(database)
        session, _ = await service.create(
            create_command(f"recovery-progress-{positions}{after:02d}", replay_start=START)
        )
        await open_positions(service, session.session_id, positions)
        before = (await service.get(session.session_id)).session.cursor

        arm_paper_failure(service, after=after)
        with pytest.raises(ReplayServiceError) as error:
            await service.step(session.session_id, steps=6, command_key=None, expected_version=None)
        assert f"completed {expected} of 6 steps" in error.value.detail

        # And the claim is true: the cursor really is at that step.
        cursor = (await service.get(session.session_id)).session.cursor
        assert cursor.version == before.version + expected
