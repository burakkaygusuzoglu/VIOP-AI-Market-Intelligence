"""Project one `AnalysisOutcome` onto the public response shape (§19).

A projection, not a calculation. Every value here is read off a domain object
that an engine produced; nothing is summed, averaged, rounded or re-derived.
The only transformations are `Decimal` -> exact string and enum -> its value,
both of which are lossless.

Two rules this module exists to enforce:

**Absent is absent.** Where an engine produced nothing, the field is ``None``
or the tuple is empty. Nothing is defaulted to zero, and nothing unavailable is
rendered as neutral — the distinction survives all the way to the client (§9).

**Reference ids are content-derived.** Evidence, contradictions and findings
carry stable ids built from a SHA-256 of their content, matching the Phase 7
scheme. `hash()` is randomised per process and would give the same evidence a
different id on every request, which would make an audit trail meaningless.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from app.api.schemas.analysis import (
    AnalysisIdentityResponse,
    AnalysisResponse,
    CandleResponse,
    ChartSeriesResponse,
    ComponentScoreResponse,
    ContractResponse,
    ContradictionResponse,
    DataQualityIssueResponse,
    DatasetIdentityResponse,
    EvidenceItemResponse,
    FindingResponse,
    NumericFactResponse,
    RiskResponse,
    ScenarioResponse,
    SuitabilityResponse,
    SynthesisResponse,
    TimeframeResultResponse,
    ZoneResponse,
)
from app.api.schemas.analysis_technical import build_overlays, build_panel
from app.api.schemas.analysis_why import build_why
from app.application.analysis.orchestrator import AnalysisOutcome, TimeframeOutcome
from app.domain.analysis.evidence import EvidenceItem
from app.domain.analysis.scenarios import Scenario
from app.domain.futures.contract import FuturesContract
from app.domain.risk.sizing import PositionSizing
from app.domain.structure.zones import Zone

CHART_WINDOW_POLICY = "latest-400/v1"
"""The named display-window policy (§4).

**Latest N authoritative candles, unmodified.** Not a downsample, not an
aggregation, not a synthetic overview bar: the drawn candles are the most
recent ones exactly as the engines validated them, and the older ones are
simply absent. Inventing a summarised OHLC to represent a span would be
fabricating a price, which is the one thing the chart layer may never do.

The alternative - a deterministic aggregation - would need its own algorithm,
its own tests and its own answer to "which price is this?", and Phase 8 does
not need it.
"""

MAX_EVIDENCE_PER_DIRECTION = 48
"""One of every kind the evidence engine can produce, per direction.

Derived rather than chosen: the engine emits evidence keyed by category and
timeframe role, and there are twelve categories and four roles, so forty-eight
is exactly the number of distinct (category, role) kinds a single direction can
hold. Anything beyond that is the same kind of reading found again at another
candle.

Measured before this existed: a range-bound series - three superimposed cycles,
which is what an oscillating market looks like - produced 52 evidence items at
300 rows per timeframe, 532 at 1 200 and **1 388** at the 2 500-row ceiling. The
list grew with the input, and nobody reads 1 388 items.

The cap is applied per direction so a flood of bullish readings can never crowd
out the bearish ones: an evidence panel that silently lost one side would be
worse than a long one.
"""

MAX_ZONES_PER_TIMEFRAME = 24
"""Support and resistance bands the chart will draw.

Also measured: the same oscillating series produced 3 zones per timeframe at 300
rows, 17 at 1 200 and 29 at 2 500. Bounded by structure rather than by row count,
but still following it upward, and a 400-candle chart carrying more than about
two dozen bands is a solid block rather than a chart.

The strongest are kept and the rest are counted, because a zone is context for
reading the chart - it is never a blocker, and no decision depends on the
twenty-fifth one.
"""

MAX_ISSUES_PER_TIMEFRAME = 25
"""How many data-quality findings one timeframe reports.

Bounded because the count follows the *input*, not the engine's vocabulary:
2 400 malformed rows produce 2 400 findings, and a page rendering all of them
tells a reader nothing the first few did not.

