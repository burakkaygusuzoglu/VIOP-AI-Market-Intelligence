/**
 * The frontend's own vocabulary (§4).
 *
 * React components consume these, never raw backend JSON. The boundary buys
 * three things:
 *
 * 1. **Semantics survive the wire.** `WAIT` and `NO_TRADE` are different
 *    states with different meanings, and a component that received a bare
 *    string would eventually treat them as two shades of "no".
 * 2. **Unknown values fail loudly.** A backend enum this build has never heard
 *    of becomes `UNKNOWN` at the mapper, not a crash three components deep and
 *    not a silent default that reads as "fine".
 * 3. **No recalculation.** Every number here arrives finished. The frontend
 *    formats; it never computes. There is no arithmetic in this directory and
 *    an architecture test enforces it.
 *
 * These mirror backend semantics exactly. Where a name differs from the
 * backend's, it is because the backend name is not meaningful to a reader -
 * never because the frontend has its own idea of what the value means.
 */

// ----------------------------------------------------------------------
// Final action - the four states, and their meanings kept apart
// ----------------------------------------------------------------------

export type FinalAction = 'LONG' | 'SHORT' | 'WAIT' | 'NO_TRADE';

/**
 * How an action should read to a user.
 *
 * `WAIT` is deliberately not `negative`. It means a setup may exist and
 * something has not happened yet; painting it the same red as NO_TRADE would
 * tell the user the trade is refused when it is merely early (§8).
 */
export type ActionTone = 'bullish' | 'bearish' | 'waiting' | 'blocked';

export const ACTION_TONE: Record<FinalAction, ActionTone> = {
  LONG: 'bullish',
  SHORT: 'bearish',
  WAIT: 'waiting',
  NO_TRADE: 'blocked',
};

// ----------------------------------------------------------------------
// System status - never a market opinion
// ----------------------------------------------------------------------

/**
 * Why there is no synthesis. **None of these is an action** (§23).
 *
 * A provider timeout is not WAIT. An unconfigured model is not NO_TRADE. The
 * union is deliberately disjoint from `FinalAction` so the two cannot be
 * assigned to one another even by accident.
 */
export type SystemStatus =
  | 'NOT_CONFIGURED'
  | 'PROVIDER_FAILURE'
  | 'INVALID_OUTPUT'
  | 'CONTEXT_TOO_LARGE'
  | 'CONTEXT_UNAVAILABLE'
  | 'ANALYSIS_UNAVAILABLE';

// ----------------------------------------------------------------------
// Provenance - where a value came from
// ----------------------------------------------------------------------

/**
 * Mirrors the backend's `DataSourcePriority` and `AuthorityClass`.
 *
 * The frontend does not invent a provenance and does not re-rank these: the
 * ordering is the backend's, and a badge only reports what it was told (§20).
 */
export type Provenance =
  | 'CALCULATED'
  | 'STRUCTURED_DATA'
  | 'USER_CONFIRMED'
  | 'VISION_READ'
  | 'AI_INFERENCE'
  | 'UNVERIFIED'
  | 'MISSING';

/** Whether a value may be presented as authoritative for a calculation. */
export function isAuthoritative(source: Provenance): boolean {
  return source === 'CALCULATED' || source === 'STRUCTURED_DATA';
}

// ----------------------------------------------------------------------
// Data quality and confirmation - five states, not two
// ----------------------------------------------------------------------

/**
 * §15: these must not collapse into each other. "Not evaluated" is not a pass
 * and "missing" is not neutral.
 */
export type DataQuality =
  'GOOD' | 'PARTIAL' | 'BLOCKED' | 'MISSING' | 'NOT_EVALUATED' | 'UNVERIFIED';

/** §16: a forming 5M signal must never look like a confirmed entry. */
export type ConfirmationState = 'CONFIRMED' | 'FORMING' | 'PENDING' | 'POINT_IN_TIME' | 'UNKNOWN';

// ----------------------------------------------------------------------
// Numbers, with their origin attached
// ----------------------------------------------------------------------

/**
 * One number, its raw backend text, and where it came from.
 *
 * `raw` is the exact string the backend sent and is never mutated - it is what
 * an audit view shows and what a tooltip reveals. Display formatting happens
 * at render time from `raw`, so a rounded figure can never become the stored
 * value (§5).
 */
