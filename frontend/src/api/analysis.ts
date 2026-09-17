import { z } from 'zod';
import { postJson } from './client';

/**
 * The analysis API boundary (§19).
 *
 * Every field the backend sends is described here and parsed before it reaches
 * a component. Two consequences, both deliberate:
 *
 * 1. **A malformed payload fails loudly.** `postJson` throws rather than
 *    handing components a half-shaped object. A missing `risk` would otherwise
 *    surface as a blank risk panel, which reads as "no risk".
 * 2. **An unknown enum member does not crash.** Directions, states and
 *    provenances are `z.string()` here and are narrowed by the mapper, which
 *    maps anything it does not recognise to an explicit `UNKNOWN`. A backend
 *    that grows a new evidence strength must not blank the page.
 *
 * Numbers stay strings all the way across. The backend computed them as
 * `Decimal`; parsing them into JavaScript numbers at the boundary would undo
 * that before anything had a chance to display them.
 */

const numericFactSchema = z.object({
  id: z.string(),
  label: z.string(),
  raw: z.string(),
  unit: z.string(),
  source: z.string(),
  currency: z.string().nullable().default(null),
});

const issueSchema = z.object({
  code: z.string(),
  severity: z.string(),
  message: z.string(),
});

const timeframeResultSchema = z.object({
  timeframe: z.string(),
  role: z.string(),
  usable: z.boolean(),
  verdict: z.string(),
  candle_count: z.number(),
  direction: z.string(),
  confirmation: z.string(),
  issues: z.array(issueSchema).default([]),
  omitted_issue_count: z.number().default(0),
});

const candleSchema = z.object({
  open_time: z.string(),
  open: z.string(),
  high: z.string(),
  low: z.string(),
  close: z.string(),
  volume: z.string(),
  is_closed: z.boolean(),
});

const zoneSchema = z.object({
  id: z.string(),
  kind: z.string(),
  lower: z.string(),
  upper: z.string(),
  score: z.number().nullable().default(null),
});

const overlaySchema = z.object({
  key: z.string(),
  label: z.string(),
  values: z.array(z.string().nullable()).default([]),
});

const technicalReadingSchema = z.object({
  key: z.string(),
  label: z.string(),
  raw: z.string().default(''),
  unit: z.string().default('indicator'),
  available: z.boolean().default(true),
  unavailable_reason: z.string().default(''),
  beginner: z.boolean().default(false),
});

const technicalPanelSchema = z.object({
  timeframe: z.string(),
  role: z.string(),
  readings: z.array(technicalReadingSchema).default([]),
});

const chartSeriesSchema = z.object({
  timeframe: z.string(),
  candles: z.array(candleSchema),
  zones: z.array(zoneSchema).default([]),
  analysed_count: z.number().default(0),
  omitted_count: z.number().default(0),
  omitted_zone_count: z.number().default(0),
  window_policy: z.string().default(''),
  overlays: z.array(overlaySchema).default([]),
});

const evidenceSchema = z.object({
  id: z.string(),
  direction: z.string(),
  strength: z.string(),
  category: z.string(),
  reason: z.string(),
  timeframe: z.string().nullable(),
  confirmation: z.string(),
  source: z.string(),
});

const contradictionSchema = z.object({
  id: z.string(),
  type: z.string(),
  severity: z.string(),
  detail: z.string(),
});

const componentSchema = z.object({
  component: z.string(),
  awarded: z.number().nullable(),
  weight: z.number(),
  availability: z.string(),
});

const scenarioSchema = z.object({
  case: z.string(),
  state: z.string(),
  reason: z.string(),
  quality_score: z.number().nullable().default(null),
  quality_label: z.string().default(''),
  entry_score: z.number().nullable().default(null),
  supporting: z.array(evidenceSchema).default([]),
  counter: z.array(evidenceSchema).default([]),
  requirements: z.array(z.string()).default([]),
  invalidations: z.array(z.string()).default([]),
  components: z.array(componentSchema).default([]),
});

const findingSchema = z.object({
  id: z.string(),
  code: z.string(),
  severity: z.string(),
  detail: z.string(),
});

const suitabilitySchema = z.object({
  direction: z.string(),
  no_trade: z.boolean().nullable(),
  findings: z.array(findingSchema).default([]),
  missing_requirements: z.array(z.string()).default([]),
});

const contractSchema = z.object({
  symbol: z.string(),
  verified: z.boolean(),
  multiplier: z.string().nullable().default(null),
  multiplier_status: z.string().nullable().default(null),
  tick_size: z.string().nullable().default(null),
  tick_size_status: z.string().nullable().default(null),
});

