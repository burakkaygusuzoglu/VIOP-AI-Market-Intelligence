"""The final action, and the deterministic envelope that constrains it (§7, §8).

Phase 7 is the first phase allowed to own LONG / SHORT / WAIT / NO_TRADE. Every
earlier phase deliberately refused to: Phase 4's veto answers "is there a reason
not to act", which is a different and much safer question than "what should I
do", and a test there proves the words are absent.

Owning the concept is not the same as handing it to a model.

## The envelope

`ActionEnvelope` is computed by this module from finished deterministic results
- the Phase 4 veto, Phase 3 sizing, Phase 1 data quality - and says which
actions are **permitted at all**. A synthesis model may later propose one; the
validator's job is to prove the proposal is inside the envelope before it can
become application state. The model chooses *within* a set it cannot widen.

This is deliberately not a score, a ranking or a probability. It is a
permission set: an action is in it or it is not.

## What the rules are, and where they come from

None of these are new market thresholds - §8 forbids inventing any. Each reads
a typed result some earlier phase already produced:

* a **BLOCKING** finding (Phase 4 `FindingSeverity`) means waiting cannot help.
  Risk that permits zero contracts, data that failed integrity checks, a
  structure nobody can read. The only honest action is NO_TRADE, and offering
  WAIT would be a lie about what waiting would achieve.
* a **PENDING** finding means a future candle could genuinely resolve it. The
  setup may be sound and simply has not triggered, so the directional action is
  premature while WAIT is exactly right.
* `no_trade is None` means the veto could not be answered. Not knowing is not
  permission: the directional action is withheld, and the honest options are to
  wait for what is missing or to stand down.
* an action whose suitability was **never assessed** is not permitted. The veto
  is computed for one direction; SHORT has no assessment on a bullish run, and
  unassessed must never read as allowed.

`NO_TRADE` is always permitted. It is the floor of the envelope - there is no
state of the world in which declining to trade needs justification.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique

from app.domain.analysis.evidence import EvidenceDirection
from app.domain.market.quality import DataQualityReport, DataQualityVerdict
from app.domain.risk.sizing import PositionSizing, SizingOutcome
from app.domain.suitability.no_trade import NoTradeAssessment


@unique
class FinalAction(StrEnum):
    """The four final states, and the only four."""

    LONG = "LONG"
    SHORT = "SHORT"
    WAIT = "WAIT"
    """The setup is not refused - something has simply not happened yet. A
    future candle could change the answer."""

    NO_TRADE = "NO_TRADE"
    """Something blocks, and waiting does not help. **Not** a stronger WAIT:
    §10 keeps them apart precisely because collapsing them would turn "the
    account cannot fund this" into "check back shortly"."""

    @property
    def is_directional(self) -> bool:
        return self in {FinalAction.LONG, FinalAction.SHORT}

    @classmethod
    def for_direction(cls, direction: EvidenceDirection) -> FinalAction | None:
        """The directional action matching an evidence direction, if any."""
        if direction is EvidenceDirection.BULLISH:
            return cls.LONG
        if direction is EvidenceDirection.BEARISH:
            return cls.SHORT
        return None


@unique
class ConstraintSource(StrEnum):
    """Which deterministic result removed an action.

    Recorded per constraint so a refusal can be traced to the engine that
    caused it, rather than appearing as an unexplained smaller set.
    """

    SUITABILITY_BLOCKING = "SUITABILITY_BLOCKING"
    SUITABILITY_PENDING = "SUITABILITY_PENDING"
    SUITABILITY_UNDETERMINED = "SUITABILITY_UNDETERMINED"
    RISK_SIZING = "RISK_SIZING"
    DATA_QUALITY = "DATA_QUALITY"
    DIRECTION_NOT_ASSESSED = "DIRECTION_NOT_ASSESSED"


@dataclass(frozen=True, slots=True)
class ActionConstraint:
    """One reason one or more actions are not permitted."""

    source: ConstraintSource
    removed: tuple[FinalAction, ...]
    detail: str

    def __post_init__(self) -> None:
        if not self.removed:
            raise ValueError(f"{self.source.value} constraint removes nothing")
        if not self.detail.strip():
            raise ValueError(f"{self.source.value} constraint carries no detail")


