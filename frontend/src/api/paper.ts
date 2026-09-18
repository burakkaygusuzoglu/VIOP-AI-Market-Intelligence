import { z } from 'zod';
import { ApiError, getJson, postJson } from './client';

/**
 * Paper-trading transport (Phase 9). Simulation only.
 *
 * Every response is validated before it reaches state. Every number arrives as
 * an exact decimal string and stays one: nothing here parses a price into a
 * float, and nothing computes a fill, a P&L or a state. The request types carry
 * a person's plan and nothing else - there is no field for a result.
 */

const eventSchema = z.object({
  sequence: z.number().int(),
  type: z.string(),
  market_time: z.string().nullable(),
  data: z.record(z.string(), z.string()),
  recorded_at: z.string().nullable().default(null),
});

const summarySchema = z.object({
  id: z.string(),
  simulated: z.literal(true),
  symbol: z.string(),
  asset_class: z.string(),
  direction: z.string(),
  state: z.string(),
  quantity: z.number().int(),
  remaining: z.number().int(),
  entry_fill_price: z.string().nullable(),
  stop: z.string(),
  last_mark: z.string().nullable(),
  realized_gross: z.string(),
  fees_total: z.string().nullable(),
  realized_net: z.string().nullable(),
  unrealized_gross: z.string().nullable(),
  close_pending: z.boolean(),
  created_at: z.string(),
  updated_at: z.string(),
});

export const paperPositionSchema = summarySchema.extend({
  origin: z.string(),
  timeframe: z.string(),
  decision_time: z.string(),
  intended_entry: z.string(),
  initial_stop: z.string(),
  closed_quantity: z.number().int(),
  targets: z.array(
    z.object({
      index: z.number().int(),
      price: z.string(),
      quantity: z.number().int(),
      filled: z.boolean(),
      fill_price: z.string().nullable(),
    }),
  ),
  last_bar_time: z.string().nullable(),
  bars_applied: z.number().int(),
  event_count: z.number().int(),
  simulation: z.object({
    rules_version: z.string(),
    same_bar: z.string(),
    slippage_mode: z.string(),
    slippage_points: z.string().nullable(),
    fee_mode: z.string(),
    fee_per_unit: z.string().nullable(),
    entry_model: z.string(),
    stop_fill_model: z.string(),
    target_fill_model: z.string(),
    manual_exit_model: z.string(),
  }),
  risk: z.object({
    outcome: z.string(),
    allowed_units: z.number().int().nullable(),
    reason: z.string(),
  }),
  provenance: z.object({
    fills: z.literal('SIMULATED'),
    market_data: z.literal('USER_SUPPLIED_HISTORICAL_BARS'),
    origin: z.string(),
    asset_class: z.string(),
    asset_class_status: z.string(),
    point_value: z.string(),
    point_value_status: z.string(),
    point_value_source: z.string(),
    unit: z.string(),
  }),
  note: z.string().nullable(),
  key_events: z.array(eventSchema),
  idempotent_replay: z.boolean().default(false),
});

export const paperPositionListSchema = z.object({
  items: z.array(summarySchema),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
});

export const paperEventPageSchema = z.object({
  position_id: z.string(),
  items: z.array(eventSchema),
  total: z.number().int(),
  after_sequence: z.number().int(),
  limit: z.number().int(),
  next_after_sequence: z.number().int().nullable(),
});

export type PaperPositionDto = z.infer<typeof paperPositionSchema>;
export type PaperSummaryDto = z.infer<typeof summarySchema>;
export type PaperEventDto = z.infer<typeof eventSchema>;
export type PaperListDto = z.infer<typeof paperPositionListSchema>;
export type PaperEventPageDto = z.infer<typeof paperEventPageSchema>;

/** A plan for a simulated trade. No field here is a result. */
export interface CreatePaperPositionPayload {
  readonly symbol: string;
  readonly direction: 'LONG' | 'SHORT';
  readonly quantity: number;
  readonly intended_entry: string;
  readonly stop: string;
  readonly targets: ReadonlyArray<{ readonly price: string; readonly quantity: number }>;
  readonly timeframe: '5M' | '15M' | '1H' | '1D';
  readonly decision_time: string;
  readonly account: { readonly equity: string; readonly used_margin: string };
  readonly risk: {
    readonly mode: 'FIXED' | 'PERCENTAGE';
    readonly fixed_risk?: string;
    readonly risk_ratio?: string;
  };
  readonly simulation: {
    readonly same_bar: 'STOP_FIRST' | 'HALT';
    readonly slippage_mode: 'ZERO' | 'FIXED_POINTS';
    readonly slippage_points?: string;
    readonly fee_mode: 'NOT_MODELLED' | 'USER_DEFINED_PER_UNIT';
    readonly fee_per_unit?: string;
  };
  readonly note?: string;
}

const JSON_HEADERS = { Accept: 'application/json', 'Content-Type': 'application/json' };

function withSignal(signal?: AbortSignal): RequestInit | undefined {
  return signal ? { signal } : undefined;
}

export function listPaperPositions(offset = 0, signal?: AbortSignal): Promise<PaperListDto> {
  return getJson(
    `/paper/positions?offset=${offset}&limit=20`,
    paperPositionListSchema,
    withSignal(signal),
  );
}

export function getPaperPosition(id: string, signal?: AbortSignal): Promise<PaperPositionDto> {
  return getJson(
    `/paper/positions/${encodeURIComponent(id)}`,
    paperPositionSchema,
    withSignal(signal),
  );
}

export function listPaperEvents(
  id: string,
  afterSequence: number,
  signal?: AbortSignal,
): Promise<PaperEventPageDto> {
  return getJson(
    `/paper/positions/${encodeURIComponent(id)}/events?after_sequence=${afterSequence}&limit=50`,
    paperEventPageSchema,
    withSignal(signal),
  );
}

/**
 * Create a paper position.
 *
 * The idempotency key is chosen by the caller once per plan and reused on
 * retry, so a double click or a retried request can never record two positions.
 */
export function createPaperPosition(
  payload: CreatePaperPositionPayload,
  idempotencyKey: string,
): Promise<PaperPositionDto> {
  return postJson('/paper/positions', payload, paperPositionSchema, {
    headers: { ...JSON_HEADERS, 'Idempotency-Key': idempotencyKey },
  });
}

export function postPaperObservations(
  id: string,
  content: string,
  sourceName: string,
): Promise<PaperPositionDto> {
  return postJson(
    `/paper/positions/${encodeURIComponent(id)}/observations`,
    { content, source_name: sourceName },
    paperPositionSchema,
  );
}

export type PaperAction = 'close' | 'stop/breakeven' | 'cancel';

export function postPaperAction(id: string, action: PaperAction): Promise<PaperPositionDto> {
  return postJson(`/paper/positions/${encodeURIComponent(id)}/${action}`, {}, paperPositionSchema);
}

/** A fresh idempotency key for one plan. Not a security token; only a dedupe key. */
export function newIdempotencyKey(): string {
  return `paper-${crypto.randomUUID()}`;
}

export { ApiError };
