import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { liveEnvelopeSchema, liveSessionSchema } from '../api/live';
import {
  acceptSnapshot,
  applyEnvelope,
  initialState,
  olderPageCursor,
  TIMELINE_LIMIT,
  withOlderEntries,
} from '../domain/live';
import { LiveScreen } from '../screens/Live';
import {
  SESSION_A,
  SESSION_B,
  capabilityDto,
  candleDto,
  entryDto,
  envelopeDto,
  liveAnalysisDto,
  pageDto,
  sessionDto,
  sourceDto,
  timeframeDto,
  unavailableSession,
} from '../test/liveDto';

/**
 * The Live Intelligence UI (Phase 13 Part 2A).
 *
 * What matters is not layout. The screen must say, everywhere, that the data
 * is simulated history; it must never let transport freshness read as a
 * current price or "connected" read as "complete"; it must start nothing by
 * itself; and no late message - from another session, an older stream, or
 * after a skipped notification - may overwrite what is shown.
 */

// ----------------------------------------------------------------------
// Test doubles
// ----------------------------------------------------------------------

class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }
  close() {
    this.closed = true;
  }
  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
  }
  fail() {
    this.onerror?.();
  }
}

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

type Handler = (url: string, method: string) => Response | Promise<Response>;

/** Answers a request it recognises; `undefined` falls through to the default backend. */
type Extra = (url: string, method: string) => Response | Promise<Response> | undefined;

function stubFetch(handler: Handler): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      const method = init?.method ?? 'GET';
      const body = typeof init?.body === 'string' ? (JSON.parse(init.body) as unknown) : null;
      calls.push({ url, method, body });
      return handler(url, method);
    }),
  );
  return calls;
}

/** A backend with one source and, optionally, running sessions. */
function backend(
  options: {
    session?: () => ReturnType<typeof sessionDto>;
    sessions?: string[];
    extra?: Extra;
  } = {},
): Handler {
  return (url, method) => {
    const extra = options.extra?.(url, method);
    if (extra) return extra;
    if (url.endsWith('/api/live/capability')) return json(capabilityDto());
    if (url.endsWith('/api/live/sources'))
      return json({ items: [sourceDto()], total: 1, offset: 0, limit: 20 });
    if (url.endsWith('/api/live/sessions') && method === 'GET')
      return json({
        items: (options.sessions ?? []).map((id) => ({
          id,
          instrument_label: 'TEST_FIXTURE_FUT',
          source_id: sourceDto().source_id,
          lifecycle: 'RUNNING',
          connection: 'CONNECTED',
          created_at: '2026-09-21T12:00:00+00:00',
          cursor: 20,
        })),
        capacity: 8,
      });
    if (url.includes('/timeline')) return json(pageDto([entryDto(19), entryDto(20)]));
    if (/\/api\/live\/sessions\/LS-[a-z]+$/.test(url) && method === 'GET')
      return json((options.session ?? (() => sessionDto()))());
    return json({ detail: { code: 'UNEXPECTED', detail: url } }, 500);
  };
}

async function openSession(mode: 'BEGINNER' | 'PRO' = 'BEGINNER', handler?: Handler) {
  const calls = stubFetch(handler ?? backend({ sessions: [SESSION_A] }));
  const user = userEvent.setup();
  const view = render(<LiveScreen mode={mode} onBack={vi.fn()} />);
  await user.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));
  await screen.findByRole('tablist');
  return { calls, user, view };
}

