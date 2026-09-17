import type {
  AnalysisResponseDto,
  EvidenceDto,
  ScenarioDto,
  SegmentDto,
  SuitabilityDto,
} from './analysis';
import type {
  AnalysisReadModel,
  ChartSeries,
  ConfirmationState,
  Contradiction,
  DataQuality,
  EvidenceDirection,
  EvidenceItem,
  EvidenceStrength,
  Finding,
  FinalAction,
  FindingSeverity,
  NarrativeSegment,
  NumericFact,
  NumericUnit,
  Provenance,
  ReasonSeverity,
  ScenarioCase,
  ScenarioReadModel,
  SizingOutcome,
  Suitability,
  SynthesisReadModel,
  SynthesisStatus,
  SystemStatus,
  TimeframeReading,
  TechnicalPanel,
  TimeframeRole,
  WhyExplanation,
  ZoneKind,
} from '../domain/models';

/**
 * Turn one validated DTO into the frontend's own vocabulary (§19).
 *
 * ## Unknown values fail *safely*, not silently
 *
 * Every enum crosses the wire as a string, and every one of them is narrowed
 * here through a lookup with an explicit fallback. A backend that grows a new
 * evidence strength, confirmation state or synthesis status does not crash the
 * page and does not get silently cast into a neighbouring meaning — it becomes
 * `UNKNOWN`, which components render as unknown.
 *
 * The fallbacks are chosen so that an unrecognised value is never mistaken for
 * good news. An unknown direction is `UNAVAILABLE`, not `NEUTRAL`: a gap in
 * understanding is not a balanced market. An unknown sizing outcome is
 * `UNDETERMINED`, not `ALLOWED`.
 *
 * ## Nothing is computed
 *
 * There is no arithmetic in this file. Values arrive finished and are renamed,
 * never recalculated — an architecture test enforces that across the tree.
 */

function oneOf<T extends string>(allowed: readonly T[], fallback: T) {
  const set = new Set<string>(allowed);
  return (value: string | null | undefined): T =>
    value !== null && value !== undefined && set.has(value) ? (value as T) : fallback;
}

const toDirection = oneOf<EvidenceDirection>(
  ['BULLISH', 'BEARISH', 'NEUTRAL', 'UNAVAILABLE'],
  'UNAVAILABLE',
);
const toStrength = oneOf<EvidenceStrength>(['WEAK', 'MODERATE', 'STRONG'], 'WEAK');
const toConfirmation = oneOf<ConfirmationState>(
  ['CONFIRMED', 'FORMING', 'PENDING', 'POINT_IN_TIME', 'UNKNOWN'],
  'UNKNOWN',
);
const toProvenance = oneOf<Provenance>(
  [
    'CALCULATED',
    'STRUCTURED_DATA',
    'USER_CONFIRMED',
    'VISION_READ',
    'AI_INFERENCE',
    'UNVERIFIED',
    'MISSING',
  ],
  'UNVERIFIED',
);
const toRole = oneOf<TimeframeRole>(['REGIME', 'BIAS', 'SETUP', 'ENTRY'], 'REGIME');
const toUnit = oneOf<NumericUnit>(
  [
    'price',
    'percentage',
    'score',
    'ratio',
    'contracts',
    'currency',
    'indicator',
    'timestamp',
    'unknown',
  ],
  'unknown',
);
const toSeverity = oneOf<FindingSeverity>(['BLOCKING', 'PENDING', 'CAUTION'], 'CAUTION');
const toReasonSeverity = oneOf<ReasonSeverity>(['INFO', 'NOTABLE', 'CRITICAL'], 'INFO');
const toScenarioCase = oneOf<ScenarioCase>(['BULL', 'BEAR', 'NEUTRAL'], 'NEUTRAL');
const toZoneKind = oneOf<ZoneKind>(['SUPPORT', 'RESISTANCE', 'UNKNOWN'], 'UNKNOWN');
const toFinalAction = oneOf<FinalAction>(['LONG', 'SHORT', 'WAIT', 'NO_TRADE'], 'NO_TRADE');
const toSynthesisStatus = oneOf<SynthesisStatus>(
  [
    'SUCCESS',
    'NOT_CONFIGURED',
    'NOT_APPLICABLE',
    'PROVIDER_FAILURE',
    'INVALID_OUTPUT',
    'CONTEXT_TOO_LARGE',
    'CONTEXT_UNAVAILABLE',
  ],
  'UNKNOWN',
);

/** Sizing outcome. Unknown becomes UNDETERMINED, never ALLOWED. */
const toSizingOutcome = (value: string): SizingOutcome => {
  if (value === 'ALLOWED') return 'ALLOWED';
  if (value === 'NOT_PERMITTED') return 'NOT_PERMITTED';
  if (value === 'INVALID') return 'INVALID';
  return 'UNDETERMINED';
};

