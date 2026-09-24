"""May this boundary be evaluated at all? (Phase 14 Part 1)

Phase 13 already decides whether a timeframe is *available*: connected (or
recovering), transport-fresh, integrity COMPLETE, and holding at least one
confirmed candle. Shadow does not re-derive any of that and does not soften
it. This module answers the one question Phase 13 does not: whether the
timeframes a *particular run* depends on are all in that state.

The rule is deliberately narrow:

* the driver timeframe must be available - without it there is no boundary;
* every timeframe the policy actually requires must be available;
* a timeframe the run merely subscribed to, and the policy does not require,
  may be absent. Inventing a dependency a rule does not have would refuse
  decisions the rule could legitimately make.

CONNECTED alone is never enough, and FRESH alone is never enough: both are
inputs to Phase 13's availability, and it is availability that gates here.

Pure: stdlib and ``app.domain.common`` only. The application maps a Phase 13
snapshot into :class:`~app.domain.shadow.decision.TimeframeEvidence`, so this
module needs no knowledge of the live domain.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.domain.common.enums import Timeframe
from app.domain.shadow.decision import TimeframeEvidence

__all__ = ["Eligibility", "eligible_for_evaluation"]


@dataclass(frozen=True, slots=True)
class Eligibility:
    permitted: bool
    code: str
    reasons: tuple[str, ...] = ()

    @property
    def refused(self) -> bool:
        return not self.permitted


def eligible_for_evaluation(
    *,
    driver: Timeframe,
    required: Sequence[Timeframe],
    evidence: Sequence[TimeframeEvidence],
    connection: str,
    warm_up_bars: int,
) -> Eligibility:
    """Whether the rule may be evaluated on the evidence actually held."""
    by_timeframe = {item.timeframe: item for item in evidence}
    needed = [driver, *(tf for tf in required if tf is not driver)]
    reasons: list[str] = []
    for timeframe in needed:
        item = by_timeframe.get(timeframe)
        if item is None:
            reasons.append(f"{timeframe.value}: not subscribed by this run")
            continue
        if not item.available:
            stated = "; ".join(item.reasons) or "not available"
            reasons.append(f"{timeframe.value}: {stated}")
    if reasons:
        return Eligibility(False, "EVIDENCE_UNAVAILABLE", tuple(reasons))

    driver_evidence = by_timeframe[driver]
    if driver_evidence.confirmed_count < warm_up_bars:
        return Eligibility(
            False,
            "WARM_UP_INCOMPLETE",
            (
                f"{driver.value}: {driver_evidence.confirmed_count} of {warm_up_bars} "
                "warm-up candles confirmed",
            ),
        )
    return Eligibility(True, "ELIGIBLE", ())
