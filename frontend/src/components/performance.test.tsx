import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { filtersToQuery } from '../api/performance';
import {
  coverageSentence,
  metricText,
  percentText,
  streakSentence,
  timelineRows,
} from '../domain/performance';
import { PerformanceScreen } from '../screens/Performance';
import {
  breakdownsDto,
  emptyPerformanceDto,
  journalPageDto,
  performanceDto,
  unavailable,
} from '../test/performanceDto';
import { JournalEntry, JournalPanel } from './JournalPanel';
import { PerformanceBreakdowns } from './PerformanceBreakdowns';
import { PerformanceOverview } from './PerformanceOverview';

/**
 * The performance and journal UI (Phase 10).
 *
 * The screen must print what the server computed, say why a metric is missing
 * rather than showing a zero, keep a person's note visibly theirs, and never
 * show a filtered chart beside unfiltered headline numbers.
 */

/** The first item, or a failed test - clearer than a non-null assertion. */
function first<T>(items: readonly T[]): T {
  const item = items[0];
  if (item === undefined) throw new Error('expected at least one item');
  return item;
}

function withQuery(ui: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function respond(url: string): Response {
  const body = url.includes('/breakdowns')
    ? breakdownsDto()
    : url.includes('/journal/tags')
      ? { items: [{ tag: 'breakout', positions: 2 }], limit: 100 }
      : url.includes('/journal')
        ? journalPageDto()
        : performanceDto();
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('performance overview', () => {
  it('prints the server’s own numbers, including one that does not add up', () => {
    const dto = performanceDto();
    render(<PerformanceOverview dto={dto} mode="BEGINNER" />);

    // gross 60.00 and fees 16 would give 44 if the UI did arithmetic; the
    // server says net is unavailable, and that is what must appear.
    const metrics = screen.getByText('Tamamlanan işlemlerin brüt K/Z').closest('.perf-metrics');
    expect(within(metrics as HTMLElement).getByText('60.00')).toBeInTheDocument();
    expect(within(metrics as HTMLElement).getByText('16')).toBeInTheDocument();
    expect(screen.queryByText('44')).not.toBeInTheDocument();
    expect(screen.queryByText('44.00')).not.toBeInTheDocument();
    expect(screen.getAllByText(/Kısmi kapsam/).length).toBeGreaterThan(0);
  });

  it('shows a win rate with its numerator and denominator', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    expect(screen.getByText('%33.3333')).toBeInTheDocument();
    expect(screen.getByText('1 / 3 tamamlanan işlem')).toBeInTheDocument();
  });

  it('never renders an unavailable metric as zero', () => {
    const dto = performanceDto({
      profit_factor: unavailable('no losing completed position, so the ratio has no finite value'),
    });
    render(<PerformanceOverview dto={dto} mode="BEGINNER" />);

    const card = screen.getByText('Kâr faktörü').closest('.perf-metric');
    expect(card).not.toBeNull();
    expect(within(card as HTMLElement).queryByText('0')).not.toBeInTheDocument();
    expect(
      within(card as HTMLElement).getByText(/no losing completed position/),
    ).toBeInTheDocument();
  });

  it('tells a person there is nothing to measure yet rather than 0%', () => {
    render(<PerformanceOverview dto={emptyPerformanceDto()} mode="BEGINNER" />);

    expect(screen.getAllByText(/Henüz tamamlanmış işlem yok/).length).toBeGreaterThan(0);
    expect(screen.queryByText('%0')).not.toBeInTheDocument();
    expect(screen.queryByText('%0.0000')).not.toBeInTheDocument();
  });

  it('keeps realized and unrealized apart', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    expect(screen.getByText('Tamamlanan işlemlerin brüt K/Z')).toBeInTheDocument();
    expect(screen.getByText('Açık pozisyonların gerçekleşmemiş K/Z')).toBeInTheDocument();
    expect(screen.getByText('85.00')).toBeInTheDocument();
  });

  it('calls the curve cumulative realized P&L, not account equity', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    expect(screen.getByText('Kümülatif gerçekleşen K/Z')).toBeInTheDocument();
    expect(screen.getByText(/hesap bakiyesi eğrisi değil/)).toBeInTheDocument();
  });

  it('gives the timeline a textual equivalent, row by row', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    const table = screen.getByRole('table', { name: /Her satır bir tamamlanan pozisyondur/ });
    const rows = within(table).getAllByRole('row');
    expect(rows).toHaveLength(4); // header plus three points
    expect(within(table).getAllByText('100.00').length).toBe(2); // amount and cumulative
    expect(within(table).getAllByText('60.00').length).toBe(2);
    expect(
      screen.getByRole('img', { name: /Kümülatif gerçekleşen K\/Z eğrisi/ }),
    ).toBeInTheDocument();
  });

  it('shows Pro detail without changing any value', () => {
    const beginner = render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);
    const beginnerText = beginner.container.textContent ?? '';
    beginner.unmount();

    render(<PerformanceOverview dto={performanceDto()} mode="PRO" />);

    for (const value of ['60.00', '%33.3333', '20.0000', '2.5000', '40.00']) {
      expect(screen.getAllByText(value).length).toBeGreaterThan(0);
      expect(beginnerText).toContain(value);
    }
    expect(screen.getByText(/BREAKEVEN_BREAKS_BOTH_STREAKS/)).toBeInTheDocument();
    expect(screen.getByText(/PAPER_SIMULATION/)).toBeInTheDocument();
  });

  it('names the metrics this data model cannot produce', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="PRO" />);

    for (const label of ['MAE', 'MFE', 'Sharpe', 'Sortino', 'Yüzde düşüş']) {
      expect(screen.getByText(`${label}:`)).toBeInTheDocument();
    }
    expect(screen.getAllByText(/Bu veri modelinde yok/).length).toBeGreaterThan(0);
    expect(screen.getByText(/no analysis snapshot is persisted/)).toBeInTheDocument();
  });

  it('states which population and basis the numbers describe', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    expect(screen.getByText(/brüt \(ücretler hariç\)/)).toBeInTheDocument();
    expect(
      screen.getByText(/Tamamlanan 3 işlemin 1 tanesinde ücret modellendi/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Girilmeyen \(bekleyen, iptal, reddedilen\)/)).toBeInTheDocument();
  });

  it('uses no advice or prediction language', () => {
    const { container } = render(<PerformanceOverview dto={performanceDto()} mode="PRO" />);
    const text = (container.textContent ?? '').toLocaleLowerCase('tr');

    for (const banned of [
      'daha fazla işlem yap',
      'kazanacak',
      'riski artır',
      'tavsiye',
      'garanti',
      'en iyi enstrüman',
    ]) {
      expect(text).not.toContain(banned);
    }
  });
});

