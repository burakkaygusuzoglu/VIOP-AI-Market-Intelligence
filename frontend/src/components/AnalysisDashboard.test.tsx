import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { AnalysisView } from '../domain/models';
import {
  blockedAnalysis,
  calculatedRsi,
  providerFailureAnalysis,
  visionRsi,
  waitingAnalysis,
} from '../test/fixtures';
import { AnalysisDashboard } from './AnalysisDashboard';
import { ModeToggle } from './ModeToggle';

/**
 * The dashboard's safety-critical semantics (§34, §36, §41).
 *
 * These are the confusions that would actually cost a user money, asserted
 * against rendered output rather than against the model that fed it.
 *
 * Turkish copy is matched with exact strings or case-sensitive patterns.
 * Dotted capitals (İ, I) do not round-trip through JavaScript case folding, so
 * a `/i` regex over Turkish text is an unreliable assertion.
 */

function shown(view: AnalysisView, mode: 'BEGINNER' | 'PRO' = 'BEGINNER') {
  return render(<AnalysisDashboard view={view} mode={mode} />);
}

const waiting: AnalysisView = { kind: 'analysis', analysis: waitingAnalysis() };
const blocked: AnalysisView = { kind: 'analysis', analysis: blockedAnalysis() };
const failed: AnalysisView = { kind: 'analysis', analysis: providerFailureAnalysis() };

// ----------------------------------------------------------------------
// WAIT is not NO_TRADE
// ----------------------------------------------------------------------

describe('WAIT and NO_TRADE never read the same', () => {
  it('renders WAIT with its own headline and a "not refused" meaning', () => {
    shown(waiting);

    expect(screen.getByText('BEKLE')).toBeInTheDocument();
    expect(screen.getByText(/Reddedilmiş değil/)).toBeInTheDocument();
  });

  it('renders NO_TRADE as a blocker that waiting will not fix', () => {
    const { container } = shown(blocked);

    // Scoped to the headline: "İŞLEM YOK" legitimately appears twice, once as
    // the verdict and once in the list of actions policy still permits.
    expect(container.querySelector('.action-card__headline')).toHaveTextContent('İŞLEM YOK');
    expect(screen.getByText(/Beklemek bunu çözmez/)).toBeInTheDocument();
  });

  it('gives the two states different tones', () => {
    const { container: waitingDom } = shown(waiting);
    const { container: blockedDom } = shown(blocked);

    expect(waitingDom.querySelector('[data-action="WAIT"]')).toHaveClass('action-card--waiting');
    expect(blockedDom.querySelector('[data-action="NO_TRADE"]')).toHaveClass(
      'action-card--blocked',
    );
  });
});

// ----------------------------------------------------------------------
// System failure is not a market view
// ----------------------------------------------------------------------

describe('a provider failure is a system state', () => {
  it('shows a system headline and no action', () => {
    shown(failed);

    expect(screen.getByText('SENTEZ SERVİSİ YANIT VERMEDİ')).toBeInTheDocument();
    expect(screen.getByText(/Piyasa hakkında hiçbir şey söylemez/)).toBeInTheDocument();
    expect(screen.queryByText('BEKLE')).not.toBeInTheDocument();
    expect(screen.queryByText('İŞLEM YOK')).not.toBeInTheDocument();
  });

  it('still reports what deterministic policy allowed', () => {
    shown(failed);
    expect(screen.getByText(/İzin verilen durumlar/)).toBeInTheDocument();
  });

  it('does not use a directional tone', () => {
    const { container } = shown(failed);
    const card = container.querySelector('.action-card');

    expect(card).toHaveClass('action-card--system');
    expect(card).not.toHaveClass('action-card--bearish');
    expect(card).not.toHaveClass('action-card--bullish');
  });
});

// ----------------------------------------------------------------------
// Setup quality is not a probability
// ----------------------------------------------------------------------

