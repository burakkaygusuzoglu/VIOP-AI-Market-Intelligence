"""Shadow run lifecycle over a real live session (Phase 14 Part 2A).

Every test here drives an actual :class:`LiveWorkspace` session: the shadow run
attaches to it as a reader, exactly as it does in the application. What is
pinned is the lifecycle - one stream for many readers, no retrospective
signals, idempotent creation, cancellation that keeps the journal, and a
browser's SSE subscription having nothing to do with any of it.
"""

from __future__ import annotations

import asyncio

import pytest

from app.application.live.workspace import LiveWorkspace
from app.application.shadow.workspace import (
    ShadowWorkspace,
    ShadowWorkspaceError,
    WorkspaceErrorKind,
)
from app.domain.backtest.strategies.ema_crossover import IDENTIFIER, VERSION
from app.domain.common.enums import Timeframe
from app.domain.shadow.decision import JournalEntryKind, OperationalKind
from app.domain.shadow.run import EndReason, ShadowLimits, ShadowRunStatus
from tests.unit.live.workspace_support import (
    END,
    FakeCatalog,
    bar,
    create,
    fixture_bars,
    settle,
    workspace,
)
from tests.unit.shadow.support import MemoryJournal, runner_for

pytestmark = pytest.mark.unit

M5 = Timeframe.M5


def shadow(
    journal: MemoryJournal | None = None, **limits: object
) -> tuple[ShadowWorkspace, MemoryJournal, LiveWorkspace, FakeCatalog]:
    """A shadow workspace over a real live workspace."""
    live, catalog, clock = workspace()
    store = journal or MemoryJournal()
    bounds = ShadowLimits(**limits) if limits else None  # type: ignore[arg-type]
    runner = runner_for(
        store, clock=clock, limits=bounds, supported={IDENTIFIER: frozenset({VERSION})}
    )
    space = ShadowWorkspace(runner=runner, store=store, live=live, limits=bounds)
    return space, store, live, catalog


async def started(space: ShadowWorkspace, live: LiveWorkspace, **kwargs: object) -> str:
    session_id = await create(live)
    run = await space.create(
        session_id=session_id,
        strategy_id=IDENTIFIER,
        strategy_version=VERSION,
        driver=M5,
        timeframes=(M5,),
        analysis_evidence=False,
        **kwargs,  # type: ignore[arg-type]
    )
    return run.run_id


class TestCapability:
    def test_it_states_the_simulation_and_the_missing_metadata(self) -> None:
        space, _, _, _ = shadow()

        capability = space.capability()

        assert capability.provenance == "SIMULATED_HISTORICAL_STREAM"
        assert capability.market_currency == "HISTORICAL"
        # The default deployment composes no verified contract provider, and
        # the capability says so rather than letting a screen discover it.
        assert capability.financial_metadata is False
        assert capability.strategies == {IDENTIFIER: (VERSION,)}

    def test_only_registered_rules_can_be_asked_for(self) -> None:
        space, _, live, _ = shadow()

        with pytest.raises(ShadowWorkspaceError) as caught:
            space._policy("../../etc/passwd", "1.0.0")  # noqa: SLF001

        assert caught.value.code == "STRATEGY_UNSUPPORTED"
        assert caught.value.kind is WorkspaceErrorKind.INVALID
        del live

    def test_an_unknown_version_is_refused_not_approximated(self) -> None:
        space, _, _, _ = shadow()

        with pytest.raises(ShadowWorkspaceError) as caught:
            space._policy(IDENTIFIER, "9.9.9")  # noqa: SLF001

        assert caught.value.code == "STRATEGY_UNSUPPORTED"


