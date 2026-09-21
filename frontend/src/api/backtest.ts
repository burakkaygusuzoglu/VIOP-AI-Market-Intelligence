import { z } from 'zod';
import { getJson, postJson } from './client';
import { performanceSchema, type PerformanceDto } from './performance';

/**
 * Backtest transport (Phase 12 Part 2A). Simulation only.
 *
 * ## The browser asks a question; it never states an answer
 *
 * There is no request type in this file with a field for a fill, a P&L, a
 * metric, a decision, a risk approval, a digest, a status or a product fact.
 * The payload says which immutable dataset, which window, which registered
 * strategy and which policies - and the server computes everything else. A
 * field the server would not accept is not one this module can send.
 *
 * ## Every list is a page, and says how much it left out
 *
 * A run at the 2,500-boundary ceiling has 2,500 trace rows. None of these
 * calls ever asks for all of them: each page carries its own `total`, so a
 * bounded view is visibly bounded rather than quietly truncated.
 *
 * ## Money stays text
 *
 * Every amount is a string here and stays a string all the way to the screen.
 * Parsing one into a JavaScript number would round a value the backend spent
 * twelve phases keeping exact, and nothing in the frontend computes with them.
 */

const parameterSchema = z.object({
  name: z.string(),
  value: z.string(),
  configurable: z.boolean(),
});

export const strategySchema = z.object({
  identifier: z.string(),
  version: z.string(),
  summary: z.string(),
  warm_up_bars: z.number().int(),
  parameters: z.array(parameterSchema),
  directions: z.array(z.enum(['LONG', 'SHORT'])),
  exposure: z.string(),
  stop_model: z.string(),
  target_model: z.string(),
  entry_timing: z.string(),
});

export const strategyCatalogueSchema = z.object({
  strategies: z.array(strategySchema),
});

export const capabilitySchema = z.object({
  financial_execution_available: z.boolean(),
  reason: z.string(),
  refusal_code: z.string().nullable(),
  max_boundaries: z.number().int(),
  max_positions: z.number().int(),
  max_warm_up_bars: z.number().int(),
});

const datasetTimeframeSchema = z.object({
  timeframe: z.string(),
  candles: z.number().int(),
  first_open_time: z.string().nullable(),
  last_open_time: z.string().nullable(),
});

export const datasetSchema = z.object({
  dataset_id: z.string(),
  symbol: z.string(),
  total_rows: z.number().int(),
  timeframes: z.array(datasetTimeframeSchema),
  provenance: z.string(),
});

export const datasetListSchema = z.object({
  items: z.array(datasetSchema),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
});

const runStatusSchema = z.enum(['PENDING', 'RUNNING', 'COMPLETED', 'FAILED']);