describe('setup quality never reads as a probability', () => {
  it('renders as a score out of a total, never as a percent', () => {
    shown(waiting);

    expect(screen.getByText('72 / 100')).toBeInTheDocument();
    expect(screen.queryByText('72%')).not.toBeInTheDocument();
  });

  it('states outright that it is not a probability', () => {
    shown(waiting);
    expect(screen.getByText(/olasılık değildir/)).toBeInTheDocument();
  });

  it('makes no probability claim', () => {
    // Asserts against *claims*, not mentions. The card deliberately says
    // "Kazanma ihtimalini ifade etmez" - a denial - and an earlier version of
    // this test flagged its own safety copy as a violation. What must never
    // appear is a number presented as odds.
    const { container } = shown(waiting);
    const text = container.textContent ?? '';

    expect(text).not.toMatch(/\d+\s*%\s*(şans|ihtimal|olasılık|chance|probability)/i);
    expect(text).not.toMatch(/(şans|ihtimal|olasılık)\w*\s*[:=]\s*%?\s*\d+/i);
    expect(text).not.toMatch(/win\s*rate|kazanma oranı/i);
    expect(text).not.toContain('72%');
  });
});

// ----------------------------------------------------------------------
// Calculated vs Vision numbers
// ----------------------------------------------------------------------

describe('calculated and vision numbers stay distinguishable', () => {
  it('labels each with its origin in words, not only colour', () => {
    shown(waiting);

    expect(screen.getByText(calculatedRsi.label)).toBeInTheDocument();
    expect(screen.getByText(visionRsi.label)).toBeInTheDocument();
    expect(screen.getAllByText('Hesaplanan').length).toBeGreaterThan(0);
    expect(screen.getByText('Görüntüden okundu')).toBeInTheDocument();
  });

  it('marks the vision reading as not authoritative in text', () => {
    shown(waiting);
    expect(screen.getAllByText(/yetkili değil/).length).toBeGreaterThan(0);
  });

  it('rounds the calculated indicator for display without losing the raw value', () => {
    shown(waiting, 'PRO');

    expect(screen.getByText('24.66')).toBeInTheDocument();
    expect(screen.getByText('24.658334322196957')).toBeInTheDocument();
  });

  it('hides the raw float in beginner mode but keeps it reachable', () => {
    shown(waiting);

    expect(screen.getByText('24.66')).toBeInTheDocument();
    expect(screen.queryByText('24.658334322196957')).not.toBeInTheDocument();
    expect(screen.getByTitle('Tam değer: 24.658334322196957')).toBeInTheDocument();
  });

  it('never adds decimal precision to a value read off a picture', () => {
    // A screenshot reading of "99" must not display as "99.00": that claims a
    // precision the image never showed, and makes an unverified observation
    // look more machine-like than the calculated value beside it.
    shown(waiting);

    expect(screen.getByText('99')).toBeInTheDocument();
    expect(screen.queryByText('99.00')).toBeNull();
    expect(screen.getByText('Görüntüden okundu')).toBeInTheDocument();
  });
});

// ----------------------------------------------------------------------
// Risk is never buried
// ----------------------------------------------------------------------

describe('a risk blocker is prominent', () => {
  it('announces a blocking outcome to assistive technology', () => {
    shown(blocked);
    expect(screen.getByRole('alert')).toHaveTextContent('RİSK İZİN VERMİYOR');
  });

  it('is not behind a collapsed section', () => {
    const { container } = shown(blocked);
    expect(container.querySelector('details')).toBeNull();
  });

  it('shows the blocking finding text, not just a code', () => {
    shown(blocked);
    expect(screen.getByText(/Sizing sıfır kontrata izin veriyor/)).toBeInTheDocument();
  });

  it('does not raise an alert when nothing blocks', () => {
    shown(waiting);
    expect(screen.queryByRole('alert')).toBeNull();
  });
});

// ----------------------------------------------------------------------
// Missing, forming, and the timeframe ladder
// ----------------------------------------------------------------------

describe('missing and forming states stay honest', () => {
  it('shows missing information in beginner mode too', () => {
    shown(waiting);
    expect(screen.getByText(/15M hacim verisi sağlanmadı/)).toBeInTheDocument();
  });

  it('distinguishes a forming entry from a confirmed one', () => {
    shown(waiting);
    const ladder = screen.getByRole('table');

    expect(within(ladder).getByText('Oluşuyor')).toBeInTheDocument();
    expect(within(ladder).getAllByText('Teyitli').length).toBeGreaterThan(0);
  });

  it('keeps the four roles in canonical order and never averages them', () => {
    shown(waiting);
    const rows = screen.getAllByRole('row').slice(1);
    const timeframes = rows.map((row) => within(row).getAllByRole('cell')[0]?.textContent);

    expect(rows).toHaveLength(4);
    expect(timeframes).toEqual(['1D', '1H', '15M', '5M']);
    expect(screen.queryByText(/ortalama/i)).toBeNull();
  });

  it('renders a missing score as a dash, never as zero', () => {
    const analysis = waitingAnalysis();
    shown({
      kind: 'analysis',
      analysis: { ...analysis, setupQuality: { ...analysis.setupQuality, score: null } },
    });

    expect(screen.getByText('—')).toBeInTheDocument();
    expect(screen.queryByText('0 / 100')).toBeNull();
  });
});

