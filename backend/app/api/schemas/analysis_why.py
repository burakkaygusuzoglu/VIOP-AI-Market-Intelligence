"""Project the Phase 5 Why Engine through the analysis API (§6, §7).

Phase 8 shipped with this gap stated in its own report: the Why Engine existed
and was tested, and nothing carried its output to a screen. Reasons reached the
UI only as whatever prose the underlying engines happened to attach to a
scenario or a finding.

This module closes it by **calling the existing engine**. There is no second
explanation engine here and none in the frontend: every string below was
produced by `app.application.presentation.why`, which in turn walks the
breakdown the scoring engine produced. Nothing is written alongside a
conclusion; everything is read out of it.

## What can be explained, and what deliberately cannot

The engine has no `WHY_LONG`, `WHY_SHORT` or `WHY_WAIT` topic. Master spec §92
asks for them and Phase 7 owns the synthesis that produces them, so an
explanation of a final action would have to be invented here. It is not. What
*is* explained is the deterministic ground a final action would stand on: the
directional evidence, the setup and entry quality, the scenario state, what a
case is still waiting for, the contradictions, the zones, the sizing result and
the suitability block.

Where a conclusion does not exist, the topic is reported **unavailable with a
reason** rather than omitted or filled in. A position-size explanation for an
analysis that never sized a position would be an explanation of nothing.

## Safety (§7)

This module is read-only over a finished `AnalysisOutcome`. It creates no
financial value, changes no action, touches no provenance, and cannot turn a
forming reading into a confirmed one - it has no way to write any of them. The
API assembles the Why block *after* the rest of the response and never feeds it
back in, and a test asserts every other field is byte-identical with the Why
block present and absent.
"""

from __future__ import annotations

from app.api.schemas.analysis import (
    WhyExplanationResponse,
    WhyReasonResponse,
)
from app.application.analysis.orchestrator import AnalysisOutcome
from app.application.presentation.why import (
    Explanation,
    Reason,
    WhyTopic,
    why_contradiction,
    why_direction,
    why_entry_quality,
    why_no_trade_block,
    why_pending_confirmation,
    why_position_size,
    why_scenario_state,
    why_setup_quality,
    why_zone,
)
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.scenarios import ScenarioCase

_MAX_REASONS_PER_TOPIC = 12
"""Reasons listed under one explanation (§4).

The engine writes one reason per contributing item, so a topic that walks the
evidence inherits however much evidence there is. Measured with a range-bound
series at the 2 500-row ceiling: 231 reasons under a single topic, which is not
an explanation any more.

Twelve is derived from the breakdown being explained rather than chosen for
size: the widest engine breakdown here is entry quality, whose components number
ten, plus the two summary lines a topic can carry. A topic never has more than
twelve *distinct* things to say; anything past that is the same kind of reason
found again at another candle.

Truncation is reported in the response so nothing looks complete when it is not,
and it is applied only to explanations - never to a finding, a blocker or a
missing-data statement.
"""

_MAX_ZONE_EXPLANATIONS = 4
"""Zones are explained for the strongest few.

Every zone the engine found is already in the chart payload; explaining all of
them would be a wall of text whose length is decided by the data rather than by
what a reader can use. The count is stated in the response so nothing looks
complete when it is not.
"""


def _render(explanation: Explanation) -> WhyExplanationResponse:
    shown = _bounded_reasons(explanation.reasons)
    return WhyExplanationResponse(
        topic=explanation.topic.value,
        subject=explanation.subject,
        available=explanation.available,
        unavailable_reason=explanation.unavailable_reason,
        omitted_reason_count=len(explanation.reasons) - len(shown),
        reasons=tuple(
            WhyReasonResponse(
                code=reason.code,
                source=reason.source.value,
                severity=reason.severity.value,
                beginner=reason.beginner,
                pro=reason.pro,
                timeframe=reason.timeframe.value if reason.timeframe is not None else None,
                role=reason.role.value if reason.role is not None else None,
            )
            for reason in shown
        ),
    )


