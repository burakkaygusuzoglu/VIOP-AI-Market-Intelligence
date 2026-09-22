import { z } from 'zod';
import { ApiError, API_BASE, getJson, postJson } from './client';
import { analysisResponseSchema } from './analysis';

/**
 * Live Intelligence transport (Phase 13 Part 2A). Simulated history only.
 *
 * ## Every response is checked for what it claims to be
 *
 * Provenance and market currency are `z.literal`s. A response that called
 * itself an exchange feed, or a current market, would fail to parse and never
 * reach application state - the browser does not relabel data, and it does
 * not accept data that relabels itself.
 *
 * ## The browser sends intent, never facts
 *
 * The only request bodies here name a stored dataset, timeframes, a window and
 * a playback pace - or ask for an analysis of what the server already holds.
 * There is no field for a candle, a price, a time, a status or a provenance.
 *
 * ## One place owns the event stream
 *
 * `openLiveEvents` is the only `EventSource` in the application. It closes the
 * stream on any transport error instead of letting the browser reconnect on
 * its own: reconnection is a *resynchronisation* - re-read the authoritative
 * snapshot, then resume from its cursor - and that decision belongs to the
 * screen, not to a retry loop that would silently skip what it missed.
 */

const provenance = z.literal('SIMULATED_HISTORICAL_STREAM');
const currency = z.literal('HISTORICAL');

const limitsSchema = z.object({
  max_sessions: z.number().int(),
  max_subscribers_per_session: z.number().int(),
  max_subscribers_total: z.number().int(),
  subscriber_queue: z.number().int(),
  timeline_retention: z.number().int(),
  timeline_page_max: z.number().int(),
  max_session_seconds: z.number(),
  creations_per_minute: z.number().int(),
  max_concurrent_analyses: z.number().int(),
  min_window_candles: z.number().int(),
  max_window_candles: z.number().int(),
  max_closed_candles: z.number().int(),
  max_missing_sequences: z.number().int(),
  max_recorded_issues: z.number().int(),
  max_reconnects: z.number().int(),
  heartbeat_seconds: z.number(),
});

export const liveCapabilitySchema = z.object({
  state: z.enum(['AVAILABLE', 'DISABLED']),
  detail: z.string(),
  provenance,
  market_currency: currency,
  real_exchange_connected: z.literal(false),
  execution: z.literal('DISABLED'),
  transport: z.literal('SSE'),
  scope: z.literal('LOCAL_DEVELOPMENT'),
  paces: z.array(z.string()),
  limits: limitsSchema.nullable(),
});

export const liveSourceSchema = z.object({
  source_id: z.string(),
  instrument_label: z.string(),
  origin: z.literal('USER_SUPPLIED_HISTORICAL'),
  contract_identity: z.literal('NOT_ESTABLISHED'),
  streamable: z.boolean(),
  refusal: z.string().nullable(),
  timeframes: z.array(
    z.object({
      timeframe: z.string(),
      rows: z.number().int(),
      first_open_time: z.string(),
      last_open_time: z.string(),
    }),
  ),
});

const sourceListSchema = z.object({
  items: z.array(liveSourceSchema),
  total: z.number().int(),
  offset: z.number().int(),
  limit: z.number().int(),
});

const candleSchema = z.object({
  state: z.enum(['CLOSED', 'FORMING']),
  market_open_time: z.string(),
  market_coverage_end: z.string(),
  market_event_time: z.string(),
  received_at: z.string(),
  open: z.string(),
  high: z.string(),
  low: z.string(),
  close: z.string(),
  volume: z.string(),
  sequence: z.number().int().nullable(),
});

export const FRESHNESS = ['NO_DATA', 'FRESH', 'STALE'] as const;
export const INTEGRITY = [
  'COMPLETE',
  'GAPPED',
  'DISCONTINUOUS',
  'CONFLICTED',
  'UNVERIFIED',
] as const;
export const CONNECTION = [
  'INITIALIZING',
  'CONNECTED',
  'DISCONNECTED',
  'RECOVERING',
  'TERMINATED',
] as const;

const timeframeSchema = z.object({
  timeframe: z.string(),
  freshness: z.enum(FRESHNESS),
  freshness_threshold_seconds: z.number(),
  integrity: z.enum(INTEGRITY),
  availability: z.enum(['AVAILABLE', 'UNAVAILABLE']),
  reasons: z.array(z.string()),
  closed_count: z.number().int(),
  trimmed: z.number().int(),
  first_closed_open_time: z.string().nullable(),
  last_closed_open_time: z.string().nullable(),
  last_sequence: z.number().int().nullable(),
  last_market_event_time: z.string().nullable(),
  last_received_at: z.string().nullable(),
  missing_sequences: z.number().int(),
  missing_overflowed: z.boolean(),
  temporal_gaps: z.number().int(),
  sequence_mismatches: z.number().int(),
  conflicts: z.number().int(),
  duplicates: z.number().int(),
  late_fills: z.number().int(),
  awaiting_continuity: z.boolean(),
  forming_ahead: z.boolean(),
  unresolved_trimmed: z.number().int(),
  version: z.number().int(),
  latest_confirmed: candleSchema.nullable(),
  forming: candleSchema.nullable(),
});