**Blocking findings are never omitted.** They are taken first, and the count of
what was left out is reported, so a truncation can never quietly remove the
reason a dataset was refused."""

MAX_CHART_CANDLES = 400
"""How many bars the chart payload carries per timeframe.

The analytical dataset is bounded separately and is larger: every candle
supplied is analysed, and an omitted bar still moves the analysis if an engine
depends on it. This bounds only what crosses the wire for drawing, because one
DOM node per bar is what the chart costs.
"""


def _ref(kind: str, *parts: str) -> str:
    """A stable, content-derived reference id."""
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()
    return f"{kind}-{digest[:10].upper()}"


def _dec(value: Decimal | None) -> str | None:
    """Exact decimal text, never a float."""
    return None if value is None else str(value)


def project(outcome: AnalysisOutcome, synthesis: SynthesisResponse) -> AnalysisResponse:
    """Build the public response for one analysis."""
    analysis = outcome.analysis
    technical_available = any(item.usable for item in outcome.timeframes)

    return AnalysisResponse(
        identity=_identity(outcome),
        technical_available=technical_available,
        timeframes=tuple(_timeframe(item, outcome) for item in outcome.timeframes),
        missing_timeframes=tuple(tf.value for tf in outcome.missing_timeframes),
        input_errors=outcome.input_errors,
        chart=tuple(_chart(item) for item in outcome.timeframes if item.usable),
        evidence=_bounded_evidence(analysis.all_evidence) if analysis else (),
        omitted_evidence_count=(
            max(len(analysis.all_evidence) - len(_bounded_evidence(analysis.all_evidence)), 0)
            if analysis
            else 0
        ),
        contradictions=(
            tuple(
                ContradictionResponse(
                    id=_ref("CX", item.contradiction_type.value, item.reason),
                    type=item.contradiction_type.value,
                    severity=item.severity.value,
                    detail=item.reason,
                )
                for item in analysis.contradictions.contradictions
            )
            if analysis
            else ()
        ),
        scenarios=(
            tuple(_scenario(item) for item in analysis.scenarios.scenarios) if analysis else ()
        ),
        suitability=tuple(
            SuitabilityResponse(
                direction=direction.value,
                no_trade=verdict.no_trade,
                findings=tuple(
                    FindingResponse(
                        id=_ref("FN", item.reason.value, item.detail),
                        code=item.reason.value,
                        severity=item.severity.value,
                        detail=item.detail,
                    )
                    for item in verdict.findings
                ),
                missing_requirements=verdict.missing_requirements,
            )
            for direction, verdict in outcome.suitability
        ),
        risk=_risk(outcome),
        synthesis=synthesis,
        facts=_facts(outcome),
        technical=tuple(
            panel
            for panel in (build_panel(item) for item in outcome.timeframes)
            if panel is not None
        ),
        missing=_missing(outcome),
        # Last, and from the finished outcome only. Nothing above
        # reads this back (§7).
        why=build_why(outcome),
    )


def _identity(outcome: AnalysisOutcome) -> AnalysisIdentityResponse:
    identity = outcome.identity
    return AnalysisIdentityResponse(
        analysis_id=identity.analysis_id,
        symbol=identity.symbol,
        analysis_as_of=(
            identity.analysis_as_of.isoformat() if identity.analysis_as_of is not None else None
        ),
        generated_at=identity.generated_at.isoformat(),
        contract_metadata_verified=identity.contract_metadata_verified,
        datasets=tuple(
            DatasetIdentityResponse(
                timeframe=item.timeframe.value,
                digest=item.digest,
                source_name=item.source_name,
                row_count=item.row_count,
                usable=item.usable,
            )
            for item in identity.datasets
        ),
        ephemeral=True,
    )


def _timeframe(item: TimeframeOutcome, outcome: AnalysisOutcome) -> TimeframeResultResponse:
    """One timeframe row, with its direction taken from the engine's own view.

    When the timeframe produced no usable series the direction is
    ``UNAVAILABLE`` — never ``NEUTRAL``. A gap in the data is not a balanced
    market, and collapsing the two is the §15 failure the whole project guards
    against.
    """
    direction = "UNAVAILABLE"
    confirmation = "UNKNOWN"
    analysis = outcome.analysis
    if item.usable and analysis is not None:
        view = next((v for v in analysis.views.views if v.role is item.role), None)
        if view is not None:
            evidence = analysis.evidence_for(item.role)
            direction = _dominant_direction(evidence)
            confirmation = "POINT_IN_TIME" if _any_point_in_time(evidence) else "CONFIRMED"

    shown_issues = _bounded_issues(item.report.issues)

    return TimeframeResultResponse(
        timeframe=item.timeframe.value,
        role=item.role.value,
        usable=item.usable,
        verdict=item.report.verdict.value,
        candle_count=len(item.series) if item.series is not None else 0,
        coverage_end=_coverage_end(item, outcome),
        bars_behind=_bars_behind(item, outcome),
        direction=direction,
        confirmation=confirmation,
        issues=tuple(
            DataQualityIssueResponse(
                code=issue.code.value,
                severity=issue.severity.value,
                message=issue.message,
            )
            for issue in shown_issues
        ),
        omitted_issue_count=len(item.report.issues) - len(shown_issues),
    )


def _coverage_end(item: TimeframeOutcome, outcome: AnalysisOutcome) -> str | None:
    """The instant this timeframe is known through, as the assessment measured it."""
    for coverage in outcome.temporal.coverages:
        if coverage.timeframe is item.timeframe:
            return coverage.coverage_end.isoformat()
    return None


def _bars_behind(item: TimeframeOutcome, outcome: AnalysisOutcome) -> int:
    """How far this timeframe lags the analysis instant, in its own bars (§3)."""
    for entry in outcome.temporal.stale:
        if entry.timeframe is item.timeframe:
            return entry.bars_behind
    return 0


def _bounded_evidence(items: tuple[EvidenceItem, ...]) -> tuple[EvidenceItemResponse, ...]:
    """Every kind of reading, per direction, strongest first (§4).

    Three rules, in order, and each earns its place:

    **Per direction**, so a market producing hundreds of bullish readings cannot
    push the bearish ones off the end. Bull and bear are shown side by side and
    never netted; a cap that could empty one side would net them by accident.

    **One of each (category, role) first**, so the reader learns *what kinds* of
    evidence exist rather than forty copies of whichever kind sorted highest.
    This is the same reasoning as `_bounded_issues`, and it was measured there:
    a severity-only sort kept twenty-five identical notes and dropped the one
    finding that said something new.

    **Strongest first within a kind**, so the survivor of a repeated reading is
    the strongest instance of it rather than an arbitrary one.
    """
    rank = {"STRONG": 0, "MODERATE": 1, "WEAK": 2}

    def strength_of(item: EvidenceItem) -> int:
        return rank.get(item.strength.value, 3)

    kept: list[EvidenceItem] = []
    for direction in sorted({item.direction for item in items}, key=lambda d: d.value):
        pool = sorted(
            (item for item in items if item.direction is direction),
            key=lambda item: (strength_of(item), item.category.value, item.reason),
        )
        first_of_each: list[EvidenceItem] = []
        repeats: list[EvidenceItem] = []
        seen: set[tuple[str, str]] = set()
        for item in pool:
            key = (item.category.value, item.role.value if item.role else "")
            if key in seen:
                repeats.append(item)
            else:
                seen.add(key)
                first_of_each.append(item)

        room = max(MAX_EVIDENCE_PER_DIRECTION - len(first_of_each), 0)
        kept.extend(first_of_each[:MAX_EVIDENCE_PER_DIRECTION] + repeats[:room])

    # Restored to the engines' own order, so the panel reads as the analysis
    # produced it rather than as this function happened to group it.
    order = {id(item): index for index, item in enumerate(items)}
    kept.sort(key=lambda item: order[id(item)])
    return tuple(_evidence(item) for item in kept)


def _bounded_issues(issues: tuple[Any, ...]) -> tuple[Any, ...]:
    """The findings worth listing: **every distinct code, blocking first.**

    Sorting by severity alone is not enough, and measuring showed why. A file
    with 2 400 malformed rows *and* one OHLC violation produces 2 401 findings
    that are all `BLOCK`, so a severity sort keeps twenty-five identical
    malformed-row notes and drops the violation entirely - losing the only
    finding that said something new.

    So one example of each code is taken first, blocking codes before warnings,
    and the remaining budget is spent on repeats. A reader then learns *what
    kinds* of problem the file has, which is the question, rather than
    twenty-five instances of whichever kind happened to come first.
    """
    if len(issues) <= MAX_ISSUES_PER_TIMEFRAME:
        return issues

    rank = {"BLOCK": 0, "WARNING": 1, "INFO": 2}

    def order(issue: Any) -> tuple[int, str]:
        return (rank.get(issue.severity.value, 3), issue.code.value)

    ordered = sorted(issues, key=order)

    first_of_each: list[Any] = []
    repeats: list[Any] = []
    seen: set[str] = set()
    for issue in ordered:
        if issue.code.value in seen:
            repeats.append(issue)
        else:
            seen.add(issue.code.value)
            first_of_each.append(issue)

    room = max(MAX_ISSUES_PER_TIMEFRAME - len(first_of_each), 0)
    return tuple(first_of_each[:MAX_ISSUES_PER_TIMEFRAME] + repeats[:room])


def _dominant_direction(evidence: tuple[EvidenceItem, ...]) -> str:
    """The direction this role's evidence agrees on, or NEUTRAL when it does not.

    Not a score and not a vote weighted by strength: unanimity among the
    directional items, or nothing. Anything cleverer would be a new fusion
    rule living outside the engine that owns fusion.
    """
    directions = {item.direction.value for item in evidence if item.direction.is_directional}
    if len(directions) == 1:
        return directions.pop()
    return "NEUTRAL"


def _any_point_in_time(evidence: tuple[EvidenceItem, ...]) -> bool:
    return any(item.point_in_time for item in evidence)


def _chart(item: TimeframeOutcome) -> ChartSeriesResponse:
    """The bars the chart may draw, and the zones it may band.

    Only real validated candles. The client is forbidden from synthesising a
    price, and this is the only price data it receives.
    """
    series = item.series
    # Callers filter on `usable`, so this is unreachable; it is an early return
    # rather than an assert because an assert disappears under `python -O`.
    if series is None:
        return ChartSeriesResponse(
            timeframe=item.timeframe.value,
            candles=(),
            window_policy=CHART_WINDOW_POLICY,
        )
    analysed = len(series)
    candles = list(series)[-MAX_CHART_CANDLES:]
    zones: tuple[Zone, ...] = ()
    if item.structure is not None:
        zones = item.structure.support_zones + item.structure.resistance_zones
    found_zones = len(zones)
    if found_zones > MAX_ZONES_PER_TIMEFRAME:
        zones = tuple(sorted(zones, key=lambda zone: zone.strength or 0.0, reverse=True))[
            :MAX_ZONES_PER_TIMEFRAME
        ]

    return ChartSeriesResponse(
        timeframe=item.timeframe.value,
        analysed_count=analysed,
        omitted_count=analysed - len(candles),
        window_policy=CHART_WINDOW_POLICY,
        omitted_zone_count=found_zones - len(zones),
        overlays=build_overlays(item.technicals, len(candles)),
        candles=tuple(
            CandleResponse(
                open_time=candle.open_time.isoformat(),
                open=str(candle.open),
                high=str(candle.high),
                low=str(candle.low),
                close=str(candle.close),
                volume=str(candle.volume),
                is_closed=candle.is_closed,
            )
            for candle in candles
        ),
        zones=tuple(
            ZoneResponse(
                id=_ref("ZN", zone.kind.value, str(zone.low), str(zone.high)),
                kind=zone.kind.value,
                lower=str(zone.low),
                upper=str(zone.high),
                score=round(zone.strength * 100) if zone.strength is not None else None,
            )
            for zone in zones
        ),
    )


def _evidence(item: EvidenceItem) -> EvidenceItemResponse:
    return EvidenceItemResponse(
        id=_ref("EV", item.category.value, item.direction.value, item.reason),
        direction=item.direction.value,
        strength=item.strength.value,
        category=item.category.value,
        reason=item.reason,
        timeframe=item.timeframe.value if item.timeframe is not None else None,
        confirmation="POINT_IN_TIME" if item.point_in_time else "CONFIRMED",
        source=_evidence_source(item),
    )


def _evidence_source(item: EvidenceItem) -> str:
    """Provenance as the frontend names it.

    Evidence built from validated candles by the deterministic engines is
    CALCULATED. Anything carrying a non-authoritative verification status is
    reported as unverified rather than promoted.
    """
    if item.provenance is None:
        return "CALCULATED"
    return "CALCULATED" if item.provenance.is_authoritative else "UNVERIFIED"


def _scenario(item: Scenario) -> ScenarioResponse:
    quality = item.quality
    return ScenarioResponse(
        case=item.case.value,
        state=item.state.value,
        reason=item.reason,
        quality_score=quality.score if quality is not None else None,
        quality_label=quality.label if quality is not None else "",
        entry_score=item.entry.score if item.entry is not None else None,
        # Same rule as the top-level list, applied here because the scenario
        # carries its own copies by another route. Measured before this: a
        # range-bound series put 1 372 items in one scenario's supporting list.
        supporting=_bounded_evidence(
            tuple(evidence for group in item.supporting for evidence in group.items)
        ),
        counter=_bounded_evidence(
            tuple(evidence for group in item.counter for evidence in group.items)
        ),
        requirements=tuple(
            f"{req.code.value}: {req.reason}" for req in item.outstanding_requirements
        ),
        invalidations=tuple(f"{inv.code.value}: {inv.description}" for inv in item.invalidations),
        components=(
            tuple(
                ComponentScoreResponse(
                    component=component.component.value,
                    awarded=component.awarded,
                    weight=component.weight,
                    availability=component.availability.value,
                )
                for component in quality.components
            )
            if quality is not None
            else ()
        ),
    )


def _contract(contract: FuturesContract | None) -> ContractResponse | None:
    if contract is None:
        return None
    return ContractResponse(
        symbol=contract.symbol,
        verified=contract.multiplier.is_authoritative and contract.tick_size.is_authoritative,
        multiplier=str(contract.multiplier.value),
        multiplier_status=contract.multiplier.status.value,
        tick_size=str(contract.tick_size.value),
        tick_size_status=contract.tick_size.status.value,
    )


def _risk(outcome: AnalysisOutcome) -> RiskResponse:
    """The risk panel's payload.

    ``outcome`` distinguishes three states that must never be collapsed:
    ALLOWED, NOT_PERMITTED (the engine ran and refused) and UNAVAILABLE (it
    could not run). Only the first two mean anything about the trade.
    """
    risk = outcome.risk
    contract = _contract(risk.contract)
    sizing = risk.sizing

    if sizing is None:
        return RiskResponse(
            available=False,
            outcome="UNAVAILABLE",
            direction=risk.direction.value if risk.direction is not None else None,
            detail="Pozisyon büyüklüğü hesaplanamadı.",
            unavailable_reasons=risk.unavailable_reasons,
            contract=contract,
        )

    # Three outcomes, and the difference between the last two matters.
    #
    # `allowed_contracts is None` means the engine ran and could **not
    # conclude** - typically because no initial margin was supplied, so the
    # final allowance is unknown. That is not a refusal. Reporting it as
    # NOT_PERMITTED would tell a user their trade was rejected when in fact a
    # missing input stopped the calculation, which is the same collapse §32
    # forbids between "0 contracts" and "no answer".
    if sizing.allowed_contracts is None:
        return RiskResponse(
            available=False,
            outcome="UNDETERMINED",
            direction=risk.direction.value if risk.direction is not None else None,
            detail=sizing.reason,
            unavailable_reasons=(sizing.reason,),
            facts=_risk_facts(sizing, outcome),
            warnings=_feasibility(sizing),
            contract=contract,
        )

    permitted = sizing.allowed_contracts > 0
    return RiskResponse(
        available=True,
        outcome="ALLOWED" if permitted else "NOT_PERMITTED",
        direction=risk.direction.value if risk.direction is not None else None,
        detail=sizing.reason,
        facts=_risk_facts(sizing, outcome),
        warnings=_feasibility(sizing),
        contract=contract,
    )


def _feasibility(sizing: PositionSizing) -> tuple[str, ...]:
    """What limited the size, named rather than scored.

    `PositionSizing` reports feasibility rather than a warning list. The enum
    members are the engine's own words, prefixed so a bare ``MISSING`` on
    screen cannot be mistaken for a missing *value* - it is a missing margin
    specification, which is a different thing.
    """
    return (
        f"margin:{sizing.margin_feasibility.value}",
        f"tick:{sizing.tick_feasibility.value}",
    )


def _risk_facts(
    sizing: PositionSizing, outcome: AnalysisOutcome
) -> tuple[NumericFactResponse, ...]:
    """Only values the engine actually produced.

    A ``None`` from the engine yields no fact at all rather than a zero — §32
    forbids displaying "0" for a calculation that did not happen.
    """
    del outcome
    facts: list[NumericFactResponse] = []

    if sizing.allowed_contracts is not None:
        facts.append(
            NumericFactResponse(
                id="FACT-ALLOWED_CONTRACTS",
                label="İzin verilen kontrat",
                raw=str(sizing.allowed_contracts),
                unit="contracts",
                source="CALCULATED",
            )
        )
    if sizing.risk_amount is not None:
        facts.append(
            NumericFactResponse(
                id="FACT-RISK_AMOUNT",
                label="Risk tutarı",
                raw=str(sizing.risk_amount),
                unit="currency",
                source="CALCULATED",
            )
        )
    if sizing.stop_distance is not None:
        facts.append(
            NumericFactResponse(
                id="FACT-STOP_DISTANCE",
                label="Stop mesafesi",
                raw=str(sizing.stop_distance),
                unit="price",
                source="CALCULATED",
            )
        )
    if sizing.loss_per_contract is not None:
        facts.append(
            NumericFactResponse(
                id="FACT-LOSS_PER_CONTRACT",
                label="Kontrat başına zarar",
                raw=str(sizing.loss_per_contract),
                unit="currency",
                source="CALCULATED",
            )
        )
    return tuple(facts)


def _facts(outcome: AnalysisOutcome) -> tuple[NumericFactResponse, ...]:
    """Headline technical readings, one per usable timeframe.

    RSI only: it is the reading the Phase 1 engine produces for every series
    without needing a period long enough to be absent on short data, and adding
    more here would be choosing a dashboard's content in a projection module.
    """
    facts: list[NumericFactResponse] = []
    for item in outcome.timeframes:
        if item.technicals is None:
            continue
        rsi = item.technicals.rsi
        latest = rsi[-1] if len(rsi) else None
        if latest is None:
            continue
        facts.append(
            NumericFactResponse(
                id=f"FACT-RSI-{item.timeframe.value}",
                label=f"RSI ({item.timeframe.value})",
                raw=repr(latest),
                unit="indicator",
                source="CALCULATED",
            )
        )
    return tuple(facts)


def _missing(outcome: AnalysisOutcome) -> tuple[str, ...]:
    """Everything the user should know was not available."""
    missing: list[str] = []
    for timeframe in outcome.missing_timeframes:
        missing.append(f"{timeframe.value} verisi sağlanmadı.")
    for error in outcome.input_errors:
        missing.append(error)
    for item in outcome.timeframes:
        if item.usable:
            continue
        if item.temporal_exclusion is not None:
            # The engine's own words for why this timeframe describes a
            # different moment. A user told only "did not pass validation"
            # would look for a data problem that is not there.
            missing.append(item.temporal_exclusion)
        else:
            missing.append(f"{item.timeframe.value} verisi doğrulamayı geçemedi.")
    for entry in outcome.temporal.stale:
        # Coherent but older than the snapshot. Not a refusal - the data is
        # legitimate and is used - but the response is stamped with a later
        # instant than this timeframe knows about, and that gap must be stated
        # rather than left for a reader to infer from two timestamps (§3).
        missing.append(
            f"{entry.timeframe.value} verisi analiz anının "
            f"{entry.bars_behind} mum gerisinde "
            f"({entry.coverage_end.isoformat()} anına kadar biliniyor)."
        )
    missing.extend(outcome.risk.unavailable_reasons)
    return tuple(missing)