export interface NumericFact {
  readonly id: string;
  readonly label: string;
  /**
   * The account currency this money is denominated in, or `null` (§12).
   *
   * Only ever a code the **user supplied**. It is not inferred from locale,
   * not defaulted to TRY because VİOP is a Turkish exchange, and not derived
   * from the instrument. No currency means no symbol is rendered, which is the
   * honest outcome: a number wearing the wrong currency is worse than a number
   * wearing none.
   */
  readonly currency?: string | null;
  /** Exact backend representation. Never rounded, never re-parsed to a float. */
  readonly raw: string;
  readonly unit: NumericUnit;
  readonly source: Provenance;
}

/**
 * What kind of quantity a number is, which decides how it is displayed (§6).
 *
 * Not a formatting instruction - a semantic one. "Two decimals" is a
 * consequence; "this is a price" is the fact.
 */
export type NumericUnit =
  | 'price'
  | 'percentage'
  | 'score'
  | 'ratio'
  | 'contracts'
  | 'currency'
  | 'indicator'
  | 'timestamp'
  | 'unknown';

// ----------------------------------------------------------------------
// Evidence, risk, timeframes
// ----------------------------------------------------------------------

export type EvidenceDirection = 'BULLISH' | 'BEARISH' | 'NEUTRAL' | 'UNAVAILABLE';
export type EvidenceStrength = 'WEAK' | 'MODERATE' | 'STRONG';

export interface EvidenceItem {
  readonly id: string;
  readonly direction: EvidenceDirection;
  readonly strength: EvidenceStrength;
  readonly category: string;
  readonly reason: string;
  readonly timeframe: string | null;
  readonly confirmation: ConfirmationState;
  readonly source: Provenance;
}

export type FindingSeverity = 'BLOCKING' | 'PENDING' | 'CAUTION';

export interface Finding {
  readonly id: string;
  readonly code: string;
  readonly severity: FindingSeverity;
  readonly detail: string;
}

/** The canonical roles. Never averaged, never reordered (§17). */
export type TimeframeRole = 'REGIME' | 'BIAS' | 'SETUP' | 'ENTRY';

export interface DataQualityIssue {
  readonly code: string;
  readonly severity: string;
  readonly message: string;
}

export interface TimeframeReading {
  readonly role: TimeframeRole;
  readonly timeframe: string;
  readonly direction: EvidenceDirection;
  readonly confirmation: ConfirmationState;
  readonly summary: string;
  /** Data-quality findings, blocking ones first and bounded (§13). */
  readonly issues?: readonly DataQualityIssue[];
  /** Findings not listed. Never includes a blocking one. */
  readonly omittedIssueCount?: number;
}

export const ROLE_LABEL: Record<TimeframeRole, string> = {
  REGIME: 'Rejim',
  BIAS: 'Eğilim',
  SETUP: 'Kurulum',
  ENTRY: 'Giriş',
};

// ----------------------------------------------------------------------
// Setup quality - a heuristic score, never a probability
// ----------------------------------------------------------------------

/**
 * §9: `72` must never read as "72% chance".
 *
 * `label` carries the backend's own wording (which says HEURISTIC) and
 * `outOf` is present so the number is always rendered as `72 / 100` - a score
 * out of a total, which is much harder to misread as a percentage than a bare
 * `72` sitting next to a progress bar.
 */
export interface SetupQuality {
  readonly score: number | null;
  readonly outOf: number;
  readonly label: string;
  readonly isHeuristic: true;
}

// ----------------------------------------------------------------------
// Risk
// ----------------------------------------------------------------------

export type SizingOutcome = 'ALLOWED' | 'NOT_PERMITTED' | 'UNDETERMINED' | 'INVALID';

export interface RiskSummary {
  readonly outcome: SizingOutcome;
  readonly detail: string;
  readonly facts: readonly NumericFact[];
  readonly findings: readonly Finding[];
  /**
   * Why there is no risk answer. Non-empty only when `outcome` is
   * `UNDETERMINED`, and shown instead of - never alongside - a number.
   */
  readonly unavailableReasons?: readonly string[];
  readonly warnings?: readonly string[];
  readonly contract?: ContractInfo | null;
}

// ----------------------------------------------------------------------
// The whole analysis, as a screen consumes it
// ----------------------------------------------------------------------

