import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { advanceReplay } from '../api/replay';
import { delayForSpeed, isNewerThan, toChartSeries, windowOf } from '../domain/replay';
import { ReplayScreen } from '../screens/Replay';
import { candle, listDto, replayDto, stepDto, steppedDto } from '../test/replayDto';
import { ReplayControls } from './ReplayControls';

/**
 * The replay UI (Phase 11).
 *
 * The rules that matter here are not about layout. A replay screen is only
 * honest if the browser never holds a candle the server did not reveal, if a
 * slow answer cannot move the clock backwards, and if the speed control is
 * visibly a scheduling choice rather than a market one.
 */

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** A fetch stub that answers by URL and records every request it saw. */
function stubFetch(handler: (url: string, init?: RequestInit) => Response) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === 'string' ? input : input.toString();
      calls.push(init === undefined ? { url } : { url, init });
      return handler(url, init);
    }),
  );
  return calls;
}

function controls(overrides: Partial<Parameters<typeof ReplayControls>[0]> = {}) {
  const props = {
    atEnd: false,
    busy: false,
    playing: false,
    speed: 1 as const,
    maxSteps: 50,
    onStep: vi.fn(),
    onPlay: vi.fn(),
    onPause: vi.fn(),
    onSpeed: vi.fn(),
    onAnalyse: vi.fn(),
    onMeasure: vi.fn(),
    onClose: vi.fn(),
    ...overrides,
  };
  return { props, ...render(<ReplayControls {...props} />) };
}

/** The window for a timeframe, or a failed expectation rather than a crash. */
function requireWindow(session: Parameters<typeof windowOf>[0], timeframe: string) {
  const found = windowOf(session, timeframe);
  expect(found, timeframe).not.toBeNull();
  if (found === null) throw new Error(`no window for ${timeframe}`);
  return found;
}

afterEach(() => vi.unstubAllGlobals());

describe('the browser never holds an unrevealed candle', () => {
  it('draws only the candles the server sent', () => {
    const series = toChartSeries(requireWindow(replayDto(), '5M'));

    expect(series.candles).toHaveLength(3);
    expect(series.analysedCount).toBe(12);
  });

  it('never marks a revealed candle as still forming', () => {
    // A replay only reveals finished candles, so a forming bar cannot appear.
    const series = toChartSeries(requireWindow(replayDto(), '5M'));
    expect(series.candles.every((item) => item.isClosed)).toBe(true);
  });

  it('reports what the window left out using the server counts', () => {
    const session = replayDto({
      availability: [
        {
          timeframe: '5M',
          candles: [candle(0), candle(5)],
          revealed: 900,
          dataset_total: 1000,
          window_limit: 400,
          truncated: true,
        },
      ],
    });
    const series = toChartSeries(requireWindow(session, '5M'));

    expect(series.analysedCount).toBe(900);
    expect(series.omittedCount).toBe(898);
  });

  it('shows the revealed and dataset totals side by side', async () => {
    stubFetch((url) =>
      url.endsWith('/positions')
        ? jsonResponse([])
        : url.includes('RS-')
          ? jsonResponse(replayDto())
          : jsonResponse(listDto([replayDto()])),
    );
    render(<ReplayScreen mode="BEGINNER" onBack={() => {}} />);

    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));

    const table = await screen.findByRole('table', { name: '' }).catch(() => null);
    expect(table ?? screen.getByText(/12 mum açıklandı/)).toBeTruthy();
    expect(screen.getByText(/288 mum veri kümesinde var/)).toBeInTheDocument();
  });
});