class TestOneStreamManyReaders:
    async def test_a_run_observes_the_session_it_was_given(self) -> None:
        space, store, live, catalog = shadow()
        run_id = await started(space, live)

        catalog.providers[0].put(*fixture_bars(30), END)
        await settle()
        await asyncio.sleep(0.2)

        run = await space.get(run_id)
        entries, total, _ = await space.journal(run_id, after=0, limit=100)
        assert run.provenance == "SIMULATED_HISTORICAL_STREAM"
        assert run.market_currency == "HISTORICAL"
        assert total >= 1
        assert entries[0].operational is OperationalKind.RUN_OPENED

    async def test_two_runs_share_one_provider_stream(self) -> None:
        space, store, live, catalog = shadow()
        session_id = await create(live)

        first = await space.create(
            session_id=session_id,
            strategy_id=IDENTIFIER,
            strategy_version=VERSION,
            driver=M5,
            timeframes=(M5,),
            analysis_evidence=False,
        )
        second = await space.create(
            session_id=session_id,
            strategy_id=IDENTIFIER,
            strategy_version=VERSION,
            driver=M5,
            timeframes=(M5,),
            analysis_evidence=False,
        )

        assert first.run_id != second.run_id
        # One session was opened, and only one provider exists for it.
        assert catalog.opened == 1
        assert len(catalog.providers) == 1

    async def test_a_run_never_sees_candles_from_before_it_existed(self) -> None:
        """No retrospective signal: the session consumes history, then a run is
        created, and the run starts at the next confirmed boundary."""
        space, store, live, catalog = shadow()
        session_id = await create(live)
        catalog.providers[0].put(*fixture_bars(10))
        await settle()

        run = await space.create(
            session_id=session_id,
            strategy_id=IDENTIFIER,
            strategy_version=VERSION,
            driver=M5,
            timeframes=(M5,),
            analysis_evidence=False,
        )
        catalog.providers[0].put(END)
        await asyncio.sleep(0.2)

        entries, _, _ = await space.journal(run.run_id, after=0, limit=200)
        decisions = [item for item in entries if item.kind is JournalEntryKind.DECISION]
        assert decisions == []  # every boundary had already passed


class TestCreationIsIdempotent:
    async def test_the_same_attempt_key_returns_the_same_run(self) -> None:
        space, store, live, _ = shadow()
        session_id = await create(live)
        arguments = {
            "session_id": session_id,
            "strategy_id": IDENTIFIER,
            "strategy_version": VERSION,
            "driver": M5,
            "timeframes": (M5,),
            "analysis_evidence": False,
            "attempt_key": "attempt-0001",
        }

        first = await space.create(**arguments)  # type: ignore[arg-type]
        second = await space.create(**arguments)  # type: ignore[arg-type]

        assert first.run_id == second.run_id
        runs, total = await space.list(offset=0, limit=10)
        assert total == 1
        assert len(runs) == 1

    async def test_the_same_key_with_other_rules_is_a_conflict(self) -> None:
        space, store, live, _ = shadow()
        session_id = await create(live)
        await space.create(
            session_id=session_id,
            strategy_id=IDENTIFIER,
            strategy_version=VERSION,
            driver=M5,
            timeframes=(M5,),
            analysis_evidence=False,
            attempt_key="attempt-0002",
        )

        with pytest.raises(ShadowWorkspaceError) as caught:
            await space.create(
                session_id=session_id,
                strategy_id=IDENTIFIER,
                strategy_version=VERSION,
                driver=M5,
                timeframes=(M5,),
                analysis_evidence=True,  # a different configuration
                attempt_key="attempt-0002",
            )

        assert caught.value.code == "SHADOW_ATTEMPT_CONFLICT"
        assert caught.value.kind is WorkspaceErrorKind.CONFLICT

    async def test_a_key_that_lost_the_race_holds_no_capacity(self) -> None:
        space, store, live, _ = shadow(max_runs=2)
        session_id = await create(live)
        arguments = {
            "session_id": session_id,
            "strategy_id": IDENTIFIER,
            "strategy_version": VERSION,
            "driver": M5,
            "timeframes": (M5,),
            "analysis_evidence": False,
            "attempt_key": "attempt-0003",
        }

        for _ in range(5):
            await space.create(**arguments)  # type: ignore[arg-type]

        # Five requests, one run, and the capacity bound was never consumed by
        # the four that lost.
        runs, total = await space.list(offset=0, limit=10)
        assert total == 1
        del runs

    async def test_a_retry_is_answered_even_when_every_slot_is_taken(self) -> None:
        """Found by measurement: the capacity check used to run first, so a
        retry of a request that had already succeeded was refused with
        SHADOW_CAPACITY - by the load it had itself created."""
        space, _, live, _ = shadow(max_runs=2)
        session_id = await create(live)
        arguments = {
            "session_id": session_id,
            "strategy_id": IDENTIFIER,
            "strategy_version": VERSION,
            "driver": M5,
            "timeframes": (M5,),
            "analysis_evidence": False,
        }
        first = await space.create(**arguments, attempt_key="full-attempt-0001")  # type: ignore[arg-type]
        await space.create(**arguments, attempt_key="full-attempt-0002")  # type: ignore[arg-type]

        retried = await space.create(**arguments, attempt_key="full-attempt-0001")  # type: ignore[arg-type]
        with pytest.raises(ShadowWorkspaceError) as caught:
            await space.create(**arguments, attempt_key="full-attempt-0003")  # type: ignore[arg-type]

        assert retried.run_id == first.run_id
        assert caught.value.code == "SHADOW_CAPACITY"


