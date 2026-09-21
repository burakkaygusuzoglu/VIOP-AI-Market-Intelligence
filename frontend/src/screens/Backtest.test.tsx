import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { performanceDto } from '../test/performanceDto';
import { BacktestScreen } from './Backtest';

/**
 * The backtesting workspace, against a stubbed transport (Phase 12 Part 2A).
 *
 * These tests are mostly about what the screen *refuses* to say. A backtesting
 * UI can flatter a result in three cheap ways - by showing a half-finished run
 * as a report, by turning an unmodelled fee into a zero, and by computing a
 * missing metric in the browser - and each of those has a test here.
 *
 * The payloads below are the real response shapes. Anything the screen renders
 * had to arrive in one of them.
 */

const CAPABILITY = {
  financial_execution_available: true,
  reason: 'Verified contract metadata is available.',
  refusal_code: null,
  max_boundaries: 2500,
  max_positions: 200,
  max_warm_up_bars: 500,
};

const STRATEGIES = {
  strategies: [
    {
      identifier: 'ema-crossover-atr',
      version: '1.0.0',
      summary: 'EMA 9/20 crossover. A validation instrument, not a recommendation.',
      warm_up_bars: 21,
      parameters: [
        { name: 'fast_period', value: '9', configurable: false },
        { name: 'slow_period', value: '20', configurable: false },
      ],
      directions: ['LONG', 'SHORT'],
      exposure: 'One position at a time.',
      stop_model: 'ATR x 1.5, aligned to the grid.',
      target_model: 'ATR x 3.0, aligned the same way.',
      entry_timing: 'The first bar opening at or after the decision boundary.',
    },
  ],
};

const DATASET_ID = 'RD-' + 'a'.repeat(32);

const DATASETS = {
  items: [
    {
      dataset_id: DATASET_ID,
      symbol: 'XU030',
      total_rows: 360,
      timeframes: [
        {
          timeframe: '5M',
          candles: 260,
          first_open_time: '2026-03-02T09:00:00+00:00',
          last_open_time: '2026-03-02T20:00:00+00:00',
        },
      ],
      provenance: 'Historical market data supplied by the user, stored immutably.',
    },
  ],
  total: 1,
  offset: 0,
  limit: 25,
};

function runPayload(overrides: Record<string, unknown> = {}) {
  return {
    run_id: 'BR-' + '1'.repeat(24),
    status: 'COMPLETED',
    results_are_final: true,
    configuration_fingerprint: 'BC-' + 'b'.repeat(32),
    configuration: {
      dataset_id: DATASET_ID,
      symbol: 'XU030',
      driver_timeframe: '5M',
      interval_start: '2026-03-02T14:00:00+00:00',
      interval_end: '2026-03-02T18:00:00+00:00',
      strategy_id: 'ema-crossover-atr',
      strategy_version: '1.0.0',
      strategy_parameters: { fast_period: '9' },
      simulation: { fee_mode: 'NOT_MODELLED', same_bar: 'STOP_FIRST' },
      risk: { mode: 'FIXED' },
      product_snapshot: { multiplier: '10' },
    },
    totals: {
      boundaries_evaluated: 171,
      first_boundary: '2026-03-02T14:00:00+00:00',
      last_boundary: '2026-03-02T18:00:00+00:00',
      decision_count: 171,
      position_count: 2,
      result_digest: 'BD-' + 'c'.repeat(32),
    },
    failure_code: null,
    failure_reason: null,
    created_at: '2026-03-02T18:00:00+00:00',
    updated_at: '2026-03-02T18:00:01+00:00',
    provenance: 'Simulated trades over historical data. No order was placed.',
    ...overrides,
  };
}

const RUN_LIST = {
  items: [
    {
      run_id: 'BR-' + '1'.repeat(24),
      configuration: 'BC-' + 'b'.repeat(32),
      dataset_id: DATASET_ID,
      symbol: 'XU030',
      driver_timeframe: '5M',
      strategy_id: 'ema-crossover-atr',
      strategy_version: '1.0.0',
      status: 'COMPLETED',
      boundaries_evaluated: 171,
      position_count: 2,
      created_at: '2026-03-02T18:00:00+00:00',
      updated_at: '2026-03-02T18:00:01+00:00',
    },
  ],
  total: 1,
  offset: 0,
  limit: 20,
};

