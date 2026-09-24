"""Shadow outcomes, attempt keys and restart against real PostgreSQL (Part 2A).

What only the real database can show: a published development is append-only
and written once; the forward-only rule holds even for a writer that bypasses
the domain; two concurrent creations with one attempt key produce exactly one
run; and a run a dead process left observing is closed from its own journal.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.adapters.persistence.database import Database
from app.adapters.persistence.shadow_store import SqlAlchemyShadowStore
from app.application.shadow.ports import AttemptKeyHeldError
from app.application.shadow.workspace import recover_interrupted_runs
from app.core.config import Settings
from app.domain.common.enums import Direction
from app.domain.shadow.decision import OperationalKind
from app.domain.shadow.outcome import (
    LevelEvent,
    OutcomeState,
    PriceDevelopment,
    ShadowOutcomeRecord,
)
from app.domain.shadow.run import EndReason, ShadowRunStatus
from tests.integration.paper_support import truncate
from tests.integration.test_shadow_persistence import FIRST, SECOND, a_decision, a_run
from tests.unit.live.support import RECEIVE_START, at

pytestmark = pytest.mark.integration


class FixedClock:
    def now(self) -> datetime:
        return RECEIVE_START


@pytest.fixture
async def store(migrated: Settings) -> AsyncIterator[SqlAlchemyShadowStore]:
    database = Database(migrated.sqlalchemy_url)
    await truncate(database)
    try:
        yield SqlAlchemyShadowStore(database)
    finally:
        await database.dispose()


def touched(
    sequence: int, key: str = "k1", *, state: OutcomeState = OutcomeState.OBSERVED
) -> ShadowOutcomeRecord:
    return ShadowOutcomeRecord(
        run_id=FIRST,
        decision_key=key,
        sequence=sequence,
        outcome_key=f"{key}-{state.value}-{sequence}".ljust(64, "0")[:64],
        recorded_at=RECEIVE_START,
        decision_boundary=at(5),
        direction=Direction.LONG,
        development=PriceDevelopment(
            state=state,
            event=LevelEvent.TARGET_LEVEL_TOUCHED
            if state is OutcomeState.OBSERVED
            else LevelEvent.NONE_REACHED,
            observed_from=at(10),
            observed_to=at(15),
            candles_observed=2,
            event_at=at(15) if state is OutcomeState.OBSERVED else None,
            target_ordinal=1 if state is OutcomeState.OBSERVED else None,
            best_price=Decimal("102.12345678"),
            worst_price=Decimal("99.5"),
            last_close=Decimal("101"),
            unresolved_reason=None if state is not OutcomeState.UNAVAILABLE else "a gap",
        ),
    )


class TestPublishedDevelopments:
    async def test_a_development_survives_a_restart_exactly(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        await store.create_run(a_run())
        assert await store.append_outcomes(FIRST, [touched(1)]) == 1

        database = Database(migrated.sqlalchemy_url)
        try:
            (read,), total = await SqlAlchemyShadowStore(database).read_outcomes(
                FIRST, after_sequence=0, limit=10
            )
        finally:
            await database.dispose()

        assert total == 1
        assert read.development.best_price == Decimal("102.12345678")  # exact, not a float
        assert read.development.event is LevelEvent.TARGET_LEVEL_TOUCHED
        assert read.development.event_at == at(15)
        assert read.decision_boundary == at(5)

    async def test_the_same_development_is_written_once(self, store: SqlAlchemyShadowStore) -> None:
        await store.create_run(a_run())

        first = await store.append_outcomes(FIRST, [touched(1)])
        again = await store.append_outcomes(FIRST, [touched(1)])

        assert (first, again) == (1, 0)
        assert (await store.read_outcomes(FIRST, after_sequence=0, limit=10))[1] == 1

    async def test_a_published_development_cannot_be_edited_or_removed(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        await store.create_run(a_run())
        await store.append_outcomes(FIRST, [touched(1)])
        database = Database(migrated.sqlalchemy_url)
        try:
            for statement in (
                "UPDATE shadow_outcomes SET event = 'STOP_LEVEL_TOUCHED'",
                "DELETE FROM shadow_outcomes",
            ):
                with pytest.raises(Exception) as caught:  # noqa: B017, PT011
                    async with database.engine.begin() as connection:
                        await connection.execute(text(statement))
                assert "append-only" in str(caught.value)
        finally:
            await database.dispose()

    async def test_the_database_refuses_an_outcome_citing_the_past(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        """Forward-only causality is a table constraint, not only a habit of the
        code that writes: a raw insert citing the decision's own boundary fails."""
        await store.create_run(a_run())
        database = Database(migrated.sqlalchemy_url)
        try:
            with pytest.raises(Exception) as caught:  # noqa: B017, PT011
                async with database.engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO shadow_outcomes (run_id, decision_key, sequence,"
                            " outcome_key, recorded_at, decision_boundary, direction, state,"
                            " event, rules, observed_from, candles_observed, ambiguous)"
                            " VALUES (:run, 'k', 1, 'o', now(), :boundary, 'LONG', 'OBSERVED',"
                            " 'TARGET_LEVEL_TOUCHED', 'shadow-outcome/v1', :boundary, 1, false)"
                        ),
                        {"run": FIRST, "boundary": at(5)},
                    )
            assert "outcomes_observe_only_what_came_after" in str(caught.value)
        finally:
            await database.dispose()

    async def test_an_unavailable_development_must_say_why_in_the_table(
        self, store: SqlAlchemyShadowStore, migrated: Settings
    ) -> None:
        await store.create_run(a_run())
        database = Database(migrated.sqlalchemy_url)
        try:
            with pytest.raises(Exception) as caught:  # noqa: B017, PT011
                async with database.engine.begin() as connection:
                    await connection.execute(
                        text(
                            "INSERT INTO shadow_outcomes (run_id, decision_key, sequence,"
                            " outcome_key, recorded_at, decision_boundary, direction, state,"
                            " event, rules, candles_observed, ambiguous)"
                            " VALUES (:run, 'k', 1, 'o', now(), now(), 'LONG', 'UNAVAILABLE',"
                            " 'NOT_OBSERVED', 'shadow-outcome/v1', 0, false)"
                        ),
                        {"run": FIRST},
                    )
            assert "unavailable_outcomes_say_why" in str(caught.value)
        finally:
            await database.dispose()

    async def test_the_current_answer_is_the_newest_and_the_old_one_stays(
        self, store: SqlAlchemyShadowStore
    ) -> None:
        await store.create_run(a_run())
        await store.append_outcomes(
            FIRST, [touched(1, state=OutcomeState.UNAVAILABLE), touched(2), touched(3, "k2")]
        )

        latest = await store.latest_outcomes(FIRST, ["k1", "k2", "k-missing"])
        _, total = await store.read_outcomes(FIRST, after_sequence=0, limit=10)

        assert latest["k1"].sequence == 2
        assert latest["k2"].sequence == 3
        assert "k-missing" not in latest  # no development is not "nothing happened"
        assert total == 3  # the earlier answer was not replaced

    async def test_outcomes_never_cross_runs(self, store: SqlAlchemyShadowStore) -> None:
        await store.create_run(a_run(FIRST))
        await store.create_run(a_run(SECOND))
        await store.append_outcomes(FIRST, [touched(1)])

        assert await store.latest_outcomes(SECOND, ["k1"]) == {}
        assert (await store.read_outcomes(SECOND, after_sequence=0, limit=10))[1] == 0