class TestConcurrentCreation:
    async def test_different_keys_racing_at_capacity_never_exceed_it(self) -> None:
        space, store, live, _ = shadow(max_runs=4)
        session_id = await create(live)

        results = await asyncio.gather(
            *(
                space.create(
                    session_id=session_id,
                    strategy_id=IDENTIFIER,
                    strategy_version=VERSION,
                    driver=M5,
                    timeframes=(M5,),
                    analysis_evidence=False,
                    attempt_key=f"race-capacity-{index:04d}",
                )
                for index in range(7)
            ),
            return_exceptions=True,
        )

        created = [item for item in results if not isinstance(item, BaseException)]
        refused = [item for item in results if isinstance(item, ShadowWorkspaceError)]
        assert len(created) == 4
        assert {item.code for item in refused} == {"SHADOW_CAPACITY"}
        assert len(refused) == 3
        assert len(store.runs) == 4  # a refused request wrote nothing

    async def test_a_creation_in_flight_during_shutdown_starts_no_orphan(self) -> None:
        """Found in Part 2B review: shutdown did not take the creation lock, so a
        creation already waiting on I/O could start an observer after shutdown
        returned. The same defect Phase 13 fixed for live sessions."""
        space, store, live, _ = shadow()
        session_id = await create(live)
        gate = asyncio.Event()
        original = store.create_run

        async def slow_create_run(run, *, attempt_key=None):  # type: ignore[no-untyped-def]
            await gate.wait()
            return await original(run, attempt_key=attempt_key)

        store.create_run = slow_create_run  # type: ignore[method-assign]
        creating = asyncio.create_task(
            space.create(
                session_id=session_id,
                strategy_id=IDENTIFIER,
                strategy_version=VERSION,
                driver=M5,
                timeframes=(M5,),
                analysis_evidence=False,
            )
        )
        await asyncio.sleep(0)  # creation is now inside the lock, waiting on storage
        stopping = asyncio.create_task(space.shutdown())
        await asyncio.sleep(0)
        gate.set()
        await stopping
        outcome = await asyncio.gather(creating, return_exceptions=True)

        # Either the run was created before shutdown and then ended by it, or it
        # was refused. It is never left observing with nobody to stop it.
        tasks = [run.task for run in space._runs.values()]  # noqa: SLF001
        assert all(task is None or task.done() for task in tasks)
        for run in store.runs.values():
            assert run.status is ShadowRunStatus.ENDED
        if isinstance(outcome[0], ShadowWorkspaceError):
            assert outcome[0].code == "SHADOW_CLOSED"

    async def test_nothing_can_be_created_after_shutdown(self) -> None:
        space, _, live, _ = shadow()
        session_id = await create(live)
        await space.shutdown()

        with pytest.raises(ShadowWorkspaceError) as caught:
            await space.create(
                session_id=session_id,
                strategy_id=IDENTIFIER,
                strategy_version=VERSION,
                driver=M5,
                timeframes=(M5,),
            )

        assert caught.value.code == "SHADOW_CLOSED"


class TestTheProviderEnding:
    async def test_a_failing_provider_ends_the_run_and_it_is_never_complete(self) -> None:
        from app.api.schemas.shadow_projection import run_response

        space, _, live, catalog = shadow()
        run_id = await started(space, live)
        catalog.providers[0].put(*fixture_bars(3), RuntimeError("feed broke C:\\secret sk-x"))

        for _ in range(100):
            await asyncio.sleep(0.02)
            ended = await space.get(run_id)
            if ended.status is ShadowRunStatus.ENDED:
                break

        assert ended.status is ShadowRunStatus.ENDED  # not left OBSERVING
        assert ended.end_reason is not EndReason.STREAM_ENDED
        assert run_response(ended).completeness != "COMPLETE"