export const runSummarySchema = z.object({
  run_id: z.string(),
  configuration: z.string(),
  dataset_id: z.string(),
  symbol: z.string(),
  driver_timeframe: z.string(),
  strategy_id: z.string(),
  strategy_version: z.string(),
  status: runStatusSchema,
  boundaries_evaluated: z.number().int(),
  position_count: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const runListSchema = z.object({
  items: z.array(runSummarySchema),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
});

export const runSchema = z.object({
  run_id: z.string(),
  status: runStatusSchema,
  results_are_final: z.boolean(),
  configuration_fingerprint: z.string(),
  configuration: z.object({
    dataset_id: z.string(),
    symbol: z.string(),
    driver_timeframe: z.string(),
    interval_start: z.string(),
    interval_end: z.string(),
    strategy_id: z.string(),
    strategy_version: z.string(),
    strategy_parameters: z.record(z.string(), z.string()),
    simulation: z.record(z.string(), z.string()),
    risk: z.record(z.string(), z.string()),
    product_snapshot: z.record(z.string(), z.string()).nullable(),
  }),
  totals: z.object({
    boundaries_evaluated: z.number().int(),
    first_boundary: z.string().nullable(),
    last_boundary: z.string().nullable(),
    decision_count: z.number().int(),
    position_count: z.number().int(),
    result_digest: z.string().nullable(),
  }),
  failure_code: z.string().nullable(),
  failure_reason: z.string().nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  provenance: z.string(),
});

export const decisionSchema = z.object({
  sequence: z.number().int(),
  as_of: z.string(),
  outcome: z.string(),
  reason: z.string(),
  bars_available: z.number().int(),
  position_id: z.string().nullable(),
  risk_outcome: z.string().nullable(),
  risk_reason: z.string().nullable(),
  direction: z.string().nullable(),
});

export const traceSchema = z.object({
  run_id: z.string(),
  items: z.array(decisionSchema),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
});

const targetSchema = z.object({
  price: z.string(),
  quantity: z.number().int(),
  filled: z.boolean(),
  fill_price: z.string().nullable(),
});

export const backtestPositionSchema = z.object({
  position_id: z.string(),
  ordinal: z.number().int(),
  direction: z.enum(['LONG', 'SHORT']),
  symbol: z.string(),
  timeframe: z.string(),
  quantity: z.number().int(),
  remaining: z.number().int(),
  state: z.string(),
  intended_entry: z.string(),
  entry_fill_price: z.string().nullable(),
  stop: z.string(),
  targets: z.array(targetSchema),
  decision_time: z.string(),
  entry_time: z.string().nullable(),
  realized_gross: z.string(),
  fees_total: z.string().nullable(),
  realized_net: z.string().nullable(),
  unrealized_gross: z.string().nullable(),
  event_count: z.number().int(),
  origin: z.string(),
  provenance: z.string(),
});

export const positionListSchema = z.object({
  run_id: z.string(),
  items: z.array(backtestPositionSchema),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
});

export const eventListSchema = z.object({
  run_id: z.string(),
  position_id: z.string(),
  items: z.array(
    z.object({
      sequence: z.number().int(),
      type: z.string(),
      market_time: z.string().nullable(),
      data: z.record(z.string(), z.string()),
    }),
  ),
  total: z.number().int(),
  after_sequence: z.number().int(),
  limit: z.number().int(),
});

export const backtestPerformanceSchema = z.object({
  run_id: z.string(),
  status: z.string(),
  performance: performanceSchema,
});

export type StrategyDto = z.infer<typeof strategySchema>;
export type CapabilityDto = z.infer<typeof capabilitySchema>;
export type BacktestDatasetDto = z.infer<typeof datasetSchema>;
export type BacktestRunDto = z.infer<typeof runSchema>;
export type BacktestRunSummaryDto = z.infer<typeof runSummarySchema>;
export type BacktestTraceDto = z.infer<typeof traceSchema>;
export type BacktestPositionDto = z.infer<typeof backtestPositionSchema>;
export type BacktestEventListDto = z.infer<typeof eventListSchema>;
export type BacktestPerformanceDto = z.infer<typeof backtestPerformanceSchema>;
export type { PerformanceDto };

/**
 * What a person may choose. Notably absent: anything the server computes, and
 * anything about the strategy beyond *which registered rules to use*.
 */
export interface CreateBacktestPayload {
  readonly dataset_id: string;
  readonly driver_timeframe: string;
  readonly start: string;
  readonly end: string;
  readonly strategy_id: string;
  readonly strategy_version: string;
  readonly account: { readonly equity: string; readonly used_margin: string };
  readonly risk: {
    readonly mode: string;
    readonly fixed_risk?: string | null;
    readonly risk_ratio?: string | null;
  };
  readonly simulation?: {
    readonly same_bar: string;
    readonly slippage_mode: string;
    readonly slippage_points?: string | null;
    readonly fee_mode: string;
    readonly fee_per_unit?: string | null;
  };
}

const BASE = '/backtest';

/**
 * Under `exactOptionalPropertyTypes`, `{signal: undefined}` is not the same as
 * omitting it, so the request init is built rather than spread - the same
 * helper the replay transport uses, for the same reason.
 */
function init(signal?: AbortSignal, headers?: Record<string, string>): RequestInit | undefined {
  const base: RequestInit = {};
  if (signal) base.signal = signal;
  if (headers) base.headers = headers;
  return signal || headers ? base : undefined;
}

export function readCapability(signal?: AbortSignal): Promise<CapabilityDto> {
  return getJson(`${BASE}/capability`, capabilitySchema, init(signal));
}

export function listStrategies(signal?: AbortSignal) {
  return getJson(`${BASE}/strategies`, strategyCatalogueSchema, init(signal));
}

export function listDatasets(limit = 25, signal?: AbortSignal) {
  return getJson(`${BASE}/datasets?limit=${limit}`, datasetListSchema, init(signal));
}

export function createBacktestRun(
  payload: CreateBacktestPayload,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<BacktestRunDto> {
  return postJson(
    `${BASE}/runs`,
    payload,
    runSchema,
    init(signal, {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    }),
  );
}

export function listBacktestRuns(limit = 20, signal?: AbortSignal) {
  return getJson(`${BASE}/runs?limit=${limit}`, runListSchema, init(signal));
}

export function getBacktestRun(runId: string, signal?: AbortSignal): Promise<BacktestRunDto> {
  return getJson(`${BASE}/runs/${runId}`, runSchema, init(signal));
}

export function abandonBacktestRun(runId: string, signal?: AbortSignal): Promise<BacktestRunDto> {
  return postJson(`${BASE}/runs/${runId}/abandon`, {}, runSchema, init(signal));
}

export function readTrace(
  runId: string,
  offset: number,
  limit: number,
  signal?: AbortSignal,
): Promise<BacktestTraceDto> {
  return getJson(
    `${BASE}/runs/${runId}/trace?offset=${offset}&limit=${limit}`,
    traceSchema,
    init(signal),
  );
}

export function listBacktestPositions(
  runId: string,
  offset: number,
  limit: number,
  signal?: AbortSignal,
) {
  return getJson(
    `${BASE}/runs/${runId}/positions?offset=${offset}&limit=${limit}`,
    positionListSchema,
    init(signal),
  );
}

export function readPositionEvents(
  runId: string,
  positionId: string,
  limit: number,
  signal?: AbortSignal,
): Promise<BacktestEventListDto> {
  return getJson(
    `${BASE}/runs/${runId}/positions/${positionId}/events?limit=${limit}`,
    eventListSchema,
    init(signal),
  );
}

export function readBacktestPerformance(
  runId: string,
  signal?: AbortSignal,
): Promise<BacktestPerformanceDto> {
  return getJson(`${BASE}/runs/${runId}/performance`, backtestPerformanceSchema, init(signal));
}
