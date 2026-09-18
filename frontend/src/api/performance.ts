import { z } from 'zod';
import { ApiError, API_BASE, getJson } from './client';

/**
 * Performance and journal transport (Phase 10). Simulated history only.
 *
 * Every metric arrives as a status with an optional value, and the value is an
 * exact decimal string. Nothing in this file - or anywhere in the frontend -
 * adds, divides or classifies a financial number: a win rate, a profit factor,
 * a drawdown and a streak are all server-derived, and this layer only checks
 * that what arrived has the shape it claims.
 */

const metricSchema = z.object({
  status: z.enum(['AVAILABLE', 'UNAVAILABLE', 'PARTIAL_COVERAGE', 'NOT_IMPLEMENTED']),
  value: z.string().nullable().default(null),
  basis: z.string().nullable().default(null),
  sample_size: z.number().int().default(0),
  coverage: z
    .object({ covered: z.number().int(), total: z.number().int() })
    .nullable()
    .default(null),
  reason: z.string().nullable().default(null),
  numerator: z.number().int().nullable().default(null),
  denominator: z.number().int().nullable().default(null),
});

const countsSchema = z.object({
  total: z.number().int(),
  pending_entry: z.number().int(),
  open_positions: z.number().int(),
  partially_closed: z.number().int(),
  ambiguous_halted: z.number().int(),
  closed: z.number().int(),
  cancelled: z.number().int(),
  rejected: z.number().int(),
  entered: z.number().int(),
  open_exposure: z.number().int(),
  never_entered: z.number().int(),
});

const filtersSchema = z.object({
  closed_from: z.string().nullable(),
  closed_to: z.string().nullable(),
  direction: z.string().nullable(),
  symbol: z.string().nullable(),
  timeframe: z.string().nullable(),
  tag: z.string().nullable(),
  range_rule: z.string(),
});

export const performanceSchema = z.object({
  simulated: z.literal(true),
  source: z.string(),
  basis: z.string(),
  basis_reason: z.string(),
  filters: filtersSchema,
  counts: countsSchema,
  sample_size: z.number().int(),
  fill_count: z.number().int(),
  wins: z.number().int(),
  losses: z.number().int(),
  breakevens: z.number().int(),
  fee_coverage: z.object({ covered: z.number().int(), total: z.number().int() }),
  realized_accounting: z.object({
    fill_count: z.number().int(),
    position_count: z.number().int(),
    from_completed_positions: z.number().int(),
    from_open_positions: z.number().int(),
    coverage: z.object({ covered: z.number().int(), total: z.number().int() }),
    gross: metricSchema,
    fees_known: metricSchema,
    net: metricSchema,
    time_rule: z.string(),
  }),
  realized_gross: metricSchema,
  fees_known: metricSchema,
  realized_net: metricSchema,
  unrealized_gross_open: metricSchema,
  win_rate: metricSchema,
  average_win: metricSchema,
  average_loss: metricSchema,
  profit_factor: metricSchema,
  expectancy: metricSchema,
  max_drawdown_absolute: metricSchema,
  drawdown_percentage: metricSchema,
  realized_r_expectancy: metricSchema,
  mae: metricSchema,
  mfe: metricSchema,
  sharpe_ratio: metricSchema,
  sortino_ratio: metricSchema,
  annualised_return: metricSchema,
  streaks: z.object({
    current_kind: z.string().nullable(),
    current_length: z.number().int(),
    max_win_streak: z.number().int(),
    max_loss_streak: z.number().int(),
    policy: z.string(),
  }),
  timeline: z.array(
    z.object({
      position_id: z.string(),
      terminal_time: z.string(),
      amount: z.string(),
      cumulative: z.string(),
    }),
  ),
  analysis_linkage: z.string(),
});

const groupSchema = z.object({
  key: z.string(),
  label: z.string(),
  counts: countsSchema,
  sample_size: z.number().int(),
  wins: z.number().int(),
  losses: z.number().int(),
  breakevens: z.number().int(),
  realized_gross: metricSchema,
  realized_net: metricSchema,
  win_rate: metricSchema,
  expectancy: metricSchema,
});

const groupSetSchema = z.object({
  rows: z.array(groupSchema),
  total: z.number().int(),
  returned: z.number().int(),
  omitted: z.number().int(),
  is_complete: z.boolean(),
});

export const breakdownsSchema = z.object({
  simulated: z.literal(true),
  filters: filtersSchema,
  by_direction: groupSetSchema,
  by_instrument: groupSetSchema,
  by_timeframe: groupSetSchema,
  unavailable_breakdowns: z.array(z.string()),
});

export const annotationSchema = z.object({
  position_id: z.string(),
  note: z.string().nullable(),
  tags: z.array(z.string()),
  version: z.number().int(),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
  provenance: z.literal('USER_AUTHORED'),
});

