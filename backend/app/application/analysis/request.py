"""What a client may ask for, and — structurally — what it may not (§6).

## The trust boundary is a type, not a rule people remember

Phase 6 shipped a provenance-escalation defect: a request could hand over a
value and have it stamped with an authority the request had not earned. The fix
was not a validation rule, it was making the escalation *unrepresentable*.

The same shape applies here. This module defines the complete set of things a
client is allowed to supply:

* raw OHLCV text per timeframe
* an instrument identifier — as **text a user typed**, never as verified
  contract metadata
* account equity and used margin
* risk policy settings
* an optional account currency code, carried as user-provided provenance

There is no field here for an indicator, a market structure, a setup score, an
entry score, a suitability verdict, a risk result, an `ActionEnvelope`, a final
action, or a synthesis result. Not "there is a field and we ignore it" — no
field exists, so a forged one is a schema rejection at the API boundary and a
type error here. Every derived value is constructed server-side by the engines,
from this input and nothing else.

## No client-supplied screenshot observations (§5)

There was a `SuppliedObservation` type here, and an `observations` field, for
readings a client would replay back from the Phase 6 screenshot endpoint. It
was never wired: no API field carried it, the route never populated it, the
orchestrator never read it, and it never reached a `SynthesisContext`.

It has been removed rather than left dormant. An unused shape for
client-supplied observations is the exact structure a future wiring would reach
for, and its docstring described a trust rank it had never actually been given.
Vision does not feed the deterministic analysis in this phase; the type system
now says so as plainly as the UI does.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.domain.common.enums import Timeframe
from app.domain.risk.sizing import AccountState, RiskPolicy


@dataclass(frozen=True, slots=True)
class TimeframeDataset:
    """One timeframe's raw CSV, exactly as the user supplied it.

    ``content`` is undecoded text. It is not parsed, sorted, deduplicated or
    repaired here; that happens once, in the shared parser, so the upload path
    and the on-disk path cannot drift.
    """

    timeframe: Timeframe
    content: str
    source_name: str
    """A label for error messages — a filename or "1H". Rendered as text by the
    client and never interpolated into markup."""


@dataclass(frozen=True, slots=True)
class AccountCurrency:
    """The account's currency, **as the user stated it** (§12).

    This is the only currency in the system, and it is `USER_CONFIRMED`, never
    verified. It is not inferred from locale, not defaulted to TRY because VİOP
    is a Turkish exchange, and not derived from the instrument. A user who does
    not supply one gets no currency symbol anywhere, which is the honest
    outcome — a number with the wrong currency attached is worse than a number
    with none.
    """

    code: str
    """An ISO-4217-*style* code as typed. Length and character shape are
    checked; membership of the real ISO list is not, because this project has
    no verified currency registry and pretending otherwise would be exactly the
    fabricated-fact pattern §10 forbids."""

    def __post_init__(self) -> None:
        code = self.code.strip()
        if len(code) != 3 or not code.isalpha() or not code.isascii():
            raise ValueError("currency code must be three ASCII letters")
        object.__setattr__(self, "code", code.upper())


@dataclass(frozen=True, slots=True)
class AnalysisRequest:
    """Everything one on-demand analysis may be given."""

    symbol: str
    """The instrument the user says this data is for. Used to bind the
    timeframe datasets together and to look up contract metadata through the
    trusted provider. Typing it here does **not** make it verified metadata."""

    datasets: tuple[TimeframeDataset, ...]

    account: AccountState | None = None
    risk_policy: RiskPolicy | None = None
    currency: AccountCurrency | None = None

    entry_price: Decimal | None = None
    """A user-supplied intended entry. Sizing needs an entry and a stop, and
    this project will not invent either: with no entry there is no position to
    size, and the risk panel says so rather than showing a number."""

    stop_price: Decimal | None = None

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        return tuple(dataset.timeframe for dataset in self.datasets)

    def dataset_for(self, timeframe: Timeframe) -> TimeframeDataset | None:
        for dataset in self.datasets:
            if dataset.timeframe is timeframe:
                return dataset
        return None