const PERFORMANCE = {
  run_id: 'BR-' + '1'.repeat(24),
  status: 'COMPLETED',
  // The Phase 10 payload comes from the shared factory rather than being
  // hand-built here: a stub that drifts from the real schema would pass while
  // the product failed.
  performance: performanceDto(),
};

const TRACE = {
  run_id: 'BR-' + '1'.repeat(24),
  items: [
    {
      sequence: 1,
      as_of: '2026-03-02T14:00:00+00:00',
      outcome: 'NO_SIGNAL',
      reason: 'no crossover on this candle',
      bars_available: 60,
      position_id: null,
      risk_outcome: null,
      risk_reason: null,
      direction: null,
    },
    {
      sequence: 2,
      as_of: '2026-03-02T14:05:00+00:00',
      outcome: 'REFUSED_BY_RISK',
      reason: 'long crossover confirmed',
      bars_available: 61,
      position_id: null,
      risk_outcome: 'INVALID',
      risk_reason: 'stop is not on the 0.25 tick grid',
      direction: 'LONG',
    },
  ],
  total: 171,
  offset: 0,
  limit: 25,
};

const POSITIONS = {
  run_id: 'BR-' + '1'.repeat(24),
  items: [
    {
      position_id: 'BP-abc',
      ordinal: 1,
      direction: 'LONG',
      symbol: 'XU030',
      timeframe: '5M',
      quantity: 1,
      remaining: 0,
      state: 'CLOSED',
      intended_entry: '100',
      entry_fill_price: '100',
      stop: '97',
      targets: [{ price: '106', quantity: 1, filled: true, fill_price: '106' }],
      decision_time: '2026-03-02T14:10:00+00:00',
      entry_time: '2026-03-02T14:10:00+00:00',
      realized_gross: '60',
      fees_total: null,
      realized_net: null,
      unrealized_gross: null,
      event_count: 8,
      origin: 'STRATEGY_BACKTEST',
      provenance: 'Simulated fill computed by the paper-trading engine.',
    },
  ],
  total: 1,
  offset: 0,
  limit: 25,
};

interface Routes {
  readonly capability?: unknown;
  readonly strategies?: unknown;
  readonly datasets?: unknown;
  readonly runList?: unknown;
  readonly run?: unknown;
  readonly trace?: unknown;
  readonly positions?: unknown;
  readonly events?: unknown;
  readonly performance?: unknown;
}

/**
 * Route by shape, not by substring.
 *
 * `/backtest/runs` is a prefix of every run URL, so any "first match" or
 * "longest match" lookup answers the trace request with the run list. The
 * order below is most specific first, which is the only ordering that is
 * actually correct.
 */
function resolve(path: string, routes: Routes): unknown {
  if (path.includes('/events')) return routes.events;
  if (path.includes('/trace')) return routes.trace;
  if (path.includes('/positions')) return routes.positions;
  if (path.includes('/performance')) return routes.performance;
  if (path.includes('/capability')) return routes.capability;
  if (path.includes('/strategies')) return routes.strategies;
  if (path.includes('/datasets')) return routes.datasets;
  if (/\/runs\/BR-/.test(path)) return routes.run;
  if (path.includes('/runs')) return routes.runList;
  return undefined;
}

function stubApi(routes: Routes, onRequest?: (path: string) => void) {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
    const path = String(input);
    onRequest?.(path);
    const payload = resolve(path, routes);
    if (payload === undefined) {
      return new Response(JSON.stringify({ detail: 'not stubbed' }), { status: 404 });
    }
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    });
  });
}

const HAPPY: Routes = {
  capability: CAPABILITY,
  strategies: STRATEGIES,
  datasets: DATASETS,
  runList: RUN_LIST,
  run: runPayload(),
  trace: TRACE,
  positions: POSITIONS,
  performance: PERFORMANCE,
  events: {
    run_id: 'BR-' + '1'.repeat(24),
    position_id: 'BP-abc',
    items: [
      {
        sequence: 1,
        type: 'ENTRY_FILLED',
        market_time: '2026-03-02T14:10:00+00:00',
        data: { fill_price: '100' },
      },
    ],
    total: 8,
    after_sequence: 0,
    limit: 100,
  },
};

afterEach(() => {
  vi.unstubAllGlobals();
});

