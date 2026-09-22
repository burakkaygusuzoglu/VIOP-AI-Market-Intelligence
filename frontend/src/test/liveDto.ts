import type {
  LiveAnalysisDto,
  LiveCandleDto,
  LiveCapabilityDto,
  LiveEnvelopeDto,
  LiveSessionDto,
  LiveSourceDto,
  LiveTimeframeDto,
  LiveTimelineEntryDto,
  LiveTimelinePageDto,
} from '../api/live';
import { analysisDto } from './dto';

/**
 * Live Intelligence fixtures (Phase 13 Part 2A). TEST_FIXTURE data.
 *
 * Market times are March 2026 and receive times September 2026, on purpose:
 * a historical candle that arrived recently is the case the screen must
 * never present as a current price.
 */

export const SESSION_A = 'LS-aaaaaaaaaaaaaaaaaaaaaaaa';
export const SESSION_B = 'LS-bbbbbbbbbbbbbbbbbbbbbbbb';
export const SOURCE = 'RD-' + 'c'.repeat(32);

export function candleDto(overrides: Partial<LiveCandleDto> = {}): LiveCandleDto {
  return {
    state: 'CLOSED',
    market_open_time: '2026-03-02T10:00:00+00:00',
    market_coverage_end: '2026-03-02T10:05:00+00:00',
    market_event_time: '2026-03-02T10:05:00+00:00',
    received_at: '2026-09-21T12:00:01+00:00',
    open: '100.10',
    high: '101.20',
    low: '99.90',
    close: '100.70',
    volume: '1000',
    sequence: 12,
    ...overrides,
  };
}

export function timeframeDto(
  timeframe: string,
  overrides: Partial<LiveTimeframeDto> = {},
): LiveTimeframeDto {
  return {
    timeframe,
    freshness: 'FRESH',
    freshness_threshold_seconds: 10,
    integrity: 'COMPLETE',
    availability: 'AVAILABLE',
    reasons: [],
    closed_count: 13,
    trimmed: 0,
    first_closed_open_time: '2026-03-02T09:00:00+00:00',
    last_closed_open_time: '2026-03-02T10:00:00+00:00',
    last_sequence: 12,
    last_market_event_time: '2026-03-02T10:05:00+00:00',
    last_received_at: '2026-09-21T12:00:01+00:00',
    missing_sequences: 0,
    missing_overflowed: false,
    temporal_gaps: 0,
    sequence_mismatches: 0,
    conflicts: 0,
    duplicates: 0,
    late_fills: 0,
    awaiting_continuity: false,
    forming_ahead: false,
    unresolved_trimmed: 0,
    version: 13,
    latest_confirmed: candleDto(),
    forming: null,
    ...overrides,
  };
}

export function sessionDto(overrides: Partial<LiveSessionDto> = {}): LiveSessionDto {
  const id = overrides.id ?? SESSION_A;
  return {
    id,
    simulated: true,
    execution: 'DISABLED',
    identity: {
      stream_id: id,
      source_id: SOURCE,
      source_origin: 'USER_SUPPLIED_HISTORICAL',
      provider_id: 'STORED_DATASET_PLAYBACK',
      instrument_label: 'TEST_FIXTURE_FUT',
      contract_identity: 'NOT_ESTABLISHED',
      provenance: 'SIMULATED_HISTORICAL_STREAM',
      market_currency: 'HISTORICAL',
    },
    lifecycle: 'RUNNING',
    end_origin: null,
    connection: 'CONNECTED',
    termination_reason: null,
    reconnects: 0,
    created_at: '2026-09-21T12:00:00+00:00',
    ended_at: null,
    snapshot_at: '2026-09-21T12:00:02+00:00',
    playback: {
      pace: 'NORMAL',
      event_spacing_seconds: 0.25,
      total_events: 408,
      market_window_start: '2026-03-02T09:00:00+00:00',
      market_window_end: '2026-03-03T09:00:00+00:00',
      candles_per_timeframe: { '5M': 288, '15M': 96, '1H': 24 },
      forming_candles_published: false,
    },
    timeframes: [timeframeDto('5M'), timeframeDto('15M'), timeframeDto('1H')],
    alerts: [],
    rejection_counts: {},
    recent_rejections: [],
    analysis: {
      analyses_run: 0,
      available_timeframes: ['5M', '15M', '1H'],
      last_market_as_of: null,
      last_requested_at: null,
      last_current: false,
    },
    cursor: 20,
    oldest_retained: 1,
    subscribers: 1,
    ...overrides,
  };
}