export interface AnalysisContextInfo {
  readonly symbol: string;
  readonly analysisAsOf: string | null;
  readonly contextDigest: string | null;
  readonly analysisId?: string;
  readonly generatedAt?: string;
  /** Always true in Phase 8. Nothing is stored; a refresh loses the result. */
  readonly ephemeral?: boolean;
  readonly contractVerified?: boolean;
}

// ----------------------------------------------------------------------
// Scenarios, contradictions and the chart
// ----------------------------------------------------------------------

export type ScenarioCase = 'BULL' | 'BEAR' | 'NEUTRAL';

/**
 * §31: the three cases are **not** shares of a total.
 *
 * Nothing sums them and nothing normalises them against each other. Each
 * carries its own independently scored quality, and the neutral case carries
 * none at all - the Phase 4 model scores the coherence of a *directional*
 * case, so inventing a neutral score would be a number with no meaning.
 */
export interface ScenarioReadModel {
  readonly case: ScenarioCase;
  readonly state: string;
  readonly reason: string;
  readonly quality: SetupQuality | null;
  readonly entryScore: number | null;
  readonly supporting: readonly EvidenceItem[];
  readonly counter: readonly EvidenceItem[];
  readonly requirements: readonly string[];
  readonly invalidations: readonly string[];
  readonly components: readonly QualityComponent[];
}

export interface QualityComponent {
  readonly component: string;
  readonly awarded: number | null;
  readonly weight: number;
  readonly availability: string;
}

export interface Contradiction {
  readonly id: string;
  readonly type: string;
  readonly severity: string;
  readonly detail: string;
}

export interface Candle {
  readonly openTime: string;
  readonly open: string;
  readonly high: string;
  readonly low: string;
  readonly close: string;
  readonly volume: string;
  readonly isClosed: boolean;
}

/**
 * One Phase 1 indicator reading, or an honest absence (§10).
 *
 * `available: false` means the indicator's warm-up has not elapsed. It is
 * never a zero - a zero ATR reads as "no volatility measured" rather than
 * "not enough candles yet".
 */
export interface TechnicalReading {
  readonly key: string;
  readonly label: string;
  readonly raw: string;
  readonly unit: NumericUnit;
  readonly available: boolean;
  readonly unavailableReason: string;
  /** In the Beginner subset. Pro sees more of the same list, not another one. */
  readonly beginner: boolean;
}

export interface TechnicalPanel {
  readonly timeframe: string;
  readonly role: TimeframeRole;
  readonly readings: readonly TechnicalReading[];
}

/**
 * A per-bar indicator series aligned one-to-one with the drawn candles (§11).
 *
 * Produced by the backend and sliced to the display window, so a point can
 * never sit beside a bar that is not drawn. `null` where the indicator had no
 * value; the chart breaks the line rather than interpolating one.
 */
export interface ChartOverlay {
  readonly key: string;
  readonly label: string;
  readonly values: readonly (string | null)[];
}

export type ZoneKind = 'SUPPORT' | 'RESISTANCE' | 'UNKNOWN';

export interface Zone {
  readonly id: string;
  readonly kind: ZoneKind;
  readonly lower: string;
  readonly upper: string;
  readonly score: number | null;
}

/**
 * One timeframe's bars, and the only price data the chart may draw (§24).
 *
 * The chart layer receives this and nothing else. It does not compute a
 * moving average, does not synthesise a bar, and does not know how to make a
 * price up - an architecture test enforces that it has no arithmetic.
 */
export interface ChartSeries {
  readonly timeframe: string;
  readonly candles: readonly Candle[];
  readonly zones: readonly Zone[];
  /** Every candle the engines read, which is more than the chart draws. */
  readonly analysedCount: number;
  /** Analysed but older than the display window. Absent, never summarised. */
  readonly omittedCount: number;
  readonly windowPolicy: string;
  readonly overlays: readonly ChartOverlay[];
}

// ----------------------------------------------------------------------
// Synthesis narrative
// ----------------------------------------------------------------------

/**
 * One piece of model narrative.
 *
 * A `fact` segment carries a value Python rendered from the deterministic
 * registry, so the number on screen is the analysis's, not the model's. An
 * `observation` is also Python-rendered but was *read off a picture* rather
 * than calculated, and must be shown as the weaker thing it is.
 */
export type NarrativeSegment =
  | { readonly kind: 'text'; readonly text: string }
  | {
      readonly kind: 'fact' | 'observation';
      readonly id: string;
      readonly label: string;
      readonly value: string;
      readonly source: Provenance;
    };

