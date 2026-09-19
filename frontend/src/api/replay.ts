import { z } from 'zod';
import { getJson, postJson } from './client';
import { paperPositionSchema, type PaperPositionDto } from './paper';
import { analysisResponseSchema, type AnalysisResponseDto } from './analysis';
import { performanceSchema, type PerformanceDto } from './performance';

/**
 * Replay transport (Phase 11). Simulation only.
 *
 * ## The browser asks how far, never where to
 *
 * There is no request type here with a field for `replay_as_of`, a cursor, a
 * version to write, or a candle. The server owns progression; this module can
 * say "one more step" and can say which version it last saw, and that is the
 * whole vocabulary. `expectedVersion` is not state being submitted - it is the
 * client stating what it read, so a step that would race another tab is
 * refused rather than silently applied.
 *
 * ## What comes back has already been bounded
 *
 * A window carries only candles that had finished at the replay moment, and
 * says how many it left out. Nothing here filters, hides or trims a candle:
 * if this code had to remove a future bar, the bar would already have been in
 * the browser's memory, which is the leak itself.
 */

const candleSchema = z.object({
  open_time: z.string(),
  open: z.string(),
  high: z.string(),
  low: z.string(),
  close: z.string(),
  volume: z.string(),
});

const windowSchema = z.object({
  timeframe: z.string(),
  candles: z.array(candleSchema),
  revealed: z.number().int(),
  dataset_total: z.number().int(),
  window_limit: z.number().int(),
  truncated: z.boolean(),
});

const cursorSchema = z.object({
  replay_as_of: z.string(),
  revealed_driver_candles: z.number().int(),
  driver_total_candles: z.number().int(),
  state: z.enum(['READY', 'IN_PROGRESS', 'END_OF_DATASET']),
  version: z.number().int(),
});

export const replaySessionSchema = z.object({
  id: z.string(),
  simulated: z.literal(true),
  plan: z.object({
    symbol: z.string(),
    driver_timeframe: z.string(),
    replay_start: z.string(),
  }),
  cursor: cursorSchema,
  dataset: z.object({
    dataset_id: z.string(),
    symbol: z.string(),
    total_rows: z.number().int(),
    timeframes: z.array(
      z.object({
        timeframe: z.string(),
        rows: z.number().int(),
        first_open_time: z.string(),
        last_open_time: z.string(),
        last_coverage_end: z.string(),
      }),
    ),
  }),
  availability: z.array(windowSchema),
  linked_position_ids: z.array(z.string()),
  provenance: z.object({
    market_data: z.literal('USER_SUPPLIED_HISTORICAL'),
    fills: z.literal('SIMULATED'),
    origin: z.literal('REPLAY'),
    execution: z.literal('DISABLED'),
  }),
  max_advance_steps: z.number().int(),
  created_at: z.string(),
  updated_at: z.string(),
  idempotent_replay: z.boolean().default(false),
});

const stepSchema = z.object({
  session: replaySessionSchema,
  revealed_boundaries: z.array(z.string()),
  observed_positions: z.number().int(),
  idempotent_replay: z.boolean().default(false),
});

const listSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      dataset_id: z.string(),
      symbol: z.string(),
      driver_timeframe: z.string(),
      replay_start: z.string(),
      replay_as_of: z.string(),
      revealed_driver_candles: z.number().int(),
      driver_total_candles: z.number().int(),
      state: z.string(),
      version: z.number().int(),
      created_at: z.string(),
      updated_at: z.string(),
    }),
  ),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
});

const replayAnalysisSchema = z.object({
  session_id: z.string(),
  replay_as_of: z.string(),
  analysis: analysisResponseSchema,
});

const replayPerformanceSchema = z.object({
  session_id: z.string(),
  replay_as_of: z.string(),
  position_ids: z.array(z.string()),
  performance: performanceSchema,
});