describe('realized accounting beside trade statistics', () => {
  it('shows money realized by positions that have not finished', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    const section = screen.getByRole('region', {
      name: 'Gerçekleşen para (kapanmamış pozisyonlar dahil)',
    });
    expect(within(section).getByText('145.00')).toBeInTheDocument();
    expect(
      within(section).getByText(/6 dolum · 3 tamamlanan \+ 1 açık pozisyon/),
    ).toBeInTheDocument();
  });

  it('says plainly that accounting is not a trade statistic', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    expect(screen.getByText(/tamamlanan işlem istatistiği değildir/)).toBeInTheDocument();
    expect(screen.getByText(/Her dolum kendi piyasa zamanına aittir/)).toBeInTheDocument();
  });

  it('keeps completed-trade money under its own label', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    // 60.00 is the completed-trade total; 145.00 is every fill so far.
    const completed = screen.getByText('Tamamlanan işlemlerin brüt K/Z').closest('.perf-metric');
    expect(within(completed as HTMLElement).getByText('60.00')).toBeInTheDocument();
    expect(within(completed as HTMLElement).queryByText('145.00')).not.toBeInTheDocument();
  });

  it('does not invent a net for partly costed fills', () => {
    render(<PerformanceOverview dto={performanceDto()} mode="BEGINNER" />);

    const net = screen.getByText('Gerçekleşen net (tüm dolumlar)').closest('.perf-metric');
    expect(within(net as HTMLElement).getByText('—')).toBeInTheDocument();
    expect(within(net as HTMLElement).getByText(/only 2 of 6 fills/)).toBeInTheDocument();
  });
});