def _bounded_reasons(reasons: tuple[Reason, ...]) -> tuple[Reason, ...]:
    """One reason of each code first, most severe first, then repeats.

    The same shape as the data-quality findings cap, and for the same measured
    reason: taking the head of a severity sort keeps a dozen copies of whichever
    code sorted highest and drops the one that said something new. Here the
    codes are the engine's own component names, so keeping one of each means the
    reader still sees every component that contributed.
    """
    if len(reasons) <= _MAX_REASONS_PER_TOPIC:
        return reasons

    rank = {"BLOCK": 0, "WARNING": 1, "NOTABLE": 2, "INFO": 3}

    def order(reason: Reason) -> tuple[int, str]:
        return (rank.get(reason.severity.value, 4), reason.code)

    ordered = sorted(reasons, key=order)
    first_of_each: list[Reason] = []
    repeats: list[Reason] = []
    seen: set[str] = set()
    for reason in ordered:
        if reason.code in seen:
            repeats.append(reason)
        else:
            seen.add(reason.code)
            first_of_each.append(reason)

    room = max(_MAX_REASONS_PER_TOPIC - len(first_of_each), 0)
    return tuple(first_of_each[:_MAX_REASONS_PER_TOPIC] + repeats[:room])


def build_why(outcome: AnalysisOutcome) -> tuple[WhyExplanationResponse, ...]:
    """Explain every conclusion this analysis actually reached."""
    analysis = outcome.analysis
    explanations: list[Explanation] = []

    if analysis is None:
        return (
            _render(
                Explanation.unavailable(
                    WhyTopic.SCENARIO_STATE,
                    "Analiz",
                    "Hiçbir zaman dilimi doğrulamayı geçemediği için açıklanacak bir sonuç yok.",
                )
            ),
        )

    # Directional evidence, both ways. Explaining only the "leading" case would
    # be the presentation layer picking a side.
    for direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH):
        explanations.append(why_direction(analysis, direction))

    for case in (ScenarioCase.BULL, ScenarioCase.BEAR, ScenarioCase.NEUTRAL):
        scenario = analysis.scenarios.case(case)
        explanations.append(why_scenario_state(scenario))
        explanations.append(why_pending_confirmation(scenario))
        if scenario.quality is not None:
            explanations.append(why_setup_quality(scenario.quality))
        if scenario.entry is not None:
            explanations.append(why_entry_quality(scenario.entry))

    for contradiction in analysis.contradictions.contradictions:
        explanations.append(why_contradiction(contradiction))

    explanations.extend(_zone_explanations(outcome))
    explanations.append(_sizing_explanation(outcome))

    # One block explanation, from the first direction that has findings. The
    # suitability engine reports the same blockers for both directions when the
    # cause is structural, so explaining both would print it twice.
    for _, assessment in outcome.suitability:
        if assessment.findings:
            explanations.append(why_no_trade_block(assessment))
            break

    return tuple(_render(item) for item in _deduplicate(explanations))


def _zone_explanations(outcome: AnalysisOutcome) -> list[Explanation]:
    """The strongest few support and resistance zones, explained.

    Read from the narrowest usable timeframe available, because that is where a
    level is acted on; a daily zone explained against a 5M chart would answer a
    question nobody asked.
    """
    usable = [item for item in outcome.timeframes if item.usable and item.structure is not None]
    if not usable:
        return []

    narrowest = max(usable, key=lambda item: item.role.rank)
    structure = narrowest.structure
    if structure is None:  # pragma: no cover - excluded by the filter above
        return []

    zones = sorted(
        structure.support_zones + structure.resistance_zones,
        key=lambda zone: zone.strength,
        reverse=True,
    )[:_MAX_ZONE_EXPLANATIONS]
    return [why_zone(zone) for zone in zones]


def _sizing_explanation(outcome: AnalysisOutcome) -> Explanation:
    """Why a position size exists, or why it could not.

    `why_position_size` explains a finished `PositionSizing`. When none exists
    there is nothing for it to walk, so the topic is reported unavailable with
    the orchestrator's own reasons rather than given an invented breakdown.
    """
    sizing = outcome.risk.sizing
    if sizing is not None:
        return why_position_size(sizing)

    reasons = outcome.risk.unavailable_reasons
    detail = " ".join(reasons) if reasons else "Pozisyon büyüklüğü hesaplanmadı."
    return Explanation.unavailable(WhyTopic.POSITION_SIZE, "Pozisyon büyüklüğü", detail)


def _deduplicate(explanations: list[Explanation]) -> list[Explanation]:
    """One explanation per (topic, subject).

    The same scenario can legitimately produce the same quality explanation
    twice when two cases share a breakdown; showing it twice would suggest two
    findings where there is one.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[Explanation] = []
    for item in explanations:
        key = (item.topic.value, item.subject)
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique
