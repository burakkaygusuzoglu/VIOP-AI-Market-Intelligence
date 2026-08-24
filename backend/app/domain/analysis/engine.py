"""The assembled Phase 4A view: timeframes, evidence, contradictions.

One entry point. It takes timeframe views the Phase 1-3 engines have already
produced, translates them into evidence, and compares them.

It calculates **no** indicator, swing, structural event, zone, breakout,
regime, contract or risk figure of its own. Every number it reports was
computed by the engine that owns that formula; there is exactly one
implementation of each in the codebase and none of them is here.

Phase 4B - setup quality, entry quality, the NO TRADE engine and the
Bull/Bear/Neutral scenarios - is not present. Nothing in this module converts
evidence into a recommendation, and nothing produces a score, a probability or
a LONG/SHORT verdict.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.analysis.contradictions import (
    ContradictionConfig,
    ContradictionReport,
    detect_contradictions,
)
from app.domain.analysis.evidence import EvidenceItem
from app.domain.analysis.fusion import FusedEvidence, fuse_evidence
from app.domain.analysis.generation import (
    EvidenceConfig,
    build_contract_evidence,
    build_timeframe_evidence,
)
from app.domain.analysis.scenarios import ScenarioConfig, ScenarioSet, build_scenarios
from app.domain.analysis.timeframes import (
    MultiTimeframeView,
    TimeframeRole,
    TimeframeRolePolicy,
    TimeframeView,
)
from app.domain.futures.basis import BasisResult
from app.domain.futures.open_interest import OpenInterestReading


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Every Phase 4A setting in one frozen object.

    Held together for the same reason `TechnicalConfig` and `StructureConfig`
    are: a replay, a backtest and a live run can then be shown to have used
    identical rules.
    """

    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    contradictions: ContradictionConfig = field(default_factory=ContradictionConfig)
    scenarios: ScenarioConfig = field(default_factory=ScenarioConfig)


@dataclass(frozen=True, slots=True)
class MultiTimeframeAnalysis:
    """What Phase 4A can establish from a set of analysed timeframes."""

    symbol: str
    config: AnalysisConfig
    views: MultiTimeframeView

    evidence: tuple[EvidenceItem, ...]
    """Timeframe evidence, ordered by role from broadest to narrowest and, in
    each role, in the fixed order the builders run."""

    contract_evidence: tuple[EvidenceItem, ...]
    """Basis and open-interest context. Timeframe-independent, and neutral by
    construction - see ``generation.py``."""

    contradictions: ContradictionReport

    fused: FusedEvidence
    """The §16 fusion: evidence partitioned by direction and grouped by
    category, so a category speaks once however many records it produced."""

    scenarios: ScenarioSet
    """Bull, bear and neutral, each with its own state and - for the
    directional pair - its own independently scored quality. The three are
    deliberately not normalised against each other."""

    @property
    def policy(self) -> TimeframeRolePolicy:
        return self.views.policy

    def evidence_for(self, role: TimeframeRole) -> tuple[EvidenceItem, ...]:
        """Evidence from one timeframe.

        Indices only mean anything within one timeframe, so this is the right
        unit to hand to ``evidence_known_at``.
        """
        return tuple(item for item in self.evidence if item.role is role)

    @property
    def all_evidence(self) -> tuple[EvidenceItem, ...]:
        return self.evidence + self.contract_evidence


def analyse_multi_timeframe(
    views: tuple[TimeframeView, ...],
    *,
    policy: TimeframeRolePolicy | None = None,
    basis: BasisResult | None = None,
    open_interest: OpenInterestReading | None = None,
    config: AnalysisConfig | None = None,
) -> MultiTimeframeAnalysis:
    """Analyse a set of timeframe views together.

    Validation comes first and refuses rather than repairs: a swapped,
    duplicated or internally inconsistent set of views raises
    `MultiTimeframeError`. A *missing* timeframe is not an error - it stays
    missing through evidence generation and is reported as an unavailable role,
    never as a neutral one.

    ``basis`` and ``open_interest`` are optional Phase 3 readings for the
    contract being analysed. Supplying them adds context; omitting them adds
    nothing, and in particular does not add a neutral placeholder.
    """
    settings = config if config is not None else AnalysisConfig()
    validated = MultiTimeframeView.build(views, policy)

    evidence = tuple(
        item
        for view in validated.views
        for item in build_timeframe_evidence(view, settings.evidence)
    )
    contract_evidence = build_contract_evidence(basis, open_interest)
    report = detect_contradictions(validated, evidence, settings.contradictions)
    fused = fuse_evidence(evidence, contract_evidence, report)

    return MultiTimeframeAnalysis(
        symbol=validated.symbol,
        config=settings,
        views=validated,
        evidence=evidence,
        contract_evidence=contract_evidence,
        contradictions=report,
        fused=fused,
        scenarios=build_scenarios(validated, fused, settings.scenarios),
    )
