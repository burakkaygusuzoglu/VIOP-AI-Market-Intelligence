import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PaperPositionDto } from '../api/paper';
import { describeEvent, mapPaperPosition } from '../domain/paper';
import { PaperTrading } from '../screens/PaperTrading';
import { event, paperDto } from '../test/paperDto';
import { PaperCreateForm } from './PaperCreateForm';
import { PaperPositionDetail } from './PaperPositionDetail';

/**
 * The paper-trading UI (Phase 9).
 *
 * It must never read as live trading, never compute a number, and never send a
 * field that is a result. Beginner and Pro render the same object.
 */

function detail(dto: PaperPositionDto, mode: 'BEGINNER' | 'PRO' = 'BEGINNER') {
  return render(<PaperPositionDetail position={dto} mode={mode} onUpdated={() => {}} />);
}

function withQuery(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('it is visibly a simulation', () => {
  beforeEach(() => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ items: [], total: 0, offset: 0, limit: 20 })),
    );
  });
  afterEach(() => vi.unstubAllGlobals());

  it('leads with the simulation banner', async () => {
    withQuery(<PaperTrading mode="BEGINNER" />);

    const banner = screen.getByRole('note', { name: 'Simülasyon uyarısı' });
    expect(banner).toHaveTextContent('SİMÜLASYON');
    expect(banner).toHaveTextContent(/gerçek emir oluşturulmaz/);
  });

  it('says this deployment refuses new positions', () => {
    withQuery(<PaperTrading mode="BEGINNER" />);

    expect(screen.getByText(/sunucu her yeni kağıt pozisyonu reddeder/)).toBeInTheDocument();
  });

  it('marks the position itself as simulated', () => {
    detail(paperDto());

    expect(screen.getByText('SİMÜLASYON')).toBeInTheDocument();
  });

  it('never says an order was placed', () => {
    const { container } = detail(paperDto({ state: 'CLOSED', remaining: 0 }), 'PRO');
    const text = (container.textContent ?? '').toLocaleLowerCase('tr-TR');

    for (const phrase of ['emir gönderildi', 'emriniz', 'order placed', 'gerçekleşti', 'canlı']) {
      expect(text).not.toContain(phrase);
    }
  });
});

