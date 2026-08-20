"""Core vocabulary behaviour."""

from __future__ import annotations

import pytest

from app.domain.common.enums import DataSourcePriority, Timeframe, TradeDecision


@pytest.mark.unit
@pytest.mark.parametrize(
    ("timeframe", "expected_minutes"),
    [
        (Timeframe.M1, 1),
        (Timeframe.M5, 5),
        (Timeframe.M15, 15),
        (Timeframe.M30, 30),
        (Timeframe.H1, 60),
        (Timeframe.H4, 240),
        (Timeframe.D1, 1440),
    ],
)
def test_timeframe_duration(timeframe: Timeframe, expected_minutes: int) -> None:
    assert timeframe.minutes == expected_minutes


@pytest.mark.unit
def test_every_timeframe_declares_a_duration() -> None:
    for timeframe in Timeframe:
        assert timeframe.minutes > 0


@pytest.mark.unit
def test_structured_market_data_outranks_visual_inference() -> None:
    """Master spec section 1: a visual estimate never replaces verified data."""
    assert DataSourcePriority.STRUCTURED_MARKET_DATA.wins_over(
        DataSourcePriority.AI_VISUAL_INFERENCE
    )
    assert DataSourcePriority.STRUCTURED_MARKET_DATA.wins_over(
        DataSourcePriority.SCREENSHOT_EXTRACTED
    )
    assert not DataSourcePriority.AI_VISUAL_INFERENCE.wins_over(
        DataSourcePriority.STRUCTURED_MARKET_DATA
    )


@pytest.mark.unit
def test_data_source_priority_is_strictly_ordered() -> None:
    values = [source.value for source in DataSourcePriority]
    assert values == sorted(values)
    assert len(set(values)) == len(values)


@pytest.mark.unit
def test_wait_and_no_trade_are_valid_decisions() -> None:
    """Master spec sections 25 and 113."""
    decisions = {decision.value for decision in TradeDecision}
    assert {"WAIT", "NO_TRADE"} <= decisions