export type ReplaySessionDto = z.infer<typeof replaySessionSchema>;
export type ReplayStepDto = z.infer<typeof stepSchema>;
export type ReplayListDto = z.infer<typeof listSchema>;
export type ReplayWindowDto = z.infer<typeof windowSchema>;
export type ReplayCandleDto = z.infer<typeof candleSchema>;
export type ReplaySummaryDto = ReplayListDto['items'][number];
export type ReplayAnalysisDto = {
  session_id: string;
  replay_as_of: string;
  analysis: AnalysisResponseDto;
};
export type ReplayPerformanceDto = {
  session_id: string;
  replay_as_of: string;
  position_ids: string[];
  performance: PerformanceDto;
};

export interface CreateReplayPayload {
  readonly symbol: string;
  readonly driver_timeframe: string;
  readonly replay_start: string;
  readonly datasets: readonly {
    readonly timeframe: string;
    readonly content: string;
    readonly source_name: string;
  }[];
}

const SESSIONS = '/replay/sessions';

/**
 * Build a `RequestInit` without undefined keys.
 *
 * Under `exactOptionalPropertyTypes`, `{signal: undefined}` is not a
 * `RequestInit` — the key has to be absent, not present and empty.
 */
function init(signal?: AbortSignal, headers?: Record<string, string>): RequestInit | undefined {
  const base: RequestInit = {};
  if (signal) base.signal = signal;
  if (headers) base.headers = headers;
  return signal || headers ? base : undefined;
}

export function createReplaySession(
  payload: CreateReplayPayload,
  idempotencyKey: string,
  signal?: AbortSignal,
): Promise<ReplaySessionDto> {
  return postJson(
    SESSIONS,
    payload,
    replaySessionSchema,
    init(signal, {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'Idempotency-Key': idempotencyKey,
    }),
  );
}

export function listReplaySessions(signal?: AbortSignal): Promise<ReplayListDto> {
  return getJson(SESSIONS, listSchema, init(signal));
}

export function getReplaySession(
  sessionId: string,
  chart: string | null,
  signal?: AbortSignal,
): Promise<ReplaySessionDto> {
  const query = chart === null ? '' : `?chart=${encodeURIComponent(chart)}`;
  return getJson(`${SESSIONS}/${sessionId}${query}`, replaySessionSchema, init(signal));
}

/**
 * Reveal the next `steps` driver candles.
 *
 * `idempotencyKey` makes a retried command return the cursor it already
 * produced. That matters most exactly when a person is clicking quickly: a
 * dropped response must not become a second candle.
 */
export function advanceReplay(
  sessionId: string,
  steps: number,
  options: {
    /**
     * The version the client last read. Required, not optional: the caller
     * always knows it, and an advance sent without one is an advance that
     * could apply on top of a cursor nobody has seen. Making it optional also
     * left a second request shape that nothing exercised.
     */
    readonly expectedVersion: number;
    readonly chart?: string | null;
    readonly idempotencyKey?: string;
    readonly signal?: AbortSignal;
  },
): Promise<ReplayStepDto> {
  const chart = options.chart ?? null;
  const query = chart === null ? '' : `?chart=${encodeURIComponent(chart)}`;
  const headers: Record<string, string> = {
    Accept: 'application/json',
    'Content-Type': 'application/json',
  };
  if (options.idempotencyKey !== undefined) headers['Idempotency-Key'] = options.idempotencyKey;
  return postJson(
    `${SESSIONS}/${sessionId}/advance${query}`,
    { steps, expected_version: options.expectedVersion },
    stepSchema,
    init(options.signal, headers),
  );
}

export function analyseReplay(sessionId: string, signal?: AbortSignal): Promise<ReplayAnalysisDto> {
  return postJson(`${SESSIONS}/${sessionId}/analysis`, {}, replayAnalysisSchema, init(signal));
}

export function listReplayPositions(
  sessionId: string,
  signal?: AbortSignal,
): Promise<PaperPositionDto[]> {
  return getJson(`${SESSIONS}/${sessionId}/positions`, z.array(paperPositionSchema), init(signal));
}

export function readReplayPerformance(
  sessionId: string,
  signal?: AbortSignal,
): Promise<ReplayPerformanceDto> {
  return getJson(`${SESSIONS}/${sessionId}/performance`, replayPerformanceSchema, init(signal));
}
