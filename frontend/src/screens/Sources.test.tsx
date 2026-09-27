import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  calendarSchema,
  capabilitiesSchema,
  metadataSchema,
  reviewPageSchema,
} from '../api/sources';
import { SourcesScreen } from './Sources';

/**
 * The source-verification workspace against a stubbed transport (Part 2B).
 *
 * Every payload below is parsed by the real schema first, so a stub that drifts
 * from the backend fails here rather than passing while the product breaks.
 * Most tests are about what the screen must *not* do: offer an approval,
 * call a missing source available, or show a value where none is known.
 */

const CATEGORIES = [
  'MARKET_DATA',
  'CONTRACT_METADATA',
  'OPEN_INTEREST',
  'NEWS',
  'MARKET_BREADTH',
  'SESSION_CALENDAR',
] as const;

const CAPABILITIES = capabilitiesSchema.parse({
  deployment: {
    market_data_provider: 'none',
    real_provider_connected: false,
    simulated_market_data: false,
    calendar_source_composed: false,
    verification_writes: 'LOCAL_OPERATOR_COMMAND_ONLY',
    reviewer_identity: 'OPERATOR_ASSERTION_NOT_AUTHENTICATED',
    financial_use_enabled: false,
  },
  categories: CATEGORIES.map((category) => ({
    category,
    status: 'NOT_CONFIGURED',
    reason: `no provider is configured for ${category}`,
    configured: false,
    licensed: false,
    connected: false,
    available: false,
    fresh: false,
    verified: false,
    adapter_in_build: false,
  })),
  journal: { submissions: 0, approved: 0, rejected: 0, refused: 0, records: 0 },
  server_time: '2026-09-27T10:00:00+00:00',
});

const MARGINS = [
  { name: 'initial_margin', state: 'NOT_REVIEWABLE', value: null, source: null, verified_at: null },
  {
    name: 'maintenance_margin',
    state: 'NOT_REVIEWABLE',
    value: null,
    source: null,
    verified_at: null,
  },
];

const FIELDS_UNKNOWN = [
  { name: 'multiplier', state: 'UNAVAILABLE', value: null, source: null, verified_at: null },
  { name: 'tick_size', state: 'UNAVAILABLE', value: null, source: null, verified_at: null },
  { name: 'tick_value', state: 'NOT_REVIEWABLE', value: null, source: null, verified_at: null },
  { name: 'expiry_date', state: 'UNAVAILABLE', value: null, source: null, verified_at: null },
  ...MARGINS,
];

const CHECKS = {
  source_claims_value: true,
  operator_examined_evidence: true,
  source_authority_assessed: true,
  applicable_to_contract: true,
  applicable_at_market_time: true,
  known_by_requested_time: true,
  current: true,
  financial_use_enabled: false,
};

const RECORD = {
  record_id: 'R1',
  authority: 'EXCHANGE_OFFICIAL',
  reference: 'TEST_FIXTURE_DOC#spec',
  effective_from: '2026-01-01T00:00:00+00:00',
  effective_until: null,
  verified_at: '2026-09-01T13:00:00+00:00',
  known_at: '2026-09-01T14:00:00+00:00',
  corrects: null,
  reviewed_by: 'test-reviewer',
  multiplier: '10',
  tick_size: '0.25',
  expiry_date: null,
};

const USABLE = metadataSchema.parse({
  symbol: 'TEST_FIXTURE_FUT',
  applies_at: '2026-09-27T10:00:00+00:00',
  known_by: '2026-09-27T10:00:00+00:00',
  retrospective: false,
  verdict: 'USABLE',
  reason: 'governed by record R1 (EXCHANGE_OFFICIAL)',
  governing_record: 'R1',
  fields: [
    {
      name: 'multiplier',
      state: 'VERIFIED',
      value: '10',
      source: 'TEST_FIXTURE_DOC#spec',
      verified_at: '2026-09-01T13:00:00+00:00',
    },
    {
      name: 'tick_size',
      state: 'VERIFIED',
      value: '0.25',
      source: 'TEST_FIXTURE_DOC#spec',
      verified_at: '2026-09-01T13:00:00+00:00',
    },
    FIELDS_UNKNOWN[2],
    { name: 'expiry_date', state: 'MISSING', value: null, source: null, verified_at: null },
    ...MARGINS,
  ],
  checks: CHECKS,
  conflicts: [],
  superseded: [],
  records: [RECORD],
  financial_use_enabled: false,
  server_time: '2026-09-27T10:00:00+00:00',
});