export const liveSessionSchema = z.object({
  id: z.string(),
  simulated: z.literal(true),
  execution: z.literal('DISABLED'),
  identity: z.object({
    stream_id: z.string(),
    source_id: z.string(),
    source_origin: z.literal('USER_SUPPLIED_HISTORICAL'),
    provider_id: z.string(),
    instrument_label: z.string(),
    contract_identity: z.literal('NOT_ESTABLISHED'),
    provenance,
    market_currency: currency,
  }),
  lifecycle: z.enum(['RUNNING', 'ENDED']),
  end_origin: z.enum(['STREAM', 'USER_CANCELLED', 'DEADLINE', 'SHUTDOWN']).nullable(),
  connection: z.enum(CONNECTION),
  termination_reason: z.string().nullable(),
  reconnects: z.number().int(),
  created_at: z.string(),
  ended_at: z.string().nullable(),
  snapshot_at: z.string(),
  playback: z.object({
    pace: z.string(),
    event_spacing_seconds: z.number(),
    total_events: z.number().int(),
    market_window_start: z.string(),
    market_window_end: z.string(),
    candles_per_timeframe: z.record(z.number().int()),
    forming_candles_published: z.literal(false),
  }),
  timeframes: z.array(timeframeSchema),
  alerts: z.array(
    z.object({ kind: z.string(), timeframe: z.string().nullable(), detail: z.string() }),
  ),
  rejection_counts: z.record(z.number().int()),
  recent_rejections: z.array(
    z.object({
      timeframe: z.string().nullable(),
      code: z.string(),
      detail: z.string(),
      received_at: z.string(),
    }),
  ),
  analysis: z.object({
    analyses_run: z.number().int(),
    available_timeframes: z.array(z.string()),
    last_market_as_of: z.string().nullable(),
    last_requested_at: z.string().nullable(),
    last_current: z.boolean(),
  }),
  cursor: z.number().int(),
  oldest_retained: z.number().int(),
  subscribers: z.number().int(),
});

const sessionListSchema = z.object({
  items: z.array(
    z.object({
      id: z.string(),
      instrument_label: z.string(),
      source_id: z.string(),
      lifecycle: z.string(),
      connection: z.string(),
      created_at: z.string(),
      cursor: z.number().int(),
    }),
  ),
  capacity: z.number().int(),
});

export const timelineEntrySchema = z.object({
  seq: z.number().int(),
  kind: z.string(),
  recorded_at: z.string(),
  code: z.string(),
  timeframe: z.string().nullable(),
  market_open_time: z.string().nullable(),
  market_event_time: z.string().nullable(),
  sequence: z.number().int().nullable(),
  before: z.string().nullable(),
  after: z.string().nullable(),
  backfill: z.boolean(),
});

const timelinePageSchema = z.object({
  session_id: z.string(),
  entries: z.array(timelineEntrySchema),
  cursor: z.number().int(),
  oldest_retained: z.number().int(),
  gap: z.boolean(),
  limit: z.number().int(),
});

export const liveAnalysisSchema = z.object({
  session_id: z.string(),
  provenance,
  market_currency: currency,
  market_as_of: z.string(),
  requested_at: z.string(),
  included: z.array(z.string()),
  excluded: z.array(z.object({ timeframe: z.string(), reasons: z.array(z.string()) })),
  fingerprint: z.array(z.object({ timeframe: z.string(), version: z.number().int() })),
  reused: z.boolean(),
  current: z.boolean(),
  analysis: analysisResponseSchema,
  session: liveSessionSchema,
});

export const liveEnvelopeSchema = z.object({
  protocol: z.literal('viop.live.v1'),
  session_id: z.string(),
  event_id: z.number().int().nullable(),
  kind: z.enum(['TIMELINE', 'STATE', 'RESYNC_REQUIRED', 'END', 'HEARTBEAT']),
  server_time: z.string(),
  cursor: z.number().int(),
  entry: timelineEntrySchema.nullable(),
  session: liveSessionSchema.nullable(),
  reason: z.string().nullable(),
});

