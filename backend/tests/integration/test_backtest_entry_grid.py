"""No position is ever opened at a price the product could not quote (Phase 12).

Three different prices are involved in one simulated entry, and they are not
the same price:

* the **proposed** entry, which the strategy computed;
* the **executed** fill, which Phase 9 takes from the next bar's *open*;
* the **protective levels**, which the runner aligns onto the grid.

Phase 3 has always checked the proposed entry. Nothing checked the executed
one - so a dataset one tick out of step with the product would have produced
positions opened, stopped and closed at prices that product cannot quote, with
every derived figure looking perfectly ordinary. That gap is closed by refusing
the run, not by rounding the candle: a historical open is an authoritative
market price, the dataset is immutable, and moving it would fabricate a trade
at a price nobody paid.

Both directions are tested throughout, because rounding bugs are directional
and a LONG-only suite would miss half of them.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import pytest

from app.adapters.persistence.database import Database
from app.application.backtest.ports import BacktestPosition, StoredRun
from app.application.backtest.service import BacktestErrorKind, BacktestServiceError
from app.domain.backtest.levels import on_grid
from app.domain.backtest.policy import StrategyDecision
from app.domain.backtest.run import DecisionOutcome
from app.domain.common.enums import Direction, Timeframe
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import FuturesContract
from app.domain.paper.rules import SimulationPolicy, SlippageMode, SlippagePolicy
from app.domain.risk.sizing import RiskMode, RiskPolicy
from tests.factories_paper import paper_contract
from tests.factories_replay import Row
from tests.integration.backtest_support import (
    ScriptedStrategy,
    bars,
    flat_rows,
    intent,
    runner,
    scripted_request,
    seed_dataset,
)

pytestmark = pytest.mark.integration

M5_ONLY = (Timeframe.M5,)
TICK = Decimal("0.25")
"""The fixture contract's verified tick size."""


def tick_of(value: str, status: VerificationStatus) -> FuturesContract:
    return paper_contract(
        tick_size=VerifiedValue(value=Decimal(value), status=status, source="test")
    )


VERIFIED = VerificationStatus.VERIFIED_CURRENT_FACT
UNVERIFIED = VerificationStatus.DEVELOPMENT_DEFAULT


def key_for(name: str, direction: Direction) -> str:
    """An attempt key that is unique per case and long enough to be accepted."""
    return f"entrygrid-{name}-{direction.value.lower()}"


async def attempt(
    database: Database,
    rows: Sequence[Row],
    decision: StrategyDecision,
    *,
    key: str,
    contract: FuturesContract | None = None,
    simulation: SimulationPolicy | None = None,
) -> StoredRun:
    """One scripted entry attempt over one dataset."""
    dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
    strategy = ScriptedStrategy(plan={1: decision})
    service = runner(database, contract=contract)
    return await service.run(
        scripted_request(dataset, strategy, key=key, last=len(rows) - 1, simulation=simulation)
    )


def entry(direction: Direction, *, at: str, stop: str, target: str) -> StrategyDecision:
    return StrategyDecision.enter(
        intent(direction, entry=at, stop=stop, targets=((target, 1),)),
        f"scripted {direction.value} entry at {at}",
    )


def fill_prices(position: BacktestPosition) -> list[Decimal]:
    return [
        Decimal(event.data["fill_price"]) for event in position.events if "fill_price" in event.data
    ]


# ----------------------------------------------------------------------
# 1. A valid entry grid
# ----------------------------------------------------------------------


