"""The synthesis context: finished results, assembled and made citable (§2-§6).

This layer **aggregates**. It does not compute. Every number in it was produced
by a Phase 1-6 engine that owns it, and nothing here recomputes an indicator, a
score, a size or an evidence item - §2 is explicit that synthesis must not
become a second engine, and the import contract added this phase makes it
mechanical rather than a habit.

What assembly actually does:

1. reads finished results;
2. sorts everything into a canonical order that does not depend on the order
   objects arrived in;
3. assigns each citable fact a stable identifier;
4. records what is **missing**, explicitly, as its own kind of entry;
5. classifies every entry's authority so a visual inference can never be read
   as a measurement;
6. keeps screenshot-derived text inside `UntrustedText` so it cannot reach a
   prompt as anything but data.

## Missing is not neutral

§20, and it shapes the type. `MissingInformation` is a first-class entry with
its own identifier, not an absent key. A synthesis may cite and explain a gap;
it may not fill one, and the validator enforces that the explanation cites the
gap rather than inventing a value for it.

## Forming is not confirmed

§19. `EvidenceItem.point_in_time` and `Scenario.state` already carry the causal
state Phase 4 established, and both travel into the context unchanged.
`ContextEvidence.confirmation` renders it as an explicit field so a narrative
cannot quietly promote FORMING to CONFIRMED - the validator compares what the
output says against what the context recorded.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum, unique

from app.application.synthesis.untrusted import UntrustedOrigin, UntrustedText
from app.domain.analysis.contradictions import Contradiction, ContradictionReport
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.analysis.entry import EntryQuality
from app.domain.analysis.evidence import EvidenceDirection, EvidenceItem
from app.domain.analysis.quality import SetupQuality
from app.domain.analysis.scenarios import Scenario, ScenarioCase, ScenarioSet, ScenarioState
from app.domain.common.enums import DataSourcePriority, Timeframe
from app.domain.market.quality import DataQualityReport, DataQualityVerdict
from app.domain.risk.sizing import PositionSizing, SizingOutcome
from app.domain.suitability.no_trade import FindingSeverity, NoTradeAssessment
from app.domain.synthesis.actions import ActionEnvelope, derive_action_envelope
from app.domain.synthesis.references import (
    AuthorityClass,
    ContextRef,
    ReferenceKind,
    content_ref_id,
    make_ref_id,
)
from app.domain.vision.extraction import ObservationKind, VisionExtraction

CONTEXT_SCHEMA_VERSION = "synthesis-context/1"


@unique
class ConfirmationState(StrEnum):
    """Whether an observation is settled or still forming (§19)."""

    CONFIRMED = "CONFIRMED"
    """Established by a closed candle. Will not change."""

    FORMING = "FORMING"
    """Describes the current, unfinished candle. May not survive its close."""

    POINT_IN_TIME = "POINT_IN_TIME"
    """True of the latest candle by nature - a regime, a nearest level. Not a
    weaker CONFIRMED; a different kind of statement."""

    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ContextEvidence:
    """One citable piece of evidence, flattened for synthesis."""

    ref: ContextRef
    direction: EvidenceDirection
    category: str
    strength: str
    reason: str
    timeframe: Timeframe | None
    role: str | None
    confirmation: ConfirmationState
    confirmed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ContextContradiction:
    ref: ContextRef
    contradiction_type: str
    severity: str
    reason: str
    timeframes: tuple[Timeframe, ...]
    confirmed_at: datetime | None


@dataclass(frozen=True, slots=True)
class ContextComponent:
    """One setup- or entry-quality component, with its awarded points."""

    ref: ContextRef
    component: str
    availability: str
    awarded: int | None
    weight: int
    reason: str


@dataclass(frozen=True, slots=True)
class ContextFinding:
    """A risk or suitability finding."""

    ref: ContextRef
    code: str
    severity: str
    detail: str


@dataclass(frozen=True, slots=True)
class MissingInformation:
    """Something absent, recorded as its own fact (§20)."""

    ref: ContextRef
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ContextVisionObservation:
    """A screenshot reading, kept visibly weaker than a measurement."""

    ref: ContextRef
    field_name: str
    value: UntrustedText
    observation_kind: ObservationKind
    source_priority: DataSourcePriority
    confidence: Decimal | None
    """Vision uncertainty about legibility. **Not** a probability about the
    market - §15, enforced by the output validator."""


@dataclass(frozen=True, slots=True)
class ContextScenario:
    """A deterministic Phase 4 scenario, referenced not recomputed (§12)."""

    ref: ContextRef
    case: ScenarioCase
    state: ScenarioState
    reason: str
    quality_score: int | None
    quality_label: str
    entry_score: int | None
    supporting_refs: tuple[str, ...]
    counter_refs: tuple[str, ...]
    requirement_codes: tuple[str, ...]
    invalidation_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NumericFact:
    """An authoritative number, and the identifier that may cite it (§4, §14).

    **Claude chooses which fact is relevant; Python owns the fact's value.**
    A synthesis references `FACT-…` and the presentation layer renders the
    figure from here, so no narrative has to repeat a number correctly and no
    repeated number can become authority.

    `rendered` doubles as the allow-list of numeric strings a narrative may
    contain - defence in depth behind the reference model, not instead of it.
    """

    ref: ContextRef
    name: str
    value: Decimal
    unit: str = ""

    @property
    def ref_id(self) -> str:
        return self.ref.ref_id

    @property
    def authority(self) -> AuthorityClass:
        return self.ref.authority

    @property
    def rendered(self) -> str:
        """The exact decimal text, never a float."""
        return format(self.value.normalize(), "f")


@dataclass(frozen=True, slots=True)
class SynthesisContext:
    """Everything a synthesis may reason about, and nothing else.

    Frozen, ordered and self-describing: two semantically identical contexts
    produce identical canonical forms and identical digests, which is what
    makes an audit reconstruction possible at all.
    """

    schema_version: str
    symbol: str
    direction: EvidenceDirection
    envelope: ActionEnvelope

    bull_evidence: tuple[ContextEvidence, ...] = ()
    bear_evidence: tuple[ContextEvidence, ...] = ()
    neutral_evidence: tuple[ContextEvidence, ...] = ()
    contradictions: tuple[ContextContradiction, ...] = ()
    setup_components: tuple[ContextComponent, ...] = ()
    entry_components: tuple[ContextComponent, ...] = ()
    risk_findings: tuple[ContextFinding, ...] = ()
    suitability_findings: tuple[ContextFinding, ...] = ()
    missing: tuple[MissingInformation, ...] = ()
    vision_observations: tuple[ContextVisionObservation, ...] = ()
    scenarios: tuple[ContextScenario, ...] = ()
    numeric_facts: tuple[NumericFact, ...] = ()

    setup_quality_score: int | None = None
    setup_quality_label: str = ""
    entry_quality_score: int | None = None
    data_quality_verdict: DataQualityVerdict | None = None
    sizing_outcome: SizingOutcome | None = None
    no_trade_state: bool | None = None

    analysis_as_of: datetime | None = None
    """When the market data this analysis read ended - the open time of the
    latest candle.

    A **semantic input**, and therefore part of the digest: the same symbol
    analysed over a later candle is a different situation and must hash
    differently.

    Deliberately *not* the time synthesis ran. That distinction used to be
    missing: a `generated_at` field carrying the synthesis execution instant
    sat in this dataclass and so entered the canonical form, which meant the
    same deterministic analysis produced two different digests merely because
    it was synthesised twice. The digest is supposed to identify the *inputs*;
    an audit correlation that changes when nothing about the market changed
    identifies nothing. Synthesis execution time now lives only on
    `AuditRecord.generated_at`, where it is metadata about the attempt.
    """

    @property
    def all_refs(self) -> tuple[ContextRef, ...]:
        """Every citable reference, in canonical order."""
        collected: list[ContextRef] = []
        for evidence in (*self.bull_evidence, *self.bear_evidence, *self.neutral_evidence):
            collected.append(evidence.ref)
        collected.extend(item.ref for item in self.contradictions)
        collected.extend(item.ref for item in self.setup_components)
        collected.extend(item.ref for item in self.entry_components)
        collected.extend(item.ref for item in self.risk_findings)
        collected.extend(item.ref for item in self.suitability_findings)
        collected.extend(item.ref for item in self.missing)
        collected.extend(item.ref for item in self.vision_observations)
        collected.extend(item.ref for item in self.scenarios)
        collected.extend(item.ref for item in self.numeric_facts)
        return tuple(collected)

    def __post_init__(self) -> None:
        seen: set[str] = set()
        for ref in self.all_refs:
            if ref.ref_id in seen:
                # Content-derived identifiers make a collision improbable
                # rather than impossible, and a silent one would merge two
                # distinct facts under a single citation.
                raise ValueError(f"duplicate reference id in context: {ref.ref_id}")
            seen.add(ref.ref_id)

    def fact(self, ref_id: str) -> NumericFact | None:
        """The authoritative number behind a `FACT-…` citation.

        How §4 is honoured end to end: a synthesis cites, the presentation
        layer looks the value up here, and no rendered figure ever came from
        the model.
        """
        for candidate in self.numeric_facts:
            if candidate.ref_id == ref_id:
                return candidate
        return None

    def observation(self, ref_id: str) -> ContextVisionObservation | None:
        """The screenshot reading behind a `VIS-…` citation.

        The counterpart of `fact`: a narrative may need to show what a picture
        said, and must not retype the digits to do it. Rendering resolves this
        into an `ObservationSegment`, which is Python-rendered like a fact and
        explicitly **not** authoritative.
        """
        for candidate in self.vision_observations:
            if candidate.ref.ref_id == ref_id:
                return candidate
        return None

    @property
    def ref_ids(self) -> frozenset[str]:
        return frozenset(ref.ref_id for ref in self.all_refs)

    def ref(self, ref_id: str) -> ContextRef | None:
        for candidate in self.all_refs:
            if candidate.ref_id == ref_id:
                return candidate
        return None

    def refs_of_kind(self, kind: ReferenceKind) -> tuple[ContextRef, ...]:
        return tuple(ref for ref in self.all_refs if ref.kind is kind)

    @property
    def opposing_refs(self) -> tuple[str, ...]:
        """Evidence that argues against the assessed direction, plus every
        contradiction. What a Devil's Advocate has to work with (§13)."""
        against = (
            self.bear_evidence
            if self.direction is EvidenceDirection.BULLISH
            else self.bull_evidence
        )
        return tuple(
            [item.ref.ref_id for item in against]
            + [item.ref.ref_id for item in self.contradictions]
        )

    @property
    def untrusted_texts(self) -> tuple[UntrustedText, ...]:
        return tuple(item.value for item in self.vision_observations)

    @property
    def allowed_numeric_strings(self) -> frozenset[str]:
        return frozenset(fact.rendered for fact in self.numeric_facts)


