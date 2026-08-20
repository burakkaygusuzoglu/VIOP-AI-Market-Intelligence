"""Port contracts.

Phase 0 ships no market data or AI adapter. These tests verify the interfaces
are structurally usable by checking test doubles against them.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

import app.application.ports as ports_package
from app.application.ports.market_data import HistoricalMarketDataProvider
from app.application.ports.system import ClockPort, DatabaseHealthPort
from app.domain.common.enums import Timeframe
from app.domain.market.candle import Candle
from tests.conftest import FIXED_NOW, FakeClock, FakeDatabaseHealth


class StubHistoricalProvider:
    """Minimal in-memory HistoricalMarketDataProvider used only by tests."""

    def __init__(self, candles: Sequence[Candle]) -> None:
        self._candles = candles

    async def get_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> Sequence[Candle]:
        return [
            candle
            for candle in self._candles
            if candle.symbol == symbol
            and candle.timeframe is timeframe
            and start <= candle.open_time < end
        ]


@pytest.mark.unit
def test_clock_double_satisfies_the_port() -> None:
    assert isinstance(FakeClock(), ClockPort)


@pytest.mark.unit
def test_database_health_double_satisfies_the_port() -> None:
    assert isinstance(FakeDatabaseHealth(), DatabaseHealthPort)


@pytest.mark.unit
def test_historical_provider_double_satisfies_the_port() -> None:
    assert isinstance(StubHistoricalProvider([]), HistoricalMarketDataProvider)


@pytest.mark.unit
async def test_historical_provider_contract_is_usable() -> None:
    candle = Candle(
        symbol="TEST_FIXTURE_SYMBOL",
        timeframe=Timeframe.H1,
        open_time=FIXED_NOW,
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10.5"),
        volume=Decimal("100"),
        is_closed=True,
    )
    provider: HistoricalMarketDataProvider = StubHistoricalProvider([candle])
    result = await provider.get_candles(
        "TEST_FIXTURE_SYMBOL",
        Timeframe.H1,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 3, tzinfo=UTC),
    )
    assert list(result) == [candle]


@pytest.mark.unit
async def test_provider_returns_nothing_outside_the_requested_window() -> None:
    """No bar is invented to fill a requested range."""
    provider = StubHistoricalProvider([])
    result = await provider.get_candles(
        "TEST_FIXTURE_SYMBOL",
        Timeframe.H1,
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 1, 3, tzinfo=UTC),
    )
    assert list(result) == []


@pytest.mark.unit
def test_no_broker_execution_port_exists() -> None:
    """Master spec section 120: real-money execution is out of scope."""
    ports_dir = Path(ports_package.__file__).parent
    module_names = {module.stem for module in ports_dir.glob("*.py")}
    forbidden = {"broker", "execution", "order_execution", "midas"}
    assert not (module_names & forbidden)
