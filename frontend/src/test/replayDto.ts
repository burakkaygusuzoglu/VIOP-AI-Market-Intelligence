import type { ReplaySessionDto, ReplayStepDto } from '../api/replay';

/**
 * Replay DTO fixtures (Phase 11).
 *
 * **Every price here is TEST_FIXTURE data.** The shape matches the server's
 * response exactly, because a fixture that drifts from the contract lets a
 * screen test pass while the real payload would have failed validation.
 */

const BASE = '2026-03-02T';

export function candle(minutes: number, close = '100.5') {
  const hour = 9 + Math.floor(minutes / 60);
  const minute = minutes % 60;
  const stamp = `${BASE}${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00+00:00`;
  return {
    open_time: stamp,
    open: '100.0',
    high: '101.0',
    low: '99.0',
    close,
    volume: '1000',
  };
}

export function replayDto(overrides: Partial<ReplaySessionDto> = {}): ReplaySessionDto {
  const base: ReplaySessionDto = {
    id: 'RS-0000000000000000000000a1',
    simulated: true,
    plan: {
      symbol: 'TEST_FIXTURE_FUT',
      driver_timeframe: '5M',
      replay_start: `${BASE}10:00:00+00:00`,
    },
    cursor: {
      replay_as_of: `${BASE}10:00:00+00:00`,
      revealed_driver_candles: 12,
      driver_total_candles: 288,
      state: 'READY',
      version: 1,
    },
    dataset: {
      dataset_id: 'RD-00000000000000000000000000000001',
      symbol: 'TEST_FIXTURE_FUT',
      total_rows: 300,
      timeframes: [
        {
          timeframe: '5M',
          rows: 288,
          first_open_time: `${BASE}09:00:00+00:00`,
          last_open_time: `${BASE}12:55:00+00:00`,
          last_coverage_end: `${BASE}13:00:00+00:00`,
        },
      ],
    },
    availability: [
      {
        timeframe: '5M',
        candles: [candle(0), candle(5), candle(10)],
        revealed: 12,
        dataset_total: 288,
        window_limit: 400,
        truncated: false,
      },
      {
        timeframe: '1H',
        candles: [],
        revealed: 1,
        dataset_total: 24,
        window_limit: 400,
        truncated: false,
      },
    ],
    linked_position_ids: [],
    provenance: {
      market_data: 'USER_SUPPLIED_HISTORICAL',
      fills: 'SIMULATED',
      origin: 'REPLAY',
      execution: 'DISABLED',
    },
    max_advance_steps: 50,
    created_at: `${BASE}09:00:00+00:00`,
    updated_at: `${BASE}10:00:00+00:00`,
    idempotent_replay: false,
  };
  return { ...base, ...overrides };
}

/** The same session one step later, with the cursor and version moved on. */
export function steppedDto(version: number, minutes: number): ReplaySessionDto {
  const hour = 10 + Math.floor(minutes / 60);
  const minute = minutes % 60;
  const stamp = `${BASE}${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}:00+00:00`;
  const base = replayDto();
  return {
    ...base,
    cursor: {
      ...base.cursor,
      replay_as_of: stamp,
      revealed_driver_candles: 12 + version - 1,
      state: 'IN_PROGRESS',
      version,
    },
  };
}

export function stepDto(version: number, minutes: number): ReplayStepDto {
  const session = steppedDto(version, minutes);
  return {
    session,
    revealed_boundaries: [session.cursor.replay_as_of],
    observed_positions: 0,
    idempotent_replay: false,
  };
}

export function listDto(items: ReplaySessionDto[] = []) {
  return {
    items: items.map((item) => ({
      id: item.id,
      dataset_id: item.dataset.dataset_id,
      symbol: item.plan.symbol,
      driver_timeframe: item.plan.driver_timeframe,
      replay_start: item.plan.replay_start,
      replay_as_of: item.cursor.replay_as_of,
      revealed_driver_candles: item.cursor.revealed_driver_candles,
      driver_total_candles: item.cursor.driver_total_candles,
      state: item.cursor.state,
      version: item.cursor.version,
      created_at: item.created_at,
      updated_at: item.updated_at,
    })),
    total: items.length,
    offset: 0,
    limit: 20,
  };
}