const CONFLICTING = metadataSchema.parse({
  ...USABLE,
  verdict: 'CONFLICTING',
  reason: 'records R1 and R2 disagree on multiplier for the same period',
  governing_record: null,
  fields: FIELDS_UNKNOWN,
  checks: { ...CHECKS, current: false },
  conflicts: [
    {
      fact: 'multiplier',
      chosen_record: 'R1',
      chosen_value: '10',
      other_record: 'R2',
      other_value: '100',
      resolution: 'UNRESOLVED: refused, no value chosen',
    },
  ],
  records: [RECORD, { ...RECORD, record_id: 'R2', multiplier: '100' }],
});

const CALENDAR = calendarSchema.parse({
  symbol: 'TEST_FIXTURE_FUT',
  at: '2026-09-27T10:00:00+00:00',
  status: 'UNAVAILABLE',
  reason: 'no verified trading-session calendar is configured',
  source: null,
  source_verified_at: null,
  calendar_source_composed: false,
  server_time: '2026-09-27T10:00:00+00:00',
});

const REVIEWS = reviewPageSchema.parse({
  items: [
    {
      sequence: 1,
      submission_id: 'S1',
      symbol: 'TEST_FIXTURE_FUT',
      fact: 'MULTIPLIER',
      claimed_value: '10',
      reference: 'https://example.test/looks-official.pdf',
      authority: 'SECONDARY',
      effective_from: '2026-01-01T00:00:00+00:00',
      effective_until: null,
      submitted_by: 'op',
      submitted_at: '2026-09-01T12:00:00+00:00',
      origin: 'MANUAL_ENTRY',
      corrects: null,
      recorded_at: '2026-09-01T12:00:01+00:00',
      decision: {
        reviewer: 'Borsa Istanbul Official',
        reviewer_identity: 'OPERATOR_ASSERTION_NOT_AUTHENTICATED',
        decided_at: '2026-09-01T13:00:00+00:00',
        outcome: 'APPROVED',
        document_checked: true,
        note: '',
        result: 'REFUSED',
        refusal_code: 'NOT_AN_AUTHORITATIVE_SOURCE',
        recorded_at: '2026-09-01T13:00:01+00:00',
      },
    },
  ],
  total: 1,
  next_after: null,
  server_time: '2026-09-27T10:00:00+00:00',
});

interface Routes {
  capabilities?: unknown;
  metadata?: unknown;
  calendar?: unknown;
  reviews?: unknown;
}

const requests: { path: string; method: string }[] = [];

function stubApi(routes: Routes) {
  requests.length = 0;
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    requests.push({ path, method: init?.method ?? 'GET' });
    const payload = path.includes('/capabilities')
      ? routes.capabilities
      : path.includes('/metadata/')
        ? routes.metadata
        : path.includes('/calendar/')
          ? routes.calendar
          : path.includes('/reviews')
            ? routes.reviews
            : undefined;
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
  capabilities: CAPABILITIES,
  metadata: USABLE,
  calendar: CALENDAR,
  reviews: REVIEWS,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

async function show(mode: 'BEGINNER' | 'PRO' = 'BEGINNER', routes: Routes = HAPPY) {
  stubApi(routes);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <SourcesScreen mode={mode} onBack={() => undefined} />
    </QueryClientProvider>,
  );
  await screen.findByRole('heading', { name: 'Bu kurulum' });
  return view;
}

async function openTab(name: string) {
  await userEvent.click(screen.getByRole('tab', { name }));
}

async function query(symbol: string) {
  await userEvent.type(screen.getByLabelText('Sözleşme sembolü'), symbol);
  await userEvent.click(screen.getByRole('button', { name: 'Sorgula' }));
}