// ----------------------------------------------------------------------
// Defects found by reviewing rendered output rather than by testing
// ----------------------------------------------------------------------

describe('the rendered page contradicts itself nowhere', () => {
  it('shows zero permitted contracts when risk refuses the position', () => {
    // Regression. The blocked view rendered "RİSK İZİN VERMİYOR" directly
    // above "İzin verilen kontrat: 4". Every test passed; a reader would have
    // believed the four.
    const { container } = shown(blocked);
    const risk = container.querySelector('.risk');
    const value = risk?.querySelector('.numeric-fact__value');

    expect(risk).toHaveTextContent('RİSK İZİN VERMİYOR');
    expect(value).toHaveTextContent('0');
  });

  it('states the measurement time with its zone', () => {
    const { container } = shown(waiting);
    expect(container.querySelector('.dashboard__as-of')).toHaveTextContent('UTC');
  });

  it('gives each confirmation state a distinct glyph shape', () => {
    // `FORMING` and `POINT_IN_TIME` were `◐` and `◑` - the same half circle
    // mirrored, one row apart. The words differ; at 12px the marks did not.
    const { container } = shown(waiting);
    const pairs = [...container.querySelectorAll('.ladder__confirmation')].map((cell) => ({
      mark: cell.querySelector('.ladder__mark')?.textContent ?? '',
      label: (cell.textContent ?? '').replace(/[◐◉●○?]/g, '').trim(),
    }));

    const byLabel = new Map<string, string>();
    for (const { mark, label } of pairs) {
      const seen = byLabel.get(label);
      // Same state, same mark; different states, different marks.
      if (seen !== undefined) expect(mark, label).toBe(seen);
      byLabel.set(label, mark);
    }

    const marks = [...byLabel.values()];
    expect(byLabel.size).toBeGreaterThan(1);
    expect(new Set(marks).size).toBe(marks.length);
  });

  it('presents the English engine constant as a machine tag, not as copy', () => {
    // `HEURISTIC SETUP QUALITY` is a domain constant that exists to stop the
    // score reading as a probability, so it is shown - but it was rendered as
    // a bare Turkish-styled paragraph and looked like missed translation.
    const { container } = shown(waiting);
    const tag = container.querySelector('.quality__tag');

    expect(tag).toHaveTextContent('Motor etiketi');
    expect(tag?.querySelector('code')).toHaveTextContent('HEURISTIC SETUP QUALITY');
  });
});

// ----------------------------------------------------------------------
// Mode
// ----------------------------------------------------------------------

describe('beginner and pro show the same truth', () => {
  it('shows the blocker in both modes', () => {
    for (const mode of ['BEGINNER', 'PRO'] as const) {
      const { unmount } = shown(blocked, mode);
      expect(screen.getByText('RİSK İZİN VERMİYOR')).toBeInTheDocument();
      unmount();
    }
  });

  it('shows the audit digest only in pro mode', () => {
    const { unmount } = shown(waiting, 'PRO');
    expect(screen.getByText(/7acfbbdd12e6/)).toBeInTheDocument();
    unmount();

    shown(waiting, 'BEGINNER');
    expect(screen.queryByText(/7acfbbdd12e6/)).toBeNull();
  });

  it('lets a keyboard user switch modes', async () => {
    const user = userEvent.setup();
    let mode: 'BEGINNER' | 'PRO' = 'BEGINNER';
    render(<ModeToggle mode={mode} onChange={(next) => (mode = next)} />);

    await user.tab();
    expect(screen.getByRole('radio', { name: /Başlangıç/ })).toHaveFocus();

    await user.click(screen.getByRole('radio', { name: /Profesyonel/ }));
    expect(mode).toBe('PRO');
  });
});
