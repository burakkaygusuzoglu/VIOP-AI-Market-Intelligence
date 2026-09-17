"""Run one real analysis over user-supplied historical data (§8, §9).

This is orchestration and nothing else. Every number it reports was produced by
an engine that already existed and is already tested:

    parse (CandleTextParser port)
      -> DataQualityEngine.assess          Phase 1
      -> compute_technicals                Phase 1
      -> analyse_structure                 Phase 2
      -> TimeframeView per role            Phase 4
      -> analyse_multi_timeframe           Phase 4  (evidence, contradictions,
                                                     fusion, scenarios)
      -> assess_no_trade per direction     Phase 4/suitability
      -> size_for_product                  Phase 3  (only when it is honest to)

There is no formula in this module. If a value is not returned by one of the
calls above, it is not in the result — the alternative, computing "just this
one small thing" here, is how a second, untested numerical authority gets born.

## Partial is not complete (§9)

The result carries availability as data, not as an empty field:

* a timeframe with no dataset is **absent**, and stays absent through evidence
  generation. It never becomes a neutral reading.
* a dataset the Data Quality Engine blocked yields no series, and its role is
  unavailable with the engine's own reasons attached.
* risk sizing requires verified contract metadata *and* an intended entry and
  stop. Missing any of them makes sizing unavailable **with the specific
  reason**, never a zero.
* synthesis being unconfigured or failing does not disturb any of the above.

Technical availability, risk availability and synthesis availability are three
separate facts and are reported separately.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

from app.application.analysis.limits import InputLimits, InputTooLargeError
from app.application.analysis.request import AnalysisRequest, TimeframeDataset
from app.application.analysis.snapshot import (
    AnalysisIdentity,
    DatasetIdentity,
    build_identity,
    dataset_digest,
    risk_digest,
)
from app.application.analysis.temporal import TemporalAssessment
from app.application.analysis.temporal import assess as assess_temporal
from app.application.ports.contract_metadata import ContractMetadataProvider
from app.application.ports.market_data import CandleParseError, CandleTextParser
from app.application.ports.system import ClockPort
from app.domain.analysis.engine import MultiTimeframeAnalysis, analyse_multi_timeframe
from app.domain.analysis.evidence import EvidenceDirection
from app.domain.analysis.timeframes import (
    MultiTimeframeError,
    TimeframeRole,
    TimeframeRolePolicy,
    TimeframeView,
)
from app.domain.common.enums import Direction, Timeframe
from app.domain.futures.contract import FuturesContract
from app.domain.futures.policy import FuturesProductPolicy
from app.domain.instrument.policy import ProductPolicy
from app.domain.market.quality import DataQualityEngine, DataQualityReport
from app.domain.market.series import CandleSeries, ValidatedCandleSeries
from app.domain.risk.sizing import PositionSizing, RiskInputError, size_for_product
from app.domain.structure.engine import StructureSnapshot, analyse_structure
from app.domain.suitability.no_trade import NoTradeAssessment, assess_no_trade
from app.domain.technical.engine import TechnicalSnapshot, compute_technicals


class AnalysisInputError(ValueError):
    """The request cannot produce an analysis at all.

    Distinct from a data-quality finding, which is a *result*. This is "there
    is nothing to assess": no timeframes, an unreadable file, a role/timeframe
    mismatch. Carries a stable code and a message safe to show a client.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class TimeframeOutcome:
    """What one timeframe produced, including when it produced nothing."""

    timeframe: Timeframe
    role: TimeframeRole
    report: DataQualityReport
    series: ValidatedCandleSeries | None
    technicals: TechnicalSnapshot | None
    structure: StructureSnapshot | None
    temporal_exclusion: str | None = None
    """Why this timeframe was kept out of the multi-timeframe analysis despite
    passing data-quality validation (§2). ``None`` when it contributed."""

    @property
    def usable(self) -> bool:
        """Whether this timeframe contributed to the combined analysis.

        A temporally incoherent series is *valid* on its own terms and still
        unusable here: it describes a different moment. Reporting it as usable
        would be reporting a leak as a contribution.
        """
        return self.series is not None and self.temporal_exclusion is None