const riskSchema = z.object({
  available: z.boolean(),
  outcome: z.string(),
  direction: z.string().nullable().default(null),
  detail: z.string().default(''),
  unavailable_reasons: z.array(z.string()).default([]),
  facts: z.array(numericFactSchema).default([]),
  warnings: z.array(z.string()).default([]),
  contract: contractSchema.nullable().default(null),
});

const segmentSchema = z.object({
  kind: z.string(),
  text: z.string().default(''),
  fact_id: z.string().nullable().default(null),
  fact_label: z.string().nullable().default(null),
  fact_value: z.string().nullable().default(null),
  fact_source: z.string().nullable().default(null),
});

const synthesisSchema = z.object({
  status: z.string(),
  detail: z.string().default(''),
  final_action: z.string().nullable().default(null),
  allowed_actions: z.array(z.string()).default([]),
  summary: z.array(segmentSchema).default([]),
  bull_case: z.array(segmentSchema).default([]),
  bear_case: z.array(segmentSchema).default([]),
  neutral_case: z.array(segmentSchema).default([]),
  devils_advocate: z.array(segmentSchema).default([]),
  context_digest: z.string().default(''),
});

const whyReasonSchema = z.object({
  code: z.string(),
  source: z.string(),
  severity: z.string(),
  beginner: z.string(),
  pro: z.string(),
  timeframe: z.string().nullable().default(null),
  role: z.string().nullable().default(null),
});

const whySchema = z.object({
  topic: z.string(),
  subject: z.string(),
  available: z.boolean().default(true),
  unavailable_reason: z.string().default(''),
  reasons: z.array(whyReasonSchema).default([]),
  omitted_reason_count: z.number().default(0),
});

const datasetIdentitySchema = z.object({
  timeframe: z.string(),
  digest: z.string(),
  source_name: z.string(),
  row_count: z.number(),
  usable: z.boolean(),
});

const identitySchema = z.object({
  analysis_id: z.string(),
  symbol: z.string(),
  analysis_as_of: z.string().nullable(),
  generated_at: z.string(),
  contract_metadata_verified: z.boolean(),
  datasets: z.array(datasetIdentitySchema).default([]),
  ephemeral: z.boolean().default(true),
});

export const analysisResponseSchema = z.object({
  identity: identitySchema,
  technical_available: z.boolean(),
  timeframes: z.array(timeframeResultSchema).default([]),
  missing_timeframes: z.array(z.string()).default([]),
  input_errors: z.array(z.string()).default([]),
  chart: z.array(chartSeriesSchema).default([]),
  evidence: z.array(evidenceSchema).default([]),
  omitted_evidence_count: z.number().default(0),
  contradictions: z.array(contradictionSchema).default([]),
  scenarios: z.array(scenarioSchema).default([]),
  suitability: z.array(suitabilitySchema).default([]),
  risk: riskSchema,
  synthesis: synthesisSchema,
  facts: z.array(numericFactSchema).default([]),
  missing: z.array(z.string()).default([]),
  technical: z.array(technicalPanelSchema).default([]),
  why: z.array(whySchema).default([]),
});

export type AnalysisResponseDto = z.infer<typeof analysisResponseSchema>;
export type EvidenceDto = z.infer<typeof evidenceSchema>;
export type ScenarioDto = z.infer<typeof scenarioSchema>;
export type ChartSeriesDto = z.infer<typeof chartSeriesSchema>;
export type CandleDto = z.infer<typeof candleSchema>;
export type ZoneDto = z.infer<typeof zoneSchema>;
export type SegmentDto = z.infer<typeof segmentSchema>;
export type SuitabilityDto = z.infer<typeof suitabilitySchema>;
export type WhyDto = z.infer<typeof whySchema>;
export type TechnicalPanelDto = z.infer<typeof technicalPanelSchema>;

/** What the user may send. There is no field here for a derived value. */
export interface AnalysisRequestPayload {
  readonly symbol: string;
  readonly datasets: ReadonlyArray<{
    readonly timeframe: string;
    readonly content: string;
    readonly source_name: string;
  }>;
  readonly account?: {
    readonly equity: string;
    readonly used_margin: string;
    readonly currency?: string;
  };
  readonly risk?: {
    readonly mode: string;
    readonly fixed_risk?: string;
    readonly risk_ratio?: string;
    readonly max_contracts?: number;
  };
  readonly entry_price?: string;
  readonly stop_price?: string;
}

export function requestAnalysis(
  payload: AnalysisRequestPayload,
  signal?: AbortSignal,
): Promise<AnalysisResponseDto> {
  // The key is omitted rather than set to `undefined`: under
  // `exactOptionalPropertyTypes`, `{signal: undefined}` is not a `RequestInit`.
  return postJson('/analysis', payload, analysisResponseSchema, signal ? { signal } : undefined);
}
