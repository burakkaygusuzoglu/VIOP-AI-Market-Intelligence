import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  journalPageSchema,
  shadowCapabilitySchema,
  shadowRunSchema,
  type ShadowCapabilityDto,
  type ShadowDevelopmentDto,
  type ShadowEntryDto,
  type ShadowJournalPageDto,
  type ShadowOutcomeDto,
  type ShadowRunDto,
} from '../api/shadow';
import {
  MAX_HELD_ENTRIES,
  MAX_HELD_RUNS,
  describeDevelopment,
  emptyJournal,
  heldSummary,
  isSuperseded,
  supersededBoundaries,
  withPage,
  withRunPage,
} from '../domain/shadow';
import { ShadowScreen } from '../screens/Shadow';
import { sessionDto } from '../test/liveDto';

/**
 * The Shadow research UI (Phase 14 Part 2A).
 *
 * What matters is not layout. The screen must say that the stream is simulated
 * history and that nothing was traded; it must never turn a touched level into
 * money or an empty journal into a population of trades; Beginner and Pro must
 * state the same financial truth; and no late answer for one run may appear
 * under another.
 */

// ----------------------------------------------------------------------
// Test data - exact shapes the backend sends, parsed by the real schemas
// ----------------------------------------------------------------------

const RUN_A = `SR-${'a'.repeat(24)}`;
const RUN_B = `SR-${'b'.repeat(24)}`;
const SESSION = `LS-${'c'.repeat(24)}`;

function capabilityDto(overrides: Partial<ShadowCapabilityDto> = {}): ShadowCapabilityDto {
  return shadowCapabilitySchema.parse({
    available: true,
    reason: 'observing simulated historical streams; no order path exists',
    provenance: 'SIMULATED_HISTORICAL_STREAM',
    market_currency: 'HISTORICAL',
    financial_metadata_available: false,
    execution_enabled: false,
    strategies: [{ strategy_id: 'ema-crossover-atr', versions: ['1.0.0'] }],
    limits: {
      max_runs: 4,
      max_observations: 5000,
      max_journal_page: 100,
      max_outcome_page: 100,
      max_outcome_window: 24,
      max_open_watches: 500,
    },
    server_time: '2026-09-24T10:00:00+00:00',
    ...overrides,
  });
}

function runDto(runId: string, overrides: Partial<ShadowRunDto> = {}): ShadowRunDto {
  return shadowRunSchema.parse({
    run_id: runId,
    configuration: `SC-${'f'.repeat(64)}`,
    source_id: `RD-${'e'.repeat(32)}`,
    instrument_label: runId === RUN_A ? 'ALPHA_FUT' : 'BETA_FUT',
    provenance: 'SIMULATED_HISTORICAL_STREAM',
    market_currency: 'HISTORICAL',
    strategy_id: 'ema-crossover-atr',
    strategy_version: '1.0.0',
    strategy_parameters: {},
    driver: '5M',
    timeframes: ['5M'],
    required_timeframes: ['5M'],
    status: 'ENDED',
    end_reason: 'STREAM_ENDED',
    failure_code: null,
    observations: 3,
    decisions: 2,
    entries: 5,
    first_boundary: '2026-03-02T09:05:00+00:00',
    last_boundary: '2026-03-02T09:15:00+00:00',
    started_at: '2026-09-24T09:00:00+00:00',
    ended_at: '2026-09-24T09:01:00+00:00',
    completeness: 'COMPLETE',
    ...overrides,
  });
}

function development(overrides: Partial<ShadowDevelopmentDto> = {}): ShadowDevelopmentDto {
  return {
    state: 'OBSERVED',
    event: 'TARGET_LEVEL_TOUCHED',
    rules: 'shadow-outcome/v1',
    observed_from: '2026-03-02T09:10:00+00:00',
    observed_to: '2026-03-02T09:15:00+00:00',
    candles_observed: 2,
    event_at: '2026-03-02T09:15:00+00:00',
    target_ordinal: 1,
    best_price: '103.25',
    worst_price: '99.5',
    last_close: '101',
    ambiguous: false,
    unresolved_reason: null,
    recorded_at: '2026-09-24T09:00:30+00:00',
    decision_boundary: '2026-03-02T09:05:00+00:00',
    ...overrides,
  };
}