async function show(mode: 'BEGINNER' | 'PRO' = 'BEGINNER', routes: Routes = HAPPY) {
  stubApi(routes);
  const view = render(<BacktestScreen mode={mode} />);
  await screen.findByRole('heading', { name: /dönük test/ });
  return view;
}

describe('the workspace states what it is', () => {
  it('says the trades are simulated and the past guarantees nothing', async () => {
    await show();

    expect(screen.getAllByText(/simülasyondur/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/gelecekteki getiriyi/).length).toBeGreaterThan(0);
  });

  it('never claims the reference strategy is profitable', async () => {
    await show();

    expect(document.body.textContent).not.toMatch(/kârlı|karlı|kazandırır|garanti eder/i);
  });

  it('offers no optimiser, sweep or ranking control', async () => {
    await show();

    expect(document.body.textContent).not.toMatch(
      /optimiz|tarama|sıralama tablosu|en iyi strateji/i,
    );
  });
});

describe('the configuration form', () => {
  it('labels every control', async () => {
    await show();

    expect(screen.getByLabelText(/veri seti/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/zaman dilimi/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/langıç/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/büyüklüğü/i)).toBeInTheDocument();
    expect(screen.getByLabelText(/başına risk/i)).toBeInTheDocument();
  });

  it('shows the registered strategy and marks its parameters fixed', async () => {
    await show();

    expect(await screen.findByText(/EMA 9\/20 crossover/)).toBeInTheDocument();
    expect(screen.getByText(/kod içinde sabittir/i)).toBeInTheDocument();
    expect(screen.getAllByText('sabit').length).toBeGreaterThan(0);
  });

  it('offers no input that would change a pinned parameter', async () => {
    await show();

    expect(screen.queryByLabelText(/fast_period/i)).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/slow_period/i)).not.toBeInTheDocument();
  });

  it('warns that an unmodelled fee is not a zero fee', async () => {
    await show();

    expect(screen.getByText(/net sonuç hiç raporlanmaz/i)).toBeInTheDocument();
    expect(screen.getByText(/sıfır maliyet demek değildir/i)).toBeInTheDocument();
  });
});

describe('when the deployment cannot price a trade', () => {
  it('says so and disables the form', async () => {
    await show('BEGINNER', {
      ...HAPPY,
      capability: {
        ...CAPABILITY,
        financial_execution_available: false,
        refusal_code: 'PRODUCT_METADATA_UNAVAILABLE',
        reason: 'Historical candles do not establish a multiplier or a tick size.',
      },
    });

    expect(await screen.findByText(/anda kullanılamıyor/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /çalıştır/ })).toBeDisabled();
  });

  it('shows the refusal code only in pro mode', async () => {
    await show('PRO', {
      ...HAPPY,
      capability: {
        ...CAPABILITY,
        financial_execution_available: false,
        refusal_code: 'PRODUCT_METADATA_UNAVAILABLE',
        reason: 'no verified metadata',
      },
    });

    expect(await screen.findByText('PRODUCT_METADATA_UNAVAILABLE')).toBeInTheDocument();
  });
});

describe('run status presentation', () => {
  it('shows a completed run as final and renders the phase 10 metrics', async () => {
    await show('PRO', HAPPY);
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));

    expect(await screen.findByText('Tamamlandı')).toBeInTheDocument();
    // The metrics themselves are rendered by Phase 10's own component; what
    // matters here is that the screen handed it the payload rather than
    // deriving one.
    await waitFor(() =>
      expect(screen.getByRole('heading', { name: /performans/i })).toBeInTheDocument(),
    );
  });

  it('never presents a pending run as a result', async () => {
    await show('BEGINNER', {
      ...HAPPY,
      run: runPayload({
        status: 'PENDING',
        results_are_final: false,
        totals: { ...runPayload().totals, result_digest: null },
      }),
    });
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));

    expect(await screen.findByText(/kalmış olabilir/i)).toBeInTheDocument();
    expect(screen.getByText(/kesinleşmiş bir performans raporu yok/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /sonlandır/ })).toBeInTheDocument();
  });

  it('shows a failed run without fabricating a report', async () => {
    await show('PRO', {
      ...HAPPY,
      run: runPayload({
        status: 'FAILED',
        results_are_final: false,
        failure_code: 'INTERRUPTED',
        failure_reason: 'this run was left unfinished by an interruption',
      }),
    });
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));

    expect(await screen.findByText(/koşu tamamlanmadı/i)).toBeInTheDocument();
    expect(screen.getByText(/sıfır değildir/i)).toBeInTheDocument();
    expect(screen.getByText('INTERRUPTED')).toBeInTheDocument();
  });
});