function stream(): FakeEventSource {
  const last = FakeEventSource.instances.at(-1);
  if (last === undefined) throw new Error('no event stream was opened');
  return last;
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// ----------------------------------------------------------------------
// Provenance and currency
// ----------------------------------------------------------------------

describe('simulated provenance is always visible', () => {
  it.each(['BEGINNER', 'PRO'] as const)(
    'shows the banner in %s mode before any session',
    async (mode) => {
      stubFetch(backend());
      render(<LiveScreen mode={mode} onBack={vi.fn()} />);

      const banner = await screen.findByRole('note', { name: 'Veri kaynağı uyarısı' });
      expect(banner).toHaveTextContent('SIMULATED HISTORICAL STREAM');
      expect(banner).toHaveTextContent('Borsaya bağlı değildir');
      expect(banner).toHaveTextContent('güncel VİOP fiyatı değildir');
    },
  );

  it('labels a fresh stream as historical, never as a current price', async () => {
    await openSession();

    expect(screen.getByText('Kaynak: SİMÜLE GEÇMİŞ AKIŞ')).toBeInTheDocument();
    expect(screen.getByText('Piyasa güncelliği: GEÇMİŞ VERİ')).toBeInTheDocument();
    const overview = screen.getByRole('tabpanel');
    expect(within(overview).getAllByText('AKIŞ TAZE').length).toBeGreaterThan(0);
    expect(within(overview).getAllByText(/GEÇMİŞ VERİ — güncel fiyat değil/).length).toBe(3);
    expect(document.body.textContent).not.toMatch(/güncel fiyat(?!ı? değil)/i);
  });

  it('refuses a response that calls itself an exchange feed', () => {
    const forged = {
      ...sessionDto(),
      identity: { ...sessionDto().identity, provenance: 'LIVE_EXCHANGE_FEED' },
    };
    expect(liveSessionSchema.safeParse(forged).success).toBe(false);
    const current = {
      ...sessionDto(),
      identity: { ...sessionDto().identity, market_currency: 'CURRENT' },
    };
    expect(liveSessionSchema.safeParse(current).success).toBe(false);
  });
});

// ----------------------------------------------------------------------
// Nothing starts by itself
// ----------------------------------------------------------------------

describe('opening the screen starts nothing', () => {
  it('only reads: no session, no stream, no analysis', async () => {
    const calls = stubFetch(backend());
    render(<LiveScreen mode="BEGINNER" onBack={vi.fn()} />);
    await screen.findByRole('heading', { name: 'Yeni simüle akış' });

    expect(calls.every((call) => call.method === 'GET')).toBe(true);
    expect(FakeEventSource.instances).toHaveLength(0);
  });

  it('creates a session only from the button, with intent and no facts', async () => {
    const calls = stubFetch(
      backend({
        extra: (url, method) =>
          url.endsWith('/api/live/sessions') && method === 'POST'
            ? json(sessionDto(), 201)
            : undefined,
      }),
    );
    const user = userEvent.setup();
    render(<LiveScreen mode="BEGINNER" onBack={vi.fn()} />);

    await user.selectOptions(
      await screen.findByLabelText('Geçmiş veri kümesi'),
      sourceDto().source_id,
    );
    await user.click(screen.getByLabelText('5M (288 mum)'));
    await user.click(screen.getByRole('button', { name: 'AKIŞI BAŞLAT' }));
    await screen.findByRole('tablist');

    const post = calls.find((call) => call.method === 'POST');
    expect(post?.body).toEqual({
      source_id: sourceDto().source_id,
      timeframes: ['5M'],
      window_candles: 300,
      pace: 'NORMAL',
    });
  });
});

// ----------------------------------------------------------------------
// Connection is not completeness
// ----------------------------------------------------------------------

describe('connected does not mean available', () => {
  it('shows a connected provider next to zero usable timeframes', async () => {
    await openSession(
      'BEGINNER',
      backend({ sessions: [SESSION_A], session: () => unavailableSession() }),
    );

    const strip = screen.getByText('Sağlayıcı bağlantısı').closest('dl');
    expect(strip).not.toBeNull();
    expect(within(strip as HTMLElement).getByText('SAĞLAYICI BAĞLI')).toBeInTheDocument();
    expect(within(strip as HTMLElement).getByText('0 / 3')).toBeInTheDocument();
  });

  it('names every integrity state in words', async () => {
    const { user } = await openSession(
      'BEGINNER',
      backend({
        sessions: [SESSION_A],
        session: () =>
          sessionDto({
            timeframes: [
              timeframeDto('5M', {
                integrity: 'DISCONTINUOUS',
                availability: 'UNAVAILABLE',
                reasons: ['an unexplained jump in time'],
              }),
              timeframeDto('15M', { integrity: 'GAPPED', availability: 'UNAVAILABLE' }),
              timeframeDto('1H', {
                integrity: 'UNVERIFIED',
                freshness: 'STALE',
                availability: 'UNAVAILABLE',
              }),
            ],
          }),
      }),
    );

    await user.click(screen.getByRole('tab', { name: 'ZAMAN DİLİMLERİ' }));
    const table = screen.getByRole('table');
    expect(within(table).getByText('AÇIKLANAMAYAN ZAMAN BOŞLUĞU')).toBeInTheDocument();
    expect(within(table).getByText('EKSİK MUM')).toBeInTheDocument();
    expect(within(table).getByText('SÜREKLİLİK DOĞRULANMADI')).toBeInTheDocument();
    expect(within(table).getByText('CANLI VERİ BAYAT')).toBeInTheDocument();
    expect(within(table).getByText('an unexplained jump in time')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('LIVE DATA STALE');
  });

  it('shows recovery as its own state', async () => {
    await openSession(
      'BEGINNER',
      backend({ sessions: [SESSION_A], session: () => sessionDto({ connection: 'RECOVERING' }) }),
    );
    expect(screen.getByText('YENİDEN BAĞLANDI — SÜREKLİLİK DOĞRULANIYOR')).toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------
// Forming candles
// ----------------------------------------------------------------------

describe('a forming candle is kept apart', () => {
  it('is shown under its own heading, marked as not analysed', async () => {
    await openSession(
      'BEGINNER',
      backend({
        sessions: [SESSION_A],
        session: () =>
          sessionDto({
            timeframes: [
              timeframeDto('5M', { forming: candleDto({ state: 'FORMING', close: '100.95' }) }),
            ],
          }),
      }),
    );

    const heading = screen.getByRole('heading', {
      name: /Oluşan mum \(onaylı değil, analize girmez\)/,
    });
    expect(heading.nextElementSibling).toHaveTextContent('100.95');
    const confirmed = screen.getByRole('heading', { name: 'Son onaylı mum' });
    expect(confirmed.nextElementSibling).not.toHaveTextContent('100.95');
  });

  it('says plainly when the source publishes no forming candles', async () => {
    await openSession();
    const overview = screen.getByRole('tabpanel');
    expect(within(overview).getAllByText(/Bu kaynak oluşan mum yayınlamaz/).length).toBe(3);
  });
});

// ----------------------------------------------------------------------
// Analysis
// ----------------------------------------------------------------------

describe('confirmed analysis', () => {
  it('runs only on request, sends no market data, and says what it describes', async () => {
    let resolve: (value: Response) => void = () => undefined;
    const handler = backend({
      sessions: [SESSION_A],
      extra: (url, method) =>
        url.endsWith('/analysis') && method === 'POST'
          ? new Promise<Response>((done) => {
              resolve = done;
            })
          : undefined,
    });
    const { calls, user } = await openSession('BEGINNER', handler);
    expect(calls.some((call) => call.url.endsWith('/analysis'))).toBe(false);

    await user.click(screen.getByRole('tab', { name: 'ANALİZ' }));
    const button = screen.getByRole('button', { name: 'ONAYLI VERİYİ ANALİZ ET' });
    await user.click(button);
    expect(screen.getByRole('button', { name: 'Analiz ediliyor…' })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Analiz ediliyor…' }));
    expect(calls.filter((call) => call.url.endsWith('/analysis'))).toHaveLength(1);

    await act(async () => resolve(json(liveAnalysisDto())));

    const posted = calls.find((call) => call.url.endsWith('/analysis'));
    expect(posted?.body).toEqual({});
    expect(await screen.findByText(/Analiz edilen piyasa anı \(geçmiş\)/)).toBeInTheDocument();
    expect(screen.getByText(/piyasanın bugünkü durumunu değil/)).toBeInTheDocument();
    expect(screen.getByText(/işlem talimatı değildir/)).toBeInTheDocument();
  });

  it('marks a kept analysis as not current once the stream moved on', async () => {
    const handler = backend({
      sessions: [SESSION_A],
      extra: (url, method) =>
        url.endsWith('/analysis') && method === 'POST' ? json(liveAnalysisDto()) : undefined,
    });
    const { user } = await openSession('BEGINNER', handler);
    await user.click(screen.getByRole('tab', { name: 'ANALİZ' }));
    await user.click(screen.getByRole('button', { name: 'ONAYLI VERİYİ ANALİZ ET' }));
    await screen.findByText(/piyasanın bugünkü durumunu değil/);

    act(() =>
      stream().emit(
        envelopeDto({
          kind: 'TIMELINE',
          event_id: 21,
          cursor: 21,
          entry: entryDto(21),
          session: sessionDto({
            cursor: 21,
            analysis: {
              analyses_run: 1,
              available_timeframes: ['5M', '15M', '1H'],
              last_market_as_of: '2026-03-03T09:00:00+00:00',
              last_requested_at: '2026-09-21T12:00:03+00:00',
              last_current: false,
            },
          }),
        }),
      ),
    );

    expect(await screen.findByText(/GÜNCEL DEĞİL/)).toBeInTheDocument();
  });

  it('waits, with reasons, when nothing is available', async () => {
    const { user } = await openSession(
      'BEGINNER',
      backend({ sessions: [SESSION_A], session: () => unavailableSession() }),
    );
    await user.click(screen.getByRole('tab', { name: 'ANALİZ' }));

    expect(screen.getByRole('button', { name: 'ONAYLI VERİYİ ANALİZ ET' })).toBeDisabled();
    expect(screen.getByText(/analize uygun zaman dilimi yok — BEKLE/)).toBeInTheDocument();
    expect(screen.getAllByText(/no valid observation has been received/).length).toBe(3);
  });
});

// ----------------------------------------------------------------------
// The stream and its guards
// ----------------------------------------------------------------------

describe('the event stream', () => {
  it('opens one stream from the snapshot cursor', async () => {
    await openSession();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(stream().url).toBe(`/api/live/sessions/${SESSION_A}/events?after=20`);
  });

  it('applies the next event and ignores one for another session', async () => {
    const { user } = await openSession();
    await user.click(screen.getByRole('tab', { name: 'ZAMAN ÇİZELGESİ' }));

    act(() =>
      stream().emit(
        envelopeDto({
          session_id: SESSION_B,
          kind: 'TIMELINE',
          event_id: 21,
          cursor: 21,
          entry: entryDto(21, { kind: 'SESSION_ENDED', timeframe: null }),
        }),
      ),
    );
    expect(screen.queryByText('Oturum sona erdi')).not.toBeInTheDocument();

    act(() =>
      stream().emit(
        envelopeDto({
          kind: 'TIMELINE',
          event_id: 21,
          cursor: 21,
          entry: entryDto(21, { kind: 'PROVIDER_SIGNAL', code: 'CONNECTED', timeframe: null }),
        }),
      ),
    );
    expect(await screen.findByText('Sağlayıcı sinyali')).toBeInTheDocument();
  });

  it('a heartbeat is transport only and changes no candle', async () => {
    await openSession();
    const before = screen.getByRole('tabpanel').textContent;

    act(() => stream().emit(envelopeDto({ kind: 'HEARTBEAT', cursor: 20 })));

    expect(screen.getByRole('tabpanel').textContent).toBe(before);
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('a skipped notification triggers a resync from the snapshot', async () => {
    const { calls } = await openSession();
    const reads = calls.filter((call) => call.url.endsWith(`/sessions/${SESSION_A}`)).length;

    act(() =>
      stream().emit(
        envelopeDto({ kind: 'TIMELINE', event_id: 25, cursor: 25, entry: entryDto(25) }),
      ),
    );

    await waitFor(() =>
      expect(calls.filter((call) => call.url.endsWith(`/sessions/${SESSION_A}`)).length).toBe(
        reads + 1,
      ),
    );
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
    expect(FakeEventSource.instances[0]?.closed).toBe(true);
  });

  it('a lost connection is reported as the browser, not the provider, and resyncs', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    await openSession();

    act(() => stream().fail());

    expect(screen.getByRole('status')).toHaveTextContent('Sağlayıcının durumu bu değildir');
    expect(screen.getByText('Tarayıcı bağlantısı koptu (sağlayıcı değil)')).toBeInTheDocument();
    expect(screen.getByText('SAĞLAYICI BAĞLI')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(2));
  });

  it('a session gone after a restart is said to be gone, not resurrected', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let gone = false;
    const handler = backend({
      sessions: [SESSION_A],
      extra: (url) =>
        gone && url.endsWith(`/sessions/${SESSION_A}`)
          ? json({ detail: { code: 'LIVE_SESSION_NOT_FOUND', detail: 'gone' } }, 404)
          : undefined,
    });
    await openSession('BEGINNER', handler);

    gone = true;
    act(() => stream().fail());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1100);
    });

    expect(await screen.findByRole('alert')).toHaveTextContent('eski akış geri getirilmez');
    expect(FakeEventSource.instances).toHaveLength(1);
  });

  it('the end of the session closes the stream', async () => {
    await openSession();

    act(() =>
      stream().emit(
        envelopeDto({
          kind: 'END',
          event_id: 21,
          cursor: 21,
          session: sessionDto({
            lifecycle: 'ENDED',
            connection: 'TERMINATED',
            end_origin: 'STREAM',
            cursor: 21,
          }),
        }),
      ),
    );

    expect(stream().closed).toBe(true);
    expect(screen.getByText('AKIŞ SONA ERDİ')).toBeInTheDocument();
    act(() => stream().fail()); // the server closing afterwards is not a loss
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});

describe('switching sessions', () => {
  it('a slow answer for the previous session cannot paint the new one', async () => {
    let releaseA: (value: Response) => void = () => undefined;
    const calls = stubFetch((url, method) => {
      if (url.endsWith(`/sessions/${SESSION_A}`))
        return new Promise<Response>((done) => {
          releaseA = done;
        });
      if (url.endsWith(`/sessions/${SESSION_B}`))
        return json(
          sessionDto({
            id: SESSION_B,
            identity: {
              ...sessionDto().identity,
              stream_id: SESSION_B,
              instrument_label: 'SECOND_LABEL',
            },
          }),
        );
      return backend({ sessions: [SESSION_A, SESSION_B] })(url, method);
    });
    const user = userEvent.setup();
    render(<LiveScreen mode="BEGINNER" onBack={vi.fn()} />);
    const buttons = await screen.findAllByRole('button', { name: /TEST_FIXTURE_FUT/ });

    await user.click(buttons[0] as HTMLElement); // A - slow
    await user.click(buttons[1] as HTMLElement); // B - fast
    await screen.findByRole('heading', { name: 'SECOND_LABEL' });
    await act(async () => releaseA(json(sessionDto())));

    expect(screen.getByRole('heading', { name: 'SECOND_LABEL' })).toBeInTheDocument();
    expect(FakeEventSource.instances.map((item) => item.url)).toEqual([
      `/api/live/sessions/${SESSION_B}/events?after=20`,
    ]);
    expect(calls.some((call) => call.url.endsWith(`/sessions/${SESSION_A}`))).toBe(true);
  });

  it('cancels through the API and keeps the ended session visible', async () => {
    const handler = backend({
      sessions: [SESSION_A],
      extra: (url, method) =>
        url.endsWith('/cancel') && method === 'POST'
          ? json(
              sessionDto({
                lifecycle: 'ENDED',
                connection: 'TERMINATED',
                end_origin: 'USER_CANCELLED',
                cursor: 22,
              }),
            )
          : undefined,
    });
    const { calls, user } = await openSession('BEGINNER', handler);

    await user.click(screen.getByRole('button', { name: 'AKIŞI DURDUR' }));

    expect(calls.some((call) => call.url.endsWith(`/sessions/${SESSION_A}/cancel`))).toBe(true);
    expect(await screen.findByText(/Kullanıcı iptal etti/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'AKIŞI DURDUR' })).toBeDisabled();
  });
});

// ----------------------------------------------------------------------
// The pure guards
// ----------------------------------------------------------------------

describe('client state guards', () => {
  const base = () => initialState(sessionDto(), pageDto([entryDto(19), entryDto(20)]), 1);

  it('ignores an older stream generation', () => {
    const { state, action } = applyEnvelope(
      base(),
      envelopeDto({ kind: 'TIMELINE', event_id: 21, cursor: 21, entry: entryDto(21) }),
      0,
    );
    expect(state.cursor).toBe(20);
    expect(action).toBe('NONE');
  });

  it('never moves the cursor backwards on a snapshot', () => {
    const later = { ...base(), cursor: 30 };
    expect(acceptSnapshot(later, sessionDto({ cursor: 25 }), null, 2).cursor).toBe(30);
    expect(acceptSnapshot(later, sessionDto({ id: SESSION_B, cursor: 99 }), null, 2)).toBe(later);
  });

  it('asks for a resync when a heartbeat shows a newer cursor', () => {
    const { state, action } = applyEnvelope(
      base(),
      envelopeDto({ kind: 'HEARTBEAT', cursor: 23 }),
      1,
    );
    expect(action).toBe('RESYNC');
    expect(state.session).toBe(base().session.id === state.session.id ? state.session : null);
  });

  it('keeps the timeline ordered and bounded', () => {
    let state = initialState(sessionDto({ cursor: 0 }), pageDto([]), 1);
    for (let seq = 1; seq <= TIMELINE_LIMIT + 50; seq += 1) {
      state = applyEnvelope(
        state,
        envelopeDto({ kind: 'TIMELINE', event_id: seq, cursor: seq, entry: entryDto(seq) }),
        1,
      ).state;
    }
    expect(state.timeline).toHaveLength(TIMELINE_LIMIT);
    const seqs = state.timeline.map((entry) => entry.seq);
    expect(seqs).toEqual([...seqs].sort((a, b) => a - b));
    expect(seqs.at(-1)).toBe(TIMELINE_LIMIT + 50);
  });

  it('rejects an envelope that is not the documented protocol', () => {
    expect(liveEnvelopeSchema.safeParse({ ...envelopeDto(), protocol: 'other' }).success).toBe(
      false,
    );
    expect(liveEnvelopeSchema.safeParse({ ...envelopeDto(), kind: 'ORDER' }).success).toBe(false);
  });
});

// ----------------------------------------------------------------------
// Modes, accessibility, boundaries
// ----------------------------------------------------------------------

describe('Beginner and Pro agree on the facts', () => {
  it.each(['BEGINNER', 'PRO'] as const)('%s shows the same states', async (mode) => {
    await openSession(mode);
    expect(screen.getByText('SAĞLAYICI BAĞLI')).toBeInTheDocument();
    expect(screen.getByText('3 / 3')).toBeInTheDocument();
    expect(screen.getByText('Kaynak: SİMÜLE GEÇMİŞ AKIŞ')).toBeInTheDocument();
  });

  it('Pro adds identifiers, Beginner adds explanations', async () => {
    await openSession('PRO');
    expect(screen.getByText(SESSION_A)).toBeInTheDocument();
    expect(screen.getByText('HISTORICAL')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Bu ekranı okumak' })).not.toBeInTheDocument();
  });

  it('Beginner explains streams, confirmation, gaps and metadata', async () => {
    await openSession('BEGINNER');
    const guide = screen.getByRole('heading', { name: 'Bu ekranı okumak' })
      .parentElement as HTMLElement;
    for (const phrase of [
      'Veri akışı',
      'Onaylı mum',
      'Oluşan mum',
      'boşluk',
      'çarpan',
      'otomatik açılmaz',
    ]) {
      expect(guide).toHaveTextContent(phrase);
    }
  });
});

describe('accessibility', () => {
  it('tabs are keyboard operable', async () => {
    const { user } = await openSession();
    const first = screen.getByRole('tab', { name: 'GENEL BAKIŞ' });
    first.focus();

    await user.keyboard('{ArrowRight}');

    const second = screen.getByRole('tab', { name: 'ZAMAN DİLİMLERİ' });
    expect(second).toHaveAttribute('aria-selected', 'true');
    expect(second).toHaveFocus();
    await user.keyboard('{End}');
    expect(screen.getByRole('tab', { name: 'UYARILAR' })).toHaveFocus();
    expect(screen.getByRole('tabpanel')).toHaveAttribute(
      'aria-labelledby',
      screen.getByRole('tab', { name: 'UYARILAR' }).id,
    );
  });

  it('labels its inputs and announces status politely', async () => {
    stubFetch(backend());
    render(<LiveScreen mode="BEGINNER" onBack={vi.fn()} />);

    expect(await screen.findByLabelText('Geçmiş veri kümesi')).toBeInTheDocument();
    expect(screen.getByLabelText('Oynatma hızı')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveAttribute('aria-live', 'polite');
  });

  it('reports a refused creation as an alert', async () => {
    stubFetch(
      backend({
        extra: (url, method) =>
          url.endsWith('/api/live/sessions') && method === 'POST'
            ? json({ detail: { code: 'LIVE_CAPACITY', detail: 'at most 8 live sessions' } }, 409)
            : undefined,
      }),
    );
    const user = userEvent.setup();
    render(<LiveScreen mode="BEGINNER" onBack={vi.fn()} />);
    await user.selectOptions(
      await screen.findByLabelText('Geçmiş veri kümesi'),
      sourceDto().source_id,
    );
    await user.click(screen.getByLabelText('5M (288 mum)'));
    await user.click(screen.getByRole('button', { name: 'AKIŞI BAŞLAT' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('at most 8 live sessions');
  });
});

describe('boundaries', () => {
  it('offers no trade action anywhere', async () => {
    await openSession('PRO');
    const labels = screen.getAllByRole('button').map((button) => button.textContent ?? '');
    for (const label of labels) {
      expect(label).not.toMatch(/\b(AL|SAT)\b|EMİR|POZİSYON AÇ|İŞLEM AÇ|BUY|SELL/i);
    }
  });

  it('computes nothing financial in the live modules', () => {
    for (const file of ['src/screens/Live.tsx', 'src/domain/live.ts', 'src/api/live.ts']) {
      const text = readFileSync(join(process.cwd(), file), 'utf8');
      for (const banned of ['parseFloat(', 'Number(', '.reduce((', 'toFixed(']) {
        expect(text, `${file} ${banned}`).not.toContain(banned);
      }
    }
  });

  it('shows a disabled deployment as disabled', async () => {
    stubFetch((url) =>
      url.endsWith('/capability')
        ? json(
            capabilityDto({
              state: 'DISABLED',
              detail: 'Yalnızca yerel geliştirme ortamında açıktır.',
              limits: null,
            }),
          )
        : json({ detail: { code: 'LIVE_DISABLED', detail: 'disabled' } }, 503),
    );
    render(<LiveScreen mode="BEGINNER" onBack={vi.fn()} />);

    expect(
      await screen.findByText('Yalnızca yerel geliştirme ortamında açıktır.'),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'AKIŞI BAŞLAT' })).not.toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------
// Part 2B: snapshot / subscription races and timeline pagination
// ----------------------------------------------------------------------

describe('races between the snapshot and the stream', () => {
  const base = () => initialState(sessionDto(), pageDto([entryDto(19), entryDto(20)]), 1);

  it('applies the same cursor only once', () => {
    const once = applyEnvelope(
      base(),
      envelopeDto({ kind: 'TIMELINE', event_id: 21, cursor: 21, entry: entryDto(21) }),
      1,
    ).state;
    const twice = applyEnvelope(
      once,
      envelopeDto({ kind: 'TIMELINE', event_id: 21, cursor: 21, entry: entryDto(21) }),
      1,
    );
    expect(twice.state).toBe(once);
    expect(twice.action).toBe('NONE');
    expect(once.timeline.filter((entry) => entry.seq === 21)).toHaveLength(1);
  });

  it('catches up entries replayed after the snapshot cursor, then takes the state', () => {
    let state = base();
    for (const seq of [21, 22]) {
      state = applyEnvelope(
        state,
        envelopeDto({ kind: 'TIMELINE', event_id: seq, cursor: seq, entry: entryDto(seq) }),
        1,
      ).state;
    }
    state = applyEnvelope(
      state,
      envelopeDto({ kind: 'STATE', event_id: 22, cursor: 22, session: sessionDto({ cursor: 22 }) }),
      1,
    ).state;
    expect(state.cursor).toBe(22);
    expect(state.timeline.map((entry) => entry.seq)).toEqual([19, 20, 21, 22]);
  });

  it('a snapshot older than a frame already applied does not rewind it', () => {
    const ahead = applyEnvelope(
      base(),
      envelopeDto({
        kind: 'TIMELINE',
        event_id: 21,
        cursor: 21,
        entry: entryDto(21),
        session: sessionDto({ cursor: 21 }),
      }),
      1,
    ).state;
    expect(acceptSnapshot(ahead, sessionDto({ cursor: 20 }), null, 1).cursor).toBe(21);
  });

  it('a session that ended during the reconnect is shown ended, and no stream reopens', async () => {
    let ended = false;
    await openSession(
      'BEGINNER',
      backend({
        sessions: [SESSION_A],
        session: () =>
          ended
            ? sessionDto({
                lifecycle: 'ENDED',
                connection: 'TERMINATED',
                end_origin: 'USER_CANCELLED',
                cursor: 25,
              })
            : sessionDto(),
      }),
    );

    ended = true;
    act(() =>
      stream().emit(
        envelopeDto({ kind: 'TIMELINE', event_id: 25, cursor: 25, entry: entryDto(25) }),
      ),
    );

    expect(await screen.findByText('AKIŞ SONA ERDİ')).toBeInTheDocument();
    expect(FakeEventSource.instances).toHaveLength(1);
    expect(stream().closed).toBe(true);
    // An END arriving late on the closed stream changes nothing.
    act(() => stream().emit(envelopeDto({ kind: 'END', event_id: 26, cursor: 26 })));
    expect(FakeEventSource.instances).toHaveLength(1);
  });
});

describe('timeline pagination', () => {
  it('reads the page before the oldest entry and merges it in order', async () => {
    const newest = Array.from({ length: 100 }, (_, index) => entryDto(101 + index));
    const older = Array.from({ length: 100 }, (_, index) => entryDto(1 + index));
    const handler = backend({
      sessions: [SESSION_A],
      session: () => sessionDto({ cursor: 200, oldest_retained: 1 }),
      extra: (url) =>
        url.includes('/timeline?after=0')
          ? json(pageDto(older))
          : url.includes('/timeline')
            ? json(pageDto(newest))
            : undefined,
    });
    const { calls, user } = await openSession('PRO', handler);
    await user.click(screen.getByRole('tab', { name: 'ZAMAN ÇİZELGESİ' }));
    expect(document.querySelector('.live-timeline__range')).toHaveTextContent(
      'Gösterilen: 100 kayıt (#101–#200)',
    );

    await user.click(screen.getByRole('button', { name: 'Daha eski kayıtları yükle' }));

    await waitFor(() =>
      expect(document.querySelector('.live-timeline__range')).toHaveTextContent(
        'Gösterilen: 200 kayıt (#1–#200)',
      ),
    );
    expect(calls.some((call) => call.url.endsWith('/timeline?after=0&limit=100'))).toBe(true);
    expect(
      screen.queryByRole('button', { name: 'Daha eski kayıtları yükle' }),
    ).not.toBeInTheDocument();
  });

  it('discards an older page that arrives after a switch or a resync', () => {
    const state = initialState(
      sessionDto({ cursor: 200, oldest_retained: 1 }),
      pageDto([entryDto(150)]),
      3,
    );
    expect(olderPageCursor(state, 100)).toBe(49);
    expect(withOlderEntries(state, pageDto([entryDto(60)]), SESSION_A, 2)).toBe(state);
    expect(withOlderEntries(state, pageDto([entryDto(60)]), SESSION_B, 3)).toBe(state);
    expect(withOlderEntries(state, pageDto([entryDto(60)]), SESSION_A, 3).timeline).toHaveLength(2);
  });
});
