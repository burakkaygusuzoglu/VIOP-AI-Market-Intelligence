import type { ReplayCandleDto, ReplaySessionDto, ReplayWindowDto } from '../api/replay';
import type { ChartSeries } from './models';

/**
 * Replay read models (Phase 11).
 *
 * Shape only. Nothing here computes a price, a count or a state: every number
 * arrives from the server as an exact decimal string and is carried through as
 * one. The replay clock in particular is never derived in the browser - a
 * cursor the client could work out for itself is a cursor the client could get
 * wrong while the server was still right.
 */

export const REPLAY_STATE_LABEL: Record<string, string> = {
  READY: 'Hazır',
  IN_PROGRESS: 'Devam ediyor',
  END_OF_DATASET: 'Veri sonu',
};

export interface ReplayWindowModel {
  readonly timeframe: string;
  readonly candles: readonly ReplayCandleDto[];
  /** How many candles of this timeframe have been revealed so far. */
  readonly revealed: number;
  /** How many exist in the immutable dataset. */
  readonly datasetTotal: number;
  readonly windowLimit: number;
  readonly truncated: boolean;
}

export function mapWindow(dto: ReplayWindowDto): ReplayWindowModel {
  return {
    timeframe: dto.timeframe,
    candles: dto.candles,
    revealed: dto.revealed,
    datasetTotal: dto.dataset_total,
    windowLimit: dto.window_limit,
    truncated: dto.truncated,
  };
}

/**
 * A revealed window as the existing candlestick chart reads it.
 *
 * The chart is the Phase 8 component, unchanged. `analysedCount` and
 * `omittedCount` are the counts the server sent, so "showing 400 of 900
 * revealed" is the server's statement rather than the browser's arithmetic.
 */
export function toChartSeries(window: ReplayWindowModel): ChartSeries {
  return {
    timeframe: window.timeframe,
    candles: window.candles.map((candle) => ({
      openTime: candle.open_time,
      open: candle.open,
      high: candle.high,
      low: candle.low,
      close: candle.close,
      volume: candle.volume,
      // Only finished candles are ever revealed, which is the replay rule
      // itself - a forming bar has no coverage end at or before the cursor.
      isClosed: true,
    })),
    zones: [],
    analysedCount: window.revealed,
    omittedCount: window.revealed - window.candles.length,
    windowPolicy: `Son ${window.windowLimit} açıklanmış mum`,
    overlays: [],
  };
}

export function windowOf(session: ReplaySessionDto, timeframe: string): ReplayWindowModel | null {
  const found = session.availability.find((item) => item.timeframe === timeframe);
  return found === undefined ? null : mapWindow(found);
}

/** The timeframes this session holds data for, in the server's order. */
export function timeframesOf(session: ReplaySessionDto): readonly string[] {
  return session.availability.map((item) => item.timeframe);
}

export function isAtEnd(session: ReplaySessionDto): boolean {
  return session.cursor.state === 'END_OF_DATASET';
}

/**
 * Whether an arriving session is newer than the one on screen.
 *
 * Play issues one step after another, and responses can arrive out of order.
 * The cursor version is a server counter that only ever increases for a
 * session, so it - not arrival order - decides what is current. Without this,
 * a slow answer to step 7 could put the screen back to step 6 while the server
 * was at 8.
 */
export function isNewerThan(incoming: ReplaySessionDto, current: ReplaySessionDto | null): boolean {
  if (current === null) return true;
  if (incoming.id !== current.id) return true;
  return incoming.cursor.version > current.cursor.version;
}

export const PLAYBACK_SPEEDS = [0.5, 1, 2, 5] as const;

export type PlaybackSpeed = (typeof PLAYBACK_SPEEDS)[number];

const BASE_DELAY_MS = 1000;

/**
 * How long the browser waits before asking for the next candle.
 *
 * Presentation only. Speed changes the delay between commands and nothing
 * else: each command is the same single step, the server does the same work,
 * and the resulting cursor, fills and metrics are identical whether a person
 * watched at 0.5x or 5x.
 */
export function delayForSpeed(speed: PlaybackSpeed): number {
  return BASE_DELAY_MS / speed;
}
