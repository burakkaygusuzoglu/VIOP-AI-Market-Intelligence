"""Grid alignment: the derived levels move, and only ever the wrong way.

Phase 3 refuses an off-grid stop rather than round it, which is right for a
level a person typed. A level derived from an ATR reading has to be made
executable by somebody, and these tests pin down that it is made *worse* every
time - because the rounding that would flatter a backtest is rounding the stop
closer to the entry, which shrinks the measured risk and inflates the size.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.domain.backtest.levels import align_intent, away_from
from app.domain.backtest.policy import EntryIntent, TargetLevel
from app.domain.common.enums import Direction

TICK = Decimal("0.25")


def intent(
    direction: Direction,
    entry: str,
    stop: str,
    *targets: str,
    quantity: int = 1,
) -> EntryIntent:
    return EntryIntent(
        direction=direction,
        intended_entry=Decimal(entry),
        stop=Decimal(stop),
        targets=tuple(TargetLevel(price=Decimal(price), quantity=1) for price in targets),
        quantity=quantity,
    )


class TestAwayFrom:
    @pytest.mark.parametrize(
        ("entry", "price", "expected"),
        [
            # Below the entry: floored, so the distance grows.
            ("100", "97.10", "97.00"),
            ("100", "99.99", "99.75"),
            # Above the entry: ceiled, so the distance grows.
            ("100", "103.10", "103.25"),
            ("100", "100.01", "100.25"),
        ],
    )
    def test_an_off_grid_price_moves_away_from_the_entry(
        self, entry: str, price: str, expected: str
    ) -> None:
        assert away_from(Decimal(entry), Decimal(price), TICK) == Decimal(expected)

    @pytest.mark.parametrize("price", ["97.00", "97.25", "103.50", "100.00"])
    def test_a_price_already_on_the_grid_is_untouched(self, price: str) -> None:
        assert away_from(Decimal("100"), Decimal(price), TICK) == Decimal(price)

    def test_a_price_level_with_the_entry_is_left_for_the_risk_engine(self) -> None:
        """Widening a zero distance here would hide the defect from Phase 3."""
        assert away_from(Decimal("100.10"), Decimal("100.10"), TICK) == Decimal("100.10")

    def test_alignment_is_idempotent(self) -> None:
        once = away_from(Decimal("100"), Decimal("97.10"), TICK)

        assert away_from(Decimal("100"), once, TICK) == once

    @pytest.mark.parametrize("increment", ["0", "-0.25"])
    def test_a_non_positive_increment_is_refused(self, increment: str) -> None:
        with pytest.raises(ValueError, match="increment must be positive"):
            away_from(Decimal("100"), Decimal("97.1"), Decimal(increment))

    @pytest.mark.parametrize("cents", range(0, 400, 7))
    def test_the_distance_from_the_entry_never_shrinks(self, cents: int) -> None:
        """The property that matters, swept rather than asserted on one case."""
        entry = Decimal("100")
        for price in (entry - Decimal(cents) / 100, entry + Decimal(cents) / 100):
            aligned = away_from(entry, price, TICK)

            assert abs(aligned - entry) >= abs(price - entry)
            assert aligned % TICK == 0 or aligned == entry


class TestAlignIntent:
    def test_a_long_stop_falls_and_its_target_rises(self) -> None:
        aligned = align_intent(intent(Direction.LONG, "100", "97.10", "106.10"), TICK)

        assert aligned.intent.stop == Decimal("97.00")
        assert aligned.intent.targets[0].price == Decimal("106.25")

    def test_a_short_stop_rises_and_its_target_falls(self) -> None:
        aligned = align_intent(intent(Direction.SHORT, "100", "102.10", "93.90"), TICK)

        assert aligned.intent.stop == Decimal("102.25")
        assert aligned.intent.targets[0].price == Decimal("93.75")

    def test_the_intended_entry_is_never_moved(self) -> None:
        """An entry is a price the market printed, not a number we derived."""
        original = intent(Direction.LONG, "100.13", "97.10", "106.10")

        assert align_intent(original, TICK).intent.intended_entry == Decimal("100.13")

    def test_quantities_and_direction_survive_alignment(self) -> None:
        original = intent(Direction.SHORT, "100", "102.10", "93.90", quantity=3)

        aligned = align_intent(original, TICK).intent

        assert aligned.quantity == 3
        assert aligned.direction is Direction.SHORT
        assert [level.quantity for level in aligned.targets] == [1]

    def test_every_target_is_aligned_not_just_the_first(self) -> None:
        aligned = align_intent(intent(Direction.LONG, "100", "97.10", "103.10", "106.10"), TICK)

        assert [level.price for level in aligned.intent.targets] == [
            Decimal("103.25"),
            Decimal("106.25"),
        ]

    def test_what_moved_is_named(self) -> None:
        aligned = align_intent(intent(Direction.LONG, "100", "97.10", "103.00", "106.10"), TICK)

        assert aligned.moved == ("stop", "target 2")
        assert "stop, target 2 moved away from the entry" in aligned.note

    def test_levels_already_on_the_grid_report_no_movement(self) -> None:
        aligned = align_intent(intent(Direction.LONG, "100", "97.00", "106.00"), TICK)

        assert aligned.moved == ()
        assert aligned.note == "levels already sit on the 0.25 grid"

    def test_aligning_twice_changes_nothing(self) -> None:
        once = align_intent(intent(Direction.LONG, "100", "97.10", "106.10"), TICK)

        twice = align_intent(once.intent, TICK)

        assert twice.intent == once.intent
        assert twice.moved == ()