class TestAValidEntryGrid:
    @pytest.mark.parametrize(
        ("direction", "at", "stop", "target"),
        [
            (Direction.LONG, "100", "97", "106"),
            (Direction.SHORT, "100", "103", "94"),
        ],
    )
    async def test_an_on_grid_entry_opens_a_position(
        self, database: Database, direction: Direction, at: str, stop: str, target: str
    ) -> None:
        stored = await attempt(
            database,
            flat_rows(4),
            entry(direction, at=at, stop=stop, target=target),
            key=key_for("valid", direction),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.intended_entry == Decimal(at)
        assert position.spec.direction is direction

    @pytest.mark.parametrize(
        ("direction", "at", "stop", "target"),
        [
            (Direction.LONG, "100", "97", "106"),
            (Direction.SHORT, "100", "103", "94"),
        ],
    )
    async def test_every_executed_fill_price_sits_on_the_grid(
        self, database: Database, direction: Direction, at: str, stop: str, target: str
    ) -> None:
        """The assertion that matters: the *executed* prices, not the planned ones."""
        rows = bars(
            ("100", "100", "100", "100"),
            ("100", "100", "100", "100"),
            ("100", "107", "93", "100"),
            ("100", "100", "100", "100"),
        )

        stored = await attempt(
            database,
            rows,
            entry(direction, at=at, stop=stop, target=target),
            key=key_for("fills", direction),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        prices = fill_prices(position)
        assert prices
        assert all(on_grid(price, TICK) for price in prices)


# ----------------------------------------------------------------------
# 2. An invalid entry grid
# ----------------------------------------------------------------------


class TestAnInvalidEntryGrid:
    @pytest.mark.parametrize(
        ("direction", "at", "stop", "target"),
        [
            (Direction.LONG, "100.10", "97", "106"),
            (Direction.SHORT, "100.10", "103", "94"),
        ],
    )
    async def test_an_off_grid_entry_never_becomes_a_position(
        self, database: Database, direction: Direction, at: str, stop: str, target: str
    ) -> None:
        stored = await attempt(
            database,
            flat_rows(4),
            entry(direction, at=at, stop=stop, target=target),
            key=key_for("bad", direction),
        )

        assert stored.result is not None
        assert stored.result.positions == ()
        (refused,) = [
            record
            for record in stored.result.decisions
            if record.outcome is DecisionOutcome.REFUSED_BY_RISK
        ]
        assert refused.risk_reason is not None
        assert "not a whole number of 0.25 ticks" in refused.risk_reason
        assert "were not rounded to the grid" in refused.risk_reason

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    async def test_aligning_the_stop_cannot_rescue_an_off_grid_entry(
        self, database: Database, direction: Direction
    ) -> None:
        """Alignment moves the stop. It must not launder the entry.

        Both the entry and the stop are off the grid here. The stop is aligned,
        the entry is not - and the whole attempt is still refused, so alignment
        can never be the reason an invalid entry became tradeable.
        """
        levels = (
            ("100.10", "97.10", "106.10")
            if direction is Direction.LONG
            else ("100.10", "102.10", "93.90")
        )
        stored = await attempt(
            database,
            flat_rows(4),
            entry(direction, at=levels[0], stop=levels[1], target=levels[2]),
            key=key_for("launder", direction),
        )

        assert stored.result is not None
        assert stored.result.positions == ()
        (refused,) = [
            record
            for record in stored.result.decisions
            if record.outcome is DecisionOutcome.REFUSED_BY_RISK
        ]
        assert refused.risk_reason is not None
        assert "entry 100.10" in refused.risk_reason
        # The stop was aligned, and said so - and it changed nothing.
        assert "moved away from the entry" in refused.reason


# ----------------------------------------------------------------------
# 3. Valid aligned stop and target
# ----------------------------------------------------------------------


class TestValidAlignedLevels:
    @pytest.mark.parametrize(
        ("direction", "stop", "target", "expected_stop", "expected_target"),
        [
            (Direction.LONG, "97.10", "106.10", "97.00", "106.25"),
            (Direction.SHORT, "102.10", "93.90", "102.25", "93.75"),
        ],
    )
    async def test_aligned_levels_are_on_the_grid_and_further_out(
        self,
        database: Database,
        direction: Direction,
        stop: str,
        target: str,
        expected_stop: str,
        expected_target: str,
    ) -> None:
        stored = await attempt(
            database,
            flat_rows(4),
            entry(direction, at="100", stop=stop, target=target),
            key=key_for("aligned", direction),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.stop == Decimal(expected_stop)
        assert position.spec.targets[0].price == Decimal(expected_target)
        assert on_grid(position.spec.stop, TICK)
        assert on_grid(position.spec.targets[0].price, TICK)
        # Phase 3 geometry: the stop is still on the losing side of the entry.
        if direction is Direction.LONG:
            assert position.spec.stop < position.spec.intended_entry
            assert position.spec.targets[0].price > position.spec.intended_entry
        else:
            assert position.spec.stop > position.spec.intended_entry
            assert position.spec.targets[0].price < position.spec.intended_entry

    @pytest.mark.parametrize(
        ("direction", "stop"),
        [(Direction.LONG, "97.10"), (Direction.SHORT, "102.10")],
    )
    async def test_alignment_never_shrinks_the_modelled_stop_distance(
        self, database: Database, direction: Direction, stop: str
    ) -> None:
        target = "106.10" if direction is Direction.LONG else "93.90"
        stored = await attempt(
            database,
            flat_rows(4),
            entry(direction, at="100", stop=stop, target=target),
            key=key_for("distance", direction),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        planned = abs(Decimal("100") - Decimal(stop))
        final = abs(position.spec.intended_entry - position.spec.stop)
        assert final >= planned


# ----------------------------------------------------------------------
# 4. An increment the dataset does not obey
# ----------------------------------------------------------------------


class TestAnIncrementTheDataDoesNotObey:
    async def test_a_dataset_off_the_products_grid_refuses_the_whole_run(
        self, database: Database
    ) -> None:
        """0.10 steps against a 0.25 tick. No candle is rounded to fit."""
        rows = bars(
            ("100.10", "100.10", "100.10", "100.10"),
            ("100.20", "100.20", "100.20", "100.20"),
            ("100.30", "100.30", "100.30", "100.30"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database)

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="grid-dataset-0001", last=2)
            )

        assert raised.value.kind is BacktestErrorKind.REFUSED
        assert raised.value.code == "DATASET_OFF_PRODUCT_GRID"
        assert "not rounded to make one possible" in raised.value.detail

    async def test_a_coarser_verified_tick_also_refuses(self, database: Database) -> None:
        """The fixture's 0.25 prices are not whole numbers of a 1.00 tick."""
        rows = bars(
            ("100.25", "100.25", "100.25", "100.25"), ("100.50", "100.50", "100.50", "100.50")
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database, contract=tick_of("1.00", VERIFIED))

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="grid-coarse-00001", last=1)
            )

        assert raised.value.code == "DATASET_OFF_PRODUCT_GRID"
        assert "1.00 ticks" in raised.value.detail

    async def test_the_refusal_names_the_first_offending_candle(self, database: Database) -> None:
        rows = [*flat_rows(3), *bars(("100.10", "100.10", "100.10", "100.10"), start=3)]
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database)

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="grid-which-000001", last=3)
            )

        assert rows[3].open_time.isoformat() in raised.value.detail
        assert "100.10" in raised.value.detail

    async def test_no_run_row_is_created_for_a_refused_dataset(self, database: Database) -> None:
        """The request can never be valid, so it leaves nothing behind."""
        from sqlalchemy import text

        rows = bars(
            ("100.10", "100.10", "100.10", "100.10"),
            ("100.10", "100.10", "100.10", "100.10"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database)

        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="grid-norow-000001", last=1)
            )

        async with database.engine.connect() as connection:
            runs = await connection.execute(text("SELECT count(*) FROM backtest_runs"))
        assert runs.scalar_one() == 0

    async def test_the_dataset_is_left_exactly_as_it_was(self, database: Database) -> None:
        """A refusal must not be a rewrite."""
        from sqlalchemy import text

        rows = bars(
            ("100.10", "100.10", "100.10", "100.10"),
            ("100.10", "100.10", "100.10", "100.10"),
        )
        dataset = await seed_dataset(database, rows, timeframes=M5_ONLY)
        service = runner(database)

        with pytest.raises(BacktestServiceError):
            await service.run(
                scripted_request(dataset, ScriptedStrategy(), key="grid-intact-00001", last=1)
            )

        async with database.engine.connect() as connection:
            stored = await connection.execute(
                text("SELECT open, high, low, close FROM replay_candles LIMIT 1")
            )
        assert [str(value) for value in stored.one()] == [
            "100.10",
            "100.10",
            "100.10",
            "100.10",
        ]
        assert dataset.total_rows == 2