# ----------------------------------------------------------------------
# Assembly
# ----------------------------------------------------------------------


def _confirmation_of(item: EvidenceItem) -> ConfirmationState:
    """Read Phase 4's causal state; never upgrade it.

    An item with a confirmation index was established by a closed candle. A
    point-in-time item describes the latest candle by nature. Anything else has
    not been confirmed, and is reported as FORMING rather than assumed settled.
    """
    if item.point_in_time:
        return ConfirmationState.POINT_IN_TIME
    if item.confirmed_index is not None or item.confirmed_time is not None:
        return ConfirmationState.CONFIRMED
    return ConfirmationState.FORMING


def _evidence_sort_key(item: EvidenceItem) -> tuple[str, str, str, str, str]:
    """Canonical order, independent of how the caller collected the items."""
    return (
        item.category.value,
        item.source.value,
        item.timeframe.value if item.timeframe else "",
        item.role.value if item.role else "",
        item.reason,
    )


def _to_context_evidence(
    items: tuple[EvidenceItem, ...], kind: ReferenceKind, authority: AuthorityClass
) -> tuple[ContextEvidence, ...]:
    ordered = sorted(items, key=_evidence_sort_key)
    built: list[ContextEvidence] = []
    for item in ordered:
        ref = ContextRef(
            # Identity from content, never from position - see the note in
            # `references.py` on why a counter renumbered everything.
            ref_id=content_ref_id(kind, *_evidence_sort_key(item)),
            kind=kind,
            label=f"{item.category.value} / {item.source.value}",
            authority=authority,
        )
        built.append(
            ContextEvidence(
                ref=ref,
                direction=item.direction,
                category=item.category.value,
                strength=item.strength.value,
                reason=item.reason,
                timeframe=item.timeframe,
                role=item.role.value if item.role else None,
                confirmation=_confirmation_of(item),
                confirmed_at=item.confirmed_time,
            )
        )
    return tuple(built)