describe('breakdowns', () => {
  it('shows long and short with the same columns', () => {
    render(<PerformanceBreakdowns dto={breakdownsDto()} />);

    const table = screen.getByRole('table', { name: 'Yöne göre' });
    expect(within(table).getByText('Uzun (LONG)')).toBeInTheDocument();
    expect(within(table).getByText('Kısa (SHORT)')).toBeInTheDocument();
  });

  it('shows a group whose net is unknown as unknown', () => {
    render(<PerformanceBreakdowns dto={breakdownsDto()} />);

    expect(screen.getAllByText('Hesaplanamıyor').length).toBeGreaterThan(0);
  });

  it('lists the breakdowns that cannot exist', () => {
    render(<PerformanceBreakdowns dto={breakdownsDto()} />);

    expect(screen.getByText('setup: no persisted analysis linkage')).toBeInTheDocument();
    expect(screen.getByText('regime: no persisted analysis linkage')).toBeInTheDocument();
  });

  it('marks an incomplete breakdown as incomplete', () => {
    const dto = breakdownsDto();
    render(
      <PerformanceBreakdowns
        dto={{
          ...dto,
          by_instrument: { ...dto.by_instrument, total: 140, omitted: 139, is_complete: false },
        }}
      />,
    );

    expect(screen.getByText(/140 gruptan 1 tanesi gösteriliyor, 139/)).toBeInTheDocument();
    expect(screen.getByText(/Üstteki toplamlar yine tüm seçimi kapsar/)).toBeInTheDocument();
  });

  it('reports an empty group honestly', () => {
    render(
      <PerformanceBreakdowns
        dto={breakdownsDto({
          by_instrument: { rows: [], total: 0, returned: 0, omitted: 0, is_complete: true },
        })}
      />,
    );

    expect(screen.getByText('Bu kırılım için tamamlanmış işlem yok.')).toBeInTheDocument();
  });
});

describe('journal', () => {
  it('separates simulated facts from the person’s own writing', () => {
    const page = journalPageDto();
    withQuery(<JournalPanel rows={page.items} total={page.total} />);

    expect(screen.getByText('SİMÜLASYON KAYDI')).toBeInTheDocument();
    expect(screen.getByText('SİZİN NOTUNUZ')).toBeInTheDocument();
    expect(screen.getByLabelText(/Not \(yalnızca sizin yazdığınız metin\)/)).toHaveValue(
      'Plana sadık kaldım.',
    );
  });

  it('renders a hostile note as text rather than markup', () => {
    const page = journalPageDto();
    const row = first(page.items);
    const hostile = [
      {
        ...row,
        annotation: {
          ...row.annotation,
          note: '<script>alert(1)</script><img src=x onerror=alert(1)>',
        },
      },
    ];

    const { container } = withQuery(<JournalPanel rows={hostile} total={1} />);

    expect(container.querySelector('script')).toBeNull();
    expect(container.querySelector('img')).toBeNull();
    expect(screen.getByLabelText(/Not \(yalnızca/)).toHaveValue(
      '<script>alert(1)</script><img src=x onerror=alert(1)>',
    );
  });

  it('shows a position’s own outcome facts, gross and net', () => {
    const page = journalPageDto();
    withQuery(<JournalPanel rows={page.items} total={page.total} />);

    const facts = screen.getByText('Kendi sonucu (brüt / net)').closest('div');
    expect(within(facts as HTMLElement).getByText('Kazanç / Bilinmiyor')).toBeInTheDocument();
  });

  it('says a tag is the person’s own classification', () => {
    const page = journalPageDto();
    withQuery(<JournalPanel rows={page.items} total={page.total} />);

    expect(
      screen.getByText(/Etiket sizin sınıflandırmanızdır; sistemin doğruladığı/),
    ).toBeInTheDocument();
  });

  it('sends the version it read and reports a conflict honestly', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: 'JOURNAL_VERSION_CONFLICT',
            kind: 'CONFLICT',
            detail: 'this note was version 3 when the change arrived, not 2',
          },
        }),
        { status: 409, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    const page = journalPageDto();
    withQuery(<JournalEntry row={first(page.items)} />);

    await userEvent.click(screen.getByRole('button', { name: 'Notu kaydet' }));

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent('this note was version 3');
    const [, request] = first(fetchMock.mock.calls);
    expect(JSON.parse(String(request.body)).expected_version).toBe(2);
    expect(request.method).toBe('PUT');
  });

  it('sends only a note, tags and a version', async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify(first(journalPageDto().items).annotation), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    );
    const page = journalPageDto();
    withQuery(<JournalEntry row={first(page.items)} />);

    await userEvent.click(screen.getByRole('button', { name: 'Notu kaydet' }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const body = JSON.parse(String(first(fetchMock.mock.calls)[1].body));
    expect(Object.keys(body).sort()).toEqual(['expected_version', 'note', 'tags']);
  });

  it('shows the position’s money exactly as the server sent it', () => {
    const page = journalPageDto();
    withQuery(<JournalPanel rows={page.items} total={page.total} />);

    expect(screen.getByText('100.00 / Modellenmedi / Bilinmiyor')).toBeInTheDocument();
  });
});

