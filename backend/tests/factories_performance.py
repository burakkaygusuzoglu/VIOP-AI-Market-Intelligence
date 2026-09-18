"""Builders for Phase 10 performance tests.

Every amount here is TEST_FIXTURE data. The numbers are deliberately small and
round so that each expected metric in the golden tests can be derived by hand
and written into the test as a literal, never by calling the code under test.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.common.enums import Direction, Timeframe
from app.domain.performance import (
    InstrumentIdentity,
    Population,
    PositionOutcome,
    RealizedFill,
)

D = Decimal
START = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
FIXTURE_INSTRUMENT = InstrumentIdentity(symbol="TEST_FIXTURE_FUT", asset_class="FUTURES")
OTHER_INSTRUMENT = InstrumentIdentity(symbol="TEST_FIXTURE_FUT_TWO", asset_class="FUTURES")


def outcome(
    index: int,
    gross: str | Decimal,
    *,
    fees: str | Decimal | None = None,
    population: Population = Population.CLOSED,
    direction: Direction = Direction.LONG,
    timeframe: Timeframe = Timeframe.H1,
    instrument: InstrumentIdentity = FIXTURE_INSTRUMENT,
    quantity: int = 4,
    hours: int | None = None,
    unrealized: str | Decimal | None = None,
    position_id: str | None = None,
) -> PositionOutcome:
    """One completed position with a hand-chosen realized result.

    ``fees=None`` means the position's fee policy was NOT_MODELLED, so its net
    result is unknown - not zero.
    """
    gross_amount = D(gross)
    fees_amount = None if fees is None else D(fees)
    terminal = None
    if population.completed:
        terminal = START + HOUR * (index if hours is None else hours)
    return PositionOutcome(
        position_id=position_id or f"PP-{index:024d}",
        instrument=instrument,
        direction=direction,
        timeframe=timeframe,
        quantity=quantity,
        population=population,
        realized_gross=gross_amount,
        fees_total=fees_amount,
        realized_net=None if fees_amount is None else gross_amount - fees_amount,
        unrealized_gross=None if unrealized is None else D(unrealized),
        fee_mode="NOT_MODELLED" if fees_amount is None else "USER_DEFINED_PER_UNIT",
        decision_time=START,
        entry_time=START if population.entered else None,
        terminal_time=terminal,
        fills=(
            (RealizedFill(amount=gross_amount, fee=fees_amount, market_time=terminal),)
            if terminal is not None
            else ()
        ),
    )


def sequence(*amounts: str, fees: str | None = None) -> list[PositionOutcome]:
    """Completed positions in the order given, one hour apart."""
    return [outcome(index + 1, amount, fees=fees) for index, amount in enumerate(amounts)]


def creation_event(**overrides: Any) -> dict[str, str]:
    """The ledger's POSITION_CREATED payload, as Phase 9 writes it."""
    data = {
        "origin": "USER_CREATED",
        "symbol": "TEST_FIXTURE_FUT",
        "asset_class": "FUTURES",
        "direction": "LONG",
        "quantity": "4",
        "timeframe": "1H",
        "fee_mode": "NOT_MODELLED",
        "fee_per_unit": "",
        "rules_version": "paper-sim/v1",
    }
    data.update({key: str(value) for key, value in overrides.items()})
    return data