def _to_context_contradictions(
    items: tuple[Contradiction, ...],
) -> tuple[ContextContradiction, ...]:
    ordered = sorted(
        items,
        key=lambda item: (item.contradiction_type.value, item.severity.value, item.reason),
    )
    built: list[ContextContradiction] = []
    for item in ordered:
        ref = ContextRef(
            ref_id=content_ref_id(
                ReferenceKind.CONTRADICTION,
                item.contradiction_type.value,
                item.severity.value,
                item.reason,
            ),
            kind=ReferenceKind.CONTRADICTION,
            label=item.contradiction_type.value,
            authority=AuthorityClass.CALCULATED_METRIC,
        )
        built.append(
            ContextContradiction(
                ref=ref,
                contradiction_type=item.contradiction_type.value,
                severity=item.severity.value,
                reason=item.reason,
                timeframes=tuple(sorted(item.timeframes, key=lambda tf: tf.value)),
                confirmed_at=item.confirmed_at,
            )
        )
    return tuple(built)


def _quality_components(quality: SetupQuality | None) -> tuple[ContextComponent, ...]:
    if quality is None:
        return ()
    built: list[ContextComponent] = []
    for score in sorted(quality.components, key=lambda item: item.component.value):
        ref = ContextRef(
            ref_id=make_ref_id(ReferenceKind.SETUP_QUALITY_COMPONENT, score.component.value),
            kind=ReferenceKind.SETUP_QUALITY_COMPONENT,
            label=score.component.value,
            authority=AuthorityClass.CALCULATED_METRIC,
        )
        built.append(
            ContextComponent(
                ref=ref,
                component=score.component.value,
                availability=score.availability.value,
                awarded=score.awarded,
                weight=score.weight,
                reason=score.reason,
            )
        )
    return tuple(built)


