import { z } from 'zod';
import { getJson, postJson } from './client';

/**
 * Shadow research transport (Phase 14 Part 2A). Observation only.
 *
 * ## Every response is checked for what it claims to be
 *
 * Provenance and market currency are `z.literal`s, and `execution_enabled` is
 * `z.literal(false)`: a response claiming an exchange feed, a current market
 * or an order path fails to parse and never reaches the screen.
 *
 * ## There is nowhere to put money
 *
 * No schema below has a profit, a fill, a return, a win or a loss field. A
 * development says where *price* went relative to proposed levels; parsing
 * would reject a backend that started sending a profit, because a strict
 * object with no such key cannot silently carry one into the UI.
 *
 * ## The browser asks; it never answers
 *
 * The one request body names a live session, a registered strategy id and
 * version, timeframes and an attempt key. There is no field for a decision,
 * an outcome, an approval, metadata or a status.
 */

const provenance = z.literal('SIMULATED_HISTORICAL_STREAM');
const currency = z.literal('HISTORICAL');

export const OUTCOMES = [
  'NO_SIGNAL',
  'WAIT',
  'ENTRY_INTENT',
  'EXIT_INTENT',
  'UNAVAILABLE',
  'REFUSED',
] as const;
export const FINANCIAL = [
  'NOT_APPLICABLE',
  'NOT_CONFIGURED',
  'METADATA_UNAVAILABLE',
  'APPROVED',
  'REFUSED',
  'UNDETERMINED',
] as const;
export const DEVELOPMENT_STATES = [
  'NOT_EVALUATED',
  'PENDING',
  'OBSERVED',
  'UNAVAILABLE',
  'INVALIDATED',
] as const;
export const LEVEL_EVENTS = [
  'NONE_REACHED',
  'STOP_LEVEL_TOUCHED',
  'TARGET_LEVEL_TOUCHED',
  'BOTH_LEVELS_TOUCHED_SAME_BAR',
  'NOT_OBSERVED',
] as const;
export const COMPLETENESS = ['OBSERVING', 'COMPLETE', 'PARTIAL', 'INTERRUPTED'] as const;

const timeframe = z.enum(['5M', '15M', '1H', '1D']);

export const shadowCapabilitySchema = z
  .object({
    available: z.boolean(),
    reason: z.string(),
    provenance,
    market_currency: currency,
    financial_metadata_available: z.boolean(),
    execution_enabled: z.literal(false),
    strategies: z.array(
      z.object({ strategy_id: z.string(), versions: z.array(z.string()) }).strict(),
    ),
    limits: z
      .object({
        max_runs: z.number().int(),
        max_observations: z.number().int(),
        max_journal_page: z.number().int(),
        max_outcome_page: z.number().int(),
        max_outcome_window: z.number().int(),
        max_open_watches: z.number().int(),
      })
      .strict(),
    server_time: z.string(),
  })
  .strict();

export const shadowRunSchema = z
  .object({
    run_id: z.string(),
    configuration: z.string(),
    source_id: z.string(),
    instrument_label: z.string(),
    provenance,
    market_currency: currency,
    strategy_id: z.string(),
    strategy_version: z.string(),
    strategy_parameters: z.record(z.string()),
    driver: timeframe,
    timeframes: z.array(timeframe),
    required_timeframes: z.array(timeframe),
    status: z.enum(['OBSERVING', 'ENDED']),
    end_reason: z.string().nullable(),
    failure_code: z.string().nullable(),
    observations: z.number().int(),
    decisions: z.number().int(),
    entries: z.number().int(),
    first_boundary: z.string().nullable(),
    last_boundary: z.string().nullable(),
    started_at: z.string(),
    ended_at: z.string().nullable(),
    completeness: z.enum(COMPLETENESS),
  })
  .strict();

const runListSchema = z
  .object({ items: z.array(shadowRunSchema), total: z.number().int() })
  .strict();

const timeframeEvidenceSchema = z
  .object({
    timeframe,
    available: z.boolean(),
    freshness: z.string(),
    integrity: z.string(),
    confirmed_count: z.number().int(),
    reasons: z.array(z.string()),
    last_coverage_end: z.string().nullable(),
  })
  .strict();

const readingsSchema = z
  .object({
    timeframe,
    ema_fast: z.string().nullable(),
    ema_slow: z.string().nullable(),
    rsi: z.string().nullable(),
    atr: z.string().nullable(),
    adx: z.string().nullable(),
  })
  .strict();

const evidenceSchema = z
  .object({
    provenance,
    market_currency: currency,
    connection: z.string(),
    bars_available: z.number().int(),
    included: z.array(timeframe),
    excluded: z.array(timeframeEvidenceSchema),
    timeframes: z.array(timeframeEvidenceSchema),
    readings: z.array(readingsSchema),
    regime: z.string().nullable(),
    suitability: z.string().nullable(),
    setup_quality: z.string().nullable(),
    analysis_market_as_of: z.string().nullable(),
  })
  .strict();

export const developmentSchema = z
  .object({
    state: z.enum(DEVELOPMENT_STATES),
    event: z.enum(LEVEL_EVENTS),
    rules: z.string(),
    observed_from: z.string().nullable(),
    observed_to: z.string().nullable(),
    candles_observed: z.number().int(),
    event_at: z.string().nullable(),
    target_ordinal: z.number().int().nullable(),
    best_price: z.string().nullable(),
    worst_price: z.string().nullable(),
    last_close: z.string().nullable(),
    ambiguous: z.boolean(),
    unresolved_reason: z.string().nullable(),
    recorded_at: z.string().nullable(),
    decision_boundary: z.string().nullable(),
  })
  .strict();