function fact(dto: {
  id: string;
  label: string;
  raw: string;
  unit: string;
  source: string;
  currency: string | null;
}): NumericFact {
  return {
    id: dto.id,
    label: dto.label,
    raw: dto.raw,
    unit: toUnit(dto.unit),
    source: toProvenance(dto.source),
    currency: dto.currency,
  };
}

function evidence(dto: EvidenceDto): EvidenceItem {
  return {
    id: dto.id,
    direction: toDirection(dto.direction),
    strength: toStrength(dto.strength),
    category: dto.category,
    reason: dto.reason,
    timeframe: dto.timeframe,
    confirmation: toConfirmation(dto.confirmation),
    source: toProvenance(dto.source),
  };
}

function scenario(dto: ScenarioDto): ScenarioReadModel {
  return {
    case: toScenarioCase(dto.case),
    state: dto.state,
    reason: dto.reason,
    quality:
      dto.quality_score === null && dto.quality_label === ''
        ? null
        : {
            score: dto.quality_score,
            outOf: 100,
            label: dto.quality_label,
            isHeuristic: true,
          },
    entryScore: dto.entry_score,
    supporting: dto.supporting.map(evidence),
    counter: dto.counter.map(evidence),
    requirements: dto.requirements,
    invalidations: dto.invalidations,
    components: dto.components.map((item) => ({
      component: item.component,
      awarded: item.awarded,
      weight: item.weight,
      availability: item.availability,
    })),
  };
}

function segment(dto: SegmentDto): NarrativeSegment {
  if (dto.kind === 'fact' || dto.kind === 'observation') {
    return {
      kind: dto.kind,
      id: dto.fact_id ?? '',
      label: dto.fact_label ?? '',
      value: dto.fact_value ?? '',
      source: toProvenance(dto.fact_source ?? (dto.kind === 'fact' ? 'CALCULATED' : 'VISION_READ')),
    };
  }
  return { kind: 'text', text: dto.text };
}

function suitability(dto: SuitabilityDto): Suitability {
  return {
    direction: toDirection(dto.direction),
    noTrade: dto.no_trade,
    findings: dto.findings.map((item): Finding => ({
      id: item.id,
      code: item.code,
      severity: toSeverity(item.severity),
      detail: item.detail,
    })),
    missingRequirements: dto.missing_requirements,
  };
}

/**
 * Which case the workspace leads with.
 *
 * The higher setup-quality score, which the Phase 4 engine already computed.
 * This chooses what to show first; it never changes what any of them say, and
 * every case stays on screen.
 */
function leadScenario(scenarios: readonly ScenarioReadModel[]): ScenarioReadModel | null {
  const directional = scenarios.filter((item) => item.case !== 'NEUTRAL');
  if (directional.length === 0) return null;
  return directional.reduce((best, item) =>
    (item.quality?.score ?? -1) > (best.quality?.score ?? -1) ? item : best,
  );
}

/**
 * The system status for a synthesis that produced no action.
 *
 * `null` when synthesis succeeded. Deliberately never maps to a `FinalAction`:
 * a provider outage is a fact about this system, not about the market (§33).
 */
function systemStatusFor(status: SynthesisStatus): SystemStatus | null {
  switch (status) {
    case 'SUCCESS':
      return null;
    case 'PROVIDER_FAILURE':
      return 'PROVIDER_FAILURE';
    case 'INVALID_OUTPUT':
      return 'INVALID_OUTPUT';
    case 'CONTEXT_TOO_LARGE':
      return 'CONTEXT_TOO_LARGE';
    case 'CONTEXT_UNAVAILABLE':
      return 'CONTEXT_UNAVAILABLE';
    default:
      return 'NOT_CONFIGURED';
  }
}

/** Overall data quality, read off the timeframes rather than recomputed. */
function dataQualityOf(dto: AnalysisResponseDto): DataQuality {
  if (!dto.technical_available) return 'BLOCKED';
  if (dto.missing_timeframes.length > 0 || dto.input_errors.length > 0) return 'PARTIAL';
  if (dto.timeframes.some((item) => !item.usable)) return 'PARTIAL';
  return 'GOOD';
}

