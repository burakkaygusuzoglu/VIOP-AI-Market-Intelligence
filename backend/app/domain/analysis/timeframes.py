"""Timeframe roles and the multi-timeframe input contract (master spec §10).

The specification is explicit that timeframes are **not equal** and must not be
averaged. Each one answers a different question, so this module gives each a
named *role* and refuses input where the role and the timeframe disagree.

    1D  ->  REGIME   broader regime
    1H  ->  BIAS     primary directional bias
    15M ->  SETUP    setup formation
    5M  ->  ENTRY    entry timing

The mapping is a policy object rather than a hard-coded table, because §10
calls the hierarchy *recommended* and supports seven timeframes. What is not
negotiable is that a view arrives carrying its own identity: a 15M series
handed in under the ENTRY role is a wiring bug that would otherwise produce a
confident, entirely wrong reading, so it raises instead.

**Missing is not neutral.** A role with no view is absent, and stays absent all
the way through evidence generation and contradiction detection. Substituting a
neutral reading would manufacture agreement out of a gap in the data, which
§2 forbids.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.common.enums import Timeframe
from app.domain.market.series import ValidatedCandleSeries
from app.domain.structure.engine import StructureSnapshot
from app.domain.technical.engine import TechnicalSnapshot


@unique
class TimeframeRole(StrEnum):
    """What a timeframe is being consulted *for*.

    Ordered from broadest to narrowest by ``rank``; the ordering is what lets
    the contradiction engine say "higher timeframe" without hard-coding 1D.
    """

    REGIME = "REGIME"
    """The broader regime. Slowest to change, and the context everything else
    is read against."""

    BIAS = "BIAS"
    """Primary directional bias."""

    SETUP = "SETUP"
    """Where a setup forms."""

    ENTRY = "ENTRY"
    """Entry timing only. Opposition here is routinely a retracement rather
    than a disagreement - see ``contradictions.py``."""

    @property
    def rank(self) -> int:
        """0 for the broadest role, rising as the role narrows."""
        return _ROLE_RANK[self]


_ROLE_RANK: dict[TimeframeRole, int] = {
    TimeframeRole.REGIME: 0,
    TimeframeRole.BIAS: 1,
    TimeframeRole.SETUP: 2,
    TimeframeRole.ENTRY: 3,
}

ROLES_BROADEST_FIRST: tuple[TimeframeRole, ...] = (
    TimeframeRole.REGIME,
    TimeframeRole.BIAS,
    TimeframeRole.SETUP,
    TimeframeRole.ENTRY,
)


@unique
class MultiTimeframeIssue(StrEnum):
    """Why a set of views was refused.

    Each is a distinct failure with a distinct cause, so each gets its own
    code rather than one generic "invalid input".
    """

    SWAPPED_TIMEFRAME = "SWAPPED_TIMEFRAME"
    """A view's timeframe is not the one its role expects."""

    DUPLICATE_ROLE = "DUPLICATE_ROLE"
    """Two views claim the same role."""

    DUPLICATE_TIMEFRAME = "DUPLICATE_TIMEFRAME"
    """Two views carry the same timeframe under different roles."""

    SYMBOL_MISMATCH = "SYMBOL_MISMATCH"
    """The views do not all describe the same instrument."""

    INCOMPLETE_INPUT = "INCOMPLETE_INPUT"
    """A view is internally inconsistent, or there is nothing to analyse."""

    INVALID_POLICY = "INVALID_POLICY"
    """The role-to-timeframe mapping is not a strict hierarchy."""


class MultiTimeframeError(ValueError):
    """Raised when a set of views cannot honestly be analysed together.

    Carries the ``issue`` so a caller can distinguish a swapped timeframe from
    a duplicated one without parsing the message.
    """

    def __init__(self, issue: MultiTimeframeIssue, message: str) -> None:
        super().__init__(message)
        self.issue = issue


@dataclass(frozen=True, slots=True)
class TimeframeRolePolicy:
    """Which timeframe fills which role.

    Defaults to the §10 recommended hierarchy. Validated as a strict
    hierarchy - four distinct timeframes, each strictly faster than the role
    above it - because a policy that put 5M above 1H would silently invert
    every "higher timeframe" statement the contradiction engine makes.
    """

    regime: Timeframe = Timeframe.D1
    bias: Timeframe = Timeframe.H1
    setup: Timeframe = Timeframe.M15
    entry: Timeframe = Timeframe.M5

    def __post_init__(self) -> None:
        ordered = [self.timeframe_for(role) for role in ROLES_BROADEST_FIRST]
        if len(set(ordered)) != len(ordered):
            raise MultiTimeframeError(
                MultiTimeframeIssue.INVALID_POLICY,
                f"each role needs its own timeframe, got {[tf.value for tf in ordered]}",
            )
        for broader, narrower in zip(ordered, ordered[1:], strict=False):
            if narrower.minutes >= broader.minutes:
                raise MultiTimeframeError(
                    MultiTimeframeIssue.INVALID_POLICY,
                    f"{narrower.value} is not faster than {broader.value}; roles must "
                    "run from broadest to narrowest",
                )

    def timeframe_for(self, role: TimeframeRole) -> Timeframe:
        if role is TimeframeRole.REGIME:
            return self.regime
        if role is TimeframeRole.BIAS:
            return self.bias
        if role is TimeframeRole.SETUP:
            return self.setup
        return self.entry

    def role_for(self, timeframe: Timeframe) -> TimeframeRole | None:
        """The role this policy assigns to ``timeframe``, if any.

        ``None`` for a supported timeframe the policy does not use - 4H under
        the default hierarchy. That is a legitimate answer, not an error.
        """
        for role in ROLES_BROADEST_FIRST:
            if self.timeframe_for(role) is timeframe:
                return role
        return None