export const shadowEntrySchema = z
  .object({
    kind: z.enum(['DECISION', 'OPERATIONAL']),
    sequence: z.number().int(),
    decision_key: z.string(),
    market_boundary: z.string().nullable(),
    recorded_at: z.string(),
    outcome: z.enum(OUTCOMES).nullable(),
    operational: z.string().nullable(),
    strategy_kind: z.string().nullable(),
    reason: z.string(),
    direction: z.enum(['LONG', 'SHORT']).nullable(),
    entry: z
      .object({
        direction: z.enum(['LONG', 'SHORT']),
        intended_entry: z.string(),
        stop: z.string(),
        targets: z.array(z.tuple([z.string(), z.number().int()])),
        requested_quantity: z.number().int(),
        approved_quantity: z.number().int().nullable(),
      })
      .strict()
      .nullable(),
    financial_state: z.enum(FINANCIAL),
    risk_outcome: z.string().nullable(),
    risk_reason: z.string().nullable(),
    evidence: evidenceSchema.nullable(),
    input_fingerprint: z.string().nullable(),
    development: developmentSchema.nullable(),
  })
  .strict();

export const journalPageSchema = z
  .object({
    run_id: z.string(),
    items: z.array(shadowEntrySchema),
    total: z.number().int(),
    next_after: z.number().int().nullable(),
    server_time: z.string(),
  })
  .strict();

export const outcomeSchema = z
  .object({
    decision_key: z.string(),
    sequence: z.number().int(),
    outcome_key: z.string(),
    recorded_at: z.string(),
    decision_boundary: z.string(),
    direction: z.enum(['LONG', 'SHORT']),
    development: developmentSchema,
  })
  .strict();

export const outcomePageSchema = z
  .object({
    run_id: z.string(),
    items: z.array(outcomeSchema),
    total: z.number().int(),
    next_after: z.number().int().nullable(),
    server_time: z.string(),
  })
  .strict();

export type ShadowCapabilityDto = z.infer<typeof shadowCapabilitySchema>;
export type ShadowRunDto = z.infer<typeof shadowRunSchema>;
export type ShadowRunListDto = z.infer<typeof runListSchema>;
export type ShadowEntryDto = z.infer<typeof shadowEntrySchema>;
export type ShadowJournalPageDto = z.infer<typeof journalPageSchema>;
export type ShadowDevelopmentDto = z.infer<typeof developmentSchema>;
export type ShadowOutcomeDto = z.infer<typeof outcomeSchema>;
export type ShadowOutcomePageDto = z.infer<typeof outcomePageSchema>;

/** What a person chooses. Nothing here is a market fact or a result. */
export interface CreateShadowPayload {
  readonly session_id: string;
  readonly strategy_id: string;
  readonly strategy_version: string;
  readonly driver: '5M' | '15M' | '1H' | '1D';
  readonly timeframes: readonly ('5M' | '15M' | '1H' | '1D')[];
  readonly analysis_evidence: boolean;
  readonly attempt_key: string;
}

const BASE = '/shadow';

function init(signal?: AbortSignal): RequestInit | undefined {
  return signal ? { signal } : undefined;
}

export function getShadowCapability(signal?: AbortSignal): Promise<ShadowCapabilityDto> {
  return getJson(`${BASE}/capability`, shadowCapabilitySchema, init(signal));
}

export function listShadowRuns(
  offset: number,
  limit: number,
  signal?: AbortSignal,
): Promise<ShadowRunListDto> {
  return getJson(`${BASE}/runs?offset=${offset}&limit=${limit}`, runListSchema, init(signal));
}

export function getShadowRun(runId: string, signal?: AbortSignal): Promise<ShadowRunDto> {
  return getJson(`${BASE}/runs/${encodeURIComponent(runId)}`, shadowRunSchema, init(signal));
}

export function createShadowRun(
  payload: CreateShadowPayload,
  signal?: AbortSignal,
): Promise<ShadowRunDto> {
  return postJson(`${BASE}/runs`, payload, shadowRunSchema, init(signal));
}

export function cancelShadowRun(runId: string, signal?: AbortSignal): Promise<ShadowRunDto> {
  return postJson(
    `${BASE}/runs/${encodeURIComponent(runId)}/cancel`,
    {},
    shadowRunSchema,
    init(signal),
  );
}

export function readShadowJournal(
  runId: string,
  after: number,
  limit: number,
  signal?: AbortSignal,
): Promise<ShadowJournalPageDto> {
  return getJson(
    `${BASE}/runs/${encodeURIComponent(runId)}/journal?after=${after}&limit=${limit}`,
    journalPageSchema,
    init(signal),
  );
}

export function readShadowOutcomes(
  runId: string,
  after: number,
  limit: number,
  signal?: AbortSignal,
): Promise<ShadowOutcomePageDto> {
  return getJson(
    `${BASE}/runs/${encodeURIComponent(runId)}/outcomes?after=${after}&limit=${limit}`,
    outcomePageSchema,
    init(signal),
  );
}