def _entry_components(entry: EntryQuality | None) -> tuple[ContextComponent, ...]:
    if entry is None:
        return ()
    built: list[ContextComponent] = []
    for score in sorted(entry.components, key=lambda item: item.component.value):
        ref = ContextRef(
            ref_id=make_ref_id(ReferenceKind.ENTRY_QUALITY_COMPONENT, score.component.value),
            kind=ReferenceKind.ENTRY_QUALITY_COMPONENT,
            label=score.component.value,
            authority=AuthorityClass.CALCULATED_METRIC,
        )
        built.append(
            ContextComponent(
                ref=ref,
                component=score.component.value,
                availability=score.availability.value,
                awarded=score.awarded,
                weight=score.weight,
                reason=score.reason,
            )
        )
    return tuple(built)


def _suitability_findings(
    assessment: NoTradeAssessment | None,
) -> tuple[tuple[ContextFinding, ...], tuple[MissingInformation, ...]]:
    if assessment is None:
        return (), ()

    findings: list[ContextFinding] = []
    for item in sorted(
        assessment.findings, key=lambda entry: (entry.severity.value, entry.reason.value)
    ):
        ref = ContextRef(
            ref_id=content_ref_id(
                ReferenceKind.SUITABILITY_FINDING, item.reason.value, item.severity.value
            ),
            kind=ReferenceKind.SUITABILITY_FINDING,
            label=item.reason.value,
            authority=AuthorityClass.CALCULATED_METRIC,
        )
        findings.append(
            ContextFinding(
                ref=ref,
                code=item.reason.value,
                severity=item.severity.value,
                detail=item.detail,
            )
        )

    missing: list[MissingInformation] = []
    for requirement in sorted(assessment.missing_requirements):
        ref = ContextRef(
            ref_id=content_ref_id(ReferenceKind.MISSING_INFORMATION, requirement),
            kind=ReferenceKind.MISSING_INFORMATION,
            label=requirement,
            authority=AuthorityClass.MISSING,
        )
        missing.append(MissingInformation(ref=ref, code="MISSING_REQUIREMENT", detail=requirement))

    return tuple(findings), tuple(missing)