function decision(
  sequence: number,
  outcome: ShadowEntryDto['outcome'],
  overrides: Partial<ShadowEntryDto> = {},
): ShadowEntryDto {
  const intent = outcome === 'ENTRY_INTENT';
  return {
    kind: 'DECISION',
    sequence,
    decision_key: `${sequence}`.padStart(64, '0'),
    market_boundary: `2026-03-02T09:${String(sequence * 5).padStart(2, '0')}:00+00:00`,
    recorded_at: '2026-09-24T09:00:10+00:00',
    outcome,
    operational: null,
    strategy_kind: outcome,
    reason: `kural cevabı ${sequence}`,
    direction: intent ? 'LONG' : null,
    entry: intent
      ? {
          direction: 'LONG',
          intended_entry: '100.5',
          stop: '99.5',
          targets: [['102.5', 1]],
          requested_quantity: 1,
          approved_quantity: null,
        }
      : null,
    financial_state: intent ? 'METADATA_UNAVAILABLE' : 'NOT_APPLICABLE',
    risk_outcome: null,
    risk_reason: intent ? 'no verified contract metadata provider is configured' : null,
    evidence: {
      provenance: 'SIMULATED_HISTORICAL_STREAM',
      market_currency: 'HISTORICAL',
      connection: 'CONNECTED',
      bars_available: 40,
      included: ['5M'],
      excluded: [],
      timeframes: [],
      readings: [
        {
          timeframe: '5M',
          ema_fast: '100.4',
          ema_slow: '100.1',
          rsi: '55.2',
          atr: '0.8',
          adx: null,
        },
      ],
      regime: null,
      suitability: null,
      setup_quality: null,
      analysis_market_as_of: null,
    },
    input_fingerprint: 'e'.repeat(64),
    development: null,
    ...overrides,
  };
}

function operational(
  sequence: number,
  kind: string,
  boundary: string | null = null,
): ShadowEntryDto {
  return {
    kind: 'OPERATIONAL',
    sequence,
    decision_key: `op${sequence}`.padStart(64, '0'),
    market_boundary: boundary,
    recorded_at: '2026-09-24T09:00:00+00:00',
    outcome: null,
    operational: kind,
    strategy_kind: null,
    reason: '',
    direction: null,
    entry: null,
    financial_state: 'NOT_APPLICABLE',
    risk_outcome: null,
    risk_reason: null,
    evidence: null,
    input_fingerprint: null,
    development: null,
  };
}

function page(runId: string, items: ShadowEntryDto[], total = items.length): ShadowJournalPageDto {
  return journalPageSchema.parse({
    run_id: runId,
    items,
    total,
    next_after: items.at(-1)?.sequence ?? null,
    server_time: '2026-09-24T10:00:00+00:00',
  });
}

function outcomeDto(sequence: number, overrides: Partial<ShadowDevelopmentDto>): ShadowOutcomeDto {
  return {
    decision_key: `${sequence}`.padStart(64, '0'),
    sequence,
    outcome_key: `o${sequence}`.padStart(64, '0'),
    recorded_at: '2026-09-24T09:00:30+00:00',
    decision_boundary: '2026-03-02T09:05:00+00:00',
    direction: 'LONG',
    development: development(overrides),
  };
}

// ----------------------------------------------------------------------
// A routed fetch
// ----------------------------------------------------------------------

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

interface Call {
  readonly url: string;
  readonly method: string;
  readonly body: unknown;
}

type Route = (
  url: string,
  method: string,
  body: unknown,
) => Response | Promise<Response> | undefined;

interface Backend {
  capability?: ShadowCapabilityDto;
  runs?: ShadowRunDto[];
  journals?: Record<string, ShadowJournalPageDto>;
  outcomes?: Record<string, ShadowOutcomeDto[]>;
  extra?: Route;
}