export type LiveCapabilityDto = z.infer<typeof liveCapabilitySchema>;
export type LiveSourceDto = z.infer<typeof liveSourceSchema>;
export type LiveSessionDto = z.infer<typeof liveSessionSchema>;
export type LiveTimeframeDto = LiveSessionDto['timeframes'][number];
export type LiveCandleDto = z.infer<typeof candleSchema>;
export type LiveSessionListDto = z.infer<typeof sessionListSchema>;
export type LiveTimelineEntryDto = z.infer<typeof timelineEntrySchema>;
export type LiveTimelinePageDto = z.infer<typeof timelinePageSchema>;
export type LiveAnalysisDto = z.infer<typeof liveAnalysisSchema>;
export type LiveEnvelopeDto = z.infer<typeof liveEnvelopeSchema>;

/** What a person chooses. Nothing here is a market fact. */
export interface CreateLivePayload {
  readonly source_id: string;
  readonly timeframes: readonly string[];
  readonly window_candles: number;
  readonly pace: 'SLOW' | 'NORMAL' | 'FAST';
}

const LIVE = '/live';

function sessionPath(sessionId: string): string {
  return `${LIVE}/sessions/${encodeURIComponent(sessionId)}`;
}

export function getLiveCapability(signal?: AbortSignal): Promise<LiveCapabilityDto> {
  return getJson(`${LIVE}/capability`, liveCapabilitySchema, signal ? { signal } : undefined);
}

export function listLiveSources(signal?: AbortSignal) {
  return getJson(`${LIVE}/sources`, sourceListSchema, signal ? { signal } : undefined);
}

export function listLiveSessions(signal?: AbortSignal): Promise<LiveSessionListDto> {
  return getJson(`${LIVE}/sessions`, sessionListSchema, signal ? { signal } : undefined);
}

export function createLiveSession(payload: CreateLivePayload): Promise<LiveSessionDto> {
  return postJson(`${LIVE}/sessions`, payload, liveSessionSchema);
}

export function getLiveSession(sessionId: string, signal?: AbortSignal): Promise<LiveSessionDto> {
  return getJson(sessionPath(sessionId), liveSessionSchema, signal ? { signal } : undefined);
}

export function cancelLiveSession(sessionId: string): Promise<LiveSessionDto> {
  return postJson(`${sessionPath(sessionId)}/cancel`, {}, liveSessionSchema);
}

export async function removeLiveSession(sessionId: string): Promise<void> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${sessionPath(sessionId)}`, { method: 'DELETE' });
  } catch (cause) {
    throw new ApiError(cause instanceof Error ? cause.message : 'network error', null);
  }
  // 404 means it is already gone - the outcome the caller asked for.
  if (!response.ok && response.status !== 404) {
    throw new ApiError(`request failed (${response.status})`, response.status);
  }
}

export function readLiveTimeline(
  sessionId: string,
  after: number | null,
  signal?: AbortSignal,
): Promise<LiveTimelinePageDto> {
  const query = after === null ? '?limit=100' : `?after=${after}&limit=100`;
  return getJson(
    `${sessionPath(sessionId)}/timeline${query}`,
    timelinePageSchema,
    signal ? { signal } : undefined,
  );
}

export function analyseLiveSession(
  sessionId: string,
  signal?: AbortSignal,
): Promise<LiveAnalysisDto> {
  return postJson(
    `${sessionPath(sessionId)}/analysis`,
    {},
    liveAnalysisSchema,
    signal ? { signal } : undefined,
  );
}

export interface LiveEventHandlers {
  readonly onOpen: () => void;
  readonly onEnvelope: (envelope: LiveEnvelopeDto) => void;
  /** The browser's connection to *this server* failed. Not the provider. */
  readonly onTransportLost: () => void;
  readonly onInvalid: () => void;
}

/** Open the session's event stream. Returns the function that closes it. */
export function openLiveEvents(
  sessionId: string,
  after: number | null,
  handlers: LiveEventHandlers,
): () => void {
  const query = after === null ? '' : `?after=${after}`;
  const source = new EventSource(`${API_BASE}${sessionPath(sessionId)}/events${query}`);
  let closed = false;
  const close = () => {
    closed = true;
    source.close();
  };
  source.onopen = () => {
    if (!closed) handlers.onOpen();
  };
  source.onmessage = (event: MessageEvent<string>) => {
    if (closed) return;
    let payload: unknown;
    try {
      payload = JSON.parse(event.data);
    } catch {
      handlers.onInvalid();
      return;
    }
    const parsed = liveEnvelopeSchema.safeParse(payload);
    if (!parsed.success) {
      handlers.onInvalid();
      return;
    }
    handlers.onEnvelope(parsed.data);
  };
  source.onerror = () => {
    if (closed) return;
    // Never let the browser retry on its own: a reconnect must resync.
    close();
    handlers.onTransportLost();
  };
  return close;
}