@dataclass(frozen=True, slots=True)
class TimeframeView:
    """One timeframe, already analysed by the Phase 1-3 engines.

    Phase 4 computes nothing here. The series, its indicators and its
    structural picture arrive finished; this object binds them to a role and
    checks they actually belong together.
    """

    role: TimeframeRole
    series: ValidatedCandleSeries
    technicals: TechnicalSnapshot
    structure: StructureSnapshot

    def __post_init__(self) -> None:
        counts = {
            "series": len(self.series),
            "technicals": self.technicals.candle_count,
            "structure": self.structure.candle_count,
        }
        if len(set(counts.values())) != 1:
            raise MultiTimeframeError(
                MultiTimeframeIssue.INCOMPLETE_INPUT,
                f"{self.role.value} view is inconsistent: {counts}; the indicators and "
                "the structural picture must be computed from the same candles",
            )
        symbols = {self.series.symbol, self.technicals.symbol, self.structure.symbol}
        if len(symbols) != 1:
            raise MultiTimeframeError(
                MultiTimeframeIssue.INCOMPLETE_INPUT,
                f"{self.role.value} view mixes instruments: {sorted(symbols)}",
            )
        if len(self.series) == 0:
            raise MultiTimeframeError(
                MultiTimeframeIssue.INCOMPLETE_INPUT,
                f"{self.role.value} view has no candles",
            )

    @property
    def timeframe(self) -> Timeframe:
        return self.series.timeframe

    @property
    def symbol(self) -> str:
        return self.series.symbol

    @property
    def last_index(self) -> int:
        """Index of the most recent candle. Evidence about *now* is dated here."""
        return len(self.series) - 1

    @property
    def last_time(self) -> datetime:
        return self.series.candles[-1].open_time

    @property
    def last_close(self) -> Decimal:
        return self.series.candles[-1].close


@dataclass(frozen=True, slots=True)
class MultiTimeframeView:
    """The validated set of views, keyed by role.

    Built through ``build``; the constructor is not the validation point
    because the checks are about the *relationships* between views.
    """

    symbol: str
    policy: TimeframeRolePolicy
    views: tuple[TimeframeView, ...]
    """Ordered broadest role first. Ordering happens only after validation
    passes - invalid input is refused, never quietly rearranged into
    something that looks valid."""

    def view_for(self, role: TimeframeRole) -> TimeframeView | None:
        """The view filling ``role``, or ``None`` when it was not supplied.

        ``None`` means *absent*. It does not mean neutral, and no caller may
        substitute a neutral reading for it.
        """
        for view in self.views:
            if view.role is role:
                return view
        return None

    @property
    def present_roles(self) -> tuple[TimeframeRole, ...]:
        return tuple(view.role for view in self.views)

    @property
    def missing_roles(self) -> tuple[TimeframeRole, ...]:
        present = set(self.present_roles)
        return tuple(role for role in ROLES_BROADEST_FIRST if role not in present)

    @property
    def is_complete(self) -> bool:
        return not self.missing_roles

    @classmethod
    def build(
        cls,
        views: tuple[TimeframeView, ...],
        policy: TimeframeRolePolicy | None = None,
    ) -> MultiTimeframeView:
        """Validate a set of views and bind them to their roles.

        Refuses, in this order: nothing to analyse, a duplicated role, a
        duplicated timeframe, a timeframe that is not the one its role
        expects, and views describing different instruments. Every one of
        those would otherwise yield a fluent analysis of a market nobody
        looked at.
        """
        settings = policy if policy is not None else TimeframeRolePolicy()

        if not views:
            raise MultiTimeframeError(
                MultiTimeframeIssue.INCOMPLETE_INPUT,
                "at least one timeframe view is required",
            )

        roles = [view.role for view in views]
        duplicate_role = _first_duplicate([role.value for role in roles])
        if duplicate_role is not None:
            raise MultiTimeframeError(
                MultiTimeframeIssue.DUPLICATE_ROLE,
                f"role {duplicate_role} was supplied more than once",
            )

        timeframes = [view.timeframe for view in views]
        duplicate_timeframe = _first_duplicate([timeframe.value for timeframe in timeframes])
        if duplicate_timeframe is not None:
            raise MultiTimeframeError(
                MultiTimeframeIssue.DUPLICATE_TIMEFRAME,
                f"timeframe {duplicate_timeframe} was supplied under more than one role",
            )

        for view in views:
            expected = settings.timeframe_for(view.role)
            if view.timeframe is not expected:
                raise MultiTimeframeError(
                    MultiTimeframeIssue.SWAPPED_TIMEFRAME,
                    f"the {view.role.value} role expects {expected.value} but the view "
                    f"carries {view.timeframe.value}; the input was not reordered",
                )

        symbols = {view.symbol for view in views}
        if len(symbols) != 1:
            raise MultiTimeframeError(
                MultiTimeframeIssue.SYMBOL_MISMATCH,
                f"views describe different instruments: {sorted(symbols)}",
            )

        return cls(
            symbol=next(iter(symbols)),
            policy=settings,
            views=tuple(sorted(views, key=lambda view: view.role.rank)),
        )


def _first_duplicate(values: list[str]) -> str | None:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            return value
        seen.add(value)
    return None
