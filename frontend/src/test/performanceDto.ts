import type { BreakdownsDto, JournalPageDto, MetricDto, PerformanceDto } from '../api/performance';

/**
 * Performance responses as the backend sends them (Phase 10 tests).
 *
 * TEST_FIXTURE data. The numbers deliberately do not "add up" from each other:
 * the realized net here is not gross minus fees, so a test can prove the UI
 * prints what the server said instead of deriving anything itself.
 */

export function metric(changes: Partial<MetricDto> = {}): MetricDto {
  return {
    status: 'AVAILABLE',
    value: '123.45',
    basis: 'REALIZED_GROSS',
    sample_size: 3,
    coverage: null,
    reason: null,
    numerator: null,
    denominator: null,
    ...changes,
  };
}

export function unavailable(
  reason: string,
  status: MetricDto['status'] = 'UNAVAILABLE',
): MetricDto {
  return { ...metric(), status, value: null, reason };
}

const counts = {
  total: 6,
  pending_entry: 1,
  open_positions: 1,
  partially_closed: 0,
  ambiguous_halted: 0,
  closed: 3,
  cancelled: 1,
  rejected: 0,
  entered: 4,
  open_exposure: 1,
  never_entered: 2,
};

const filters = {
  closed_from: null,
  closed_to: null,
  direction: null,
  symbol: null,
  timeframe: null,
  tag: null,
  range_rule: 'A position belongs to a range by the MARKET time of its closing fill.',
};

export function performanceDto(changes: Partial<PerformanceDto> = {}): PerformanceDto {
  return {
    simulated: true,
    source: 'PAPER_SIMULATION',
    basis: 'REALIZED_GROSS',
    basis_reason: '1 of 3 completed positions modelled fees, so a net result is not knowable',
    filters,
    counts,
    sample_size: 3,
    fill_count: 5,
    wins: 1,
    losses: 1,
    breakevens: 1,
    fee_coverage: { covered: 1, total: 3 },
    realized_accounting: {
      fill_count: 6,
      position_count: 4,
      from_completed_positions: 3,
      from_open_positions: 1,
      coverage: { covered: 2, total: 6 },
      // Deliberately not gross minus fees: the UI must print what it is given.
      gross: metric({ value: '145.00' }),
      fees_known: metric({ value: '18', coverage: { covered: 2, total: 6 } }),
      net: unavailable('only 2 of 6 fills modelled a fee', 'PARTIAL_COVERAGE'),
      time_rule: 'Each fill is attributed to its own market time.',
    },
    realized_gross: metric({ value: '60.00' }),
    fees_known: metric({ value: '16', coverage: { covered: 1, total: 3 } }),
    realized_net: unavailable(
      'only 1 of 3 completed positions modelled fees; a net total over a partly costed population would be a fabricated number',
      'PARTIAL_COVERAGE',
    ),
    unrealized_gross_open: metric({ value: '85.00', sample_size: 1 }),
    win_rate: metric({ value: '33.3333', numerator: 1, denominator: 3 }),
    average_win: metric({ value: '100.0000', sample_size: 1 }),
    average_loss: metric({ value: '40.0000', sample_size: 1 }),
    profit_factor: metric({ value: '2.5000' }),
    expectancy: metric({ value: '20.0000' }),
    max_drawdown_absolute: metric({ value: '40.00' }),
    drawdown_percentage: unavailable(
      'the application holds no account-capital timeline, so a percentage of capital cannot be computed',
    ),
    realized_r_expectancy: unavailable(
      'an R multiple needs one unambiguous initial-risk amount',
      'NOT_IMPLEMENTED',
    ),
    mae: unavailable('bar OHLC records no order inside a bar', 'NOT_IMPLEMENTED'),
    mfe: unavailable('bar OHLC records no order inside a bar', 'NOT_IMPLEMENTED'),
    sharpe_ratio: unavailable('a risk-adjusted ratio needs a capital base', 'NOT_IMPLEMENTED'),
    sortino_ratio: unavailable('a risk-adjusted ratio needs a capital base', 'NOT_IMPLEMENTED'),
    annualised_return: unavailable('a risk-adjusted ratio needs a capital base', 'NOT_IMPLEMENTED'),
    streaks: {
      current_kind: 'WIN',
      current_length: 1,
      max_win_streak: 1,
      max_loss_streak: 1,
      policy: 'BREAKEVEN_BREAKS_BOTH_STREAKS',
    },
    timeline: [
      {
        position_id: 'PP-0123456789abcdef01234561',
        terminal_time: '2026-03-02T12:00:00+00:00',
        amount: '100.00',
        cumulative: '100.00',
      },
      {
        position_id: 'PP-0123456789abcdef01234562',
        terminal_time: '2026-03-03T12:00:00+00:00',
        amount: '-40.00',
        cumulative: '60.00',
      },
      {
        position_id: 'PP-0123456789abcdef01234563',
        terminal_time: '2026-03-04T12:00:00+00:00',
        amount: '0.00',
        cumulative: '60.00',
      },
    ],
    analysis_linkage:
      'Positions are USER_CREATED and no analysis snapshot is persisted, so setup, regime, signal and AI-verdict performance cannot be derived.',
    ...changes,
  };
}

