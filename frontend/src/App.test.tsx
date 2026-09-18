import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactElement } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { capability, isLive } from './domain/capabilities';
import { analysisDto } from './test/dto';

/**
 * The shell now makes a real request (§34, §35).
 *
 * Phase 8A asserted the opposite - that no analysis source existed and the app
 * said so. That tripwire has been paid off deliberately: `POST /api/analysis`
 * is wired, so these tests exercise the flow it enabled, including the stale
 * response guard that had nothing to guard before.
 */

function renderApp(node: ReactElement = <App />) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

/** A fetch stub that resolves analyses in an order the test controls. */
function stubFetch(handler: (url: string, init?: RequestInit) => Promise<unknown>) {
  vi.stubGlobal('fetch', async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : String(input);
    const payload = await handler(url, init);
    return {
      ok: true,
      status: 200,
      json: async () => payload,
    } as Response;
  });
}

const healthPayload = {
  status: 'ok',
  app_env: 'test',
  version: '0.0.0',
  checked_at: '2026-03-02T12:00:00Z',
  components: [],
};

beforeEach(() => {
  stubFetch(async (url) => (url.includes('/health') ? healthPayload : analysisDto()));
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function goToAnalyse(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'PİYASA ANALİZİ' }));
  await user.type(screen.getByLabelText('Sözleşme / sembol'), 'TEST_FIXTURE_FUT');
}

async function attachFile(user: ReturnType<typeof userEvent.setup>) {
  const input = screen.getByLabelText('1H (Eğilim) CSV dosyası');
  const file = new File(['open_time,open,high,low,close,volume\n'], '1H.csv', { type: 'text/csv' });
  await user.upload(input, file);
  // Reading the file is asynchronous, so the component has not accepted it
  // until its name appears. Waiting on the rendered name rather than on a
  // timer keeps the assertion tied to what a user would actually see.
  await waitFor(() => expect(screen.getByText('1H.csv')).toBeInTheDocument());
}

describe('the dashboard is the entry point', () => {
  it('offers the real analysis action', () => {
    renderApp();
    expect(screen.getByRole('button', { name: 'PİYASA ANALİZİ' })).toBeInTheDocument();
  });

  it('shows no market verdict before an analysis exists', () => {
    renderApp();
    for (const word of ['AL', 'SAT', 'BEKLE', 'İŞLEM YOK']) {
      expect(screen.queryByText(word), word).toBeNull();
    }
  });

  it('lists future modules as unavailable rather than faking them', () => {
    renderApp();

    // Phase 9 shipped paper positions; the journal and performance are Phase 10.
    expect(screen.getByText('İşlem günlüğü ve performans')).toBeInTheDocument();
    expect(screen.getAllByText('Faz 10').length).toBeGreaterThan(0);
    expect(screen.queryByText('Kağıt pozisyonlar')).not.toBeInTheDocument();
  });

  it('keeps the execution-mode disclosure visible', () => {
    renderApp();
    expect(screen.getByText('YALNIZCA ANALİZ VE SİNYAL')).toBeInTheDocument();
  });

  it('still offers no action for a capability that is not live', () => {
    for (const id of ['paper-trading', 'persisted-analysis']) {
      expect(isLive(id), id).toBe(false);
    }
    expect(capability('deterministic-analysis')?.state).toBe('AVAILABLE_NOW');
  });
});

describe('the analyse flow reaches a real workspace', () => {
  it('renders the workspace from the backend response', async () => {
    const user = userEvent.setup();
    renderApp();

    await goToAnalyse(user);
    await attachFile(user);
    await user.click(screen.getByRole('button', { name: 'ANALİZ ET' }));

    await waitFor(() => expect(screen.getByText('TEST_FIXTURE_FUT')).toBeInTheDocument());
    expect(screen.getByRole('heading', { name: /Senaryolar/ })).toBeInTheDocument();
  });

  it('states that the result is not saved', async () => {
    const user = userEvent.setup();
    renderApp();

    await goToAnalyse(user);
    await attachFile(user);
    await user.click(screen.getByRole('button', { name: 'ANALİZ ET' }));

    await waitFor(() => expect(screen.getByText(/kaydedilmez/)).toBeInTheDocument());
  });

  it('warns before submission that a typed symbol is not verified metadata', async () => {
    const user = userEvent.setup();
    renderApp();

    await user.click(screen.getByRole('button', { name: 'PİYASA ANALİZİ' }));
    expect(screen.getByText(/doğrulanmış sözleşme bilgisi değildir/)).toBeInTheDocument();
  });

  it('lists the missing timeframes before submission', async () => {
    const user = userEvent.setup();
    renderApp();

    await goToAnalyse(user);
    await attachFile(user);

    const summary = screen.getByRole('region', { name: /Gönderim öncesi durum/ });
    // The label and the value are separate elements, so match on the item's
    // combined text rather than a single node.
    expect(summary.textContent).toContain('Eksik zaman dilimleri:');
    expect(summary.textContent).toContain('1D, 15M, 5M');
  });
});

describe('concurrency (§34)', () => {
  it('prevents a second submission while one is running', async () => {
    const user = userEvent.setup();
    stubFetch(async (url) => {
      if (url.includes('/health')) return healthPayload;
      await new Promise((resolve) => setTimeout(resolve, 50));
      return analysisDto();
    });

    renderApp();
    await goToAnalyse(user);
    await attachFile(user);
    await user.click(screen.getByRole('button', { name: 'ANALİZ ET' }));

    // The first line of defence is that there is no second button to press.
    const submit = screen.getByRole('button', { name: /Analiz ediliyor/ });
    expect(submit).toBeDisabled();
  });

  it('discards a late response from a cancelled request', async () => {
    const user = userEvent.setup();

    /*
     * The reachable A/B path. Submitting twice at once is impossible - the
     * button disables itself - so the interleaving a user can actually create
     * is: submit, cancel, submit again, and the first response arrives late.
     *
     * Without the ticket guard in `App`, that late STALE_SYMBOL result would
     * be written over the workspace the second request produced.
     */
    let call = 0;
    // Typed as a mutable holder: TypeScript narrows a `let` assigned only
    // inside a callback to `never` at the call site otherwise.
    const release: { fn: (() => void) | null } = { fn: null };
    stubFetch(async (url) => {
      if (url.includes('/health')) return healthPayload;
      call += 1;
      if (call === 1) {
        await new Promise<void>((resolve) => {
          release.fn = resolve;
        });
        return analysisDto({ symbol: 'STALE_SYMBOL' });
      }
      return analysisDto({ symbol: 'FRESH_SYMBOL' });
    });

    renderApp();
    await goToAnalyse(user);
    await attachFile(user);

    await user.click(screen.getByRole('button', { name: 'ANALİZ ET' }));
    await waitFor(() => expect(release.fn).not.toBeNull());

    await user.click(screen.getByRole('button', { name: 'İptal' }));
    await user.click(screen.getByRole('button', { name: 'ANALİZ ET' }));
    await waitFor(() => expect(screen.getByText('FRESH_SYMBOL')).toBeInTheDocument());

    // Now let the abandoned first request finish. It must be ignored.
    release.fn?.();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(screen.queryByText('STALE_SYMBOL')).toBeNull();
    expect(screen.getByText('FRESH_SYMBOL')).toBeInTheDocument();
  });
});
