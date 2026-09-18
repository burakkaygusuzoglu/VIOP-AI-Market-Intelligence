"""Builders for Phase 9 paper-trading tests.

**Every price, multiplier and fee here is TEST_FIXTURE data** (master spec
section 118). A multiplier of 10 and a tick of 0.25 are round numbers chosen so
that every expected P&L in the golden scenarios can be checked by hand; they
describe no VİOP contract.

The contract's multiplier and tick size are marked ``VERIFIED_CURRENT_FACT``
explicitly, at the call site, because the P&L engine refuses to compute money
from anything less - and a paper test that could only exercise refusals would
prove nothing about fills.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from app.domain.common.enums import Direction, Timeframe
from app.domain.common.verification import VerificationStatus, VerifiedValue
from app.domain.futures.contract import FuturesContract
from app.domain.futures.policy import FuturesProductPolicy
from app.domain.instrument import (
    AssetClass,
    InstrumentId,
    MarginFeasibility,
    MarginRequirement,
    PriceIncrementCheck,
    ProductCapabilities,
    ProductPolicy,
    ProductVocabulary,
    Support,
    TickFeasibility,
)
from app.domain.market.candle import Candle
from app.domain.paper import (
    PaperPosition,
    PositionSpec,
    RiskApproval,
    SimulationPolicy,
    TargetSpec,
    apply_observation,
    open_position,
)
from app.domain.risk.sizing import AccountState, RiskMode, RiskPolicy, size_for_product
from tests.factories_futures import FIXTURE_SYMBOL, contract, fact, verified

D = Decimal
V = VerificationStatus
DECISION = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)


def paper_contract(
    *,
    multiplier: VerifiedValue[Decimal] | None = None,
    tick_size: VerifiedValue[Decimal] | None = None,
) -> FuturesContract:
    return contract(
        multiplier=multiplier if multiplier is not None else verified("10"),
        tick_size=tick_size if tick_size is not None else verified("0.25"),
        initial_margin=verified("500"),
    )


def product(**overrides: VerifiedValue[Decimal]) -> FuturesProductPolicy:
    return FuturesProductPolicy(paper_contract(**overrides))


ACCOUNT = AccountState(equity=D("100000"))
RISK = RiskPolicy(mode=RiskMode.FIXED, fixed_risk=D("1000"))


def approval_for(spec: PositionSpec, policy: ProductPolicy | None = None) -> RiskApproval:
    """What the real risk engine says about this spec on the fixture account."""
    chosen = policy if policy is not None else product()
    sizing = size_for_product(spec.direction, spec.intended_entry, spec.stop, chosen, ACCOUNT, RISK)
    return RiskApproval.from_sizing(sizing)


LONG_SPEC = PositionSpec(
    position_id="PP-TEST-LONG",
    symbol=FIXTURE_SYMBOL,
    direction=Direction.LONG,
    quantity=4,
    intended_entry=D("100.00"),
    stop=D("98.00"),
    targets=(TargetSpec(D("104.00"), 2), TargetSpec(D("106.00"), 2)),
    timeframe=Timeframe.H1,
    decision_time=DECISION,
    policy=SimulationPolicy(),
)
"""Long 4 at 100, stop 98, targets 104 x2 and 106 x2. One point = 10 per unit."""

SHORT_SPEC = PositionSpec(
    position_id="PP-TEST-SHORT",
    symbol=FIXTURE_SYMBOL,
    direction=Direction.SHORT,
    quantity=4,
    intended_entry=D("100.00"),
    stop=D("102.00"),
    targets=(TargetSpec(D("96.00"), 2), TargetSpec(D("94.00"), 2)),
    timeframe=Timeframe.H1,
    decision_time=DECISION,
    policy=SimulationPolicy(),
)
"""The exact mirror: short 4 at 100, stop 102, targets 96 x2 and 94 x2."""


def long_spec(**changes: Any) -> PositionSpec:
    return replace(LONG_SPEC, **changes)


def short_spec(**changes: Any) -> PositionSpec:
    return replace(SHORT_SPEC, **changes)


def bar(hour: int, o: str, h: str, low: str, c: str, *, symbol: str = FIXTURE_SYMBOL) -> Candle:
    """A closed 1H bar opening ``hour`` hours after the decision time."""
    return Candle(
        symbol=symbol,
        timeframe=Timeframe.H1,
        open_time=DECISION + HOUR * hour,
        open=D(o),
        high=D(h),
        low=D(low),
        close=D(c),
        volume=D("1000"),
        is_closed=True,
    )


def run(
    spec: PositionSpec, bars: Sequence[Candle], policy: FuturesProductPolicy | None = None
) -> PaperPosition:
    chosen = policy if policy is not None else product()
    position = open_position(spec, approval_for(spec, chosen), chosen)
    for item in bars:
        position = apply_observation(position, item, chosen)
    return position


@dataclass(frozen=True, slots=True)
class FakeProduct:
    """A ``ProductPolicy`` for a class the registry does not implement.

    Used only to prove refusal. Its arithmetic is never reached, so its answers
    are deliberately the least plausible ones rather than placeholder maths.
    """

    asset_class: AssetClass
    symbol: str = FIXTURE_SYMBOL
    capabilities: ProductCapabilities = field(
        default_factory=lambda: ProductCapabilities(
            margin=Support.UNKNOWN,
            expiry=Support.UNKNOWN,
            short_selling=Support.UNKNOWN,
            fractional_quantity=Support.UNSUPPORTED,
            funding=Support.UNKNOWN,
            open_interest=Support.UNKNOWN,
        )
    )
    vocabulary: ProductVocabulary = ProductVocabulary("unit", "unit(s)", "value", "value")

    @property
    def instrument(self) -> InstrumentId:
        return InstrumentId(
            symbol=self.symbol,
            asset_class=VerifiedValue(value=self.asset_class, status=V.UNVERIFIED, source="test"),
        )

    def require_calculable(self, operation: str) -> None:
        raise AssertionError("a refused product must never be asked this")

    def point_value(self) -> VerifiedValue[Decimal]:
        return fact("1", V.UNVERIFIED)

    def price_increment_check(
        self, entry_price: Decimal, stop_price: Decimal
    ) -> PriceIncrementCheck:
        return PriceIncrementCheck(TickFeasibility.MISSING, "not modelled")

    def margin_requirement(self) -> MarginRequirement:
        return MarginRequirement(MarginFeasibility.MISSING, None)
