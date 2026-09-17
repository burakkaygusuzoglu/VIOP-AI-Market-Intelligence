import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { mapAnalysis } from '../api/mapAnalysis';
import type { AnalysisReadModel } from '../domain/models';
import { analysisDto } from '../test/dto';
import { AnalysisWorkspace } from './AnalysisWorkspace';

/**
 * The workspace, rendered from a real mapped DTO (§47).
 *
 * The model under test comes through `mapAnalysis`, not hand-built, so these
 * exercise the same path the product uses.
 */

function build(mutate: (dto: ReturnType<typeof analysisDto>) => void = () => {}) {
  const dto = analysisDto();
  mutate(dto);
  return mapAnalysis(dto);
}

function show(analysis: AnalysisReadModel, mode: 'BEGINNER' | 'PRO' = 'BEGINNER') {
  return render(<AnalysisWorkspace analysis={analysis} mode={mode} onBack={() => {}} />);
}

describe('safety-critical information leads', () => {
  it('shows the final-state card and the risk card first', () => {
    const { container } = show(build());
    const primary = container.querySelector('.workspace__primary');

    expect(primary?.querySelector('.action-card')).not.toBeNull();
    expect(primary?.querySelector('.risk')).not.toBeNull();
  });

  it('shows a system status rather than an action when synthesis is unconfigured', () => {
    show(build());
    expect(screen.queryByText('BEKLE')).toBeNull();
    expect(screen.queryByText('AL')).toBeNull();
    expect(screen.getByText(/Sistem durumu/)).toBeInTheDocument();
  });

  it('never presents a provider failure as a market verdict', () => {
    show(
      build((dto) => {
        (dto.synthesis as { status: string }).status = 'PROVIDER_FAILURE';
      }),
    );
    for (const word of ['AL', 'SAT', 'BEKLE', 'İŞLEM YOK']) {
      const found = screen.queryAllByText(word);
      // "İŞLEM YOK" may legitimately appear in the allowed-actions list; it
      // must not be the headline.
      for (const node of found) {
        expect(node.className).not.toContain('action-card__headline');
      }
    }
  });

  it('shows risk as unavailable with reasons, not as a zero', () => {
    const { container } = show(build());
    const risk = container.querySelector('.risk');

    expect(risk?.textContent).toContain('Doğrulanmış kontrat bilgisi yok');
    expect(risk?.querySelector('.numeric-fact__value')).toBeNull();
  });
});

describe('the three scenarios stay separate (§31)', () => {
  it('renders all three cases', () => {
    show(build());
    expect(screen.getByText('Yükseliş senaryosu')).toBeInTheDocument();
    expect(screen.getByText('Düşüş senaryosu')).toBeInTheDocument();
    expect(screen.getByText('Nötr senaryo')).toBeInTheDocument();
  });

  it('says outright that they are not shares of a total', () => {
    show(build());
    expect(screen.getByText(/birbirinin yüzdesi değildir/)).toBeInTheDocument();
  });

  it('shows each score against its total, never as a percent', () => {
    const { container } = show(build());
    expect(screen.getByText('72 / 100')).toBeInTheDocument();
    expect(screen.getByText('21 / 100')).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/\b72\s*%/);
  });

  it('gives the neutral case no invented score', () => {
    const { container } = show(build());
    const neutral = container.querySelector('.scenario--neutral');
    expect(neutral?.textContent).toContain('yönlü kalite puanı üretilmez');
  });

  it('shows contradictions rather than averaging them away', () => {
    show(build());
    expect(screen.getByText(/1D ve 1H yönleri uyuşmuyor/)).toBeInTheDocument();
  });

  it('says the challenge is absent rather than inventing one', () => {
    show(build());
    expect(screen.getByText(/model tarafından yazılmış bir karşı görüş yok/)).toBeInTheDocument();
  });
});

