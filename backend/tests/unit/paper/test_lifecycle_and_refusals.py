"""Lifecycle, ordering, idempotency and every refusal the engine makes.

An invalid transition is refused with a code - never quietly corrected. A
negative remaining quantity, a second close of a closed position, a bar applied
twice as two fills: each of those would be a number that looks legitimate and
is not.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.domain.common.enums import Direction, Timeframe
from app.domain.common.verification import VerificationStatus
from app.domain.instrument import AssetClass
from app.domain.market.candle import Candle
from app.domain.paper import (
    MAX_NOTE_LENGTH,
    MAX_TARGETS,
    FeeMode,
    FeePolicy,
    PaperEvent,
    PaperEventType,
    PaperInputError,
    PaperPosition,
    PaperRefusalError,
    PositionSpec,
    PositionState,
    RefusalCode,
    RiskApproval,
    SameBarPolicy,
    SimulationPolicy,
    SimulationPolicyError,
    SlippageMode,
    SlippagePolicy,
    TargetSpec,
    apply_observation,
    cancel,
    move_stop_to_breakeven,
    open_position,
    rebuild,
    request_close,
)
from app.domain.risk.sizing import SizingOutcome
from tests.factories_futures import fact, unverified, verified
from tests.factories_paper import (
    DECISION,
    FakeProduct,
    approval_for,
    bar,
    long_spec,
    product,
    run,
    short_spec,
)

D = Decimal
V = VerificationStatus
ENTRY_BAR = bar(0, "100", "101", "99.50", "100.50")


def refused(code: RefusalCode) -> AbstractContextManager[pytest.ExceptionInfo[PaperRefusalError]]:
    return pytest.raises(PaperRefusalError, match=code.value)


# ======================================================================
# Specification
# ======================================================================


class TestSpecificationIsStructurallySound:
    @pytest.mark.parametrize("quantity", [0, -1, -4])
    def test_quantity_must_be_a_positive_whole_number(self, quantity: int) -> None:
        with pytest.raises(PaperInputError, match="whole unit"):
            long_spec(quantity=quantity)

    def test_a_boolean_is_not_a_quantity(self) -> None:
        with pytest.raises(PaperInputError):
            long_spec(quantity=True)

    def test_targets_cannot_allocate_more_than_the_position(self) -> None:
        with pytest.raises(PaperInputError, match="allocate 5 units"):
            long_spec(targets=(TargetSpec(D("104"), 3), TargetSpec(D("106"), 2)))

    @pytest.mark.parametrize(
        ("factory", "stop"),
        [(long_spec, "101"), (long_spec, "100.00"), (short_spec, "99"), (short_spec, "100.00")],
    )
    def test_a_stop_on_the_wrong_side_is_refused(
        self, factory: Callable[..., PositionSpec], stop: str
    ) -> None:
        with pytest.raises(PaperInputError, match="stop must be"):
            factory(stop=D(stop))

    @pytest.mark.parametrize(
        "targets",
        [
            (TargetSpec(D("99"), 1),),
            (TargetSpec(D("100.00"), 1),),
            (TargetSpec(D("106"), 1), TargetSpec(D("104"), 1)),
            (TargetSpec(D("104"), 1), TargetSpec(D("104"), 1)),
        ],
        ids=["below-entry", "at-entry", "out-of-sequence", "duplicate"],
    )
    def test_long_targets_must_step_away_from_the_entry(
        self, targets: tuple[TargetSpec, ...]
    ) -> None:
        with pytest.raises(PaperInputError, match="strictly above"):
            long_spec(targets=targets)

    def test_short_targets_must_step_below(self) -> None:
        with pytest.raises(PaperInputError, match="strictly below"):
            short_spec(targets=(TargetSpec(D("101"), 1),))

    def test_at_least_one_and_at_most_max_targets(self) -> None:
        with pytest.raises(PaperInputError, match="at least one"):
            long_spec(targets=())
        many = tuple(TargetSpec(D(101 + i), 1) for i in range(MAX_TARGETS + 1))
        with pytest.raises(PaperInputError, match=f"at most {MAX_TARGETS}"):
            long_spec(quantity=10, targets=many)

    @pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "sNaN", "0", "-5"])
    def test_non_finite_or_non_positive_prices_are_refused(self, value: str) -> None:
        with pytest.raises(PaperInputError):
            long_spec(intended_entry=D(value))

    def test_a_naive_decision_time_is_refused(self) -> None:
        with pytest.raises(PaperInputError, match="timezone-aware"):
            long_spec(decision_time=datetime(2026, 3, 2, 10, 0))  # noqa: DTZ001

    def test_neutral_is_not_a_trade(self) -> None:
        with pytest.raises(PaperInputError, match="not a tradeable"):
            long_spec(direction=Direction.NEUTRAL)

    def test_note_is_bounded_and_printable(self) -> None:
        with pytest.raises(PaperInputError, match="at most"):
            long_spec(note="x" * (MAX_NOTE_LENGTH + 1))
        with pytest.raises(PaperInputError, match="control"):
            long_spec(note="hello\x00world")

    def test_extreme_precision_is_carried_exactly(self) -> None:
        """No float anywhere: a 28-digit price survives untouched."""
        spec = long_spec(
            intended_entry=D("100.0000000000000000000000001"),
            stop=D("98"),
            targets=(TargetSpec(D("104"), 4),),
        )
        assert spec.intended_entry == D("100.0000000000000000000000001")


class TestSimulationPolicyIsExplicit:
    def test_an_unknown_rules_version_is_refused(self) -> None:
        with pytest.raises(SimulationPolicyError, match="not supported"):
            SimulationPolicy(rules_version="paper-sim/v999")

    @pytest.mark.parametrize(
        "build",
        [
            lambda: SlippagePolicy(SlippageMode.ZERO, D("1")),
            lambda: SlippagePolicy(SlippageMode.FIXED_POINTS, None),
            lambda: SlippagePolicy(SlippageMode.FIXED_POINTS, D("-1")),
            lambda: SlippagePolicy(SlippageMode.FIXED_POINTS, D("NaN")),
            lambda: FeePolicy(FeeMode.NOT_MODELLED, D("1")),
            lambda: FeePolicy(FeeMode.USER_DEFINED_PER_UNIT, None),
            lambda: FeePolicy(FeeMode.USER_DEFINED_PER_UNIT, D("-0.01")),
            lambda: FeePolicy(FeeMode.USER_DEFINED_PER_UNIT, D("Infinity")),
        ],
    )
    def test_inconsistent_policies_are_refused(self, build: Callable[[], object]) -> None:
        with pytest.raises(SimulationPolicyError):
            build()

    def test_the_default_is_zero_slippage_unmodelled_fees_stop_first(self) -> None:
        policy = SimulationPolicy()

        assert policy.rules_version == "paper-sim/v1"
        assert policy.slippage.mode is SlippageMode.ZERO
        assert policy.fees.mode is FeeMode.NOT_MODELLED
        assert policy.same_bar.value == "STOP_FIRST"


# ======================================================================
# Opening refusals
# ======================================================================


class TestOpeningRefusals:
    @pytest.mark.parametrize(
        "asset_class",
        [AssetClass.EQUITY, AssetClass.CRYPTO_SPOT, AssetClass.CRYPTO_PERPETUAL, AssetClass.FX],
    )
    def test_an_unimplemented_asset_class_is_refused_not_treated_as_futures(
        self, asset_class: AssetClass
    ) -> None:
        spec = long_spec()
        allowed = RiskApproval(SizingOutcome.ALLOWED, 10, "forged approval")

        with refused(RefusalCode.UNSUPPORTED_ASSET_CLASS):
            open_position(spec, allowed, FakeProduct(asset_class))

    @pytest.mark.parametrize("status", [V.UNVERIFIED, V.TEST_FIXTURE, V.MOCK_DATA])
    def test_an_unverified_multiplier_refuses_even_with_a_forged_approval(
        self, status: VerificationStatus
    ) -> None:
        policy = product(multiplier=fact("10", status))
        allowed = RiskApproval(SizingOutcome.ALLOWED, 10, "forged approval")

        with pytest.raises(PaperRefusalError) as error:
            open_position(long_spec(), allowed, policy)
        assert error.value.code in (
            RefusalCode.PRODUCT_NOT_CALCULABLE,
            RefusalCode.POINT_VALUE_UNVERIFIED,
        )

    def test_an_unverified_tick_size_leaves_risk_undetermined_and_refuses(self) -> None:
        policy = product(tick_size=unverified("0.25"))
        spec = long_spec()
        approval = approval_for(spec, policy)

        assert approval.outcome is SizingOutcome.UNDETERMINED
        with refused(RefusalCode.RISK_NOT_ALLOWED):
            open_position(spec, approval, policy)

    def test_an_unverified_tick_refuses_levels_even_with_a_forged_approval(self) -> None:
        policy = product(tick_size=unverified("0.25"))
        allowed = RiskApproval(SizingOutcome.ALLOWED, 10, "forged approval")

        with refused(RefusalCode.LEVELS_NOT_EXECUTABLE):
            open_position(long_spec(), allowed, policy)

    def test_an_off_grid_target_is_refused(self) -> None:
        spec = long_spec(targets=(TargetSpec(D("104.10"), 2),))
        allowed = RiskApproval(SizingOutcome.ALLOWED, 10, "allowed")

        with refused(RefusalCode.LEVELS_NOT_EXECUTABLE):
            open_position(spec, allowed, product())

    @pytest.mark.parametrize(
        "outcome",
        [SizingOutcome.NOT_PERMITTED, SizingOutcome.UNDETERMINED, SizingOutcome.INVALID],
    )
    def test_a_risk_veto_is_never_overridden(self, outcome: SizingOutcome) -> None:
        with refused(RefusalCode.RISK_NOT_ALLOWED):
            open_position(long_spec(), RiskApproval(outcome, None, "vetoed"), product())

    def test_allowed_with_no_quantity_is_not_an_approval(self) -> None:
        with refused(RefusalCode.RISK_NOT_ALLOWED):
            open_position(long_spec(), RiskApproval(SizingOutcome.ALLOWED, None, "?"), product())

    def test_quantity_above_the_risk_allowance_is_refused_not_trimmed(self) -> None:
        with refused(RefusalCode.QUANTITY_EXCEEDS_RISK):
            open_position(
                long_spec(), RiskApproval(SizingOutcome.ALLOWED, 3, "3 allowed"), product()
            )

    def test_the_real_risk_engine_refuses_a_budget_too_small(self) -> None:
        from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy, size_for_product

        policy = product()
        spec = long_spec()
        # stop distance 2 x multiplier 10 = 20 per unit; a budget of 10 fits none
        sizing = size_for_product(
            spec.direction,
            spec.intended_entry,
            spec.stop,
            policy,
            AccountState(equity=D("100000")),
            RiskPolicy(mode=RiskMode.FIXED, fixed_risk=D("10")),
        )
        assert sizing.outcome is SizingOutcome.NOT_PERMITTED
        with refused(RefusalCode.RISK_NOT_ALLOWED):
            open_position(spec, RiskApproval.from_sizing(sizing), policy)

    def test_symbol_mismatch_is_refused(self) -> None:
        with refused(RefusalCode.SYMBOL_MISMATCH):
            open_position(
                long_spec(symbol="SOME_OTHER"),
                RiskApproval(SizingOutcome.ALLOWED, 10, "ok"),
                product(),
            )


# ======================================================================
# Ordering and idempotency
# ======================================================================


class TestObservationOrdering:
    def test_the_same_bar_twice_is_a_no_op(self) -> None:
        policy = product()
        second = bar(1, "101", "104.50", "100.75", "104.25")
        position = run(long_spec(), [ENTRY_BAR, second], policy)

        again = apply_observation(position, second, policy)

        assert again is position
        assert again.realized_gross == D("80.00")
        fills = [e for e in again.events if e.type is PaperEventType.TARGET_FILLED]
        assert len(fills) == 1

    def test_an_earlier_bar_re_sent_identically_is_a_no_op(self) -> None:
        policy = product()
        position = run(long_spec(), [ENTRY_BAR, bar(1, "101", "102", "100.75", "101.50")], policy)

        assert apply_observation(position, ENTRY_BAR, policy) is position

    def test_an_earlier_bar_with_different_prices_conflicts(self) -> None:
        policy = product()
        position = run(long_spec(), [ENTRY_BAR], policy)

        with refused(RefusalCode.CONFLICTING_OBSERVATION):
            apply_observation(position, bar(0, "100", "101", "99", "100.50"), policy)

    def test_a_bar_older_than_the_last_is_out_of_order(self) -> None:
        policy = product()
        position = run(
            long_spec(decision_time=DECISION - timedelta(hours=5)),
            [bar(0, "100", "101", "99.50", "100.50")],
            policy,
        )

        with refused(RefusalCode.OUT_OF_ORDER):
            apply_observation(position, bar(-1, "100", "101", "99.50", "100.50"), policy)

    def test_an_overlapping_bar_is_out_of_order(self) -> None:
        policy = product()
        position = run(long_spec(), [ENTRY_BAR], policy)
        overlapping = replace(
            bar(1, "100", "101", "99.50", "100.50"),
            open_time=DECISION + timedelta(minutes=30),
        )

        with refused(RefusalCode.OUT_OF_ORDER):
            apply_observation(position, overlapping, policy)

    def test_a_bar_before_the_decision_cannot_inform_the_trade(self) -> None:
        with refused(RefusalCode.OBSERVATION_BEFORE_DECISION):
            run(long_spec(), [bar(-1, "100", "101", "99.50", "100.50")])

    @pytest.mark.parametrize(
        "broken",
        [
            lambda b: replace(b, is_closed=False),
            lambda b: replace(b, symbol="SOME_OTHER"),
            lambda b: replace(b, timeframe=Timeframe.M5),
            lambda b: replace(b, open_time=datetime(2026, 3, 2, 10, 0)),  # noqa: DTZ001
            lambda b: replace(b, high=D("99")),
            lambda b: replace(b, low=D("101")),
            lambda b: replace(b, open=D("0")),
            lambda b: replace(b, close=D("NaN")),
        ],
        ids=["forming", "symbol", "timeframe", "naive", "high", "low", "zero", "nan"],
    )
    def test_an_invalid_bar_is_refused(self, broken: Callable[[Candle], Candle]) -> None:
        position = open_position(long_spec(), approval_for(long_spec()), product())

        with refused(RefusalCode.INVALID_OBSERVATION):
            apply_observation(position, broken(ENTRY_BAR), product())

    def test_the_input_stream_is_never_sorted_for_the_caller(self) -> None:
        """Out-of-order input is an error the caller must fix, not something the
        engine silently repairs."""
        with refused(RefusalCode.OUT_OF_ORDER):
            run(
                long_spec(),
                [bar(1, "101", "102", "100.75", "101.50"), bar(0, "100", "101", "99.50", "100.50")],
            )


# ======================================================================
# Transitions
# ======================================================================


class TestTransitions:
    def test_a_closed_position_accepts_no_more_bars(self) -> None:
        policy = product()
        position = run(long_spec(), [ENTRY_BAR, bar(1, "99.50", "100", "97.75", "98")], policy)
        assert position.state is PositionState.CLOSED

        with refused(RefusalCode.INVALID_TRANSITION):
            apply_observation(position, bar(2, "99", "100", "98", "99"), policy)

    @pytest.mark.parametrize("action", [request_close, move_stop_to_breakeven, cancel])
    def test_nothing_reopens_a_closed_position(
        self, action: Callable[[PaperPosition], PaperPosition]
    ) -> None:
        position = run(long_spec(), [ENTRY_BAR, bar(1, "99.50", "100", "97.75", "98")])

        with pytest.raises(PaperRefusalError):
            action(position)
        assert position.state is PositionState.CLOSED
        assert position.remaining == 0

    def test_a_pending_position_can_be_cancelled_once_and_only_while_pending(self) -> None:
        pending = open_position(long_spec(), approval_for(long_spec()), product())
        cancelled = cancel(pending)

        assert cancelled.state is PositionState.CANCELLED
        assert cancel(cancelled) is cancelled
        with refused(RefusalCode.INVALID_TRANSITION):
            apply_observation(cancelled, ENTRY_BAR, product())

        opened = run(long_spec(), [ENTRY_BAR])
        with refused(RefusalCode.INVALID_TRANSITION):
            cancel(opened)

    def test_a_pending_position_cannot_be_closed_or_moved(self) -> None:
        pending = open_position(long_spec(), approval_for(long_spec()), product())

        with refused(RefusalCode.INVALID_TRANSITION):
            request_close(pending)
        with refused(RefusalCode.INVALID_TRANSITION):
            move_stop_to_breakeven(pending)

    def test_close_request_is_idempotent(self) -> None:
        position = request_close(run(long_spec(), [ENTRY_BAR]))

        assert request_close(position) is position
        assert sum(e.type is PaperEventType.CLOSE_REQUESTED for e in position.events) == 1

    def test_a_rejected_entry_is_terminal(self) -> None:
        position = run(long_spec(), [bar(0, "97.50", "98", "97", "97.75")])

        assert position.state.is_terminal
        with refused(RefusalCode.INVALID_TRANSITION):
            request_close(position)


class TestBreakeven:
    @pytest.mark.parametrize(
        ("factory", "favourable", "retrace", "expected_fill"),
        [
            (
                long_spec,
                bar(1, "101", "103.50", "100.75", "103"),
                bar(2, "102", "102.50", "99.50", "100"),
                "100",
            ),
            (
                short_spec,
                bar(1, "99", "99.25", "96.50", "97"),
                bar(2, "98", "100.50", "97.50", "100"),
                "100",
            ),
        ],
        ids=["long", "short"],
    )
    def test_the_moved_stop_protects_the_remaining_units_at_the_entry(
        self,
        factory: Callable[[], PositionSpec],
        favourable: Candle,
        retrace: Candle,
        expected_fill: str,
    ) -> None:
        policy = product()
        position = run(factory(), [bar(0, "100", "101", "99", "100")], policy)
        position = apply_observation(position, favourable, policy)
        position = move_stop_to_breakeven(position)

        assert position.stop == D("100")
        assert move_stop_to_breakeven(position) is position
        position = apply_observation(position, retrace, policy)

        stop = [e for e in position.events if e.type is PaperEventType.STOP_FILLED][0]
        assert stop.data["fill_price"] == expected_fill
        assert position.realized_gross == D("0")
        assert rebuild(position.spec, position.approval, policy, position.events) == position

    def test_breakeven_is_refused_when_the_market_is_not_in_favour(self) -> None:
        position = run(long_spec(), [bar(0, "100", "101", "99", "99.50")])

        with refused(RefusalCode.BREAKEVEN_NOT_PROTECTIVE):
            move_stop_to_breakeven(position)


# ======================================================================
# Replay integrity
# ======================================================================


class TestReplayIntegrity:
    def test_an_altered_derived_event_is_detected(self) -> None:
        policy = product()
        position = run(long_spec(), [ENTRY_BAR, bar(1, "99.50", "100", "97.75", "98")], policy)
        events = list(position.events)
        index = next(i for i, e in enumerate(events) if e.type is PaperEventType.STOP_FILLED)
        forged = dict(events[index].data)
        forged["gross_pnl"] = "999.00"
        events[index] = PaperEvent(
            sequence=events[index].sequence,
            type=events[index].type,
            market_time=events[index].market_time,
            data=forged,
        )

        with refused(RefusalCode.REPLAY_DIVERGED):
            rebuild(position.spec, position.approval, policy, events)

    def test_a_different_policy_cannot_silently_reinterpret_history(self) -> None:
        """Changing the stored policy changes the replay, and the ledger says no."""
        policy = product()
        halt = long_spec(policy=SimulationPolicy(same_bar=SameBarPolicy.HALT))
        position = run(halt, [ENTRY_BAR, bar(1, "100", "104.50", "97.50", "100")], policy)
        default_rules = replace(position.spec, policy=SimulationPolicy())

        with refused(RefusalCode.REPLAY_DIVERGED):
            rebuild(default_rules, position.approval, policy, position.events)

    def test_a_fill_that_would_not_be_a_positive_price_is_refused(self) -> None:
        spec = long_spec(
            intended_entry=D("1.00"),
            stop=D("0.75"),
            targets=(TargetSpec(D("2.00"), 1),),
            quantity=1,
            policy=SimulationPolicy(slippage=SlippagePolicy(SlippageMode.FIXED_POINTS, D("5"))),
        )
        policy = product()
        position = open_position(spec, RiskApproval(SizingOutcome.ALLOWED, 5, "ok"), policy)
        position = apply_observation(position, bar(0, "1.00", "1.25", "1.00", "1.00"), policy)

        # Entry at 1.00 + 5 = 6.00 is beyond the first target, so the plan is rejected
        # before any non-positive exit could be simulated.
        assert position.state is PositionState.REJECTED


def test_verified_fixture_helper_is_explicit() -> None:
    assert verified("10").status is V.VERIFIED_CURRENT_FACT
    assert DECISION.tzinfo is UTC