describe('states render in words, not only colour', () => {
  it.each([
    ['PENDING_ENTRY', 'Giriş bekleniyor'],
    ['OPEN', 'Açık'],
    ['PARTIALLY_CLOSED', 'Kısmen kapandı'],
    ['AMBIGUOUS_HALTED', 'Belirsiz çubuk — donduruldu'],
    ['CLOSED', 'Kapandı'],
    ['CANCELLED', 'İptal edildi'],
    ['REJECTED', 'Giriş reddedildi'],
  ])('%s', (state, label) => {
    const { container } = detail(paperDto({ state }));
    // The state line, specifically: event descriptions elsewhere also say "kapandı".
    const line = container.querySelector('.paper-state');
    expect(line).toHaveTextContent(`SİMÜLASYON ${label}`);
  });

  it('an unknown state is named unknown rather than guessed', () => {
    detail(paperDto({ state: 'SOMETHING_NEW' }));
    expect(screen.getByText(/Bilinmeyen durum/)).toBeInTheDocument();
  });

  it('a halted position explains the ambiguity and offers only a close', () => {
    detail(paperDto({ state: 'AMBIGUOUS_HALTED', remaining: 4 }));

    expect(screen.getByRole('alert')).toHaveTextContent(/hangisinin önce olduğunu söylemez/);
    expect(screen.getByRole('button', { name: 'Sonraki çubukta kapat' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Stopu başabaşa taşı' })).not.toBeInTheDocument();
  });

  it('a pending position can be cancelled but not closed', () => {
    detail(paperDto({ state: 'PENDING_ENTRY', remaining: 0, entry_fill_price: null }));

    expect(screen.getByRole('button', { name: 'Planı iptal et' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Sonraki çubukta kapat' })).not.toBeInTheDocument();
  });

  it('a closed position offers no action at all', () => {
    detail(paperDto({ state: 'CLOSED', remaining: 0 }));

    expect(screen.queryByRole('button', { name: /kapat|taşı|iptal/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/çubukları yükle/)).not.toBeInTheDocument();
  });
});

describe('money is shown exactly as the server sent it', () => {
  it('shows gross, fees, net and unrealized separately', () => {
    detail(paperDto());

    expect(screen.getByText('Gerçekleşen brüt K/Z').nextSibling).toHaveTextContent('80.00');
    expect(screen.getByText('Ücretler').nextSibling).toHaveTextContent('12.00');
    expect(screen.getByText('Gerçekleşen net K/Z').nextSibling).toHaveTextContent('67.77');
    expect(screen.getByText('Gerçekleşmemiş brüt K/Z').nextSibling).toHaveTextContent('85.00');
  });

  it('does not recompute net from gross minus fees', () => {
    // 80.00 - 12.00 is 68.00; the server said 67.77, and that is what is shown.
    detail(paperDto());

    expect(screen.queryByText('68.00')).not.toBeInTheDocument();
    expect(screen.getByText('67.77')).toBeInTheDocument();
  });

  it('says net is absent when fees are not modelled, rather than showing gross', () => {
    detail(paperDto({ fees_total: null, realized_net: null }));

    expect(screen.getByText('Modellenmedi')).toBeInTheDocument();
    expect(screen.getByText('Ücret modellenmediği için yok')).toBeInTheDocument();
  });

  it('shows the intended entry and the simulated fill as different facts', () => {
    detail(paperDto({ intended_entry: '100.00', entry_fill_price: '100.50' }));

    expect(screen.getByText('Planlanan giriş').nextSibling).toHaveTextContent('100.00');
    expect(screen.getByText('Simüle giriş dolumu').nextSibling).toHaveTextContent('100.50');
  });

  it('shows the partial state through remaining units and filled targets', () => {
    detail(paperDto());

    expect(screen.getByText('Miktar / kalan').nextSibling).toHaveTextContent('4 / 2 contract');
    const table = screen.getByRole('table', { name: 'Hedefler' });
    const rows = within(table).getAllByRole('row');
    expect(rows[1]).toHaveTextContent('Doldu (104.00)');
    expect(rows[2]).toHaveTextContent('Bekliyor');
  });
});

describe('what happened, and why', () => {
  it('explains each fill from the ledger fields', () => {
    detail(paperDto());

    expect(screen.getByText('Giriş simüle edildi')).toBeInTheDocument();
    expect(screen.getByText('Hedef 1 simüle edildi')).toBeInTheDocument();
    expect(screen.getByText(/Hedef fiyatı 104.00 → dolum 104.00/)).toBeInTheDocument();
  });

  it('marks a gap and an ambiguity as important and says why', () => {
    const stop = describeEvent(
      event(8, 'STOP_FILLED', {
        trigger_price: '98.00',
        fill_price: '95',
        gap: 'true',
        ambiguous: 'true',
        targets_also_touched: '1',
        quantity: '4',
      }),
    );

    expect(stop.important).toBe(true);
    expect(stop.detail).toContain('Tetik 98.00 → dolum 95');
    expect(stop.detail).toContain('açılış stopun ötesindeydi');
    expect(stop.detail).toContain('sıra bilinemediği için stop önce varsayıldı');
  });

  it('shows the engine’s own rejection reason verbatim', () => {
    const reason = 'the entry would have filled at 97.50, at or beyond the stop';
    detail(
      paperDto({
        state: 'REJECTED',
        key_events: [event(3, 'ENTRY_REJECTED', { reason })],
      }),
    );

    expect(screen.getByText(reason)).toBeInTheDocument();
  });

  it('Pro shows the raw event data and simulation rules; Beginner does not', () => {
    const { unmount } = detail(paperDto(), 'BEGINNER');
    expect(screen.queryByText('STOP_PRICE_OR_GAPPED_OPEN')).not.toBeInTheDocument();
    expect(screen.queryByText('gross_pnl')).not.toBeInTheDocument();
    unmount();

    detail(paperDto(), 'PRO');
    expect(screen.getByText('STOP_PRICE_OR_GAPPED_OPEN')).toBeInTheDocument();
    expect(screen.getByText('gross_pnl')).toBeInTheDocument();
    expect(screen.getByText(/SIMULATED · USER_SUPPLIED_HISTORICAL_BARS/)).toBeInTheDocument();
    expect(screen.getByText(/FUTURES \(UNVERIFIED\)/)).toBeInTheDocument();
  });

  it('Beginner and Pro show the same numbers', () => {
    const grab = (mode: 'BEGINNER' | 'PRO') => {
      const { unmount } = detail(paperDto(), mode);
      const values = [
        'Gerçekleşen brüt K/Z',
        'Ücretler',
        'Gerçekleşen net K/Z',
        'Gerçekleşmemiş brüt K/Z',
        'Stop (ilk / şimdiki)',
      ].map((label) => screen.getAllByText(label)[0]?.nextSibling?.textContent);
      unmount();
      return values;
    };

    expect(grab('BEGINNER')).toEqual(grab('PRO'));
  });
});

describe('the plan form sends intentions only', () => {
  afterEach(() => vi.unstubAllGlobals());

  async function fill(user: ReturnType<typeof userEvent.setup>) {
    await user.type(screen.getByLabelText('Sembol'), 'TEST_FIXTURE_FUT');
    await user.type(screen.getByLabelText('Miktar (tam birim)'), '4');
    await user.type(screen.getByLabelText('Planlanan giriş'), '100.00');
    await user.type(screen.getByLabelText('Stop'), '98.00');
    await user.type(screen.getByLabelText('Hedef 1 fiyatı'), '104.00');
    await user.type(screen.getByLabelText('Hedef 1 miktarı'), '4');
    await user.type(screen.getByLabelText('Hesap bakiyesi'), '100000');
    await user.type(screen.getByLabelText('İşlem başına risk (tutar)'), '1000');
  }

  it('posts no field that is a result, and an idempotency key', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(paperDto(), 201));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<PaperCreateForm onCreated={() => {}} />);

    await fill(user);
    await user.click(screen.getByRole('button', { name: 'SİMÜLASYON POZİSYONU OLUŞTUR' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    const body = JSON.parse(String(init.body)) as Record<string, unknown>;
    for (const forged of [
      'state',
      'fill_price',
      'entry_fill_price',
      'realized_gross',
      'realized_pnl',
      'events',
      'asset_class',
      'provenance',
      'risk_approval',
      'allowed_units',
    ]) {
      expect(body, forged).not.toHaveProperty(forged);
    }
    expect(body.quantity).toBe(4);
    expect(body.intended_entry).toBe('100.00');
    expect((init.headers as Record<string, string>)['Idempotency-Key']).toMatch(/^paper-/);
  });

  it('reuses the key when the same plan is retried after a failure', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: { code: 'X', detail: 'geçici' } }, 409))
      .mockResolvedValueOnce(jsonResponse(paperDto(), 201));
    vi.stubGlobal('fetch', fetchMock);
    const user = userEvent.setup();
    render(<PaperCreateForm onCreated={() => {}} />);

    await fill(user);
    const submit = screen.getByRole('button', { name: 'SİMÜLASYON POZİSYONU OLUŞTUR' });
    await user.click(submit);
    await screen.findByRole('alert');
    await user.click(submit);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const keys = fetchMock.mock.calls.map(
      (call) => ((call[1] as RequestInit).headers as Record<string, string>)['Idempotency-Key'],
    );
    expect(keys[0]).toBe(keys[1]);
  });

  it('shows the server’s refusal as it was written', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () =>
        jsonResponse(
          {
            detail: {
              code: 'PRODUCT_METADATA_UNAVAILABLE',
              kind: 'REFUSED',
              detail: 'no contract metadata provider is configured',
            },
          },
          422,
        ),
      ),
    );
    const user = userEvent.setup();
    render(<PaperCreateForm onCreated={() => {}} />);

    await fill(user);
    await user.click(screen.getByRole('button', { name: 'SİMÜLASYON POZİSYONU OLUŞTUR' }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'no contract metadata provider is configured',
    );
  });

  it('labels every input', () => {
    const { container } = render(<PaperCreateForm onCreated={() => {}} />);

    for (const input of container.querySelectorAll('input, select')) {
      const id = input.getAttribute('id');
      const labelled =
        (id && container.querySelector(`label[for="${CSS.escape(id)}"]`)) || input.closest('label');
      expect(labelled, input.outerHTML).toBeTruthy();
    }
  });
});

describe('a late answer cannot overwrite a newer view', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('shows the position that was selected last, even when an older fetch resolves later', async () => {
    const first = paperDto({ id: 'PP-aaaaaaaaaaaaaaaaaaaaaaaa', symbol: 'FIRST_FIXTURE' });
    const second = paperDto({ id: 'PP-bbbbbbbbbbbbbbbbbbbbbbbb', symbol: 'SECOND_FIXTURE' });
    let releaseFirst: (value: Response) => void = () => {};
    const slowFirst = new Promise<Response>((resolve) => {
      releaseFirst = resolve;
    });

    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: string) => {
        if (url.includes('/paper/positions?')) {
          return jsonResponse({ items: [first, second], total: 2, offset: 0, limit: 20 });
        }
        if (url.endsWith(first.id)) return slowFirst;
        return jsonResponse(second);
      }),
    );
    const user = userEvent.setup();
    withQuery(<PaperTrading mode="BEGINNER" />);

    await user.click(await screen.findByRole('button', { name: 'FIRST_FIXTURE pozisyonunu aç' }));
    await user.click(screen.getByRole('button', { name: 'SECOND_FIXTURE pozisyonunu aç' }));
    await screen.findByRole('heading', { name: /SECOND_FIXTURE/ });

    releaseFirst(jsonResponse(first));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(screen.getByRole('heading', { name: /SECOND_FIXTURE/ })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /FIRST_FIXTURE/ })).not.toBeInTheDocument();
  });
});

describe('keyboard and focus', () => {
  it('moves focus to the position heading when it opens', () => {
    detail(paperDto());

    expect(document.activeElement).toBe(screen.getByRole('heading', { level: 3 }));
    expect(screen.getByRole('heading', { level: 3 })).toHaveAttribute('tabindex', '-1');
  });

  it('reaches every action by Tab', async () => {
    const user = userEvent.setup();
    detail(paperDto({ state: 'OPEN', remaining: 4 }));

    const reached = new Set<string>();
    for (let i = 0; i < 10; i += 1) {
      await user.tab();
      const active = document.activeElement;
      if (active instanceof HTMLButtonElement) reached.add(active.textContent ?? '');
    }
    expect(reached).toContain('Sonraki çubukta kapat');
    expect(reached).toContain('Stopu başabaşa taşı');
  });

  it('keeps the read model free of arithmetic', () => {
    const model = mapPaperPosition(paperDto());

    expect(model.realizedGross).toBe('80.00');
    expect(model.realizedNet).toBe('67.77');
    expect(model.unrealizedGross).toBe('85.00');
  });
});