def _risk_findings(sizing: PositionSizing | None) -> tuple[ContextFinding, ...]:
    if sizing is None:
        return ()
    ref = ContextRef(
        ref_id=content_ref_id(ReferenceKind.RISK_FINDING, "SIZING", sizing.outcome.value),
        kind=ReferenceKind.RISK_FINDING,
        label=f"sizing {sizing.outcome.value}",
        authority=AuthorityClass.RISK,
    )
    return (
        ContextFinding(
            ref=ref,
            code=sizing.outcome.value,
            severity=(
                FindingSeverity.BLOCKING.value
                if sizing.outcome is SizingOutcome.NOT_PERMITTED
                else FindingSeverity.CAUTION.value
            ),
            detail=sizing.reason,
        ),
    )


def _vision_observations(
    extraction: VisionExtraction | None,
) -> tuple[ContextVisionObservation, ...]:
    if extraction is None:
        return ()
    ordered = sorted(extraction.values, key=lambda item: (item.field.value, item.value))
    built: list[ContextVisionObservation] = []
    for item in ordered:
        ref = ContextRef(
            ref_id=content_ref_id(
                ReferenceKind.VISION_OBSERVATION, item.field.value, item.value, item.kind.value
            ),
            kind=ReferenceKind.VISION_OBSERVATION,
            label=item.field.value,
            authority=(
                AuthorityClass.VISION_EXTRACTION
                if item.kind is ObservationKind.DIRECTLY_VISIBLE
                else AuthorityClass.AI_VISUAL_INFERENCE
            ),
        )
        built.append(
            ContextVisionObservation(
                ref=ref,
                field_name=item.field.value,
                value=UntrustedText(
                    origin=(
                        UntrustedOrigin.SCREENSHOT_TEXT
                        if item.kind is ObservationKind.DIRECTLY_VISIBLE
                        else UntrustedOrigin.VISION_INFERENCE
                    ),
                    content=item.value,
                    ref_id=ref.ref_id,
                ),
                observation_kind=item.kind,
                source_priority=item.source_priority,
                confidence=item.confidence.value if item.confidence is not None else None,
            )
        )
    return tuple(built)


def _scenarios(
    scenario_set: ScenarioSet | None,
    bull: tuple[ContextEvidence, ...],
    bear: tuple[ContextEvidence, ...],
) -> tuple[ContextScenario, ...]:
    if scenario_set is None:
        return ()

    by_case: dict[ScenarioCase, Scenario] = {
        item.case: item for item in (scenario_set.bull, scenario_set.bear, scenario_set.neutral)
    }
    built: list[ContextScenario] = []
    for case in ScenarioCase:
        scenario = by_case.get(case)
        if scenario is None:
            continue
        ref = ContextRef(
            ref_id=make_ref_id(ReferenceKind.SCENARIO, case.value),
            kind=ReferenceKind.SCENARIO,
            label=f"{case.value} scenario",
            authority=AuthorityClass.SCENARIO,
        )
        supporting = (
            bull if case is ScenarioCase.BULL else bear if case is ScenarioCase.BEAR else ()
        )
        counter = bear if case is ScenarioCase.BULL else bull if case is ScenarioCase.BEAR else ()
        built.append(
            ContextScenario(
                ref=ref,
                case=case,
                state=scenario.state,
                reason=scenario.reason,
                quality_score=scenario.quality.score if scenario.quality else None,
                quality_label=scenario.quality.label if scenario.quality else "",
                entry_score=scenario.entry.score if scenario.entry else None,
                supporting_refs=tuple(item.ref.ref_id for item in supporting),
                counter_refs=tuple(item.ref.ref_id for item in counter),
                requirement_codes=tuple(sorted(item.code.value for item in scenario.requirements)),
                invalidation_codes=tuple(
                    sorted(item.code.value for item in scenario.invalidations)
                ),
            )
        )
    return tuple(built)


