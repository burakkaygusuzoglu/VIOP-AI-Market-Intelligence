"""The paper-trading engine (Phase 9).

Pure functions from a position and one input to the next position. No clock, no
randomness, no I/O, no database, no product knowledge beyond a ``ProductPolicy``.
The same inputs in the same order produce the same events, fills and money,
which is the whole of what replay, backtest and shadow mode will later need.

## One bar, in the order the bar can actually be read

A bar gives four prices and no sequence except one: the open came first. So
each bar is processed in two steps, and only two:

1. **At the open.** A pending manual close fills here. Otherwise, if the open is
   already at or beyond the stop, the stop fills at the open (a gap). If not,
   every target the open is already at or beyond fills, in sequence.
2. **Inside the bar.** Whatever is left is compared with the high and the low.
   If the range reached the stop *and* a target, the order is unknowable and
   the position's ``SameBarPolicy`` decides - never whichever result is nicer.

Then the close becomes the mark.

## Money is never computed here

Gross P&L for every exit, and the unrealized figure on the mark, come from
``app.domain.risk.pnl.pnl_for_product`` - the Phase 3 formula behind the Phase
8.5 product boundary, using the product's verified point value. The only
arithmetic this module does itself is counting units and adding up fees and
per-fill gross amounts it was given.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Direction
from app.domain.common.identity import same_instrument
from app.domain.common.verification import UnverifiedFinancialFactError
from app.domain.instrument.asset_class import UnsupportedAssetClassError
from app.domain.instrument.policy import (
    ProductPolicy,
    TickFeasibility,
    UnsupportedQuantitySemanticsError,
    require_product_calculable,
)
from app.domain.market.candle import Candle
from app.domain.paper.model import (
    INPUT_EVENT_TYPES,
    PaperEvent,
    PaperEventType,
    PaperPosition,
    PositionSpec,
    PositionState,
    RiskApproval,
    TargetState,
)
from app.domain.paper.rules import (
    ENTRY_MODEL,
    MANUAL_EXIT_MODEL,
    STOP_FILL_MODEL,
    TARGET_FILL_MODEL,
    SameBarPolicy,
    adverse_price,
)
from app.domain.risk.pnl import PnLInputError, pnl_for_product
from app.domain.risk.sizing import SizingOutcome


@unique
class RefusalCode(StrEnum):
    """Why the engine would not do what it was asked. Stable for the API and UI."""

    PRODUCT_NOT_CALCULABLE = "PRODUCT_NOT_CALCULABLE"
    UNSUPPORTED_ASSET_CLASS = "UNSUPPORTED_ASSET_CLASS"
    POINT_VALUE_UNVERIFIED = "POINT_VALUE_UNVERIFIED"
    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    RISK_NOT_ALLOWED = "RISK_NOT_ALLOWED"
    QUANTITY_EXCEEDS_RISK = "QUANTITY_EXCEEDS_RISK"
    LEVELS_NOT_EXECUTABLE = "LEVELS_NOT_EXECUTABLE"
    INVALID_TRANSITION = "INVALID_TRANSITION"
    INVALID_OBSERVATION = "INVALID_OBSERVATION"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    CONFLICTING_OBSERVATION = "CONFLICTING_OBSERVATION"
    OBSERVATION_BEFORE_DECISION = "OBSERVATION_BEFORE_DECISION"
    FILL_NOT_POSITIVE = "FILL_NOT_POSITIVE"
    BREAKEVEN_NOT_PROTECTIVE = "BREAKEVEN_NOT_PROTECTIVE"
    REPLAY_DIVERGED = "REPLAY_DIVERGED"


class PaperRefusalError(ValueError):
    """The engine declined an action, with a code and a reason a person can read."""

    def __init__(self, code: RefusalCode, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(f"{code.value}: {reason}")


# ----------------------------------------------------------------------
# Opening
# ----------------------------------------------------------------------


def open_position(
    spec: PositionSpec, approval: RiskApproval, product: ProductPolicy
) -> PaperPosition:
    """Create a pending position, or refuse.

    Refuses when the product cannot be calculated, when its point value is not a
    verified fact (P&L could not be computed later, and a position whose money
    is unknowable is not a simulation of anything), when the risk engine did not
    allow the trade or allowed fewer units, and when a level is not executable
    on the product's price grid.
    """
    _require_product(product, spec)

    if approval.outcome is not SizingOutcome.ALLOWED or approval.allowed_units is None:
        raise PaperRefusalError(
            RefusalCode.RISK_NOT_ALLOWED,
            f"the risk engine did not allow this trade ({approval.outcome.value}): "
            f"{approval.reason}",
        )
    if spec.quantity > approval.allowed_units:
        raise PaperRefusalError(
            RefusalCode.QUANTITY_EXCEEDS_RISK,
            f"{spec.quantity} units requested but the risk engine allows {approval.allowed_units}",
        )

    checks = [("stop", product.price_increment_check(spec.intended_entry, spec.stop))]
    for index, target in enumerate(spec.targets, start=1):
        checks.append((f"target {index}", product.price_increment_check(target.price, spec.stop)))
    for name, check in checks:
        if check.feasibility is not TickFeasibility.ON_GRID:
            raise PaperRefusalError(
                RefusalCode.LEVELS_NOT_EXECUTABLE,
                f"{name}: {check.feasibility.value} - {check.detail}",
            )

    created = PaperEvent(
        sequence=1,
        type=PaperEventType.POSITION_CREATED,
        market_time=spec.decision_time,
        data=_creation_data(spec, approval, product),
    )
    return PaperPosition(
        spec=spec,
        approval=approval,
        state=PositionState.PENDING_ENTRY,
        remaining=0,
        stop=spec.stop,
        targets=tuple(TargetState(spec=target) for target in spec.targets),
        events=(created,),
        fees_total=Decimal(0) if spec.policy.fees.is_modelled else None,
    )


def _require_product(product: ProductPolicy, spec: PositionSpec) -> None:
    if not same_instrument(product.instrument.symbol, spec.symbol):
        raise PaperRefusalError(
            RefusalCode.SYMBOL_MISMATCH,
            f"the product is {product.instrument.symbol!r} but the position is for {spec.symbol!r}",
        )
    try:
        require_product_calculable(product, "paper trading")
    except UnsupportedAssetClassError as error:
        raise PaperRefusalError(RefusalCode.UNSUPPORTED_ASSET_CLASS, str(error)) from error
    except (UnsupportedQuantitySemanticsError, UnverifiedFinancialFactError, ValueError) as error:
        raise PaperRefusalError(RefusalCode.PRODUCT_NOT_CALCULABLE, str(error)) from error
    if not product.point_value().is_authoritative:
        point = product.point_value()
        raise PaperRefusalError(
            RefusalCode.POINT_VALUE_UNVERIFIED,
            f"the {product.vocabulary.point_value_qualified} is {point.status.value}, so no "
            "profit or loss could be computed for this position",
        )


# ----------------------------------------------------------------------
# Inputs after opening
# ----------------------------------------------------------------------


def apply_observation(
    position: PaperPosition, bar: Candle, product: ProductPolicy
) -> PaperPosition:
    """Process one closed bar. Idempotent for a bar already applied."""
    _validate_bar(position, bar)

    if position.last_bar_time is not None and bar.open_time <= position.last_bar_time:
        previous = _applied_bar(position, bar.open_time)
        if previous is None:
            raise PaperRefusalError(
                RefusalCode.OUT_OF_ORDER,
                f"bar {bar.open_time.isoformat()} is not after the last applied bar "
                f"{position.last_bar_time.isoformat()}",
            )
        if previous != _bar_data(bar):
            raise PaperRefusalError(
                RefusalCode.CONFLICTING_OBSERVATION,
                f"bar {bar.open_time.isoformat()} was already applied with different prices",
            )
        return position

    interval = timedelta(minutes=position.spec.timeframe.minutes)
    if position.last_bar_time is not None and bar.open_time < position.last_bar_time + interval:
        raise PaperRefusalError(
            RefusalCode.OUT_OF_ORDER,
            f"bar {bar.open_time.isoformat()} overlaps the previous "
            f"{position.spec.timeframe.value} bar",
        )
    if bar.open_time < position.spec.decision_time:
        raise PaperRefusalError(
            RefusalCode.OBSERVATION_BEFORE_DECISION,
            f"bar {bar.open_time.isoformat()} opened before the decision time "
            f"{position.spec.decision_time.isoformat()}; it could not have informed this trade",
        )
    if position.state.is_terminal:
        raise PaperRefusalError(
            RefusalCode.INVALID_TRANSITION,
            f"the position is {position.state.value}; no further bars are applied",
        )

    builder = _Builder(position, product)
    builder.emit(PaperEventType.OBSERVATION_APPLIED, bar.open_time, _bar_data(bar))

    if builder.state is PositionState.PENDING_ENTRY:
        if builder.enter(bar):
            builder.intrabar(bar)
    elif builder.close_pending:
        builder.manual_exit(bar)
    elif builder.state is PositionState.AMBIGUOUS_HALTED:
        pass  # frozen: only the mark moves
    else:
        builder.at_open(bar)
        if builder.remaining > 0:
            builder.intrabar(bar)

    builder.finish(bar)
    return builder.build()


def request_close(position: PaperPosition) -> PaperPosition:
    """Ask for the remaining quantity to exit at the next bar's open.

    The request is anchored in *market* time, not in the moment the HTTP
    request arrived: it takes effect after the last bar already applied, so the
    first bar that can fill it is the next one observed. The bar the position
    has already seen cannot fill it retroactively. That anchor is recorded on
    the event, so the ledger states the meaning rather than implying it from
    sequence order. A second request while one is pending is a no-op and
    appends nothing.
    """
    if position.close_pending:
        return position
    if not position.state.has_exposure:
        raise PaperRefusalError(
            RefusalCode.INVALID_TRANSITION,
            f"a {position.state.value} position has nothing to close",
        )
    anchor = position.last_bar_time
    event = PaperEvent(
        sequence=position.next_sequence,
        type=PaperEventType.CLOSE_REQUESTED,
        market_time=anchor,
        data={
            "fills_at": MANUAL_EXIT_MODEL,
            "remaining": str(position.remaining),
            "effective_after_bar": anchor.isoformat() if anchor is not None else "NONE",
        },
    )
    return replace(position, close_pending=True, events=(*position.events, event))


def move_stop_to_breakeven(position: PaperPosition) -> PaperPosition:
    """Move the stop to the entry fill price. Idempotent once there.

    Only while the last mark is already beyond the entry in the position's
    favour: a breakeven stop above the market (for a long) is not protection,
    it is an exit order the next bar would fill immediately, and the user did
    not ask for that.
    """
    if position.state not in (PositionState.OPEN, PositionState.PARTIALLY_CLOSED):
        raise PaperRefusalError(
            RefusalCode.INVALID_TRANSITION,
            f"the stop of a {position.state.value} position cannot be moved",
        )
    entry = position.entry_fill_price
    mark = position.last_mark
    if entry is None or mark is None:  # pragma: no cover - OPEN always has both
        raise PaperRefusalError(RefusalCode.INVALID_TRANSITION, "no entry fill to move the stop to")
    if position.stop == entry:
        return position

    long = position.spec.direction is Direction.LONG
    if (mark <= entry) if long else (mark >= entry):
        raise PaperRefusalError(
            RefusalCode.BREAKEVEN_NOT_PROTECTIVE,
            f"the last mark {mark} is not beyond the entry fill {entry} in the position's "
            "favour, so a breakeven stop would be an immediate exit rather than protection",
        )
    event = PaperEvent(
        sequence=position.next_sequence,
        type=PaperEventType.STOP_MOVED_TO_BREAKEVEN,
        market_time=position.last_bar_time,
        data={
            "from": _text(position.stop),
            "to": _text(entry),
            "protects": str(position.remaining),
            # The moved stop guards the bars that follow. Bars already applied
            # are never re-examined, so the high or low of the bar that made the
            # move possible cannot trigger the new stop after the fact.
            "effective_after_bar": (
                position.last_bar_time.isoformat() if position.last_bar_time is not None else "NONE"
            ),
        },
    )
    return replace(position, stop=entry, events=(*position.events, event))


def cancel(position: PaperPosition) -> PaperPosition:
    """Cancel before the entry filled. Idempotent once cancelled."""
    if position.state is PositionState.CANCELLED:
        return position
    if position.state is not PositionState.PENDING_ENTRY:
        raise PaperRefusalError(
            RefusalCode.INVALID_TRANSITION,
            f"only a pending position can be cancelled; this one is {position.state.value}",
        )
    event = PaperEvent(
        sequence=position.next_sequence,
        type=PaperEventType.POSITION_CANCELLED,
        market_time=None,
        data={"reason": "cancelled by the user before the entry filled"},
    )
    return replace(position, state=PositionState.CANCELLED, events=(*position.events, event))


# ----------------------------------------------------------------------
# Money on the mark
# ----------------------------------------------------------------------


def unrealized_gross(position: PaperPosition, product: ProductPolicy) -> Decimal | None:
    """Gross P&L of the open quantity at the last mark.

    ``None`` before any exposure exists (pending), zero once none remains
    (closed, cancelled, rejected), and otherwise the product's own P&L on the
    remaining units only - never on units already closed.
    """
    if position.state.is_terminal:
        return Decimal(0)
    if position.remaining == 0 or position.entry_fill_price is None or position.last_mark is None:
        return None
    return pnl_for_product(
        product,
        position.spec.direction,
        position.entry_fill_price,
        position.last_mark,
        position.remaining,
    ).gross


# ----------------------------------------------------------------------
# Replay
# ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CloseInput:
    pass


@dataclass(frozen=True, slots=True)
class BreakevenInput:
    pass


@dataclass(frozen=True, slots=True)
class CancelInput:
    pass


PaperInput = Candle | CloseInput | BreakevenInput | CancelInput


def apply_input(position: PaperPosition, item: PaperInput, product: ProductPolicy) -> PaperPosition:
    if isinstance(item, Candle):
        return apply_observation(position, item, product)
    if isinstance(item, CloseInput):
        return request_close(position)
    if isinstance(item, BreakevenInput):
        return move_stop_to_breakeven(position)
    return cancel(position)


def replay(
    spec: PositionSpec,
    approval: RiskApproval,
    product: ProductPolicy,
    inputs: Iterable[PaperInput],
) -> PaperPosition:
    """Rebuild a position from its opening and its ordered inputs."""
    position = open_position(spec, approval, product)
    for item in inputs:
        position = apply_input(position, item, product)
    return position


def inputs_from_events(spec: PositionSpec, events: Sequence[PaperEvent]) -> tuple[PaperInput, ...]:
    """The inputs recorded in a ledger, in order. Derived events are skipped."""
    inputs: list[PaperInput] = []
    for event in events:
        if event.type not in INPUT_EVENT_TYPES or event.type is PaperEventType.POSITION_CREATED:
            continue
        if event.type is PaperEventType.OBSERVATION_APPLIED:
            inputs.append(_bar_from_event(spec, event))
        elif event.type is PaperEventType.CLOSE_REQUESTED:
            inputs.append(CloseInput())
        elif event.type is PaperEventType.STOP_MOVED_TO_BREAKEVEN:
            inputs.append(BreakevenInput())
        else:
            inputs.append(CancelInput())
    return tuple(inputs)


def rebuild(
    spec: PositionSpec,
    approval: RiskApproval,
    product: ProductPolicy,
    events: Sequence[PaperEvent],
) -> PaperPosition:
    """Replay a stored ledger and require it to reproduce itself exactly.

    A mismatch means the stored history was produced by different rules than the
    ones now running - which is precisely what pinning a rules version prevents -
    or that the ledger was altered. Either way the engine refuses to continue
    from a state it cannot explain.
    """
    position = replay(spec, approval, product, inputs_from_events(spec, events))
    if tuple(position.events) != tuple(events):
        raise PaperRefusalError(
            RefusalCode.REPLAY_DIVERGED,
            "replaying the stored inputs did not reproduce the stored ledger",
        )
    return position


# ----------------------------------------------------------------------
# Internals
# ----------------------------------------------------------------------


class _Builder:
    """Accumulates the events and state changes produced by one bar."""

    def __init__(self, position: PaperPosition, product: ProductPolicy) -> None:
        self.position = position
        self.product = product
        self.spec = position.spec
        self.policy = position.spec.policy
        self.long = position.spec.direction is Direction.LONG
        self.events: list[PaperEvent] = list(position.events)
        self.state = position.state
        self.remaining = position.remaining
        self.stop = position.stop
        self.targets = list(position.targets)
        self.entry_fill_price = position.entry_fill_price
        self.entry_time = position.entry_time
        self.realized_gross = position.realized_gross
        self.fees_total = position.fees_total
        self.close_pending = position.close_pending

    # -- events --------------------------------------------------------

    def emit(self, kind: PaperEventType, when: datetime | None, data: Mapping[str, str]) -> None:
        self.events.append(
            PaperEvent(sequence=len(self.events) + 1, type=kind, market_time=when, data=data)
        )

    # -- entry ---------------------------------------------------------

    def enter(self, bar: Candle) -> bool:
        """Fill or reject the entry. True when a position is now open."""
        slip = self.policy.slippage.amount
        fill = adverse_price(bar.open, slip, self.spec.direction, entering=True)
        first_target = self.targets[0].spec.price
        breach = None
        if (fill <= self.stop) if self.long else (fill >= self.stop):
            breach = "at or beyond the stop"
        elif (fill >= first_target) if self.long else (fill <= first_target):
            breach = "at or beyond the first target"
        if breach is not None:
            self.emit(
                PaperEventType.ENTRY_REJECTED,
                bar.open_time,
                {
                    "intended_entry": _text(self.spec.intended_entry),
                    "open": _text(bar.open),
                    "slippage": _text(slip),
                    "would_fill": _text(fill),
                    "reason": (
                        f"the entry would have filled at {fill}, {breach}; the plan the risk "
                        "engine approved no longer exists, so no position was opened"
                    ),
                },
            )
            self.state = PositionState.REJECTED
            return False

        _require_positive_fill(fill)
        fee = self.policy.fees.fee_for(self.spec.quantity)
        self._charge(fee)
        self.entry_fill_price = fill
        self.entry_time = bar.open_time
        self.remaining = self.spec.quantity
        self.state = PositionState.OPEN
        self.emit(
            PaperEventType.ENTRY_FILLED,
            bar.open_time,
            {
                "model": ENTRY_MODEL,
                "intended_entry": _text(self.spec.intended_entry),
                "reference_price": _text(bar.open),
                "slippage": _text(slip),
                "fill_price": _text(fill),
                "quantity": str(self.spec.quantity),
                "fee": _optional(fee),
                "difference_from_intended": _text(fill - self.spec.intended_entry),
            },
        )
        return True

    # -- exits ---------------------------------------------------------

    def at_open(self, bar: Candle) -> None:
        if (bar.open <= self.stop) if self.long else (bar.open >= self.stop):
            self.exit_stop(bar, reference=bar.open, gap=bar.open != self.stop, ambiguous=())
            return
        for index, target in enumerate(self.targets):
            if target.filled or self.remaining == 0:
                continue
            reached = bar.open >= target.spec.price if self.long else bar.open <= target.spec.price
            if not reached:
                break
            self.exit_target(bar, index, gap=bar.open != target.spec.price)

    def intrabar(self, bar: Candle) -> None:
        stop_touched = bar.low <= self.stop if self.long else bar.high >= self.stop
        touched = [
            index
            for index, target in enumerate(self.targets)
            if not target.filled
            and (bar.high >= target.spec.price if self.long else bar.low <= target.spec.price)
        ]
        if stop_touched and touched:
            names = ",".join(str(index + 1) for index in touched)
            resolution = self.policy.same_bar
            self.emit(
                PaperEventType.SAME_BAR_AMBIGUITY,
                bar.open_time,
                {
                    "stop": _text(self.stop),
                    "targets_touched": names,
                    "high": _text(bar.high),
                    "low": _text(bar.low),
                    "policy": resolution.value,
                    "reason": (
                        "this bar reached both the stop and a target; OHLC records no order "
                        "inside a bar, so which came first is unknown"
                    ),
                },
            )
            if resolution is SameBarPolicy.STOP_FIRST:
                self.exit_stop(bar, reference=self.stop, gap=False, ambiguous=tuple(touched))
            else:
                self.state = PositionState.AMBIGUOUS_HALTED
            return
        if stop_touched:
            self.exit_stop(bar, reference=self.stop, gap=False, ambiguous=())
            return
        for index in touched:
            if self.remaining == 0:
                break
            self.exit_target(bar, index, gap=False)

    def exit_stop(
        self, bar: Candle, *, reference: Decimal, gap: bool, ambiguous: tuple[int, ...]
    ) -> None:
        slip = self.policy.slippage.amount
        fill = adverse_price(reference, slip, self.spec.direction, entering=False)
        quantity = self.remaining
        gross, fee = self._exit(fill, quantity)
        self.emit(
            PaperEventType.STOP_FILLED,
            bar.open_time,
            {
                "model": STOP_FILL_MODEL,
                "trigger_price": _text(self.stop),
                "reference_price": _text(reference),
                "slippage": _text(slip),
                "fill_price": _text(fill),
                "quantity": str(quantity),
                "gap": _flag(gap),
                "ambiguous": _flag(bool(ambiguous)),
                "targets_also_touched": ",".join(str(index + 1) for index in ambiguous),
                "gross_pnl": _text(gross),
                "fee": _optional(fee),
                "remaining": str(self.remaining),
            },
        )

    def exit_target(self, bar: Candle, index: int, *, gap: bool) -> None:
        target = self.targets[index]
        quantity = target.spec.quantity
        if quantity > self.remaining:  # pragma: no cover - PositionSpec forbids it
            raise PaperRefusalError(
                RefusalCode.INVALID_TRANSITION,
                f"target {index + 1} closes {quantity} units but only {self.remaining} remain",
            )
        fill = target.spec.price
        gross, fee = self._exit(fill, quantity)
        self.targets[index] = replace(target, filled=True, fill_price=fill)
        self.emit(
            PaperEventType.TARGET_FILLED,
            bar.open_time,
            {
                "model": TARGET_FILL_MODEL,
                "target": str(index + 1),
                "trigger_price": _text(target.spec.price),
                "reference_price": _text(bar.open if gap else target.spec.price),
                "fill_price": _text(fill),
                "quantity": str(quantity),
                "gap": _flag(gap),
                "gross_pnl": _text(gross),
                "fee": _optional(fee),
                "remaining": str(self.remaining),
            },
        )
        if self.remaining > 0 and self.state is PositionState.OPEN:
            self.state = PositionState.PARTIALLY_CLOSED

    def manual_exit(self, bar: Candle) -> None:
        slip = self.policy.slippage.amount
        fill = adverse_price(bar.open, slip, self.spec.direction, entering=False)
        quantity = self.remaining
        gross, fee = self._exit(fill, quantity)
        self.close_pending = False
        self.emit(
            PaperEventType.MANUAL_EXIT_FILLED,
            bar.open_time,
            {
                "model": MANUAL_EXIT_MODEL,
                "reference_price": _text(bar.open),
                "slippage": _text(slip),
                "fill_price": _text(fill),
                "quantity": str(quantity),
                "gross_pnl": _text(gross),
                "fee": _optional(fee),
                "remaining": str(self.remaining),
            },
        )

    def _exit(self, fill: Decimal, quantity: int) -> tuple[Decimal, Decimal | None]:
        _require_positive_fill(fill)
        entry = self.entry_fill_price
        if entry is None:  # pragma: no cover - exits only follow an entry
            raise PaperRefusalError(RefusalCode.INVALID_TRANSITION, "no entry fill to exit from")
        if quantity < 1 or quantity > self.remaining:
            raise PaperRefusalError(
                RefusalCode.INVALID_TRANSITION,
                f"cannot exit {quantity} units when {self.remaining} remain",
            )
        try:
            gross = pnl_for_product(self.product, self.spec.direction, entry, fill, quantity).gross
        except PnLInputError as error:
            raise PaperRefusalError(RefusalCode.FILL_NOT_POSITIVE, str(error)) from error
        fee = self.policy.fees.fee_for(quantity)
        self._charge(fee)
        self.realized_gross += gross
        self.remaining -= quantity
        return gross, fee

    def _charge(self, fee: Decimal | None) -> None:
        if fee is None:
            return
        self.fees_total = (self.fees_total or Decimal(0)) + fee

    # -- close-out -----------------------------------------------------

    def finish(self, bar: Candle) -> None:
        if (
            self.state
            in (
                PositionState.OPEN,
                PositionState.PARTIALLY_CLOSED,
                PositionState.AMBIGUOUS_HALTED,
            )
            and self.remaining == 0
        ):
            net = None if self.fees_total is None else self.realized_gross - self.fees_total
            self.state = PositionState.CLOSED
            self.emit(
                PaperEventType.POSITION_CLOSED,
                bar.open_time,
                {
                    "realized_gross": _text(self.realized_gross),
                    "fees_total": _optional(self.fees_total),
                    "realized_net": _optional(net),
                },
            )

    def build(self) -> PaperPosition:
        bar_events = [e for e in self.events if e.type is PaperEventType.OBSERVATION_APPLIED]
        last = bar_events[-1]
        exposed = self.state.has_exposure
        return replace(
            self.position,
            state=self.state,
            remaining=self.remaining,
            stop=self.stop,
            targets=tuple(self.targets),
            events=tuple(self.events),
            entry_fill_price=self.entry_fill_price,
            entry_time=self.entry_time,
            realized_gross=self.realized_gross,
            fees_total=self.fees_total,
            last_bar_time=last.market_time,
            last_mark=Decimal(last.data["close"]),
            close_pending=self.close_pending and exposed,
            bars_applied=len(bar_events),
        )


def _validate_bar(position: PaperPosition, bar: Candle) -> None:
    spec = position.spec
    problems: list[str] = []
    if not same_instrument(bar.symbol, spec.symbol):
        problems.append(f"symbol {bar.symbol!r} is not {spec.symbol!r}")
    if bar.timeframe is not spec.timeframe:
        problems.append(f"timeframe {bar.timeframe.value} is not {spec.timeframe.value}")
    if not bar.is_closed:
        problems.append("the bar is still forming")
    if bar.open_time.tzinfo is None or bar.open_time.utcoffset() is None:
        problems.append("open_time is not timezone-aware")
    prices = (bar.open, bar.high, bar.low, bar.close)
    if not all(isinstance(p, Decimal) and p.is_finite() and p > 0 for p in prices):
        problems.append("prices must be positive finite decimals")
    elif bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close):
        problems.append("high/low do not contain open and close")
    if problems:
        raise PaperRefusalError(RefusalCode.INVALID_OBSERVATION, "; ".join(problems))


def _applied_bar(position: PaperPosition, open_time: datetime) -> Mapping[str, str] | None:
    for event in position.events:
        if event.type is PaperEventType.OBSERVATION_APPLIED and event.market_time == open_time:
            return dict(event.data)
    return None


def _bar_data(bar: Candle) -> dict[str, str]:
    return {
        "open": _text(bar.open),
        "high": _text(bar.high),
        "low": _text(bar.low),
        "close": _text(bar.close),
        "volume": _text(bar.volume),
    }


def _bar_from_event(spec: PositionSpec, event: PaperEvent) -> Candle:
    data = event.data
    missing = [key for key in ("open", "high", "low", "close", "volume") if key not in data]
    if missing or event.market_time is None:
        raise PaperRefusalError(RefusalCode.REPLAY_DIVERGED, f"stored bar is incomplete: {missing}")
    return Candle(
        symbol=spec.symbol,
        timeframe=spec.timeframe,
        open_time=event.market_time,
        open=Decimal(data["open"]),
        high=Decimal(data["high"]),
        low=Decimal(data["low"]),
        close=Decimal(data["close"]),
        volume=Decimal(data["volume"]),
        is_closed=True,
    )


def _creation_data(
    spec: PositionSpec, approval: RiskApproval, product: ProductPolicy
) -> dict[str, str]:
    point = product.point_value()
    policy = spec.policy
    return {
        "origin": spec.origin.value,
        "symbol": spec.symbol,
        "asset_class": product.instrument.asset_class.value.value,
        "asset_class_status": product.instrument.asset_class.status.value,
        "direction": spec.direction.value,
        "quantity": str(spec.quantity),
        "unit": product.vocabulary.unit,
        "intended_entry": _text(spec.intended_entry),
        "stop": _text(spec.stop),
        "timeframe": spec.timeframe.value,
        "targets": ";".join(f"{_text(t.price)}x{t.quantity}" for t in spec.targets),
        "rules_version": policy.rules_version,
        "same_bar": policy.same_bar.value,
        "slippage_mode": policy.slippage.mode.value,
        "slippage_points": _optional(policy.slippage.points),
        "fee_mode": policy.fees.mode.value,
        "fee_per_unit": _optional(policy.fees.per_unit),
        "entry_model": ENTRY_MODEL,
        "risk_outcome": approval.outcome.value,
        "risk_allowed_units": "" if approval.allowed_units is None else str(approval.allowed_units),
        "risk_reason": approval.reason,
        "point_value": _text(point.value),
        "point_value_status": point.status.value,
        "point_value_source": point.source,
        "note": spec.note or "",
    }


def _require_positive_fill(fill: Decimal) -> None:
    if fill <= 0:
        raise PaperRefusalError(
            RefusalCode.FILL_NOT_POSITIVE,
            f"the simulated fill {fill} is not a positive price; the slippage is larger than the "
            "price itself",
        )


def _text(value: Decimal) -> str:
    return format(value, "f")


def _optional(value: Decimal | None) -> str:
    return "" if value is None else _text(value)


def _flag(value: bool) -> str:
    return "true" if value else "false"