# ----------------------------------------------------------------------
# 5. A missing or unverified increment
# ----------------------------------------------------------------------


class TestAMissingVerifiedIncrement:
    async def test_an_unverified_tick_cannot_convict_a_dataset(self, database: Database) -> None:
        """An unconfirmed grid is not evidence that a price is wrong.

        The run is allowed to proceed - and every entry is then refused by
        Phase 3 anyway, because executability cannot be confirmed either. The
        refusal comes from the engine that owns the question, not from a guess
        made here.
        """
        rows = bars(
            ("100.10", "100.10", "100.10", "100.10"),
            ("100.10", "100.10", "100.10", "100.10"),
            ("100.10", "100.10", "100.10", "100.10"),
        )

        stored = await attempt(
            database,
            rows,
            entry(Direction.LONG, at="100.10", stop="97.10", target="106.10"),
            key="grid-unverified-001",
            contract=tick_of("0.25", UNVERIFIED),
        )

        assert stored.result is not None
        assert stored.result.positions == ()
        (refused,) = [
            record
            for record in stored.result.decisions
            if record.outcome is DecisionOutcome.REFUSED_BY_RISK
        ]
        assert refused.risk_reason is not None
        assert "tick size" in refused.risk_reason

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    async def test_an_unverified_tick_aligns_nothing(
        self, database: Database, direction: Direction
    ) -> None:
        """No tick is invented. The levels reach Phase 3 exactly as computed."""
        levels = (
            ("100", "97.10", "106.10")
            if direction is Direction.LONG
            else ("100", "102.10", "93.90")
        )
        stored = await attempt(
            database,
            flat_rows(4),
            entry(direction, at=levels[0], stop=levels[1], target=levels[2]),
            key=key_for("noinvent", direction),
            contract=tick_of("0.25", UNVERIFIED),
        )

        assert stored.result is not None
        assert stored.result.positions == ()
        (refused,) = [
            record
            for record in stored.result.decisions
            if record.outcome is DecisionOutcome.REFUSED_BY_RISK
        ]
        assert "moved away from the entry" not in refused.reason