export function unavailableSession(overrides: Partial<LiveSessionDto> = {}): LiveSessionDto {
  const blocked = (timeframe: string) =>
    timeframeDto(timeframe, {
      freshness: 'NO_DATA',
      availability: 'UNAVAILABLE',
      closed_count: 0,
      latest_confirmed: null,
      reasons: ['no valid observation has been received', 'no closed candle has been confirmed'],
    });
  return sessionDto({
    timeframes: [blocked('5M'), blocked('15M'), blocked('1H')],
    analysis: {
      analyses_run: 0,
      available_timeframes: [],
      last_market_as_of: null,
      last_requested_at: null,
      last_current: false,
    },
    ...overrides,
  });
}

export function capabilityDto(overrides: Partial<LiveCapabilityDto> = {}): LiveCapabilityDto {
  return {
    state: 'AVAILABLE',
    detail: 'Borsaya bağlı değildir.',
    provenance: 'SIMULATED_HISTORICAL_STREAM',
    market_currency: 'HISTORICAL',
    real_exchange_connected: false,
    execution: 'DISABLED',
    transport: 'SSE',
    scope: 'LOCAL_DEVELOPMENT',
    paces: ['SLOW', 'NORMAL', 'FAST'],
    limits: {
      max_sessions: 8,
      max_subscribers_per_session: 4,
      max_subscribers_total: 16,
      subscriber_queue: 64,
      timeline_retention: 500,
      timeline_page_max: 100,
      max_session_seconds: 1800,
      creations_per_minute: 12,
      max_concurrent_analyses: 2,
      min_window_candles: 50,
      max_window_candles: 1000,
      max_closed_candles: 2500,
      max_missing_sequences: 500,
      max_recorded_issues: 200,
      max_reconnects: 20,
      heartbeat_seconds: 15,
    },
    ...overrides,
  };
}

export function sourceDto(overrides: Partial<LiveSourceDto> = {}): LiveSourceDto {
  return {
    source_id: SOURCE,
    instrument_label: 'TEST_FIXTURE_FUT',
    origin: 'USER_SUPPLIED_HISTORICAL',
    contract_identity: 'NOT_ESTABLISHED',
    streamable: true,
    refusal: null,
    timeframes: [
      {
        timeframe: '5M',
        rows: 288,
        first_open_time: '2026-03-02T09:00:00+00:00',
        last_open_time: '2026-03-03T08:55:00+00:00',
      },
      {
        timeframe: '1H',
        rows: 24,
        first_open_time: '2026-03-02T09:00:00+00:00',
        last_open_time: '2026-03-03T08:00:00+00:00',
      },
    ],
    ...overrides,
  };
}

export function entryDto(seq: number, overrides: Partial<LiveTimelineEntryDto> = {}) {
  const entry: LiveTimelineEntryDto = {
    seq,
    kind: 'CANDLE_CONFIRMED',
    recorded_at: '2026-09-21T12:00:01+00:00',
    code: 'ACCEPTED',
    timeframe: '5M',
    market_open_time: '2026-03-02T10:00:00+00:00',
    market_event_time: '2026-03-02T10:05:00+00:00',
    sequence: seq,
    before: null,
    after: null,
    backfill: false,
    ...overrides,
  };
  return entry;
}

export function pageDto(
  entries: LiveTimelineEntryDto[],
  overrides: Partial<LiveTimelinePageDto> = {},
): LiveTimelinePageDto {
  return {
    session_id: SESSION_A,
    entries,
    cursor: entries.length === 0 ? 0 : Math.max(...entries.map((e) => e.seq)),
    oldest_retained: 1,
    gap: false,
    limit: 100,
    ...overrides,
  };
}

export function envelopeDto(overrides: Partial<LiveEnvelopeDto> = {}): LiveEnvelopeDto {
  return {
    protocol: 'viop.live.v1',
    session_id: SESSION_A,
    event_id: null,
    kind: 'HEARTBEAT',
    server_time: '2026-09-21T12:00:05+00:00',
    cursor: 20,
    entry: null,
    session: null,
    reason: null,
    ...overrides,
  };
}

export function liveAnalysisDto(overrides: Partial<LiveAnalysisDto> = {}): LiveAnalysisDto {
  return {
    session_id: SESSION_A,
    provenance: 'SIMULATED_HISTORICAL_STREAM',
    market_currency: 'HISTORICAL',
    market_as_of: '2026-03-03T09:00:00+00:00',
    requested_at: '2026-09-21T12:00:03+00:00',
    included: ['5M', '15M', '1H'],
    excluded: [],
    fingerprint: [
      { timeframe: '5M', version: 288 },
      { timeframe: '15M', version: 96 },
      { timeframe: '1H', version: 24 },
    ],
    reused: false,
    current: true,
    analysis: analysisDto(),
    session: sessionDto({
      analysis: {
        analyses_run: 1,
        available_timeframes: ['5M', '15M', '1H'],
        last_market_as_of: '2026-03-03T09:00:00+00:00',
        last_requested_at: '2026-09-21T12:00:03+00:00',
        last_current: true,
      },
    }),
    ...overrides,
  };
}