class TestAttemptKeysUnderConcurrency:
    async def test_racing_creations_with_one_key_make_one_run(self, migrated: Settings) -> None:
        """Eight separate connections - as eight workers would have - race one key."""
        database = Database(migrated.sqlalchemy_url)
        await truncate(database)
        await database.dispose()
        databases = [Database(migrated.sqlalchemy_url) for _ in range(8)]
        try:
            stores = [SqlAlchemyShadowStore(database) for database in databases]
            runs = [a_run("SR-" + f"{index:x}" * 24) for index in range(1, 9)]
            outcomes = await asyncio.gather(
                *(
                    store.create_run(run, attempt_key="race-attempt-0001")
                    for store, run in zip(stores, runs, strict=True)
                ),
                return_exceptions=True,
            )
            winners = [item for item in outcomes if not isinstance(item, BaseException)]
            held = [item for item in outcomes if isinstance(item, AttemptKeyHeldError)]
            listed, total = await stores[0].list_runs(offset=0, limit=20)
        finally:
            for database in databases:
                await database.dispose()

        assert len(winners) == 1
        assert len(held) == 7
        assert {item.run_id for item in held} == {winners[0].run_id}
        # The losers wrote nothing: no run without a key, no key without a run.
        assert total == 1
        assert listed[0].run_id == winners[0].run_id

    async def test_a_lost_claim_leaves_no_run_behind(self, store: SqlAlchemyShadowStore) -> None:
        await store.create_run(a_run(FIRST), attempt_key="lost-claim-0001")

        with pytest.raises(AttemptKeyHeldError) as caught:
            await store.create_run(a_run(SECOND), attempt_key="lost-claim-0001")

        assert caught.value.run_id == FIRST
        assert await store.get_run(SECOND) is None


class TestRestartReconciliation:
    async def test_an_orphaned_run_is_closed_from_its_own_journal(
        self, store: SqlAlchemyShadowStore
    ) -> None:
        await store.create_run(replace(a_run(), status=ShadowRunStatus.OBSERVING))
        await store.append(FIRST, [a_decision(1, "k1"), a_decision(2, "k2")])

        closed = await recover_interrupted_runs(store, FixedClock())
        run = await store.get_run(FIRST)
        entries, total = await store.read_entries(FIRST, after_sequence=0, limit=10)

        assert closed == (FIRST,)
        assert run is not None
        assert run.status is ShadowRunStatus.ENDED
        assert run.end_reason is EndReason.INTERRUPTED
        assert (run.observations, run.decisions) == (2, 2)
        assert run.first_boundary == at(5)
        assert run.last_boundary == at(10)
        assert total == 3
        assert entries[-1].operational is OperationalKind.RUN_ENDED

    async def test_reconciliation_is_idempotent_and_touches_ended_runs_not_at_all(
        self, store: SqlAlchemyShadowStore
    ) -> None:
        await store.create_run(replace(a_run(FIRST), status=ShadowRunStatus.OBSERVING))
        await store.create_run(a_run(SECOND))
        await store.finish_run(
            SECOND,
            status=ShadowRunStatus.ENDED,
            end_reason=EndReason.STREAM_ENDED,
            ended_at=RECEIVE_START,
            observations=0,
            decisions=0,
            entries=0,
            first_boundary=None,
            last_boundary=None,
        )

        first = await recover_interrupted_runs(store, FixedClock())
        second = await recover_interrupted_runs(store, FixedClock())
        ended = await store.get_run(SECOND)

        assert first == (FIRST,)
        assert second == ()
        assert ended is not None
        assert ended.end_reason is EndReason.STREAM_ENDED