class TestSubscribersAreNotRuns:
    async def test_a_browser_subscription_closing_does_not_end_a_run(self) -> None:
        """An SSE subscriber and a shadow run are different things with
        different lifetimes. Closing the one - a tab shut, a reconnect - must
        leave the other observing."""
        space, _, live, catalog = shadow()
        session_id = await create(live)
        run = await space.create(
            session_id=session_id,
            strategy_id=IDENTIFIER,
            strategy_version=VERSION,
            driver=M5,
            timeframes=(M5,),
            analysis_evidence=False,
        )
        subscriber = live.subscribe(session_id, after=None)
        catalog.providers[0].put(*fixture_bars(3))
        await settle()

        subscriber.close()
        await settle()

        assert (await space.get(run.run_id)).status is ShadowRunStatus.OBSERVING
        assert live.subscriber_count() == 0

        catalog.providers[0].put(*fixture_bars(6)[3:], END)
        await asyncio.sleep(0.2)
        ended = await space.get(run.run_id)
        # It ended because the data did, not because anybody stopped watching.
        assert ended.end_reason is EndReason.STREAM_ENDED


class TestEnding:
    async def test_cancelling_ends_the_run_and_keeps_the_journal(self) -> None:
        space, store, live, catalog = shadow()
        run_id = await started(space, live)
        catalog.providers[0].put(*fixture_bars(5))
        await settle()

        before, _, _ = await space.journal(run_id, after=0, limit=100)
        cancelled = await space.cancel(run_id)
        after, _, _ = await space.journal(run_id, after=0, limit=100)

        assert cancelled.status is ShadowRunStatus.ENDED
        assert cancelled.end_reason is EndReason.CANCELLED
        assert len(after) >= len(before)  # nothing was removed
        assert after[0].operational is OperationalKind.RUN_OPENED

    async def test_cancelling_twice_is_safe(self) -> None:
        space, _, live, _ = shadow()
        run_id = await started(space, live)

        first = await space.cancel(run_id)
        second = await space.cancel(run_id)

        assert first.status is second.status is ShadowRunStatus.ENDED
        assert first.ended_at == second.ended_at

    async def test_an_ended_session_cannot_take_a_new_run(self) -> None:
        space, _, live, catalog = shadow()
        session_id = await create(live)
        catalog.providers[0].put(END)
        await settle()
        await asyncio.sleep(0.1)

        with pytest.raises(ShadowWorkspaceError) as caught:
            await space.create(
                session_id=session_id,
                strategy_id=IDENTIFIER,
                strategy_version=VERSION,
                driver=M5,
                timeframes=(M5,),
                analysis_evidence=False,
            )

        assert caught.value.kind is WorkspaceErrorKind.CONFLICT

    async def test_an_unknown_run_is_not_found(self) -> None:
        space, _, _, _ = shadow()

        with pytest.raises(ShadowWorkspaceError) as caught:
            await space.get("SR-" + "0" * 24)

        assert caught.value.kind is WorkspaceErrorKind.NOT_FOUND
        assert caught.value.code == "SHADOW_RUN_NOT_FOUND"

    async def test_an_unknown_session_is_not_found(self) -> None:
        space, _, _, _ = shadow()

        with pytest.raises(ShadowWorkspaceError) as caught:
            await space.create(
                session_id="LS-nope",
                strategy_id=IDENTIFIER,
                strategy_version=VERSION,
                driver=M5,
                timeframes=(M5,),
            )

        assert caught.value.kind is WorkspaceErrorKind.NOT_FOUND


class TestReadsAreBounded:
    async def test_a_journal_page_is_capped(self) -> None:
        space, _, live, catalog = shadow(max_journal_page=5)
        run_id = await started(space, live)
        catalog.providers[0].put(*fixture_bars(40), END)
        await settle()
        await asyncio.sleep(0.2)

        entries, total, _ = await space.journal(run_id, after=0, limit=1000)

        assert len(entries) <= 5
        assert total >= len(entries)  # the total is the honest count

    async def test_the_run_list_is_capped(self) -> None:
        space, _, live, _ = shadow()
        await started(space, live)

        runs, _ = await space.list(offset=0, limit=10_000)

        assert len(runs) <= 100

    async def test_a_bar_helper_is_still_a_valid_event(self) -> None:
        """Guards the helper the other tests lean on."""
        assert bar(0).timeframe == M5.value