function stubBackend(backend: Backend): Call[] {
  const calls: Call[] = [];
  const runs = backend.runs ?? [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method ?? 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null;
      calls.push({ url, method, body });
      const extra = backend.extra?.(url, method, body);
      if (extra !== undefined) return extra;
      if (url.endsWith('/api/shadow/capability'))
        return json(backend.capability ?? capabilityDto());
      if (url.startsWith('/api/shadow/runs?')) return json({ items: runs, total: runs.length });
      const journal = /\/api\/shadow\/runs\/(SR-[a-f0-9]+)\/journal/.exec(url);
      if (journal) {
        const id = journal[1] ?? '';
        const after = Number(new URL(url, 'http://x').searchParams.get('after'));
        const held = backend.journals?.[id] ?? page(id, []);
        const items = held.items.filter((item) => item.sequence > after);
        return json(page(id, items, held.total));
      }
      const outcomes = /\/api\/shadow\/runs\/(SR-[a-f0-9]+)\/outcomes/.exec(url);
      if (outcomes) {
        const id = outcomes[1] ?? '';
        const items = backend.outcomes?.[id] ?? [];
        return json({
          run_id: id,
          items,
          total: items.length,
          next_after: null,
          server_time: '2026-09-24T10:00:00+00:00',
        });
      }
      const one = /\/api\/shadow\/runs\/(SR-[a-f0-9]+)$/.exec(url);
      if (one) {
        const found = runs.find((item) => item.run_id === one[1]);
        return found
          ? json(found)
          : json({ detail: { code: 'SHADOW_RUN_NOT_FOUND', detail: 'no such shadow run' } }, 404);
      }
      if (url.endsWith('/api/live/sessions')) {
        return json({
          items: [
            {
              id: SESSION,
              instrument_label: 'ALPHA_FUT',
              source_id: `RD-${'e'.repeat(32)}`,
              lifecycle: 'RUNNING',
              connection: 'CONNECTED',
              created_at: '2026-09-24T08:59:00+00:00',
              cursor: 10,
            },
          ],
          capacity: 8,
        });
      }
      return json({ detail: 'unexpected request in test' }, 500);
    }),
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function openRun(label: string) {
  const user = userEvent.setup();
  await user.click(await screen.findByRole('button', { name: new RegExp(label) }));
  await screen.findByRole('tablist', { name: 'Gölge gözlemi bölümleri' });
  return user;
}

// ----------------------------------------------------------------------
// Always-visible truth
// ----------------------------------------------------------------------

