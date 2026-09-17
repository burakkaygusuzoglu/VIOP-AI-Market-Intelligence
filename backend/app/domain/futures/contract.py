"""The futures contract: identity, verified metadata, and observations.

Master spec section 31 lists sixteen fields in one place. They are modelled
here as **two** types, because they are two different kinds of thing:

``FuturesContract``
    What the contract *is*. Symbol, expiry, multiplier, tick size, margin,
    settlement. These change rarely, and every one of them that section 118
    calls mutable is wrapped in a ``VerifiedValue`` carrying where it came from
    and whether it is trustworthy.

``FuturesQuote``
    What the market is *doing*. Futures price, spot price, open interest,
    volume, and when they were observed. These change every tick.

Folding both into one object would force a choice between a mutable contract -
so a level computed from it can go stale without anyone noticing - and
constructing a new "contract" on every tick, which destroys the idea that a
contract has an identity. Keeping them apart costs one extra parameter at call
sites and makes staleness visible.

``days_to_expiry`` is deliberately **not a stored field**. It is a function of
the expiry and the current time, so storing it guarantees it will be wrong; it
is computed on demand from a ``ClockPort``.

**Nothing in this module contains a real VIOP value.** There is no default
multiplier, no default tick size, no default margin and no default session.
Every one of those is a mutable exchange fact under section 118 and must arrive
through a ``ContractMetadataProvider`` with its provenance attached.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.identity import canonical_symbol, same_instrument
from app.domain.common.verification import VerifiedValue
from app.domain.instrument.asset_class import AssetClass


@unique
class SettlementType(StrEnum):
    """How the contract settles at expiry."""

    CASH = "CASH"
    PHYSICAL = "PHYSICAL"


@unique
class ValuationModel(StrEnum):
    """How a price move converts into money.

    The enum names the conventions that exist; **only ``LINEAR`` is
    supported**, and every calculation checks rather than assumes. The other
    two are declared precisely so that a contract using one can be
    *represented* and then *refused* - a fail-closed path that is real and
    testable, instead of an implicit assumption that every future is linear.

    These are generic derivative conventions, not exchange specifications.
    Naming them implies no VIOP fact: no multiplier, tick size or margin
    follows from any of them.
    """

    LINEAR = "LINEAR"
    """``money = price_move × multiplier × contracts``. **Supported.**

    The convention for an index or equity future quoted in the settlement
    currency. Under it, and only under it, ``tick_value == tick_size ×
    multiplier``."""

    INVERSE = "INVERSE"
    """Contract denominated in the base asset, so money per tick depends on
    the price itself. **Not supported** - linear arithmetic applied to it
    would be wrong at every price but the one it was calibrated at."""

    QUANTO = "QUANTO"
    """Settled in a currency other than the one the underlying is quoted in,
    at a fixed rate. **Not supported** - the conversion is part of the payoff
    and this engine does not model it."""


class UnsupportedValuationModelError(ValueError):
    """Raised when a calculation is attempted on a valuation it does not model.

    Fails closed. Producing a plausible number for a contract whose economics
    the engine does not understand is worse than refusing.
    """

    def __init__(self, model: ValuationModel, operation: str) -> None:
        self.model = model
        self.operation = operation
        super().__init__(
            f"{operation} supports {ValuationModel.LINEAR.value} valuation only, got {model.value}"
        )


class ContractValidationError(ValueError):
    """A contract's metadata is internally impossible."""


class QuoteMismatchError(ValueError):
    """Raised when observations and metadata describe different instruments.

    The failure this exists to prevent is silent and total: pair one
    contract's multiplier and margin with another's price and open interest and
    every downstream number - basis, P&L, exposure - is confidently wrong, with
    nothing in the output to suggest it. Splitting identity from observations
    made that pairing expressible, so the pairing has to be checked.
    """

    def __init__(self, expected: str, actual: str, operation: str) -> None:
        self.expected = expected
        self.actual = actual
        self.operation = operation
        super().__init__(
            f"{operation}: metadata describes {expected!r} but the observation describes {actual!r}"
        )