@dataclass(frozen=True, slots=True)
class ActionEnvelope:
    """Which final actions deterministic policy permits.

    ``allowed`` is a tuple in a fixed order rather than a set: §5 forbids
    depending on unordered iteration anywhere that reaches the context digest,
    and a permission list that reordered itself between runs would produce a
    different digest for the same situation.
    """

    allowed: tuple[FinalAction, ...]
    constraints: tuple[ActionConstraint, ...] = ()
    assessed_direction: EvidenceDirection = EvidenceDirection.NEUTRAL

    def __post_init__(self) -> None:
        if not self.allowed:
            raise ValueError("an envelope must permit at least NO_TRADE")
        if FinalAction.NO_TRADE not in self.allowed:
            raise ValueError("NO_TRADE is always permitted and must be present")
        if len(set(self.allowed)) != len(self.allowed):
            raise ValueError("an action is listed twice in the envelope")

    def permits(self, action: FinalAction) -> bool:
        return action in self.allowed

    @property
    def forced(self) -> FinalAction | None:
        """The only permitted action, when exactly one remains.

        `None` whenever a genuine choice exists. Named *forced* rather than
        *recommended*: it is the absence of alternatives, not a preference.
        """
        return self.allowed[0] if len(self.allowed) == 1 else None

    @property
    def disallowed(self) -> tuple[FinalAction, ...]:
        return tuple(action for action in FinalAction if action not in self.allowed)

    @property
    def permits_any_direction(self) -> bool:
        return any(action.is_directional for action in self.allowed)

    def why_not(self, action: FinalAction) -> tuple[ActionConstraint, ...]:
        """Every constraint that removed ``action``."""
        return tuple(item for item in self.constraints if action in item.removed)


_ACTION_ORDER = (FinalAction.LONG, FinalAction.SHORT, FinalAction.WAIT, FinalAction.NO_TRADE)


def derive_action_envelope(
    assessment: NoTradeAssessment,
    direction: EvidenceDirection,
    *,
    sizing: PositionSizing | None = None,
    data_quality: DataQualityReport | None = None,
) -> ActionEnvelope:
    """Compute the permitted actions from finished deterministic results.

    ``sizing`` and ``data_quality`` are optional because `assess_no_trade` may
    already have consumed them - passing them again is belt and braces, not a
    second opinion, and a blocker found either way removes the same actions.
    Supplying neither does **not** mean risk is acceptable; it means the
    assessment is the only source of blockers, which is exactly how Phase 4
    records it.
    """
    removed: dict[FinalAction, list[ActionConstraint]] = {}
    constraints: list[ActionConstraint] = []

    def remove(source: ConstraintSource, actions: tuple[FinalAction, ...], detail: str) -> None:
        constraint = ActionConstraint(source=source, removed=actions, detail=detail)
        constraints.append(constraint)
        for action in actions:
            removed.setdefault(action, []).append(constraint)

    directional = FinalAction.for_direction(direction)
    both_directions = (FinalAction.LONG, FinalAction.SHORT)

    # An action nobody assessed is not an action anybody may take.
    unassessed = tuple(item for item in both_directions if item is not directional)
    if unassessed:
        remove(
            ConstraintSource.DIRECTION_NOT_ASSESSED,
            unassessed,
            (
                f"suitability was assessed for {direction.value}; "
                f"{', '.join(item.value for item in unassessed)} was never evaluated"
            ),
        )

    if assessment.blocking:
        # Waiting cannot resolve any of these, so WAIT goes too. This is the
        # §10 distinction in its load-bearing form.
        reasons = ", ".join(item.reason.value for item in assessment.blocking)
        remove(
            ConstraintSource.SUITABILITY_BLOCKING,
            (FinalAction.LONG, FinalAction.SHORT, FinalAction.WAIT),
            f"blocking suitability finding(s): {reasons}; waiting does not resolve them",
        )
    else:
        if assessment.no_trade is None:
            remove(
                ConstraintSource.SUITABILITY_UNDETERMINED,
                both_directions,
                (
                    "the veto could not be answered ("
                    + (", ".join(assessment.missing_requirements) or "no reason recorded")
                    + "); not knowing is not permission"
                ),
            )
        if assessment.pending:
            reasons = ", ".join(item.reason.value for item in assessment.pending)
            remove(
                ConstraintSource.SUITABILITY_PENDING,
                both_directions,
                f"pending finding(s): {reasons}; a future candle could resolve this, "
                "so the directional action is premature rather than refused",
            )

    if sizing is not None and sizing.outcome is not SizingOutcome.ALLOWED:
        detail = f"position sizing is {sizing.outcome.value}: {sizing.reason}"
        if sizing.outcome is SizingOutcome.NOT_PERMITTED:
            remove(
                ConstraintSource.RISK_SIZING,
                (FinalAction.LONG, FinalAction.SHORT, FinalAction.WAIT),
                f"{detail}; no amount of waiting funds a position the account cannot take",
            )
        else:
            remove(ConstraintSource.RISK_SIZING, both_directions, detail)

    if data_quality is not None and data_quality.verdict is DataQualityVerdict.BLOCKED:
        remove(
            ConstraintSource.DATA_QUALITY,
            (FinalAction.LONG, FinalAction.SHORT, FinalAction.WAIT),
            "market data failed integrity checks; waiting does not repair the series already read",
        )

    allowed = tuple(
        action
        for action in _ACTION_ORDER
        if action is FinalAction.NO_TRADE or action not in removed
    )
    return ActionEnvelope(
        allowed=allowed,
        constraints=tuple(constraints),
        assessed_direction=direction,
    )
