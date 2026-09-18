"""What a stored position is allowed to depend on, proved against real PostgreSQL.

Phase 9 closeout sections 3, 4, 10, 11, 12 and 14. A paper position is a claim
about the past. It may depend on the bars it was given, the plan it was opened
under, the contract facts frozen at that moment and its own simulation policy.
It may not depend on today's clock, on what the metadata provider says now, or
on what the application's current defaults happen to be - and the row that
summarises it is a projection of the ledger, never a substitute for it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.adapters.persistence.database import Database
from app.adapters.products.futures import FuturesSnapshotCodec
from app.api.schemas.paper_projection import detail
from app.application.paper.service import PaperErrorKind, PaperServiceError
from app.domain.paper import SameBarPolicy, SimulationPolicy, rebuild
from app.domain.paper.rules import FeeMode, FeePolicy, SlippageMode, SlippagePolicy
from tests.factories_futures import verified
from tests.factories_paper import paper_contract
from tests.integration.paper_support import (
    ENTRY,
    STOP_ON_REST,
    TARGET_ONE,
    FixedClock,
    bars_csv,
    create_command,
    service,
)

pytestmark = pytest.mark.anyio

D = Decimal

# Deliberately far apart: one clock a few months after the bars, one seventy
# years later. Nothing financial may differ between them.
LATER = FixedClock(datetime(2026, 6, 1, tzinfo=UTC))
MUCH_LATER = FixedClock(datetime(2099, 1, 1, tzinfo=UTC))
# Before the last fixture bar has closed - the environment an old position would
# meet if the machine's clock were wrong, or if it were restored from a backup.
EARLIER = FixedClock(datetime(2026, 3, 2, 11, 30, tzinfo=UTC))


async def closed_position(database: Database, key: str = "temporal-key-00000001") -> str:
    """A finished position: entry, target 1, then the stop on the rest."""
    svc = service(database, clock=LATER)
    created = await svc.create(create_command(key))
    position_id = created.stored.position_id
    await svc.observe(position_id, bars_csv(ENTRY, TARGET_ONE, STOP_ON_REST), "bars.csv")
    return position_id


class TestWallClockCannotReachTheLedger:
    async def test_two_radically_different_clocks_read_the_same_position(
        self, database: Database
    ) -> None:
        position_id = await closed_position(database)

        first = (await service(database, clock=LATER).get(position_id)).stored
        second = (await service(database, clock=MUCH_LATER).get(position_id)).stored

        assert first.projection == second.projection
        assert first.events == second.events
        assert detail(first).model_dump(mode="json") == detail(second).model_dump(mode="json")

    async def test_replaying_the_ledger_is_pure(self, database: Database) -> None:
        position_id = await closed_position(database, "temporal-key-00000002")
        stored = (await service(database, clock=MUCH_LATER).get(position_id)).stored
        product = FuturesSnapshotCodec().restore(stored.product_snapshot)

        first = rebuild(stored.spec, stored.approval, product, stored.events)
        second = rebuild(stored.spec, stored.approval, product, stored.events)

        assert first == second
        assert first.realized_gross == D("40.00")
        assert first.state.value == "CLOSED"

    async def test_an_accepted_observation_still_replays_under_an_earlier_clock(
        self, database: Database
    ) -> None:
        """Closedness is an intake rule. It is never re-asked of stored history."""
        position_id = await closed_position(database, "temporal-key-00000003")
        reference = (await service(database, clock=LATER).get(position_id)).stored

        stale = service(database, clock=EARLIER)
        view = await stale.get(position_id)

        assert view.stored.projection == reference.projection
        assert view.stored.events == reference.events
        # Commands still reach it: replay never consults the clock, so the
        # refusal comes from the position's own state, not from the clock.
        with pytest.raises(PaperServiceError) as error:
            await stale.request_close(position_id)  # CLOSED has nothing to close
        assert error.value.kind is PaperErrorKind.REFUSED

    async def test_an_unclosed_bar_is_still_refused_at_intake(self, database: Database) -> None:
        svc = service(database, clock=EARLIER)
        created = await svc.create(create_command("temporal-key-00000004"))

        with pytest.raises(PaperServiceError) as error:
            await svc.observe(
                created.stored.position_id, bars_csv(ENTRY, TARGET_ONE, STOP_ON_REST), "bars.csv"
            )

        assert error.value.code == "OBSERVATION_NOT_CLOSED"
        assert error.value.kind is PaperErrorKind.REFUSED


class TestFrozenProductSnapshot:
    async def test_changing_current_contract_metadata_does_not_touch_an_existing_position(
        self, database: Database
    ) -> None:
        position_id = await closed_position(database, "temporal-key-00000005")
        before = (await service(database, clock=LATER).get(position_id)).stored

        # The provider now answers with a five-times larger multiplier.
        changed = service(database, clock=LATER, contract=paper_contract(multiplier=verified("50")))
        after = (await changed.get(position_id)).stored

        assert after.projection == before.projection
        assert after.projection.realized_gross == D("40.00")  # not 200
        assert after.product_snapshot == before.product_snapshot
        assert json.dumps(after.product_snapshot).count('"10"') >= 1

    async def test_a_later_write_also_uses_the_frozen_snapshot(self, database: Database) -> None:
        svc = service(database, clock=LATER)
        created = await svc.create(create_command("temporal-key-00000006"))
        position_id = created.stored.position_id
        await svc.observe(position_id, bars_csv(ENTRY), "entry.csv")

        changed = service(database, clock=LATER, contract=paper_contract(multiplier=verified("50")))
        view = await changed.observe(position_id, bars_csv(TARGET_ONE), "t1.csv")

        # Target 1 is +4 points on 2 units. At the frozen multiplier of 10 that
        # is 80; at the provider's current 50 it would have been 400.
        assert view.stored.projection.realized_gross == D("80.00")

    async def test_a_new_position_may_use_the_changed_metadata(self, database: Database) -> None:
        changed = service(database, clock=LATER, contract=paper_contract(multiplier=verified("50")))
        created = await changed.create(create_command("temporal-key-00000007"))
        view = await changed.observe(
            created.stored.position_id, bars_csv(ENTRY, TARGET_ONE), "bars.csv"
        )

        assert view.stored.projection.realized_gross == D("400.00")


class TestPersistedSimulationPolicy:
    async def test_an_existing_position_keeps_its_own_policy(self, database: Database) -> None:
        """Stored HALT and per-unit fees survive, though the defaults differ."""
        assert SimulationPolicy().same_bar is SameBarPolicy.STOP_FIRST
        assert SimulationPolicy().fees.mode is FeeMode.NOT_MODELLED

        stored_policy = SimulationPolicy(
            same_bar=SameBarPolicy.HALT,
            slippage=SlippagePolicy(mode=SlippageMode.FIXED_POINTS, points=D("0.25")),
            fees=FeePolicy(mode=FeeMode.USER_DEFINED_PER_UNIT, per_unit=D("2")),
        )
        svc = service(database, clock=LATER)
        created = await svc.create(create_command("temporal-key-00000008", policy=stored_policy))
        position_id = created.stored.position_id

        view = await svc.observe(position_id, bars_csv(ENTRY), "entry.csv")
        # Adverse slippage of 0.25 on a market-style entry: 100 -> 100.25.
        assert view.stored.projection.entry_fill_price == D("100.25")
        assert view.stored.projection.fees_total == D("8")

        # One bar that reaches both the stop and a target. The stored policy is
        # HALT, so nothing is filled; the current default would have exited.
        halted = await svc.observe(
            position_id, bars_csv((1, "101", "104.50", "97.50", "99")), "ambiguous.csv"
        )

        assert halted.stored.projection.state == "AMBIGUOUS_HALTED"
        assert halted.stored.projection.remaining == 4
        assert halted.stored.projection.rules_version == "paper-sim/v1"

        reread = (await service(database, clock=MUCH_LATER).get(position_id)).stored
        assert reread.projection == halted.stored.projection


class TestProjectionIsNotAuthority:
    """The row summarising a position is derived; the ledger is the record."""

    @pytest.mark.parametrize(
        "corruption",
        [
            "UPDATE paper_positions SET realized_gross = 999999.99 WHERE id = :id",
            "UPDATE paper_positions SET state = 'OPEN' WHERE id = :id",
            "UPDATE paper_positions SET remaining = 4 WHERE id = :id",
        ],
        ids=["money", "state", "quantity"],
    )
    async def test_a_projection_edited_outside_the_application_is_not_served(
        self, database: Database, corruption: str
    ) -> None:
        position_id = await closed_position(database, "temporal-key-00000009")

        async with database.engine.begin() as connection:
            await connection.execute(text(corruption), {"id": position_id})

        with pytest.raises(PaperServiceError) as error:
            await service(database, clock=LATER).get(position_id)

        assert error.value.code == "PROJECTION_DIVERGED"
        assert error.value.kind is PaperErrorKind.UNAVAILABLE

    @pytest.mark.parametrize(
        "bars",
        [
            (ENTRY,),
            (ENTRY, TARGET_ONE),
            (ENTRY, TARGET_ONE, STOP_ON_REST),
        ],
        ids=["open", "partially-closed", "closed"],
    )
    async def test_the_projection_matches_the_ledger_in_every_state(
        self, database: Database, bars: tuple[tuple[int, str, str, str, str], ...]
    ) -> None:
        svc = service(database, clock=LATER)
        created = await svc.create(create_command("temporal-key-00000010"))
        position_id = created.stored.position_id
        await svc.observe(position_id, bars_csv(*bars), "bars.csv")

        view = await svc.get(position_id)  # verifies against the ledger

        assert view.stored.projection.event_count == len(view.stored.events)

    async def test_a_cancelled_and_a_halted_position_also_verify(self, database: Database) -> None:
        svc = service(database, clock=LATER)
        cancelled = await svc.create(create_command("temporal-key-00000011"))
        await svc.cancel(cancelled.stored.position_id)

        halted = await svc.create(
            create_command(
                "temporal-key-00000012",
                policy=SimulationPolicy(same_bar=SameBarPolicy.HALT),
            )
        )
        await svc.observe(
            halted.stored.position_id,
            bars_csv(ENTRY, (1, "101", "104.50", "97.50", "99")),
            "bars.csv",
        )

        assert (await svc.get(cancelled.stored.position_id)).stored.projection.state == "CANCELLED"
        assert (
            await svc.get(halted.stored.position_id)
        ).stored.projection.state == "AMBIGUOUS_HALTED"

    async def test_the_next_write_repairs_the_projection_from_the_ledger(
        self, database: Database
    ) -> None:
        svc = service(database, clock=LATER)
        created = await svc.create(create_command("temporal-key-00000013"))
        position_id = created.stored.position_id
        await svc.observe(position_id, bars_csv(ENTRY), "entry.csv")

        async with database.engine.begin() as connection:
            await connection.execute(
                text("UPDATE paper_positions SET realized_gross = 4242 WHERE id = :id"),
                {"id": position_id},
            )

        repaired = await svc.observe(position_id, bars_csv(TARGET_ONE), "t1.csv")

        assert repaired.stored.projection.realized_gross == D("80.00")
        assert (await svc.get(position_id)).stored.projection.realized_gross == D("80.00")


class TestIdempotencyKeyIsNotPublished:
    async def test_the_raw_key_never_appears_in_the_response(self, database: Database) -> None:
        key = "temporal-key-secret-000001"
        svc = service(database, clock=LATER)
        created = await svc.create(create_command(key))

        body = json.dumps(detail(created.stored, replayed=created.replayed).model_dump(mode="json"))

        assert key not in body
        assert created.stored.position_id.startswith("PP-")
        assert key not in created.stored.position_id

    async def test_the_key_is_not_in_a_conflict_message(self, database: Database) -> None:
        key = "temporal-key-secret-000002"
        svc = service(database, clock=LATER)
        await svc.create(create_command(key))

        with pytest.raises(PaperServiceError) as error:
            await svc.create(create_command(key, quantity=3))

        assert error.value.kind is PaperErrorKind.CONFLICT
        assert key not in str(error.value)
        assert key not in error.value.detail