@unique
class ContractState(StrEnum):
    """Whether the contract can still be traded, as far as anyone can tell."""

    ACTIVE = "ACTIVE"
    """Not known to have expired. The expiry is verified and still ahead."""

    EXPIRED = "EXPIRED"
    """The expiry date has fully passed."""

    UNKNOWN = "UNKNOWN"
    """Cannot be decided from what is verified.

    Reached when the expiry is absent or unverified - and also, deliberately,
    **on the expiry date itself**. A contract stops trading at a specific time
    on its last day, and that time is an exchange fact this project does not
    hold. Guessing "the close" would be inventing a session hour, which is
    exactly what section 118 forbids. Reporting UNKNOWN on the day says the
    honest thing: it might still be tradable, and we cannot confirm it."""


@dataclass(frozen=True, slots=True)
class ContractExpiry:
    """When the contract expires, and how precisely that is known.

    The distinction matters. A *date* alone cannot decide whether a contract is
    still tradable at 14:30 on that date; a verified last-trading *timestamp*
    can. Modelling them separately keeps the engine from pretending it has the
    second when it only has the first.
    """

    expiry_date: VerifiedValue[date]
    last_trading_time: VerifiedValue[datetime] | None = None
    """The exact moment trading ceases, when an authoritative source gives it.

    ``None`` is the normal state today - it is a section 118 fact nobody has
    supplied - and the engine reports UNKNOWN on the expiry date rather than
    assuming one."""


@dataclass(frozen=True, slots=True)
class FuturesContract:
    """A contract's identity and its verified specification.

    Required facts (``multiplier``, ``tick_size``) are always present as a
    ``VerifiedValue`` - which may still carry ``UNVERIFIED`` status, because
    having a number and trusting it are different things. Optional facts are
    ``None`` when the provider had nothing to say at all.

    The two absences are distinct on purpose: ``None`` means "never supplied",
    while a present value with ``UNVERIFIED`` status means "supplied, do not
    rely on it". Collapsing them would lose the difference between a gap in the
    data and a number somebody is unsure about.
    """

    symbol: str
    underlying_symbol: str
    contract_name: str
    multiplier: VerifiedValue[Decimal]
    tick_size: VerifiedValue[Decimal]
    tick_value: VerifiedValue[Decimal] | None = None
    initial_margin: VerifiedValue[Decimal] | None = None
    maintenance_margin: VerifiedValue[Decimal] | None = None
    expiry: ContractExpiry | None = None
    settlement: VerifiedValue[SettlementType] | None = None
    trading_session: VerifiedValue[str] | None = None
    valuation: ValuationModel = ValuationModel.LINEAR
    classification: VerifiedValue[AssetClass] | None = None
    """What product class this record is, established by *its own* source.

    Added in Phase 8.5 and deliberately independent of every numeric fact above.
    A verified multiplier says what one point is worth; it does not say who
    established that the instrument is a future. A source that publishes the
    product class - an exchange's product list, say - is recorded here with its
    own status, and only that source can make the classification authoritative.

    ``None`` is the normal state today: no record carries a classification
    source, so the classification is reported ``UNVERIFIED`` however well the
    multiplier and tick size are verified. When present it must be ``FUTURES``;
    a futures record cannot be classified as anything else."""

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ContractValidationError("symbol must not be empty")
        if self.classification is not None and self.classification.value is not AssetClass.FUTURES:
            raise ContractValidationError(
                f"a futures contract record cannot be classified as {self.classification.value!r}"
            )

        _require_positive(self.multiplier.value, "multiplier")
        _require_positive(self.tick_size.value, "tick_size")

        if self.tick_value is not None:
            _require_positive(self.tick_value.value, "tick_value")
        if self.initial_margin is not None:
            _require_positive(self.initial_margin.value, "initial_margin")
        if self.maintenance_margin is not None and self.maintenance_margin.value < 0:
            raise ContractValidationError(
                f"maintenance_margin must not be negative, got {self.maintenance_margin.value}"
            )

    # ------------------------------------------------------------------
    # Reading verified facts
    # ------------------------------------------------------------------

    def authoritative_multiplier(self) -> Decimal | None:
        """The multiplier, but only if it is a verified current fact."""
        return _authoritative(self.multiplier)

    def authoritative_tick_size(self) -> Decimal | None:
        return _authoritative(self.tick_size)

    def authoritative_initial_margin(self) -> Decimal | None:
        """Initial margin per contract, only when verified.

        Returns ``None`` both when no margin was supplied and when one was
        supplied but is not authoritative. Callers must treat that ``None`` as
        "margin feasibility is unknown" and say so, never as "no margin
        required" - the difference between an unknown constraint and an absent
        one is the difference between a warning and a blown account.
        """
        return _authoritative(self.initial_margin)

    def requires_linear_valuation(self, operation: str) -> None:
        """Guard for every calculation that assumes linear economics."""
        if self.valuation is not ValuationModel.LINEAR:
            raise UnsupportedValuationModelError(self.valuation, operation)