describe('a late answer cannot move the clock backwards', () => {
  it('accepts a newer cursor version and rejects an older one', () => {
    const current = steppedDto(5, 25);

    expect(isNewerThan(steppedDto(6, 30), current)).toBe(true);
    expect(isNewerThan(steppedDto(4, 20), current)).toBe(false);
    expect(isNewerThan(steppedDto(5, 25), current)).toBe(false);
  });

  it('treats a different session as something to show', () => {
    const other = { ...replayDto(), id: 'RS-0000000000000000000000b2' };
    expect(isNewerThan(other, steppedDto(9, 45))).toBe(true);
  });

  it('keeps the newer cursor when responses arrive out of order', async () => {
    const session = replayDto();
    // A holder rather than a bare `let`: TypeScript narrows a closed-over
    // variable to `never` after the null check, and the point of this test is
    // to resolve it later.
    const held: { resolve: ((value: Response) => void) | null } = { resolve: null };

    stubFetch((url) => {
      if (url.endsWith('/positions')) return jsonResponse([]);
      if (url.includes('/advance')) {
        // The first advance is answered late, with an older version.
        if (held.resolve === null) {
          const slow = new Promise<Response>((resolve) => {
            held.resolve = resolve;
          });
          return slow as unknown as Response;
        }
        return jsonResponse(stepDto(3, 10));
      }
      if (url.includes('RS-')) return jsonResponse(session);
      return jsonResponse(listDto([session]));
    });

    render(<ReplayScreen mode="PRO" onBack={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));
    const step = await screen.findByRole('button', { name: 'BİR MUM İLERLE' });

    await userEvent.click(step);
    await userEvent.click(step);
    await waitFor(() => expect(screen.getByText(/Sürüm/)).toBeInTheDocument());

    // Release the stale first response afterwards.
    held.resolve?.(jsonResponse(stepDto(2, 5)));

    await waitFor(() => {
      const version = screen.getByText('Sürüm').nextElementSibling;
      expect(Number(version?.textContent)).toBeGreaterThanOrEqual(3);
    });
  });
});

describe('playback speed is presentation only', () => {
  it('changes only the delay between commands', () => {
    expect(delayForSpeed(1)).toBe(1000);
    expect(delayForSpeed(2)).toBe(500);
    expect(delayForSpeed(5)).toBe(200);
    expect(delayForSpeed(0.5)).toBe(2000);
  });

  it('sends the same request whatever the speed', async () => {
    const calls = stubFetch(() => jsonResponse(stepDto(2, 5)));

    await advanceReplay('RS-0000000000000000000000a1', 1, { expectedVersion: 1 });
    await advanceReplay('RS-0000000000000000000000a1', 1, { expectedVersion: 1 });

    const bodies = calls.map((call) => call.init?.body);
    expect(bodies[0]).toBe(bodies[1]);
    expect(JSON.stringify(calls)).not.toContain('speed');
  });

  it('says out loud that speed does not change the result', () => {
    controls();
    expect(screen.getByText(/aynı mumları, aynı dolumları ve aynı sonuçları üretir/)).toBeVisible();
  });

  it('offers pause as stopping the commands, not the market', async () => {
    const { props } = controls({ playing: true });
    await userEvent.click(screen.getByRole('button', { name: 'DURAKLAT' }));
    expect(props.onPause).toHaveBeenCalledOnce();
    expect(props.onStep).not.toHaveBeenCalled();
  });
});

describe('the request vocabulary is how far, never where to', () => {
  it('sends a step count and an expected version, and nothing else', async () => {
    const calls = stubFetch(() => jsonResponse(stepDto(2, 5)));

    await advanceReplay('RS-0000000000000000000000a1', 3, { expectedVersion: 1 });

    const body = JSON.parse(String(calls[0]?.init?.body));
    expect(body).toEqual({ steps: 3, expected_version: 1 });
    // Exactly two keys: there is no second request shape to slip a field into.
    expect(Object.keys(body)).toHaveLength(2);
    for (const forged of ['replay_as_of', 'as_of', 'cursor', 'candles', 'revealed']) {
      expect(Object.keys(body)).not.toContain(forged);
    }
  });

  it('carries an idempotency key so a retry cannot step twice', async () => {
    const calls = stubFetch(() => jsonResponse(stepDto(2, 5)));

    await advanceReplay('RS-0000000000000000000000a1', 1, {
      expectedVersion: 1,
      idempotencyKey: 'replay-step-0001',
    });

    const headers = calls[0]?.init?.headers as Record<string, string>;
    expect(headers['Idempotency-Key']).toBe('replay-step-0001');
  });
});