export function mapAnalysis(dto: AnalysisResponseDto): AnalysisReadModel {
  const scenarios = dto.scenarios.map(scenario);
  const lead = leadScenario(scenarios);
  const synthesisStatus = toSynthesisStatus(dto.synthesis.status);

  const synthesis: SynthesisReadModel = {
    status: synthesisStatus,
    detail: dto.synthesis.detail,
    summary: dto.synthesis.summary.map(segment),
    bullCase: dto.synthesis.bull_case.map(segment),
    bearCase: dto.synthesis.bear_case.map(segment),
    neutralCase: dto.synthesis.neutral_case.map(segment),
    devilsAdvocate: dto.synthesis.devils_advocate.map(segment),
  };

  const chart: ChartSeries[] = dto.chart.map((series) => ({
    timeframe: series.timeframe,
    candles: series.candles.map((candle) => ({
      openTime: candle.open_time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume,
      isClosed: candle.is_closed,
    })),
    zones: series.zones.map((zone) => ({
      id: zone.id,
      kind: toZoneKind(zone.kind),
      lower: zone.lower,
      upper: zone.upper,
      score: zone.score,
    })),
    overlays: series.overlays.map((overlay) => ({
      key: overlay.key,
      label: overlay.label,
      values: overlay.values,
    })),
    analysedCount: series.analysed_count,
    omittedCount: series.omitted_count,
    windowPolicy: series.window_policy,
  }));

  const timeframes: TimeframeReading[] = dto.timeframes.map((item) => ({
    role: toRole(item.role),
    timeframe: item.timeframe,
    direction: toDirection(item.direction),
    confirmation: toConfirmation(item.confirmation),
    summary: item.usable ? item.verdict : `Kullanılamaz: ${item.verdict}`,
    issues: item.issues.map((issue) => ({
      code: issue.code,
      severity: issue.severity,
      message: issue.message,
    })),
    omittedIssueCount: item.omitted_issue_count,
  }));

  const suitabilities = dto.suitability.map(suitability);
  const blocking = suitabilities.flatMap((item) =>
    item.findings.filter((finding) => finding.severity === 'BLOCKING'),
  );

  return {
    context: {
      symbol: dto.identity.symbol,
      analysisAsOf: dto.identity.analysis_as_of,
      contextDigest: dto.synthesis.context_digest || dto.identity.analysis_id,
      analysisId: dto.identity.analysis_id,
      generatedAt: dto.identity.generated_at,
      ephemeral: dto.identity.ephemeral,
      contractVerified: dto.identity.contract_metadata_verified,
    },
    // Only ever from an accepted synthesis. Absent otherwise (§22).
    finalAction:
      synthesisStatus === 'SUCCESS' && dto.synthesis.final_action !== null
        ? toFinalAction(dto.synthesis.final_action)
        : null,
    systemStatus: systemStatusFor(synthesisStatus),
    allowedActions: dto.synthesis.allowed_actions.map((action) => toFinalAction(action)),
    setupQuality: lead?.quality ?? {
      score: null,
      outOf: 100,
      label: '',
      isHeuristic: true,
    },
    risk: {
      outcome: toSizingOutcome(dto.risk.outcome),
      detail: dto.risk.detail,
      facts: dto.risk.facts.map(fact),
      findings: blocking,
      unavailableReasons: dto.risk.unavailable_reasons,
      warnings: dto.risk.warnings,
      contract: dto.risk.contract
        ? {
            symbol: dto.risk.contract.symbol,
            verified: dto.risk.contract.verified,
            multiplier: dto.risk.contract.multiplier,
            multiplierStatus: dto.risk.contract.multiplier_status,
            tickSize: dto.risk.contract.tick_size,
            tickSizeStatus: dto.risk.contract.tick_size_status,
            assetClass: dto.risk.contract.asset_class,
            assetClassStatus: dto.risk.contract.asset_class_status,
          }
        : null,
    },
    timeframes,
    evidence: dto.evidence.map(evidence),
    findings: suitabilities.flatMap((item) => item.findings),
    missing: dto.missing,
    facts: dto.facts.map(fact),
    dataQuality: dataQualityOf(dto),
    scenarios,
    contradictions: dto.contradictions.map((item): Contradiction => ({
      id: item.id,
      type: item.type,
      severity: item.severity,
      detail: item.detail,
    })),
    chart,
    synthesis,
    suitability: suitabilities,
    missingTimeframes: dto.missing_timeframes,
    technicalAvailable: dto.technical_available,
    technical: dto.technical.map((panel): TechnicalPanel => ({
      timeframe: panel.timeframe,
      role: toRole(panel.role),
      readings: panel.readings.map((reading) => ({
        key: reading.key,
        label: reading.label,
        raw: reading.raw,
        unit: toUnit(reading.unit),
        available: reading.available,
        unavailableReason: reading.unavailable_reason,
        beginner: reading.beginner,
      })),
    })),
    why: dto.why.map((item): WhyExplanation => ({
      topic: item.topic,
      subject: item.subject,
      available: item.available,
      unavailableReason: item.unavailable_reason,
      reasons: item.reasons.map((reason) => ({
        code: reason.code,
        source: reason.source,
        severity: toReasonSeverity(reason.severity),
        beginner: reason.beginner,
        pro: reason.pro,
        timeframe: reason.timeframe,
        role: reason.role,
      })),
    })),
  };
}