@dataclass(frozen=True, slots=True)
class FuturesQuote:
    """A market observation for a contract at one moment.

    Separate from the contract because these change constantly. ``observed_at``
    is carried so a stale quote can be recognised as stale rather than silently
    reused - the staleness check itself belongs to the live-data phase, but the
    timestamp has to exist from the start or it cannot be added later.
    """

    symbol: str
    observed_at: datetime
    futures_price: Decimal | None = None
    spot_price: Decimal | None = None
    open_interest: Decimal | None = None
    volume: Decimal | None = None

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ContractValidationError("observed_at must be timezone-aware")
        _require_optional_positive(self.futures_price, "futures_price")
        _require_optional_positive(self.spot_price, "spot_price")
        _require_optional_non_negative(self.open_interest, "open_interest")
        _require_optional_non_negative(self.volume, "volume")


def _require_positive(value: Decimal, name: str) -> None:
    if not value.is_finite():
        raise ContractValidationError(f"{name} must be finite, got {value}")
    if value <= 0:
        raise ContractValidationError(f"{name} must be positive, got {value}")


def _require_optional_positive(value: Decimal | None, name: str) -> None:
    if value is None:
        return
    _require_positive(value, name)


def _require_optional_non_negative(value: Decimal | None, name: str) -> None:
    if value is None:
        return
    if not value.is_finite():
        raise ContractValidationError(f"{name} must be finite, got {value}")
    if value < 0:
        raise ContractValidationError(f"{name} must not be negative, got {value}")


def _authoritative(fact: VerifiedValue[Decimal] | None) -> Decimal | None:
    if fact is None or not fact.is_authoritative:
        return None
    return fact.value


def require_matching_quote(contract: FuturesContract, quote: FuturesQuote, operation: str) -> None:
    """Refuse a quote that does not belong to this contract.

    Symbols are compared exactly, after stripping surrounding whitespace. **No
    VIOP symbol rule is assumed** - there is no month-code parsing, no root
    extraction, no case folding and no normalisation beyond whitespace, because
    every one of those would be an exchange convention this project has not
    verified. Two symbols match when they are the same string.

    The rule itself now lives in `domain.common.identity` so that Phase 6's
    screenshot symbol checks use this one rather than a second, looser one.
    The behaviour here is unchanged.
    """
    expected = canonical_symbol(contract.symbol)
    actual = canonical_symbol(quote.symbol)
    if not same_instrument(expected, actual):
        raise QuoteMismatchError(expected, actual, operation)


def require_same_instrument(first: FuturesQuote, second: FuturesQuote, operation: str) -> None:
    """Refuse two observations of different instruments.

    Comparing one contract's open interest against another's produces a
    perfectly plausible reading of a change that never happened.
    """
    expected = canonical_symbol(first.symbol)
    actual = canonical_symbol(second.symbol)
    if not same_instrument(expected, actual):
        raise QuoteMismatchError(expected, actual, operation)