describe('the end of a dataset is stated, not hidden', () => {
  it('disables stepping and explains that replay is forward only', () => {
    controls({ atEnd: true });

    expect(screen.getByRole('button', { name: 'BİR MUM İLERLE' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'OYNAT' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent(/yalnızca ileri gider/);
  });

  it('bounds an advance to what the server allows', async () => {
    const { props } = controls({ maxSteps: 50 });
    const input = screen.getByLabelText('Kaç mum');

    await userEvent.clear(input);
    await userEvent.type(input, '5000');
    await userEvent.click(screen.getByRole('button', { name: /^[0-9]+ MUM İLERLE$/ }));

    expect(props.onStep).toHaveBeenCalledWith(50);
  });
});

describe('analysis is user-triggered', () => {
  it('offers analysis as a button and says stepping does not run one', () => {
    controls();

    expect(screen.getByRole('button', { name: 'BU ANI ANALİZ ET' })).toBeEnabled();
    expect(screen.getByText(/ilerlemek tek başına analiz üretmez/)).toBeVisible();
  });

  it('does not analyse when a step is taken', async () => {
    const calls = stubFetch((url) => {
      if (url.endsWith('/positions')) return jsonResponse([]);
      if (url.includes('/advance')) return jsonResponse(stepDto(2, 5));
      if (url.includes('RS-')) return jsonResponse(replayDto());
      return jsonResponse(listDto([replayDto()]));
    });

    render(<ReplayScreen mode="BEGINNER" onBack={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));
    await userEvent.click(await screen.findByRole('button', { name: 'BİR MUM İLERLE' }));

    await waitFor(() => expect(calls.some((call) => call.url.includes('/advance'))).toBe(true));
    expect(calls.some((call) => call.url.includes('/analysis'))).toBe(false);
  });
});

describe('it is visibly a simulation of the past', () => {
  beforeEach(() => {
    stubFetch(() => jsonResponse(listDto([])));
  });

  it('leads with the simulation banner', async () => {
    render(<ReplayScreen mode="BEGINNER" onBack={() => {}} />);

    const banner = screen.getByRole('note', { name: 'Simülasyon uyarısı' });
    expect(banner).toHaveTextContent('SİMÜLASYON');
    expect(banner).toHaveTextContent(/Gerçek emir oluşturulmaz/);
  });

  it('never claims a typed symbol is verified contract metadata', async () => {
    render(<ReplayScreen mode="BEGINNER" onBack={() => {}} />);

    expect(
      await screen.findByText(/doğrulanmış sözleşme meta verisi değildir/),
    ).toBeInTheDocument();
  });

  it('never says an order was sent', async () => {
    const { container } = render(<ReplayScreen mode="PRO" onBack={() => {}} />);
    await screen.findByRole('note', { name: 'Simülasyon uyarısı' });
    const text = (container.textContent ?? '').toLocaleLowerCase('tr-TR');

    for (const phrase of ['emir gönderildi', 'emriniz iletildi', 'canlı veri', 'gerçek zamanlı']) {
      expect(text).not.toContain(phrase);
    }
  });
});

describe('beginner and pro render the same replay', () => {
  it('shows the same clock and counts in both modes', async () => {
    const seen: Record<string, string> = {};
    for (const mode of ['BEGINNER', 'PRO'] as const) {
      stubFetch((url) =>
        url.endsWith('/positions')
          ? jsonResponse([])
          : url.includes('RS-')
            ? jsonResponse(replayDto())
            : jsonResponse(listDto([replayDto()])),
      );
      const view = render(<ReplayScreen mode={mode} onBack={() => {}} />);
      await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));
      const region = await screen.findByRole('region', { name: 'Geçmiş piyasa anı' });
      seen[mode] = within(region).getByText(/12 \/ 288/).textContent ?? '';
      view.unmount();
      vi.unstubAllGlobals();
    }
    expect(seen.BEGINNER).toBe(seen.PRO);
  });

  it('shows the audit detail only in pro', async () => {
    stubFetch((url) =>
      url.endsWith('/positions')
        ? jsonResponse([])
        : url.includes('RS-')
          ? jsonResponse(replayDto())
          : jsonResponse(listDto([replayDto()])),
    );
    render(<ReplayScreen mode="PRO" onBack={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));

    expect(await screen.findByText('Veri kümesi')).toBeInTheDocument();
    expect(screen.getByText('RD-00000000000000000000000000000001')).toBeInTheDocument();
  });
});

describe('accessibility foundation', () => {
  it('names every control and region', async () => {
    stubFetch((url) =>
      url.endsWith('/positions')
        ? jsonResponse([])
        : url.includes('RS-')
          ? jsonResponse(replayDto())
          : jsonResponse(listDto([replayDto()])),
    );
    render(<ReplayScreen mode="PRO" onBack={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));

    for (const heading of [
      'Geçmiş piyasa anı',
      'Geçmişe sarma kontrolleri',
      'Açıklanmış mumlar',
      'Zaman dilimi uygunluğu',
    ]) {
      expect(screen.getByRole('region', { name: heading }), heading).toBeInTheDocument();
    }
    for (const button of screen.getAllByRole('button')) {
      expect((button.textContent ?? '').trim().length).toBeGreaterThan(0);
    }
  });

  it('reaches every replay control by keyboard', async () => {
    const { props } = controls();
    await userEvent.tab();
    await userEvent.tab();
    expect(document.activeElement?.tagName).toBe('BUTTON');

    screen.getByRole('button', { name: 'BİR MUM İLERLE' }).focus();
    await userEvent.keyboard('{Enter}');
    expect(props.onStep).toHaveBeenCalledWith(1);
  });

  it('states the timeframe tab selection in the accessibility tree', async () => {
    stubFetch((url) =>
      url.endsWith('/positions')
        ? jsonResponse([])
        : url.includes('RS-')
          ? jsonResponse(replayDto())
          : jsonResponse(listDto([replayDto()])),
    );
    render(<ReplayScreen mode="BEGINNER" onBack={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));

    const tabs = await screen.findAllByRole('tab');
    expect(tabs.map((tab) => tab.getAttribute('aria-selected'))).toContain('true');
  });

  it('explains the availability table in words, not only in numbers', async () => {
    stubFetch((url) =>
      url.endsWith('/positions')
        ? jsonResponse([])
        : url.includes('RS-')
          ? jsonResponse(replayDto())
          : jsonResponse(listDto([replayDto()])),
    );
    render(<ReplayScreen mode="BEGINNER" onBack={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));

    expect(
      await screen.findByText(/kapanışı geçmiş piyasa anına eşit veya ondan önceyse görünür/),
    ).toBeInTheDocument();
  });

  it('keeps a wide table inside its own scroll container', async () => {
    stubFetch((url) =>
      url.endsWith('/positions')
        ? jsonResponse([])
        : url.includes('RS-')
          ? jsonResponse(replayDto())
          : jsonResponse(listDto([replayDto()])),
    );
    const { container } = render(<ReplayScreen mode="PRO" onBack={() => {}} />);
    await userEvent.click(await screen.findByRole('button', { name: /TEST_FIXTURE_FUT/ }));

    // Phase 9's narrow-screen lesson: the scroll belongs to the table, not the
    // page. jsdom computes no layout, so this asserts the structure that makes
    // the CSS rule reachable.
    const table = await screen.findByRole('table');
    expect(table.parentElement?.className).toContain('replay-table__scroll');
    expect(container.querySelectorAll('.replay-table__scroll').length).toBeGreaterThan(0);
  });
});