@dataclass(frozen=True, slots=True)
class RiskOutcome:
    """The risk answer, or precisely why there is not one.

    ``sizing`` is ``None`` whenever anything required was missing, and
    ``unavailable_reasons`` says what. It is never a zero-contract sizing
    standing in for "we could not work it out" — those are different facts and
    §32 forbids showing the second as the first.
    """

    sizing: PositionSizing | None
    unavailable_reasons: tuple[str, ...]
    contract: FuturesContract | None
    contract_verified: bool
    direction: Direction | None

    @property
    def available(self) -> bool:
        return self.sizing is not None


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    """One finished, ephemeral analysis."""

    identity: AnalysisIdentity
    timeframes: tuple[TimeframeOutcome, ...]
    analysis: MultiTimeframeAnalysis | None
    """``None`` when no timeframe survived validation — there is nothing to
    analyse across. The per-timeframe reports still explain why."""

    suitability: tuple[tuple[EvidenceDirection, NoTradeAssessment], ...]
    """Assessed for each directional case rather than for one chosen
    direction. Choosing one would be a market judgement this layer has no
    business making; running both invents nothing and lets the caller show the
    case its user actually asked about."""

    risk: RiskOutcome
    missing_timeframes: tuple[Timeframe, ...]
    input_errors: tuple[str, ...]
    """Files that could not be read at all, as safe text. Never the file
    content."""

    temporal: TemporalAssessment
    """Whether the supplied timeframes describe one moment (§2)."""


_ROLE_BY_TIMEFRAME: dict[Timeframe, TimeframeRole] = {
    Timeframe.D1: TimeframeRole.REGIME,
    Timeframe.H1: TimeframeRole.BIAS,
    Timeframe.M15: TimeframeRole.SETUP,
    Timeframe.M5: TimeframeRole.ENTRY,
}
"""The recommended hierarchy (§10 of the master spec), as data.

Not a new policy: it mirrors `TimeframeRolePolicy`'s default, and
`analyse_multi_timeframe` re-validates the pairing and raises if a view arrives
under the wrong role. This table only decides which datasets to *offer*.
"""


async def run_analysis(
    request: AnalysisRequest,
    *,
    parser: CandleTextParser,
    clock: ClockPort,
    contracts: ContractMetadataProvider | None = None,
    limits: InputLimits | None = None,
    policy: TimeframeRolePolicy | None = None,
) -> AnalysisOutcome:
    """Produce a complete analysis, or a complete account of why not."""
    bounds = limits if limits is not None else InputLimits()
    symbol = request.symbol.strip()
    if not symbol:
        raise AnalysisInputError("SYMBOL_REQUIRED", "an instrument identifier is required")
    bounds.check_symbol(symbol)
    if not request.datasets:
        raise AnalysisInputError("NO_DATA", "at least one timeframe dataset is required")

    outcomes: list[TimeframeOutcome] = []
    identities: list[DatasetIdentity] = []
    input_errors: list[str] = []
    total_rows = 0
    engine = DataQualityEngine()

    for dataset in request.datasets:
        role = _ROLE_BY_TIMEFRAME.get(dataset.timeframe)
        if role is None:
            raise AnalysisInputError(
                "UNSUPPORTED_TIMEFRAME",
                f"{dataset.timeframe.value} has no analysis role in this phase",
            )

        bounds.check_csv_size(dataset.timeframe, len(dataset.content.encode("utf-8")))

        try:
            fetch = parser.parse(
                dataset.content,
                symbol=symbol,
                timeframe=dataset.timeframe,
                source_name=dataset.source_name,
                max_rows=bounds.max_rows_per_timeframe,
            )
        except (CandleParseError, InputTooLargeError) as error:
            # The message is ours: the parser reports the source label and the
            # structural problem, never a row's content.
            input_errors.append(f"{dataset.timeframe.value}: {error}")
            continue

        total_rows += len(fetch.candles)
        bounds.check_total_rows(total_rows)
        bounds.check_rows(dataset.timeframe, len(fetch.candles))

        assessment = engine.assess(CandleSeries.of(fetch.candles), extra_issues=fetch.issues)
        series = assessment.series

        technicals = compute_technicals(series) if series is not None else None
        structure = (
            analyse_structure(series, technicals)
            if series is not None and technicals is not None
            else None
        )

        outcomes.append(
            TimeframeOutcome(
                timeframe=dataset.timeframe,
                role=role,
                report=assessment.report,
                series=series,
                technicals=technicals,
                structure=structure,
            )
        )
        identities.append(
            DatasetIdentity(
                timeframe=dataset.timeframe,
                digest=dataset_digest(dataset.timeframe, dataset.content),
                source_name=dataset.source_name,
                row_count=len(fetch.candles),
                usable=series is not None,
            )
        )

    # Coherence before analysis (§2). A timeframe that describes a later moment
    # than a narrower one is excluded here, so nothing downstream - technicals
    # in the MTF view, evidence, contradictions, risk, suitability, the
    # synthesis context - can read information that was not available at the
    # snapshot.
    temporal = assess_temporal(tuple((item.timeframe, item.role, item.series) for item in outcomes))
    if temporal.excluded:
        reasons = {finding.timeframe: finding.detail for finding in temporal.findings}
        outcomes = [
            replace(item, temporal_exclusion=reasons.get(item.timeframe))
            if item.timeframe in temporal.excluded
            else item
            for item in outcomes
        ]
        identities = [
            replace(identity, usable=False) if identity.timeframe in temporal.excluded else identity
            for identity in identities
        ]

    analysis = _analyse_across(outcomes, policy)
    contract = await _contract_for(symbol, contracts)
    risk = _assess_risk(request, contract)
    suitability = _assess_suitability(analysis, risk)

    return AnalysisOutcome(
        identity=build_identity(
            symbol=symbol,
            datasets=tuple(identities),
            analysis_as_of=temporal.analysis_as_of,
            generated_at=clock.now(),
            risk_settings_digest=_risk_settings_digest(request),
            contract_metadata_verified=risk.contract_verified,
        ),
        timeframes=tuple(outcomes),
        analysis=analysis,
        suitability=suitability,
        risk=risk,
        missing_timeframes=_missing_timeframes(request.datasets),
        input_errors=tuple(input_errors),
        temporal=temporal,
    )