export type SynthesisStatus =
  | 'SUCCESS'
  | 'NOT_CONFIGURED'
  | 'NOT_APPLICABLE'
  | 'PROVIDER_FAILURE'
  | 'INVALID_OUTPUT'
  | 'CONTEXT_TOO_LARGE'
  | 'CONTEXT_UNAVAILABLE'
  | 'UNKNOWN';

export interface SynthesisReadModel {
  readonly status: SynthesisStatus;
  readonly detail: string;
  readonly summary: readonly NarrativeSegment[];
  readonly bullCase: readonly NarrativeSegment[];
  readonly bearCase: readonly NarrativeSegment[];
  readonly neutralCase: readonly NarrativeSegment[];
  readonly devilsAdvocate: readonly NarrativeSegment[];
}

// ----------------------------------------------------------------------
// Suitability and contract metadata
// ----------------------------------------------------------------------

// ----------------------------------------------------------------------
// Why explanations
// ----------------------------------------------------------------------

export type ReasonSeverity = 'INFO' | 'NOTABLE' | 'CRITICAL';

/**
 * One traceable cause, in both registers.
 *
 * `beginner` and `pro` are two renderings of the same fact, produced together
 * by the backend Why Engine from one breakdown. The frontend picks which to
 * show; it never writes either, and there is no explanation engine here.
 */
export interface WhyReason {
  readonly code: string;
  readonly source: string;
  readonly severity: ReasonSeverity;
  readonly beginner: string;
  readonly pro: string;
  readonly timeframe: string | null;
  readonly role: string | null;
}

/**
 * Why one conclusion holds - or an honest statement that it cannot be said.
 *
 * `available: false` is a real answer. A position-size explanation for an
 * analysis that never sized a position explains nothing, so the topic carries
 * its reason instead of an invented breakdown.
 */
export interface WhyExplanation {
  readonly topic: string;
  readonly subject: string;
  readonly available: boolean;
  readonly unavailableReason: string;
  readonly reasons: readonly WhyReason[];
}

export interface Suitability {
  readonly direction: EvidenceDirection;
  /** Three states. `null` is "could not determine", never permission. */
  readonly noTrade: boolean | null;
  readonly findings: readonly Finding[];
  readonly missingRequirements: readonly string[];
}

export interface ContractInfo {
  readonly symbol: string;
  readonly verified: boolean;
  readonly multiplier: string | null;
  readonly multiplierStatus: string | null;
  readonly tickSize: string | null;
  readonly tickSizeStatus: string | null;
  /** Asset class of the contract record (Phase 8.5), never inferred from a symbol. */
  readonly assetClass?: string | null;
  readonly assetClassStatus?: string | null;
}

/**
 * Either an analysis to show, or an honest reason there is none.
 *
 * A discriminated union rather than a nullable analysis, so a component cannot
 * render a half-empty dashboard by forgetting a check (§3, §39).
 */
export type AnalysisView =
  | { readonly kind: 'unavailable'; readonly status: SystemStatus; readonly detail: string }
  | { readonly kind: 'analysis'; readonly analysis: AnalysisReadModel };

export interface AnalysisReadModel {
  readonly context: AnalysisContextInfo;
  /** `null` when no synthesis was accepted - which is not an action (§8). */
  readonly finalAction: FinalAction | null;
  readonly systemStatus: SystemStatus | null;
  readonly allowedActions: readonly FinalAction[];
  readonly setupQuality: SetupQuality;
  readonly risk: RiskSummary;
  readonly timeframes: readonly TimeframeReading[];
  readonly evidence: readonly EvidenceItem[];
  readonly findings: readonly Finding[];
  readonly missing: readonly string[];
  readonly facts: readonly NumericFact[];
  readonly dataQuality: DataQuality;

  /** Phase 8 additions. Optional so Phase 8A fixtures stay valid. */
  readonly scenarios?: readonly ScenarioReadModel[];
  readonly contradictions?: readonly Contradiction[];
  readonly chart?: readonly ChartSeries[];
  readonly synthesis?: SynthesisReadModel;
  readonly suitability?: readonly Suitability[];
  readonly missingTimeframes?: readonly string[];
  readonly technicalAvailable?: boolean;
  readonly why?: readonly WhyExplanation[];
  readonly technical?: readonly TechnicalPanel[];
}