describe('the chart draws only supplied prices (§24)', () => {
  it('renders one candle group per supplied bar', () => {
    const { container } = show(build());
    expect(container.querySelectorAll('.chart__candle')).toHaveLength(2);
  });

  it('quotes the exact backend extremes in its description', () => {
    const { container } = show(build());
    const desc = container.querySelector('desc');

    // 99.50 is the low of the first supplied bar; 102.25 the high of the
    // second. Neither is re-rounded and neither was computed here.
    expect(desc?.textContent).toContain('99.50');
    expect(desc?.textContent).toContain('102.25');
  });

  it('contains no price that was not supplied', () => {
    const { container } = show(build());
    const svgText = container.querySelector('svg')?.textContent ?? '';
    expect(svgText).not.toContain('103');
    expect(svgText).not.toContain('98');
  });

  it('gives the chart an accessible name', () => {
    show(build());
    expect(screen.getByRole('img', { name: /mum grafiği/ })).toBeInTheDocument();
  });

  it('renders a support zone band', () => {
    const { container } = show(build());
    expect(container.querySelector('.chart__zone--support')).not.toBeNull();
  });

  it('draws a flat body for a candle that opened and closed at one price', () => {
    /*
     * The geometry invariant, and the reason it is here.
     *
     * A mutation probe scaled the drawn close by 2% and every chart test still
     * passed: they checked the *text* around the chart, and the fabricated
     * price only ever existed as a pixel coordinate. A doji makes the drawing
     * itself testable - if the close is altered by any factor, open and close
     * stop coinciding and the body stops being flat - without this test having
     * to restate the scaling formula.
     */
    const model = build((dto) => {
      const series = dto.chart[0];
      if (!series) return;
      series.candles = [
        {
          open_time: '2026-03-02T10:00:00+00:00',
          open: '100.00',
          high: '104.00',
          low: '96.00',
          close: '100.00',
          volume: '1',
          is_closed: true,
        },
        {
          open_time: '2026-03-02T11:00:00+00:00',
          open: '100.00',
          high: '104.00',
          low: '96.00',
          close: '100.00',
          volume: '1',
          is_closed: true,
        },
      ];
    });
    const { container } = show(model);

    const bodies = [...container.querySelectorAll('.chart__body')];
    expect(bodies).toHaveLength(2);
    for (const body of bodies) {
      expect(Number(body.getAttribute('height'))).toBeLessThanOrEqual(1.001);
    }
  });

  it('places a rising candle body between its own open and close', () => {
    // A body whose top is not the close of a rising bar is drawing a price the
    // payload never contained.
    const { container } = show(build());
    const rising = container.querySelector('.chart__candle--up .chart__body');
    const wick = container.querySelector('.chart__candle--up .chart__wick');

    const bodyTop = Number(rising?.getAttribute('y'));
    const bodyBottom = bodyTop + Number(rising?.getAttribute('height'));
    const high = Number(wick?.getAttribute('y1'));
    const low = Number(wick?.getAttribute('y2'));

    // SVG y grows downward, so the body must sit inside the wick's span.
    expect(bodyTop).toBeGreaterThanOrEqual(high - 0.001);
    expect(bodyBottom).toBeLessThanOrEqual(low + 0.001);
  });
});

describe('beginner and pro', () => {
  it('shows the audit identity only in pro mode', () => {
    // The id appears twice in Pro - once as the analysis id and once as the
    // context digest, which falls back to it when synthesis did not run.
    const { unmount } = show(build(), 'PRO');
    expect(screen.getAllByText(/7acfbbdd12e6/).length).toBeGreaterThan(0);
    unmount();

    show(build(), 'BEGINNER');
    expect(screen.queryAllByText(/7acfbbdd12e6/)).toHaveLength(0);
  });

  it('shows missing information in both modes', () => {
    for (const mode of ['BEGINNER', 'PRO'] as const) {
      const { unmount } = show(build(), mode);
      expect(screen.getByText(/5M verisi sağlanmadı/), mode).toBeInTheDocument();
      unmount();
    }
  });

  it('rounds a calculated indicator and keeps the exact value in pro', () => {
    show(build(), 'PRO');
    expect(screen.getByText('24.66')).toBeInTheDocument();
    expect(screen.getByText('24.658334322196957')).toBeInTheDocument();
  });

  it('shows only the beginner subset of technical readings in beginner mode', () => {
    const { unmount } = show(build(), 'BEGINNER');
    expect(screen.getByText('RSI (14)')).toBeInTheDocument();
    expect(screen.queryByText('EMA 200')).toBeNull();
    unmount();

    show(build(), 'PRO');
    expect(screen.getByText('EMA 200')).toBeInTheDocument();
  });

  it('shows an unavailable indicator as a dash with its reason, never zero', () => {
    const { container } = show(build(), 'PRO');
    const ema = [...container.querySelectorAll('.technical__reading')].find((node) =>
      node.textContent?.includes('EMA 200'),
    );

    expect(ema?.textContent).toContain('—');
    expect(ema?.textContent).toContain('Yeterli mum yok');
  });
});