# ----------------------------------------------------------------------
# 6. Slippage cannot move a fill off the grid either
# ----------------------------------------------------------------------


class TestSlippageStaysOnTheGrid:
    async def test_a_slippage_that_is_not_a_whole_tick_is_refused(self, database: Database) -> None:
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        service = runner(database)

        with pytest.raises(BacktestServiceError) as raised:
            await service.run(
                scripted_request(
                    dataset,
                    ScriptedStrategy(),
                    key="grid-slip-bad-0001",
                    last=3,
                    simulation=SimulationPolicy(
                        slippage=SlippagePolicy(
                            mode=SlippageMode.FIXED_POINTS, points=Decimal("0.10")
                        )
                    ),
                )
            )

        assert raised.value.code == "SLIPPAGE_OFF_PRODUCT_GRID"
        assert "whole ticks" in raised.value.detail

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    async def test_a_whole_tick_slippage_still_lands_on_the_grid(
        self, database: Database, direction: Direction
    ) -> None:
        levels = ("100", "97", "106") if direction is Direction.LONG else ("100", "103", "94")
        stored = await attempt(
            database,
            flat_rows(4),
            entry(direction, at=levels[0], stop=levels[1], target=levels[2]),
            key=key_for("slipok", direction),
            simulation=SimulationPolicy(
                slippage=SlippagePolicy(mode=SlippageMode.FIXED_POINTS, points=TICK)
            ),
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        prices = fill_prices(position)
        assert prices
        assert all(on_grid(price, TICK) for price in prices)
        # And it is adverse: a long pays more than the open.
        expected = Decimal("100.25") if direction is Direction.LONG else Decimal("99.75")
        assert prices[0] == expected


# ----------------------------------------------------------------------
# 7. Risk sizing uses the level that will actually be placed
# ----------------------------------------------------------------------


class TestSizingUsesTheFinalStop:
    """The quantity must follow the *aligned* stop, not the planned one.

    Alignment widens the stop, which raises the loss per unit, which lowers the
    number of units risk will allow. Sizing on the planned stop would approve a
    position slightly larger than the risk budget actually permits - a small,
    permanent, always-in-the-same-direction overstatement of size.

    The numbers are chosen so the two answers differ:

        multiplier 10, entry 100, planned stop 97.10, aligned stop 97.00
        planned  distance 2.90 -> 29.00 per unit -> floor(290/29) = 10
        aligned  distance 3.00 -> 30.00 per unit -> floor(290/30) =  9
    """

    @pytest.mark.parametrize(
        ("direction", "stop", "target"),
        [
            (Direction.LONG, "97.10", "130"),
            (Direction.SHORT, "102.90", "70"),
        ],
    )
    async def test_the_approved_quantity_follows_the_aligned_stop(
        self, database: Database, direction: Direction, stop: str, target: str
    ) -> None:
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        strategy = ScriptedStrategy(
            plan={
                1: StrategyDecision.enter(
                    intent(
                        direction,
                        entry="100",
                        stop=stop,
                        targets=((target, 100),),
                        quantity=100,
                    ),
                    "scripted sizing probe",
                )
            }
        )
        service = runner(database)

        stored = await service.run(
            scripted_request(
                dataset,
                strategy,
                key=key_for("sizing", direction),
                last=3,
                risk=RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("290")),
            )
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.quantity == 9
        assert position.approval.allowed_units == 9

    @pytest.mark.parametrize(
        ("direction", "stop", "aligned"),
        [
            (Direction.LONG, "97.10", "97.00"),
            (Direction.SHORT, "102.90", "103.00"),
        ],
    )
    async def test_the_stop_the_position_carries_is_the_one_sizing_saw(
        self, database: Database, direction: Direction, stop: str, aligned: str
    ) -> None:
        """No path stores one stop and sizes against another."""
        dataset = await seed_dataset(database, flat_rows(4), timeframes=M5_ONLY)
        target = "130" if direction is Direction.LONG else "70"
        strategy = ScriptedStrategy(
            plan={
                1: StrategyDecision.enter(
                    intent(
                        direction, entry="100", stop=stop, targets=((target, 100),), quantity=100
                    ),
                    "scripted sizing probe",
                )
            }
        )

        stored = await runner(database).run(
            scripted_request(
                dataset,
                strategy,
                key=key_for("samestop", direction),
                last=3,
                risk=RiskPolicy(mode=RiskMode.FIXED, fixed_risk=Decimal("290")),
            )
        )

        assert stored.result is not None
        (position,) = stored.result.positions
        assert position.spec.stop == Decimal(aligned)
        # 9 units x 3.00 points x 10 multiplier = 270, within the 290 budget.
        # Sizing on the unaligned 2.90 would have approved 10, risking 300.
        exposure = (
            abs(position.spec.intended_entry - position.spec.stop)
            * Decimal(position.spec.quantity)
            * Decimal("10")
        )
        assert exposure <= Decimal("290")