describe('the performance screen', () => {
  it('sends one set of filters to every view', async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(respond(String(url))));
    withQuery(<PerformanceScreen mode="BEGINNER" onBack={() => {}} />);

    await screen.findByText('Simülasyon performansı');
    await userEvent.selectOptions(screen.getByLabelText('Yön'), 'LONG');

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('/paper/performance?direction=LONG'))).toBe(true);
    });

    await userEvent.click(screen.getByRole('tab', { name: 'Kırılımlar' }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('/paper/performance/breakdowns?direction=LONG'))).toBe(
        true,
      );
    });

    await userEvent.click(screen.getByRole('tab', { name: 'Günlük' }));

    await waitFor(() => {
      const urls = fetchMock.mock.calls.map((call) => String(call[0]));
      expect(urls.some((url) => url.includes('/paper/journal?direction=LONG'))).toBe(true);
    });
  });

  it('says plainly that the performance is simulated', async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(respond(String(url))));
    withQuery(<PerformanceScreen mode="BEGINNER" onBack={() => {}} />);

    expect(screen.getByText('KAĞIT İŞLEM PERFORMANSI — SİMÜLASYON')).toBeInTheDocument();
    expect(screen.getByRole('note', { name: 'Simülasyon uyarısı' })).toBeInTheDocument();
    expect(screen.getByText(/Borsa ya da aracı kurum tarafından doğrulanmış/)).toBeInTheDocument();
  });

  it('explains the date-range rule beside the filters', async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(respond(String(url))));
    withQuery(<PerformanceScreen mode="BEGINNER" onBack={() => {}} />);

    expect(screen.getByText(/piyasa zamanındaki kapanışına/)).toBeInTheDocument();
  });

  it('labels every filter control', async () => {
    fetchMock.mockImplementation((url: string) => Promise.resolve(respond(String(url))));
    withQuery(<PerformanceScreen mode="BEGINNER" onBack={() => {}} />);

    for (const label of [
      'Başlangıç (UTC)',
      'Bitiş (UTC)',
      'Yön',
      'Zaman dilimi',
      'Sembol',
      'Etiket',
    ]) {
      expect(screen.getByLabelText(label)).toBeInTheDocument();
    }
  });

  it('surfaces a server refusal instead of showing numbers', async () => {
    fetchMock.mockResolvedValue(
      new Response(
        JSON.stringify({
          detail: {
            code: 'ANALYSIS_RANGE_TOO_LARGE',
            kind: 'TOO_LARGE',
            detail: '5000 positions match; narrow the date range',
          },
        }),
        { status: 413, headers: { 'Content-Type': 'application/json' } },
      ),
    );
    withQuery(<PerformanceScreen mode="BEGINNER" onBack={() => {}} />);

    await waitFor(() => expect(screen.getByRole('alert')).toBeInTheDocument());
    expect(screen.getByRole('alert')).toHaveTextContent('narrow the date range');
  });
});

describe('the frontend computes nothing financial', () => {
  it('formats amounts without touching them', () => {
    const dto = performanceDto();

    expect(metricText(dto.realized_gross)).toBe('60.00');
    expect(percentText(dto.win_rate)).toBe('%33.3333');
    expect(metricText(dto.realized_net)).toBe('—');
    expect(timelineRows(dto).map((row) => row.cumulative)).toEqual(['100.00', '60.00', '60.00']);
  });

  it('describes coverage and streaks from the server’s counts', () => {
    expect(coverageSentence(performanceDto())).toContain('1 tanesinde ücret modellendi');
    expect(streakSentence(performanceDto()).text).toBe('Şu anda 1 kazançlı işlemlik seri.');
    expect(streakSentence(emptyPerformanceDto()).text).toBe('Şu anda açık bir seri yok.');
  });

  it('builds query strings from filters only', () => {
    expect(filtersToQuery({ direction: 'LONG', tag: 'breakout' })).toBe(
      '?direction=LONG&tag=breakout',
    );
    expect(filtersToQuery({})).toBe('');
  });
});