describe('the decision trace', () => {
  it('shows refusals as refusals, not as trades', async () => {
    await show('PRO', HAPPY);
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Karar izi' }));

    expect(await screen.findByText('Risk reddetti')).toBeInTheDocument();
    expect(screen.getByText('Sinyal yok')).toBeInTheDocument();
    expect(screen.getByText(/reddediliş işlem değildir/)).toBeInTheDocument();
  });

  it('says how much of the trace it is showing', async () => {
    await show('PRO', HAPPY);
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Karar izi' }));

    expect(await screen.findByText(/171 karar kaydından/)).toBeInTheDocument();
  });
});

describe('positions', () => {
  it('shows an unmodelled fee as unmodelled rather than as zero', async () => {
    await show('PRO', HAPPY);
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Pozisyonlar' }));

    expect(await screen.findByText('komisyon modellenmedi')).toBeInTheDocument();
    expect(screen.getByText('60')).toBeInTheDocument();
  });

  it('opens a position ledger on request', async () => {
    await show('PRO', HAPPY);
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Pozisyonlar' }));
    await userEvent.click(await screen.findByRole('button', { name: 'Defteri aç' }));

    expect(await screen.findByText('ENTRY_FILLED')).toBeInTheDocument();
    expect(screen.getByText(/8 kayıttan ilk 1/i)).toBeInTheDocument();
  });
});

describe('beginner and pro show the same truth', () => {
  it('pro adds identifiers without changing the numbers', async () => {
    await show('PRO', HAPPY);
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));

    expect(await screen.findByText('BC-' + 'b'.repeat(32))).toBeInTheDocument();
    expect(screen.getByText('BD-' + 'c'.repeat(32))).toBeInTheDocument();
  });

  it('beginner explains the rules without exposing digests', async () => {
    await show('BEGINNER', HAPPY);

    expect(screen.getByText(/yapay zekâ değil/)).toBeInTheDocument();
    expect(screen.queryByText('BC-' + 'b'.repeat(32))).not.toBeInTheDocument();
  });
});

describe('empty states are truthful', () => {
  it('says when no dataset exists rather than offering an empty form', async () => {
    await show('BEGINNER', {
      ...HAPPY,
      datasets: { items: [], total: 0, offset: 0, limit: 25 },
    });

    expect(await screen.findByText(/veri seti yok/)).toBeInTheDocument();
  });

  it('says when no run has been made', async () => {
    await show('BEGINNER', {
      ...HAPPY,
      runList: { items: [], total: 0, offset: 0, limit: 20 },
    });
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));

    expect(screen.getByText(/test çalıştırılmadı/)).toBeInTheDocument();
  });

  it('does not show a trace or positions for an unfinished run', async () => {
    await show('BEGINNER', {
      ...HAPPY,
      run: runPayload({ status: 'PENDING', results_are_final: false }),
    });
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));
    await userEvent.click(screen.getByRole('button', { name: 'Aç' }));
    await userEvent.click(screen.getByRole('tab', { name: 'Karar izi' }));

    expect(screen.getByText(/yalnızca tamamlanmış bir koşu için gösterilir/i)).toBeInTheDocument();
  });
});

describe('accessibility', () => {
  it('exposes the sections as a tab list', async () => {
    await show();

    const tabs = screen.getAllByRole('tab');
    expect(tabs).toHaveLength(5);
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true');
  });

  it('announces status changes in a live region', async () => {
    const { container } = await show();

    expect(container.querySelector('[aria-live="polite"]')).not.toBeNull();
  });

  it('gives every table a caption', async () => {
    await show('PRO');
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));

    const table = screen.getByRole('table');
    expect(within(table).getByText('Çalıştırılan testler')).toBeInTheDocument();
  });

  it('does not carry outcome in colour alone', async () => {
    await show('PRO');
    await userEvent.click(screen.getByRole('tab', { name: 'Geçmiş' }));

    expect(screen.getByText('Tamamlandı')).toBeInTheDocument();
  });
});