describe('the ephemeral nature is stated, not implied', () => {
  it('says the analysis is not saved', () => {
    show(build());
    expect(screen.getByText(/kaydedilmez ve sayfa yenilenirse kaybolur/)).toBeInTheDocument();
  });
});

describe('timeframe switching', () => {
  it('offers no switch when only one timeframe was analysed', () => {
    show(build());
    expect(screen.queryByRole('group', { name: 'Grafik zaman dilimi' })).toBeNull();
  });

  it('switches the drawn series when more than one exists', async () => {
    const user = userEvent.setup();
    const model = build((dto) => {
      dto.chart.push({
        timeframe: '1D',
        candles: [
          {
            open_time: '2026-03-01T00:00:00+00:00',
            open: '200.00',
            high: '205.00',
            low: '199.00',
            close: '204.00',
            volume: '10',
            is_closed: true,
          },
        ],
        zones: [],
        overlays: [],
        analysed_count: 1,
        omitted_count: 0,
        omitted_zone_count: 0,
        window_policy: 'latest-400/v1',
      });
    });
    const { container } = show(model);

    await user.click(screen.getByRole('button', { name: '1D' }));
    expect(container.querySelectorAll('.chart__candle')).toHaveLength(1);
  });
});

describe('evidence stays disaggregated', () => {
  it('renders bullish and bearish columns', () => {
    const { container } = show(build());
    const bull = container.querySelector('.evidence__column--bullish');
    const bear = container.querySelector('.evidence__column--bearish');

    expect(within(bull as HTMLElement).getByText('EMA dizilimi yukarı')).toBeInTheDocument();
    expect(within(bear as HTMLElement).getByText('RSI zayıflıyor')).toBeInTheDocument();
  });
});

describe('arriving at a result is announced (§12)', () => {
  /**
   * Measured in a real browser before this was fixed: after submitting the
   * analyze form, `document.activeElement` was `<body>`. The submit button had
   * been unmounted with its form, so the browser reset focus to the document -
   * the next Tab restarted at the top of the page and nothing said an analysis
   * had arrived.
   */

  it('moves focus to the result heading', () => {
    const analysis = build();
    show(analysis);

    const heading = screen.getByRole('heading', { name: analysis.context.symbol });

    expect(document.activeElement).toBe(heading);
  });

  it('does not add the heading to the tab order', () => {
    const analysis = build();
    show(analysis);

    const heading = screen.getByRole('heading', { name: analysis.context.symbol });

    expect(heading).toHaveAttribute('tabindex', '-1');
  });

  it('re-announces when a second analysis replaces the first', () => {
    const first = build();
    const { rerender } = show(first);

    const second = build((dto) => {
      dto.identity.analysis_id = 'ANL-SECOND';
    });
    rerender(<AnalysisWorkspace analysis={second} mode="BEGINNER" onBack={() => {}} />);

    expect(document.activeElement).toBe(
      screen.getByRole('heading', { name: second.context.symbol }),
    );
  });

  it('leaves focus alone once the user has moved on', async () => {
    const user = userEvent.setup();
    show(build());

    const back = screen.getByRole('button', { name: /yeni analiz/i });
    await user.click(back);

    expect(document.activeElement).toBe(back);
  });
});