export const journalPageSchema = z.object({
  simulated: z.literal(true),
  items: z.array(
    z.object({
      position_id: z.string(),
      symbol: z.string(),
      asset_class: z.string(),
      direction: z.string(),
      timeframe: z.string(),
      quantity: z.number().int(),
      population: z.string(),
      outcome: z.string().nullable(),
      outcome_basis: z.string().nullable(),
      outcome_gross: z.string().nullable(),
      outcome_net: z.string().nullable(),
      realized_gross: z.string(),
      fees_total: z.string().nullable(),
      realized_net: z.string().nullable(),
      terminal_time: z.string().nullable(),
      decision_time: z.string(),
      annotation: annotationSchema,
    }),
  ),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
  filters: filtersSchema,
});

export const tagListSchema = z.object({
  items: z.array(z.object({ tag: z.string(), positions: z.number().int() })),
  limit: z.number().int(),
  is_complete: z.boolean(),
});

export type MetricDto = z.infer<typeof metricSchema>;
export type PerformanceDto = z.infer<typeof performanceSchema>;
export type BreakdownsDto = z.infer<typeof breakdownsSchema>;
export type GroupDto = z.infer<typeof groupSchema>;
export type GroupSetDto = z.infer<typeof groupSetSchema>;
export type JournalPageDto = z.infer<typeof journalPageSchema>;
export type JournalRowDto = JournalPageDto['items'][number];
export type AnnotationDto = z.infer<typeof annotationSchema>;
export type TagListDto = z.infer<typeof tagListSchema>;

/** What a person filtered by. Never a computed result. */
export interface PerformanceFilters {
  readonly from?: string;
  readonly to?: string;
  readonly direction?: 'LONG' | 'SHORT';
  readonly symbol?: string;
  readonly timeframe?: '5M' | '15M' | '1H' | '1D';
  readonly tag?: string;
}

function withSignal(signal?: AbortSignal): RequestInit | undefined {
  return signal ? { signal } : undefined;
}

export function filtersToQuery(filters: PerformanceFilters): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== '') query.set(key, String(value));
  }
  const text = query.toString();
  return text ? `?${text}` : '';
}

export function getPerformance(
  filters: PerformanceFilters,
  signal?: AbortSignal,
): Promise<PerformanceDto> {
  return getJson(
    `/paper/performance${filtersToQuery(filters)}`,
    performanceSchema,
    withSignal(signal),
  );
}

export function getBreakdowns(
  filters: PerformanceFilters,
  signal?: AbortSignal,
): Promise<BreakdownsDto> {
  return getJson(
    `/paper/performance/breakdowns${filtersToQuery(filters)}`,
    breakdownsSchema,
    withSignal(signal),
  );
}

export function getJournalPage(
  filters: PerformanceFilters,
  offset: number,
  signal?: AbortSignal,
): Promise<JournalPageDto> {
  const query = filtersToQuery({ ...filters });
  const separator = query ? '&' : '?';
  return getJson(
    `/paper/journal${query}${separator}offset=${offset}&limit=20`,
    journalPageSchema,
    withSignal(signal),
  );
}

export function getTags(signal?: AbortSignal): Promise<TagListDto> {
  return getJson('/paper/journal/tags', tagListSchema, withSignal(signal));
}

/**
 * Save a person's note and tags.
 *
 * `expectedVersion` is the version that was read. The server refuses a write
 * from a stale version with 409, so a second window cannot overwrite the first
 * without anyone noticing.
 */
export async function saveAnnotation(
  positionId: string,
  input: { note: string | null; tags: string[]; expectedVersion: number },
): Promise<AnnotationDto> {
  let response: Response;
  try {
    response = await fetch(
      `${API_BASE}/paper/positions/${encodeURIComponent(positionId)}/journal`,
      {
        method: 'PUT',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify({
          note: input.note,
          tags: input.tags,
          expected_version: input.expectedVersion,
        }),
      },
    );
  } catch (cause) {
    throw new ApiError(cause instanceof Error ? cause.message : 'network error', null);
  }

  let payload: unknown;
  try {
    payload = await response.json();
  } catch {
    throw new ApiError('response was not valid JSON', response.status);
  }
  if (!response.ok) {
    const detail = (payload as { detail?: { detail?: string } })?.detail;
    throw new ApiError(
      typeof detail?.detail === 'string' ? detail.detail : `request failed (${response.status})`,
      response.status,
    );
  }
  const parsed = annotationSchema.safeParse(payload);
  if (!parsed.success) {
    throw new ApiError('response did not match the expected schema', response.status);
  }
  return parsed.data;
}

export { ApiError };