def _analyse_across(
    outcomes: list[TimeframeOutcome],
    policy: TimeframeRolePolicy | None,
) -> MultiTimeframeAnalysis | None:
    """Run the Phase 4 engine over whichever timeframes survived.

    A blocked or absent timeframe is simply not a view. It is *not* replaced
    with a neutral one, which would manufacture agreement out of a gap.
    """
    views = tuple(
        TimeframeView(
            role=outcome.role,
            series=outcome.series,
            technicals=outcome.technicals,
            structure=outcome.structure,
        )
        for outcome in outcomes
        if outcome.usable
        and outcome.series is not None
        and outcome.technicals is not None
        and outcome.structure is not None
    )
    if not views:
        return None
    try:
        return analyse_multi_timeframe(views, policy=policy)
    except MultiTimeframeError as error:
        # A view whose role and timeframe disagree, or views mixing symbols.
        # Refusing is the engine's designed behaviour and is surfaced as an
        # input error rather than swallowed into a partial analysis.
        raise AnalysisInputError("INCONSISTENT_TIMEFRAMES", str(error)) from error


async def _contract_for(
    symbol: str,
    contracts: ContractMetadataProvider | None,
) -> FuturesContract | None:
    """Ask the trusted provider, and accept ``None`` as an answer.

    Nothing here constructs a contract from defaults. A symbol the provider has
    never heard of has no multiplier, no tick size and no margin, and every
    calculation that needs one is unavailable rather than approximated (§10).
    """
    if contracts is None:
        return None
    return await contracts.get_contract(symbol)


def _product_policy(contract: FuturesContract) -> ProductPolicy:
    """The one place a product record becomes a ``ProductPolicy`` (Phase 8.5).

    The contract metadata provider returns futures specifications only, so the
    only policy that can be built is ``FuturesProductPolicy``. When a second
    product exists, this is where its record is dispatched - once, at the
    boundary - and nowhere in the risk engine.
    """
    return FuturesProductPolicy(contract)