describe('the screen states what it is', () => {
  it('says simulated history, no execution and no financial approval', async () => {
    stubBackend({});
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);

    expect(await screen.findByText('GÖLGE MODU — YALNIZCA GÖZLEM')).toBeInTheDocument();
    const chips = await screen.findByRole('list', { name: 'Gölge modunun niteliği' });
    expect(within(chips).getByText('Kaynak: SİMÜLE GEÇMİŞ AKIŞ')).toBeInTheDocument();
    expect(within(chips).getByText('Emir yürütme: DEVRE DIŞI')).toBeInTheDocument();
    expect(
      within(chips).getByText(/KULLANILAMAZ — doğrulanmış sözleşme bilgisi yok/),
    ).toBeInTheDocument();
  });

  it('explains Shadow Mode to a beginner in Turkish', async () => {
    stubBackend({});
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);

    const guide = await screen.findByRole('region', { name: 'Gölge modu nedir?' });
    expect(within(guide).getByText(/varsayımsal kararlarını/)).toBeInTheDocument();
    expect(within(guide).getByText(/Gerçek emir verilmez/)).toBeInTheDocument();
    expect(within(guide).getByText(/simüle edilmiş oynatımıdır/)).toBeInTheDocument();
    expect(within(guide).getByText(/bir işlem değildir/)).toBeInTheDocument();
    expect(within(guide).getByText(/kâr demek değildir/)).toBeInTheDocument();
    expect(within(guide).getByText(/belirlenemedi/)).toBeInTheDocument();
    expect(within(guide).getByText(/finansal\s+olarak onaylanamaz/)).toBeInTheDocument();
  });

  it('does not overwhelm a Pro user with the guide', async () => {
    stubBackend({});
    render(<ShadowScreen mode="PRO" onBack={() => undefined} />);
    await screen.findByText('GÖLGE MODU — YALNIZCA GÖZLEM');

    expect(screen.queryByRole('region', { name: 'Gölge modu nedir?' })).not.toBeInTheDocument();
  });

  it('shows the disabled reason and offers no form when shadow is off', async () => {
    stubBackend({ capability: capabilityDto({ available: false, reason: 'Gölge modu kapalı.' }) });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);

    expect(await screen.findByText(/Durum: KAPALI — Gölge modu kapalı\./)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Gölge gözlemini başlat' }),
    ).not.toBeInTheDocument();
  });

  it('pages the run history instead of hiding older runs', async () => {
    const all = Array.from({ length: 30 }, (_, index) =>
      runDto(`SR-${String(index).padStart(24, '0')}`, { instrument_label: `RUN_${index}` }),
    );
    stubBackend({
      extra: (url) => {
        if (!url.startsWith('/api/shadow/runs?')) return undefined;
        const params = new URL(url, 'http://x').searchParams;
        const offset = Number.parseInt(params.get('offset') ?? '0', 10);
        const limit = Number.parseInt(params.get('limit') ?? '25', 10);
        return json({ items: all.slice(offset, offset + limit), total: all.length });
      },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = userEvent.setup();

    expect(await screen.findByText(/30 gözlemin en yeni 25 tanesi/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /RUN_29 / })).not.toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Daha eski gözlemleri yükle' }));

    expect(await screen.findByRole('button', { name: /RUN_29 / })).toBeInTheDocument();
    expect(screen.getByText(/30 gözlemin en yeni 30 tanesi/)).toBeInTheDocument();
    expect(
      screen.queryByRole('button', { name: 'Daha eski gözlemleri yükle' }),
    ).not.toBeInTheDocument();
  });

  it('bounds the history it holds and says so', () => {
    const page = Array.from({ length: 300 }, (_, index) =>
      runDto(`SR-${String(index).padStart(24, '0')}`),
    );
    const held = withRunPage([], page);

    expect(held).toHaveLength(MAX_HELD_RUNS);
    expect(withRunPage(held.slice(0, 2), held.slice(0, 3))).toHaveLength(3); // no duplicates
  });

  it('tells runs of the same rules apart: Pro shows the run id', async () => {
    const twins = [
      runDto(RUN_A, { instrument_label: 'SAME' }),
      runDto(RUN_B, { instrument_label: 'SAME' }),
    ];
    stubBackend({ runs: twins });
    const view = render(<ShadowScreen mode="PRO" onBack={() => undefined} />);

    expect(await screen.findByText(new RegExp(RUN_A))).toBeInTheDocument();
    expect(screen.getByText(new RegExp(RUN_B))).toBeInTheDocument();
    view.unmount();

    stubBackend({ runs: twins });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    await screen.findAllByRole('button', { name: /SAME/ });
    expect(screen.queryByText(new RegExp(RUN_A))).not.toBeInTheDocument();
  });

  it('shows an empty history honestly', async () => {
    stubBackend({ runs: [] });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);

    expect(await screen.findByText('Henüz gölge gözlemi yok.')).toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------
// Decisions, evidence and outcomes
// ----------------------------------------------------------------------

describe('a run, its decisions and what came after', () => {
  function backendWithDecisions(): Backend {
    return {
      runs: [runDto(RUN_A)],
      journals: {
        [RUN_A]: page(RUN_A, [
          operational(1, 'RUN_OPENED'),
          decision(2, 'NO_SIGNAL'),
          decision(3, 'ENTRY_INTENT', { development: development() }),
          decision(4, 'WAIT'),
          operational(5, 'RUN_ENDED'),
        ]),
      },
    };
  }

  it('counts answers, never trades, and shows no win rate or profit', async () => {
    stubBackend(backendWithDecisions());
    const { container } = render(<ShadowScreen mode="PRO" onBack={() => undefined} />);
    await openRun('ALPHA_FUT');

    const table = await screen.findByRole('table', { name: 'Yüklenen kararların dağılımı' });
    expect(within(table).getByText('Giriş niyeti (işlem değil)')).toBeInTheDocument();
    const text = container.textContent ?? '';
    expect(text).toMatch(/kazanma oranı ya da kâr gösterilmez/);
    expect(text).not.toMatch(/PnL|P&L|kazanma oranı:|win rate|%\s*kazan|toplam kâr/i);
  });

  it('describes a touched target as a touch, not a gain', async () => {
    stubBackend(backendWithDecisions());
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');
    await user.click(screen.getByRole('tab', { name: 'Kararlar' }));

    expect(
      await screen.findByText(/Fiyat hedef seviyesine dokundu \(dolmuş emir değil, kâr değil\)/),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/Doğrulanmış sözleşme bilgisi yok — finansal onay verilemez/).length,
    ).toBeGreaterThan(0);
  });

  it('opens the evidence of a chosen decision', async () => {
    stubBackend(backendWithDecisions());
    render(<ShadowScreen mode="PRO" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');
    await user.click(screen.getByRole('tab', { name: 'Kararlar' }));
    const buttons = await screen.findAllByRole('button', { name: 'Kanıtı göster' });
    const intent = buttons[1];
    if (intent === undefined) throw new Error('expected a second decision');
    await user.click(intent);

    const panel = screen.getByRole('tabpanel');
    expect(within(panel).getByText('Onaylanmadı')).toBeInTheDocument();
    expect(within(panel).getByText('100.5 / 99.5')).toBeInTheDocument();
    expect(within(panel).getByText(/EMA hızlı 100.4/)).toBeInTheDocument();
    expect(within(panel).getByText('e'.repeat(64))).toBeInTheDocument();
  });

  it('says a pending outcome is pending while the run observes', async () => {
    stubBackend({
      runs: [
        runDto(RUN_A, {
          status: 'OBSERVING',
          completeness: 'OBSERVING',
          end_reason: null,
          ended_at: null,
        }),
      ],
      journals: { [RUN_A]: page(RUN_A, [decision(2, 'ENTRY_INTENT')]) },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');
    expect(screen.getByText(/toplam sayılar gözlem bittiğinde kesinleşir/)).toBeInTheDocument();
    await user.click(screen.getByRole('tab', { name: 'Kararlar' }));

    expect(await screen.findByText(/Gözlem penceresi hâlâ açık/)).toBeInTheDocument();
  });

  it('lists observed, unavailable, invalidated and ambiguous developments as words', async () => {
    stubBackend({
      runs: [runDto(RUN_A)],
      journals: { [RUN_A]: page(RUN_A, [decision(2, 'ENTRY_INTENT')]) },
      outcomes: {
        [RUN_A]: [
          outcomeDto(1, {}),
          outcomeDto(2, {
            state: 'UNAVAILABLE',
            event: 'NONE_REACHED',
            unresolved_reason:
              'the confirmed series skips an interval inside the observation window',
          }),
          outcomeDto(3, {
            state: 'INVALIDATED',
            event: 'NOT_OBSERVED',
            observed_from: null,
            best_price: null,
            worst_price: null,
            unresolved_reason: 'a correction contests the evidence this decision read',
          }),
          outcomeDto(4, { event: 'BOTH_LEVELS_TOUCHED_SAME_BAR', ambiguous: true }),
        ],
      },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');
    await user.click(screen.getByRole('tab', { name: 'Sonraki gelişme' }));

    expect(await screen.findByText(/skips an interval/)).toBeInTheDocument();
    expect(screen.getAllByText('Gözlendi').length).toBe(2);
    expect(screen.getByText('Belirlenemedi')).toBeInTheDocument();
    expect(screen.getByText(/Geçersiz — kararın dayandığı kanıt düzeltildi/)).toBeInTheDocument();
    expect(screen.getByText(/SIRA BELİRSİZ/)).toBeInTheDocument();
    expect(screen.getByText(/hangisinin önce olduğu bilinemez/)).toBeInTheDocument();
    expect(screen.getByText(/dolmuş emir, kâr ya da zarar değildir/)).toBeInTheDocument();
  });

  it('says why there is no outcome at all rather than showing zeros', async () => {
    stubBackend({
      runs: [runDto(RUN_A)],
      journals: { [RUN_A]: page(RUN_A, [decision(2, 'NO_SIGNAL'), decision(3, 'WAIT')]) },
      outcomes: { [RUN_A]: [] },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');
    await user.click(screen.getByRole('tab', { name: 'Sonraki gelişme' }));

    expect(await screen.findByText(/Yalnızca giriş niyetleri izlenir/)).toBeInTheDocument();
  });

  it('marks decisions whose evidence a correction contested, without changing them', async () => {
    stubBackend({
      runs: [runDto(RUN_A)],
      journals: {
        [RUN_A]: page(RUN_A, [
          decision(1, 'NO_SIGNAL'),
          decision(2, 'ENTRY_INTENT'),
          operational(3, 'CONFLICTING_CORRECTION', '2026-03-02T09:10:00+00:00'),
          operational(4, 'DECISION_SUPERSEDED', '2026-03-02T09:10:00+00:00'),
        ]),
      },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');
    await user.click(screen.getByRole('tab', { name: 'Kararlar' }));

    expect(await screen.findAllByText(/KANITI DÜZELTİLDİ/)).toHaveLength(1);
    expect(screen.getByText('Giriş niyeti (işlem değil)')).toBeInTheDocument(); // still as published
    expect(screen.getByText(/kararlar değiştirilmedi/)).toBeInTheDocument();
  });

  it('says so when a run recorded no decision at all', async () => {
    stubBackend({
      runs: [runDto(RUN_A, { decisions: 0, observations: 0 })],
      journals: {
        [RUN_A]: page(RUN_A, [operational(1, 'RUN_OPENED'), operational(2, 'RUN_ENDED')]),
      },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    await openRun('ALPHA_FUT');

    expect(await screen.findByText(/uydurulmuş kararlarla doldurulmaz/)).toBeInTheDocument();
  });

  it('labels an interrupted and a cancelled run for what they are', async () => {
    stubBackend({
      runs: [
        runDto(RUN_A, { completeness: 'INTERRUPTED', end_reason: 'INTERRUPTED' }),
        runDto(RUN_B, { completeness: 'PARTIAL', end_reason: 'CANCELLED' }),
      ],
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);

    expect(await screen.findByText(/Kesildi — gözlem yarıda kaldı/)).toBeInTheDocument();
    expect(screen.getByText(/Kısmi — iptal edildi/)).toBeInTheDocument();
    expect(screen.queryByText(/Tamamlandı/)).not.toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------
// Beginner and Pro tell the same financial truth
// ----------------------------------------------------------------------

describe('Beginner and Pro parity', () => {
  it('states the same financial state in both modes; Pro adds identifiers only', async () => {
    const backend = (): Backend => ({
      runs: [runDto(RUN_A)],
      journals: {
        [RUN_A]: page(RUN_A, [decision(2, 'ENTRY_INTENT', { development: development() })]),
      },
    });
    const financialOf = async (mode: 'BEGINNER' | 'PRO') => {
      stubBackend(backend());
      const view = render(<ShadowScreen mode={mode} onBack={() => undefined} />);
      const user = await openRun('ALPHA_FUT');
      await user.click(screen.getByRole('tab', { name: 'Kararlar' }));
      await user.click(await screen.findByRole('button', { name: 'Kanıtı göster' }));
      const panel = screen.getByRole('tabpanel');
      const facts = {
        financial: within(panel).getByText(/finansal onay verilemez/).textContent,
        approved: within(panel).getByText('Onaylanmadı').textContent,
        development: within(panel).getByText(/Fiyat hedef seviyesine dokundu/).textContent,
        pro: within(panel).queryByText('e'.repeat(64)) !== null,
      };
      view.unmount();
      vi.unstubAllGlobals();
      return facts;
    };

    const beginner = await financialOf('BEGINNER');
    const pro = await financialOf('PRO');

    expect(beginner.financial).toBe(pro.financial);
    expect(beginner.approved).toBe(pro.approved);
    expect(beginner.development).toBe(pro.development);
    expect(beginner.pro).toBe(false);
    expect(pro.pro).toBe(true);
  });
});

// ----------------------------------------------------------------------
// A late answer never lands on the wrong run
// ----------------------------------------------------------------------

describe('stale response protection', () => {
  it('keeps run B on screen when run A answers late', async () => {
    let releaseA: (() => void) | null = null;
    stubBackend({
      runs: [runDto(RUN_A), runDto(RUN_B)],
      journals: {
        [RUN_A]: page(RUN_A, [decision(2, 'WAIT', { reason: 'A kararı' })]),
        [RUN_B]: page(RUN_B, [decision(2, 'NO_SIGNAL', { reason: 'B kararı' })]),
      },
      extra: (url) => {
        if (url === `/api/shadow/runs/${RUN_A}`) {
          return new Promise<Response>((resolve) => {
            releaseA = () => resolve(json(runDto(RUN_A)));
          });
        }
        return undefined;
      },
    });
    render(<ShadowScreen mode="PRO" onBack={() => undefined} />);
    const user = userEvent.setup();
    await user.click(await screen.findByRole('button', { name: /ALPHA_FUT/ }));
    await user.click(screen.getByRole('button', { name: /BETA_FUT/ }));
    await screen.findByRole('heading', { name: /BETA_FUT/ });

    if (releaseA === null) throw new Error('run A was never requested');
    (releaseA as () => void)();
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(screen.getByRole('heading', { name: /BETA_FUT/ })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /ALPHA_FUT/ })).not.toBeInTheDocument();
  });

  it('refuses a journal page for another run or an older ticket', () => {
    const held = emptyJournal(RUN_B, 7);

    expect(withPage(held, page(RUN_A, [decision(1, 'WAIT')]), RUN_A, 7)).toBe(held);
    expect(withPage(held, page(RUN_B, [decision(1, 'WAIT')]), RUN_B, 6)).toBe(held);
    expect(withPage(held, page(RUN_B, [decision(1, 'WAIT')]), RUN_B, 7).entries).toHaveLength(1);
  });

  it('does not duplicate an entry when pages overlap', () => {
    const first = withPage(
      emptyJournal(RUN_A, 1),
      page(RUN_A, [decision(1, 'WAIT'), decision(2, 'WAIT')], 3),
      RUN_A,
      1,
    );
    const second = withPage(
      first,
      page(RUN_A, [decision(2, 'WAIT'), decision(3, 'WAIT')], 3),
      RUN_A,
      1,
    );

    expect(second.entries.map((entry) => entry.sequence)).toEqual([1, 2, 3]);
  });

  it('bounds what it holds and says it stopped, never that it is complete', () => {
    const many = Array.from({ length: MAX_HELD_ENTRIES + 20 }, (_, index) =>
      decision(index + 1, 'WAIT'),
    );
    const held = withPage(emptyJournal(RUN_A, 1), page(RUN_A, many, 9000), RUN_A, 1);

    expect(held.entries).toHaveLength(MAX_HELD_ENTRIES);
    expect(held.capped).toBe(true);
    expect(held.nextAfter).toBeNull();
    expect(heldSummary(held)).toMatch(/9000 kaydın ilk 500 tanesi/);
    expect(heldSummary(held)).toMatch(/sunucuda eksiksiz/);
  });

  it('keeps a transport notification out of the journal', () => {
    // Nothing but a journal page can add an entry: there is no path from a
    // live SSE event into this state, and this module imports none.
    const source = readFileSync(join(__dirname, '../screens/Shadow.tsx'), 'utf8');
    expect(source).not.toContain('openLiveEvents');
    expect(source).not.toContain('EventSource');
  });
});

// ----------------------------------------------------------------------
// Creating a run
// ----------------------------------------------------------------------

describe('creating a run', () => {
  it('sends intent only, with an attempt key reused across a retry', async () => {
    let attempts = 0;
    const calls = stubBackend({
      runs: [],
      extra: (url, method) => {
        if (url === `/api/live/sessions/${SESSION}`) {
          return json(sessionDto({ id: SESSION })); // the real live schema parses it
        }
        if (url === '/api/shadow/runs' && method === 'POST') {
          attempts += 1;
          return json(
            { detail: { code: 'SHADOW_STORE_UNAVAILABLE', detail: 'journal unreachable' } },
            503,
          );
        }
        return undefined;
      },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = userEvent.setup();

    await user.selectOptions(await screen.findByLabelText('Canlı oturum'), SESSION);
    await user.selectOptions(
      screen.getByLabelText('Kayıtlı strateji ve sürüm'),
      'ema-crossover-atr@1.0.0',
    );
    await waitFor(() => expect(screen.getByLabelText('Karar zaman dilimi')).not.toBeDisabled());
    await user.selectOptions(screen.getByLabelText('Karar zaman dilimi'), '5M');
    await user.click(screen.getByRole('button', { name: 'Gölge gözlemini başlat' }));
    await screen.findByRole('alert');
    await user.click(screen.getByRole('button', { name: 'Gölge gözlemini başlat' }));
    await waitFor(() => expect(attempts).toBe(2));

    const posts = calls.filter((call) => call.method === 'POST' && call.url === '/api/shadow/runs');
    const [first, second] = posts.map((call) => call.body as Record<string, unknown>);
    if (first === undefined || second === undefined) throw new Error('expected two attempts');
    expect(first.attempt_key).toBe(second.attempt_key); // a retry is the same attempt
    expect(Object.keys(first).sort()).toEqual(
      [
        'analysis_evidence',
        'attempt_key',
        'driver',
        'session_id',
        'strategy_id',
        'strategy_version',
        'timeframes',
      ].sort(),
    );
    expect(screen.getByRole('alert')).toHaveTextContent('journal unreachable');
  });

  it('labels every control', async () => {
    stubBackend({});
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);

    expect(await screen.findByLabelText('Canlı oturum')).toBeInTheDocument();
    expect(screen.getByLabelText('Kayıtlı strateji ve sürüm')).toBeInTheDocument();
    expect(screen.getByLabelText('Karar zaman dilimi')).toBeInTheDocument();
    expect(screen.getByLabelText(/mevcut analizi/)).toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------
// Accessibility of the workspace
// ----------------------------------------------------------------------

describe('keyboard and announcements', () => {
  it('moves between tabs with the arrow keys and Home/End', async () => {
    stubBackend({
      runs: [runDto(RUN_A)],
      journals: { [RUN_A]: page(RUN_A, [decision(1, 'WAIT')]) },
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');

    screen.getByRole('tab', { name: 'Genel bakış' }).focus();
    await user.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Kararlar' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'Kararlar' })).toHaveFocus();
    await user.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'Geçmiş gözlemler' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    await user.keyboard('{Home}');
    expect(screen.getByRole('tab', { name: 'Genel bakış' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('announces status politely and errors as alerts', async () => {
    stubBackend({ runs: [runDto(RUN_A)], journals: { [RUN_A]: page(RUN_A, []) } });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    await openRun('ALPHA_FUT');

    const status = screen.getByRole('status');
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveTextContent('ALPHA_FUT gözlemi açıldı.');
  });

  it('can stop an observing run and says the record stays', async () => {
    const observing = runDto(RUN_A, {
      status: 'OBSERVING',
      completeness: 'OBSERVING',
      end_reason: null,
      ended_at: null,
    });
    const calls = stubBackend({
      runs: [observing],
      journals: { [RUN_A]: page(RUN_A, [operational(1, 'RUN_OPENED')]) },
      extra: (url, method) =>
        url === `/api/shadow/runs/${RUN_A}/cancel` && method === 'POST'
          ? json(runDto(RUN_A, { completeness: 'PARTIAL', end_reason: 'CANCELLED' }))
          : undefined,
    });
    render(<ShadowScreen mode="BEGINNER" onBack={() => undefined} />);
    const user = await openRun('ALPHA_FUT');

    await user.click(screen.getByRole('button', { name: 'GÖZLEMİ DURDUR' }));

    expect(await screen.findByText(/Kaydedilen her şey yerinde duruyor/)).toBeInTheDocument();
    expect(screen.getByText(/Kısmi — iptal edildi/)).toBeInTheDocument();
    expect(calls.some((call) => call.url.endsWith('/cancel'))).toBe(true);
    expect(screen.getByRole('button', { name: 'GÖZLEMİ DURDUR' })).toBeDisabled();
  });
});

// ----------------------------------------------------------------------
// The browser computes nothing financial
// ----------------------------------------------------------------------

describe('no financial arithmetic in the browser', () => {
  const files = ['../screens/Shadow.tsx', '../domain/shadow.ts', '../api/shadow.ts'];

  it.each(files)('%s parses no price into a number and sums nothing', (file) => {
    const source = readFileSync(join(__dirname, file), 'utf8');
    for (const banned of ['parseFloat(', 'Number(', '.reduce((', 'toFixed(']) {
      expect(source, file).not.toContain(banned);
    }
  });

  it.each(files)('%s has no profit, fill or win vocabulary', (file) => {
    const source = readFileSync(join(__dirname, file), 'utf8');
    for (const banned of [
      'pnl',
      'profit_',
      'win_rate',
      'winRate',
      'filled_',
      'fillPrice',
      'realized',
    ]) {
      expect(source, file).not.toContain(banned);
    }
  });

  it('describes developments only from the vocabulary it was given', () => {
    expect(describeDevelopment(null)).toBe('Sonraki gelişme yayımlanmadı.');
    expect(describeDevelopment(development({ state: 'PENDING', event: 'NONE_REACHED' }))).toMatch(
      /Bekleniyor/,
    );
    expect(
      describeDevelopment(development({ state: 'UNAVAILABLE', unresolved_reason: 'a gap' })),
    ).toBe('Belirlenemedi: a gap');
  });

  it('names superseded decisions by their real dependency, not by proximity', () => {
    const entries = [
      decision(1, 'WAIT'),
      decision(2, 'WAIT'),
      decision(3, 'WAIT'),
      operational(4, 'DECISION_SUPERSEDED', '2026-03-02T09:10:00+00:00'),
    ];
    const contested = supersededBoundaries(entries);

    expect(entries.map((entry) => isSuperseded(entry, contested))).toEqual([
      false,
      true,
      true,
      false,
    ]);
  });
});