describe('the workspace says what is not here', () => {
  it('states that no real provider is connected and financial use is off', async () => {
    await show();

    expect(screen.getByText(/gerçek bir borsa veri sağlayıcısı bağlı değildir/)).toBeVisible();
    expect(screen.getByText('Bağlı değil')).toBeVisible();
    expect(screen.getByText('KAPALI')).toBeVisible();
  });

  it('lists every external category as not configured, never available', async () => {
    await show();

    const list = screen.getByRole('heading', { name: /kullanılamayan kaynaklar/ })
      .parentElement as HTMLElement;
    expect(within(list).getAllByText('Yapılandırılmadı')).toHaveLength(6);
    expect(document.body.textContent).not.toMatch(/Kullanılabilir/);
  });

  it('explains price data versus contract facts to a beginner', async () => {
    await show('BEGINNER');

    expect(screen.getByText(/Fiyat verisi ile sözleşme bilgisi farklıdır/)).toBeVisible();
    expect(screen.getByText(/Seans takvimi yoksa oturum iddiası yoktur/)).toBeVisible();
    expect(screen.getByText(/Canlı borsa fiyatı gösterilmez,/)).toBeVisible();
  });

  it('refuses a response that claims a real provider is connected', async () => {
    stubApi({
      ...HAPPY,
      capabilities: {
        ...CAPABILITIES,
        deployment: { ...CAPABILITIES.deployment, real_provider_connected: true },
      },
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <SourcesScreen mode="BEGINNER" onBack={() => undefined} />
      </QueryClientProvider>,
    );

    expect(await screen.findByRole('alert')).toHaveTextContent(/expected schema/);
    expect(screen.queryByText('Bağlı değil')).not.toBeInTheDocument();
  });
});

describe('nothing on this screen can approve or write', () => {
  it('offers no verify, approve or publish control in any tab or mode', async () => {
    for (const mode of ['BEGINNER', 'PRO'] as const) {
      const view = await show(mode);
      for (const tab of ['Genel bakış', 'Kaynaklar', 'Sözleşme bilgileri', 'Seans takvimi']) {
        await openTab(tab);
        for (const button of screen.getAllByRole('button')) {
          expect(button.textContent).not.toMatch(/onayla|doğrula|verify|approve|yayımla|publish/i);
        }
      }
      view.unmount();
    }
  });

  it('makes only GET requests, even after querying', async () => {
    await show('PRO');
    await openTab('Sözleşme bilgileri');
    await query('TEST_FIXTURE_FUT');
    await screen.findByText('Doğrulanmış kayıt geçerli');

    expect(requests.length).toBeGreaterThan(1);
    expect(new Set(requests.map((r) => r.method))).toEqual(new Set(['GET']));
  });

  it('explains the operator process where a person would look for a button', async () => {
    await show('BEGINNER');
    await openTab('İnceleme geçmişi');

    expect(await screen.findByText(/tarayıcıdan onay verilemez/)).toBeVisible();
    expect(
      screen.getByText(/CSV ile yüklenen bilgi hiçbir zaman doğrulanmış sayılmaz/),
    ).toBeVisible();
  });
});

describe('contract facts', () => {
  it('shows the same verdict in both modes and the evidence only in Pro', async () => {
    const beginner = await show('BEGINNER');
    await openTab('Sözleşme bilgileri');
    await query('TEST_FIXTURE_FUT');
    expect(await screen.findByText('Doğrulanmış kayıt geçerli')).toBeVisible();
    expect(screen.queryByText(/TEST_FIXTURE_DOC#spec/)).not.toBeInTheDocument();
    beginner.unmount();

    await show('PRO');
    await openTab('Sözleşme bilgileri');
    await query('TEST_FIXTURE_FUT');
    expect(await screen.findByText('Doğrulanmış kayıt geçerli')).toBeVisible();
    expect(screen.getAllByText(/TEST_FIXTURE_DOC#spec/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Finansal hesaplarda kullanılabilir/).textContent).toMatch(/Hayır/);
  });

  it('never shows a value for an unknown field and never says financially approved', async () => {
    await show('PRO', { ...HAPPY, metadata: CONFLICTING });
    await openTab('Sözleşme bilgileri');
    await query('TEST_FIXTURE_FUT');

    expect(await screen.findByText(/Kaynaklar çelişiyor/)).toBeVisible();
    const table = screen.getAllByRole('table')[0] as HTMLElement;
    expect(within(table).getAllByText('Bilinmiyor').length).toBe(3);
    expect(within(table).queryByText('100')).not.toBeInTheDocument();
    expect(screen.getByRole('note', { name: 'Kaynak çelişkileri' })).toHaveTextContent('R2 = 100');
    expect(document.body.textContent).not.toMatch(/financially approved|finansal olarak onay/i);
  });

  it('refuses a hostile symbol before any request is made', async () => {
    await show();
    await openTab('Sözleşme bilgileri');
    const before = requests.length;
    await query('../../etc/passwd');

    expect(screen.getByRole('alert')).toHaveTextContent(/yalnızca harf, rakam/);
    expect(requests.length).toBe(before);
  });
});

describe('calendar and review history', () => {
  it('says a session is unknown without a verified calendar', async () => {
    await show();
    await openTab('Seans takvimi');
    await query('TEST_FIXTURE_FUT');

    expect(await screen.findByText(/Bilinmiyor — doğrulanmış takvim yok/)).toBeVisible();
    expect(screen.getByText(/tahmin etmez/)).toBeVisible();
  });

  it('shows a reviewer name as an assertion and the refusal code', async () => {
    await show('PRO');
    await openTab('İnceleme geçmişi');

    expect(await screen.findByText(/inceleyen \(beyan\): Borsa Istanbul Official/)).toBeVisible();
    expect(screen.getByText('NOT_AN_AUTHORITATIVE_SOURCE')).toBeVisible();
    expect(screen.getByText(/İkincil kaynak \(yetkili değil\)/)).toBeVisible();
    expect(screen.getByText(/OPERATOR_ASSERTION_NOT_AUTHENTICATED/)).toBeVisible();
  });
});

describe('keyboard', () => {
  it('moves selection and focus across the tabs with arrows, Home and End', async () => {
    await show();
    const first = screen.getByRole('tab', { name: 'Genel bakış' });
    first.focus();

    await userEvent.keyboard('{ArrowRight}');
    expect(screen.getByRole('tab', { name: 'Kaynaklar' })).toHaveFocus();
    expect(screen.getByRole('tab', { name: 'Kaynaklar' })).toHaveAttribute('aria-selected', 'true');
    await userEvent.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'İnceleme geçmişi' })).toHaveFocus();
    await userEvent.keyboard('{ArrowRight}');
    expect(first).toHaveFocus();
    await userEvent.keyboard('{ArrowLeft}');
    expect(screen.getByRole('tab', { name: 'İnceleme geçmişi' })).toHaveFocus();

    const tabbable = screen.getAllByRole('tab').filter((tab) => tab.tabIndex === 0);
    expect(tabbable).toHaveLength(1);
  });

  it('announces state politely', async () => {
    await show();

    expect(screen.getByRole('status')).toHaveTextContent(/Gerçek veri sağlayıcısı bağlı değil/);
  });
});

describe('ordering', () => {
  it('never lets an older, slower answer replace the newer query on screen', async () => {
    const slowOld = { ...USABLE, symbol: 'OLD_SLOW' };
    const fastNew = { ...CONFLICTING, symbol: 'NEW_FAST' };
    let releaseOld: () => void = () => undefined;
    const oldGate = new Promise<void>((resolve) => {
      releaseOld = resolve;
    });
    vi.stubGlobal('fetch', async (input: RequestInfo | URL) => {
      const path = String(input);
      let payload: unknown = CAPABILITIES;
      if (path.includes('/metadata/OLD_SLOW')) {
        await oldGate;
        payload = slowOld;
      } else if (path.includes('/metadata/NEW_FAST')) {
        payload = fastNew;
      }
      return new Response(JSON.stringify(payload), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <SourcesScreen mode="BEGINNER" onBack={() => undefined} />
      </QueryClientProvider>,
    );
    await screen.findByRole('heading', { name: 'Bu kurulum' });
    await openTab('Sözleşme bilgileri');

    await query('OLD_SLOW');
    const input = screen.getByLabelText('Sözleşme sembolü');
    await userEvent.clear(input);
    await query('NEW_FAST');
    expect(await screen.findByRole('heading', { name: 'NEW_FAST' })).toBeVisible();
    releaseOld();
    await new Promise((resolve) => setTimeout(resolve, 50));

    expect(screen.getByRole('heading', { name: 'NEW_FAST' })).toBeVisible();
    expect(screen.queryByRole('heading', { name: 'OLD_SLOW' })).not.toBeInTheDocument();
    expect(screen.getByText(/Kaynaklar çelişiyor/)).toBeVisible();
  });
});