def _latest(series: object) -> float | int | None:
    """The most recent non-``None`` value of an indicator series.

    Phase 1 indicators are tuples aligned to the candle series, with `None`
    for candles before the indicator had enough history. The last real value
    is the current reading.

    Returns only a number: anything else in the tuple is not an indicator
    reading and is ignored rather than coerced.
    """
    if not isinstance(series, tuple):
        return None
    for value in reversed(series):
        if isinstance(value, float | int) and not isinstance(value, bool):
            return value
    return None


def _analysis_as_of(analysis: MultiTimeframeAnalysis) -> datetime | None:
    """When the market data this analysis read ends.

    The latest candle open time across every timeframe view. A semantic
    property of the inputs, so it belongs in the digest: the same instrument
    analysed one candle later is a different situation.
    """
    latest: datetime | None = None
    for view in analysis.views.views:
        candles = getattr(view.series, "candles", ())
        if not candles:
            continue
        opened = candles[-1].open_time
        if latest is None or opened > latest:
            latest = opened
    return latest


def _indicator_facts(analysis: MultiTimeframeAnalysis) -> list[NumericFact]:
    """Current indicator readings, one per role, as citable facts.

    **On precision.** Phase 1 computes indicators as `float`. Converting via
    `Decimal(str(value))` is exact *with respect to that float* - it invents
    nothing and rounds nothing - but it means a reading can render as
    `62.50000000000001` rather than `62.5`. Rounding would be a presentation
    policy this phase has no mandate to invent (§118), so the honest value is
    carried and the limitation is recorded in the phase report instead.
    """
    facts: list[NumericFact] = []
    for view in analysis.views.views:
        snapshot = view.technicals
        role = view.role.value
        for name, attribute, unit in (
            ("RSI", "rsi", "0-100"),
            ("ATR", "atr", "price"),
            ("ADX", "adx", "0-100"),
            ("VWAP", "vwap", "price"),
        ):
            value = _latest(getattr(snapshot, attribute, None))
            if value is None:
                continue
            fact_name = f"{name}_{role}"
            facts.append(
                NumericFact(
                    ref=ContextRef(
                        ref_id=make_ref_id(ReferenceKind.NUMERIC_FACT, f"{name}-{role}"),
                        kind=ReferenceKind.NUMERIC_FACT,
                        label=f"{name} on the {role} timeframe",
                        authority=AuthorityClass.CALCULATED_METRIC,
                    ),
                    name=fact_name,
                    value=Decimal(str(value)),
                    unit=unit,
                )
            )
    return facts


def _numeric_facts(
    setup: SetupQuality | None,
    entry: EntryQuality | None,
    sizing: PositionSizing | None,
    indicators: list[NumericFact] | None = None,
) -> tuple[NumericFact, ...]:
    """Collect every authoritative number a narrative is allowed to mention.

    Deliberately narrow. A number reaches this list only because a
    deterministic engine produced it; anything a narrative states that is not
    here was invented, and §14 makes that invalid rather than merely unsourced.
    """
    facts: list[NumericFact] = list(indicators or [])

    def add(
        name: str, value: Decimal | int | None, authority: AuthorityClass, unit: str = ""
    ) -> None:
        if value is None:
            return
        facts.append(
            NumericFact(
                ref=ContextRef(
                    ref_id=make_ref_id(ReferenceKind.NUMERIC_FACT, name),
                    kind=ReferenceKind.NUMERIC_FACT,
                    label=name,
                    authority=authority,
                ),
                name=name,
                value=Decimal(str(value)),
                unit=unit,
            )
        )

    if setup is not None:
        add("SETUP_QUALITY", setup.score, AuthorityClass.CALCULATED_METRIC, "points 0-100")
    if entry is not None:
        add("ENTRY_QUALITY", entry.score, AuthorityClass.CALCULATED_METRIC, "points 0-100")
    if sizing is not None:
        add("ALLOWED_CONTRACTS", sizing.allowed_contracts, AuthorityClass.RISK, "contracts")
        add("RISK_AMOUNT", sizing.risk_amount, AuthorityClass.RISK, "account currency")
        add("STOP_DISTANCE", sizing.stop_distance, AuthorityClass.RISK, "price")
        add("LOSS_PER_CONTRACT", sizing.loss_per_contract, AuthorityClass.RISK, "account currency")
        add("MAXIMUM_BY_RISK", sizing.maximum_by_risk, AuthorityClass.RISK, "contracts")
        add("MAXIMUM_BY_MARGIN", sizing.maximum_by_margin, AuthorityClass.RISK, "contracts")

    return tuple(sorted(facts, key=lambda item: item.name))