export function emptyPerformanceDto(): PerformanceDto {
  return performanceDto({
    sample_size: 0,
    wins: 0,
    losses: 0,
    breakevens: 0,
    fee_coverage: { covered: 0, total: 0 },
    realized_accounting: {
      fill_count: 0,
      position_count: 0,
      from_completed_positions: 0,
      from_open_positions: 0,
      coverage: { covered: 0, total: 0 },
      gross: unavailable('no exit has been filled in this selection'),
      fees_known: unavailable('no exit has been filled in this selection'),
      net: unavailable('no exit has been filled in this selection'),
      time_rule: 'Each fill is attributed to its own market time.',
    },
    counts: { ...counts, total: 1, closed: 0, open_positions: 1, entered: 1, never_entered: 0 },
    realized_gross: unavailable('no completed positions in this selection'),
    fees_known: unavailable('no completed positions in this selection'),
    realized_net: unavailable('no completed positions in this selection'),
    win_rate: unavailable('no completed positions, so there is no win rate to compute'),
    average_win: unavailable('no winning completed position in this selection'),
    average_loss: unavailable('no losing completed position in this selection'),
    profit_factor: unavailable('no completed positions in this selection'),
    expectancy: unavailable('no completed positions in this selection'),
    max_drawdown_absolute: unavailable('no completed positions, so there is no curve'),
    streaks: {
      current_kind: null,
      current_length: 0,
      max_win_streak: 0,
      max_loss_streak: 0,
      policy: 'BREAKEVEN_BREAKS_BOTH_STREAKS',
    },
    timeline: [],
  });
}

export function breakdownsDto(changes: Partial<BreakdownsDto> = {}): BreakdownsDto {
  const group = (key: string, label: string, sample: number) => ({
    key,
    label,
    counts,
    sample_size: sample,
    wins: 1,
    losses: 0,
    breakevens: 0,
    realized_gross: metric({ value: '60.00' }),
    realized_net: unavailable('no completed position modelled fees'),
    win_rate: metric({ value: '100.0000', numerator: 1, denominator: 1 }),
    expectancy: metric({ value: '60.0000' }),
  });
  const set = (rows: ReturnType<typeof group>[], total = rows.length) => ({
    rows,
    total,
    returned: rows.length,
    omitted: Math.max(0, total - rows.length),
    is_complete: rows.length >= total,
  });
  return {
    simulated: true,
    filters,
    by_direction: set([group('LONG', 'LONG', 2), group('SHORT', 'SHORT', 1)]),
    by_instrument: set([group('FUTURES:TEST_FIXTURE_FUT', 'TEST_FIXTURE_FUT', 3)]),
    by_timeframe: set([group('1H', '1H', 3)]),
    unavailable_breakdowns: [
      'setup: no persisted analysis linkage',
      'regime: no persisted analysis linkage',
      'ai_verdict: no persisted analysis linkage',
    ],
    ...changes,
  };
}

export function journalPageDto(changes: Partial<JournalPageDto> = {}): JournalPageDto {
  return {
    simulated: true,
    items: [
      {
        position_id: 'PP-0123456789abcdef01234561',
        symbol: 'TEST_FIXTURE_FUT',
        asset_class: 'FUTURES',
        direction: 'LONG',
        timeframe: '1H',
        quantity: 4,
        population: 'CLOSED',
        outcome: 'WIN',
        outcome_basis: 'REALIZED_GROSS',
        outcome_gross: 'WIN',
        outcome_net: null,
        realized_gross: '100.00',
        fees_total: null,
        realized_net: null,
        terminal_time: '2026-03-02T12:00:00+00:00',
        decision_time: '2026-03-02T10:00:00+00:00',
        annotation: {
          position_id: 'PP-0123456789abcdef01234561',
          note: 'Plana sadık kaldım.',
          tags: ['breakout'],
          version: 2,
          created_at: '2026-03-02T13:00:00+00:00',
          updated_at: '2026-03-02T14:00:00+00:00',
          provenance: 'USER_AUTHORED',
        },
      },
    ],
    total: 1,
    offset: 0,
    limit: 20,
    filters,
    ...changes,
  };
}