def _is_verified(contract: FuturesContract | None) -> bool:
    """Whether the facts sizing depends on are current verified facts.

    Both the multiplier and the tick size must be verified. A contract carrying
    a development default is a contract whose numbers must not drive a position
    size, however plausible they look.
    """
    if contract is None:
        return False
    return contract.multiplier.is_authoritative and contract.tick_size.is_authoritative


def _assess_risk(request: AnalysisRequest, contract: FuturesContract | None) -> RiskOutcome:
    """Size the position, or list exactly what is missing."""
    reasons: list[str] = []
    verified = _is_verified(contract)
    account = request.account
    policy = request.risk_policy
    entry = request.entry_price
    stop = request.stop_price

    if contract is None:
        reasons.append("Doğrulanmış kontrat bilgisi yok (çarpan, tik büyüklüğü).")
    elif not verified:
        reasons.append("Kontrat bilgisi doğrulanmamış; pozisyon büyüklüğü hesaplanamaz.")
    if account is None:
        reasons.append("Hesap bakiyesi girilmedi.")
    if policy is None:
        reasons.append("Risk ayarları girilmedi.")
    if entry is None:
        reasons.append("Planlanan giriş fiyatı girilmedi.")
    if stop is None:
        reasons.append("Stop fiyatı girilmedi.")

    direction = _intended_direction(entry, stop)
    if direction is None and entry is not None and stop is not None:
        reasons.append("Giriş ve stop aynı; yön belirlenemez.")

    # The narrowing below is the `is None` checks above, restated so the type
    # checker can see it. An `assert` would say the same thing and be stripped
    # by `python -O`, which is not a property a financial guard should have.
    if (
        reasons
        or contract is None
        or direction is None
        or account is None
        or policy is None
        or entry is None
        or stop is None
    ):
        return RiskOutcome(
            sizing=None,
            unavailable_reasons=tuple(reasons),
            contract=contract,
            contract_verified=verified,
            direction=direction,
        )

    try:
        sizing = size_for_product(
            direction, entry, stop, _product_policy(contract), account, policy
        )
    except RiskInputError as error:
        return RiskOutcome(
            sizing=None,
            unavailable_reasons=(str(error),),
            contract=contract,
            contract_verified=verified,
            direction=direction,
        )

    return RiskOutcome(
        sizing=sizing,
        unavailable_reasons=(),
        contract=contract,
        contract_verified=verified,
        direction=direction,
    )


def _intended_direction(entry: Decimal | None, stop: Decimal | None) -> Direction | None:
    """The direction the user's own entry and stop imply.

    Definitional, not a market judgement: a stop below the entry is a long and
    a stop above it is a short. This layer never picks a direction *for* the
    user from the evidence — that would be the system deciding what trade
    somebody wants.
    """
    if entry is None or stop is None or entry == stop:
        return None
    return Direction.LONG if stop < entry else Direction.SHORT


def _assess_suitability(
    analysis: MultiTimeframeAnalysis | None,
    risk: RiskOutcome,
) -> tuple[tuple[EvidenceDirection, NoTradeAssessment], ...]:
    """Run the veto for both directional cases."""
    if analysis is None:
        return ()
    return tuple(
        (direction, assess_no_trade(analysis, direction, sizing=risk.sizing))
        for direction in (EvidenceDirection.BULLISH, EvidenceDirection.BEARISH)
    )


def _missing_timeframes(datasets: tuple[TimeframeDataset, ...]) -> tuple[Timeframe, ...]:
    """Roles the recommended hierarchy wanted and the request did not supply.

    Reported explicitly. A missing 5M is a missing entry timeframe, not a
    neutral entry reading.
    """
    supplied = {dataset.timeframe for dataset in datasets}
    return tuple(timeframe for timeframe in _ROLE_BY_TIMEFRAME if timeframe not in supplied)


def _risk_settings_digest(request: AnalysisRequest) -> str | None:
    account = request.account
    policy = request.risk_policy
    return risk_digest(
        equity=account.equity if account is not None else None,
        used_margin=account.used_margin if account is not None else None,
        mode=policy.mode.value if policy is not None else None,
        fixed_risk=policy.fixed_risk if policy is not None else None,
        risk_ratio=policy.risk_ratio if policy is not None else None,
        max_contracts=policy.max_contracts if policy is not None else None,
        entry_price=request.entry_price,
        stop_price=request.stop_price,
    )