def build_synthesis_context(
    analysis: MultiTimeframeAnalysis,
    direction: EvidenceDirection,
    assessment: NoTradeAssessment,
    *,
    contradictions: ContradictionReport | None = None,
    setup_quality: SetupQuality | None = None,
    entry_quality: EntryQuality | None = None,
    scenario_set: ScenarioSet | None = None,
    sizing: PositionSizing | None = None,
    data_quality: DataQualityReport | None = None,
    vision: VisionExtraction | None = None,
) -> SynthesisContext:
    """Assemble a context from finished results.

    Nothing is computed here except the action envelope, which is itself only a
    subtraction over results other phases produced.

    **No clock is read and none is accepted.** The only timestamp this context
    carries is `analysis_as_of`, derived from the market data itself. When
    synthesis ran is audit metadata and belongs on `AuditRecord`, taken from a
    `ClockPort` there - putting it here would make the digest depend on when
    somebody pressed the button.
    """
    bull_items = tuple(
        item for item in analysis.evidence if item.direction is EvidenceDirection.BULLISH
    )
    bear_items = tuple(
        item for item in analysis.evidence if item.direction is EvidenceDirection.BEARISH
    )
    neutral_items = tuple(
        item for item in analysis.evidence if item.direction is EvidenceDirection.NEUTRAL
    )

    bull = _to_context_evidence(
        bull_items, ReferenceKind.BULL_EVIDENCE, AuthorityClass.CALCULATED_METRIC
    )
    bear = _to_context_evidence(
        bear_items, ReferenceKind.BEAR_EVIDENCE, AuthorityClass.CALCULATED_METRIC
    )
    neutral = _to_context_evidence(
        neutral_items, ReferenceKind.NEUTRAL_EVIDENCE, AuthorityClass.CALCULATED_METRIC
    )

    suitability, missing = _suitability_findings(assessment)

    return SynthesisContext(
        schema_version=CONTEXT_SCHEMA_VERSION,
        symbol=analysis.symbol,
        direction=direction,
        envelope=derive_action_envelope(
            assessment, direction, sizing=sizing, data_quality=data_quality
        ),
        bull_evidence=bull,
        bear_evidence=bear,
        neutral_evidence=neutral,
        contradictions=_to_context_contradictions(
            contradictions.contradictions if contradictions is not None else ()
        ),
        setup_components=_quality_components(setup_quality),
        entry_components=_entry_components(entry_quality),
        risk_findings=_risk_findings(sizing),
        suitability_findings=suitability,
        missing=missing,
        vision_observations=_vision_observations(vision),
        scenarios=_scenarios(scenario_set, bull, bear),
        numeric_facts=_numeric_facts(
            setup_quality, entry_quality, sizing, indicators=_indicator_facts(analysis)
        ),
        setup_quality_score=setup_quality.score if setup_quality else None,
        setup_quality_label=setup_quality.label if setup_quality else "",
        entry_quality_score=entry_quality.score if entry_quality else None,
        data_quality_verdict=data_quality.verdict if data_quality else None,
        sizing_outcome=sizing.outcome if sizing else None,
        no_trade_state=assessment.no_trade,
        analysis_as_of=_analysis_as_of(analysis),
    )
