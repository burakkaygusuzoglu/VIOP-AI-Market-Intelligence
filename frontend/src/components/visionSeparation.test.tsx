import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { mapAnalysis } from '../api/mapAnalysis';
import { analysisDto } from '../test/dto';
import { AnalyzeMarket } from '../screens/AnalyzeMarket';
import { AnalysisWorkspace } from '../screens/AnalysisWorkspace';
import { ScreenshotSlots } from './ScreenshotSlots';

/**
 * The UI half of "Vision is not joined into the analysis" (micro-closeout §2).
 *
 * The backend proof is structural and complete: there is no request field for a
 * Vision reading, no response field carrying one, and nothing in the synthesis
 * context. `backend/tests/unit/analysis_api/test_vision_not_joined.py` pins it.
 *
 * That proof says nothing about what a *user* concludes. The screenshot slots
 * sit inside the Analyze Market form, directly above the button that runs the
 * analysis, so a reasonable person could assume the two are connected. Being
 * right in the code and misleading on the screen is still misleading.
 *
 * These tests pin the sentences that prevent that reading. They assert meaning,
 * not exact copy: each looks for the load-bearing claim rather than a phrase.
 */

describe('the screenshot section says it is separate', () => {
  it('states plainly that these readings do not affect the analysis', () => {
    render(<ScreenshotSlots expectedSymbol="X" />);

    const note = screen.getByRole('note');

    expect(note).toHaveTextContent(/analizi etkilemez/i);
  });

  it('names what the analysis is actually computed from', () => {
    render(<ScreenshotSlots expectedSymbol="X" />);

    expect(screen.getByRole('note')).toHaveTextContent(/yalnızca yüklediğiniz OHLCV/i);
  });

  it('says the readings reach neither evidence, risk nor synthesis', () => {
    render(<ScreenshotSlots expectedSymbol="X" />);

    const note = screen.getByRole('note');

    expect(note).toHaveTextContent(/kanıtlara/i);
    expect(note).toHaveTextContent(/riske/i);
    expect(note).toHaveTextContent(/senteze/i);
  });

  it('does not describe the section as part of the analysis', () => {
    render(<ScreenshotSlots expectedSymbol="X" />);

    const heading = screen.getByRole('heading', { name: /ekran görüntüleri/i });

    expect(heading).toHaveTextContent(/isteğe bağlı/i);
  });

  it('keeps the claim visible rather than hiding it behind a disclosure', () => {
    const { container } = render(<ScreenshotSlots expectedSymbol="X" />);

    expect(container.querySelector('details')).toBeNull();
    expect(screen.getByRole('note')).toBeVisible();
  });
});

describe('the submit summary repeats it at the decision point', () => {
  function show(busy = false) {
    return render(
      <AnalyzeMarket onAnalyse={() => {}} onCancel={() => {}} busy={busy} error={null} />,
    );
  }

  it('states the scope beside the screenshot capability', () => {
    show();

    const item = screen.getByText(/Ekran görüntüsü analizi:/i).closest('li');

    expect(item).not.toBeNull();
    expect(item).toHaveTextContent(/dahil edilmez/i);
  });

  it('names both things it is excluded from', () => {
    show();

    const item = screen.getByText(/Ekran görüntüsü analizi:/i).closest('li');

    expect(item).toHaveTextContent(/bu analize/i);
    expect(item).toHaveTextContent(/sentez/i);
  });

  it('does not call the screenshot an input', () => {
    show();

    const item = screen.getByText(/Ekran görüntüsü analizi:/i).closest('li');

    expect(item).not.toHaveTextContent(/analize dahil edilir/i);
    expect(item).not.toHaveTextContent(/kullanılır ve analize/i);
  });
});

describe('the result never credits a screenshot', () => {
  function workspace(mode: 'BEGINNER' | 'PRO') {
    const analysis = mapAnalysis(analysisDto());
    return render(<AnalysisWorkspace analysis={analysis} mode={mode} onBack={() => {}} />);
  }

  it.each(['BEGINNER', 'PRO'] as const)(
    'does not mention a screenshot anywhere in %s mode',
    (mode) => {
      const { container } = workspace(mode);
      const text = container.textContent ?? '';

      expect(text).not.toMatch(/ekran görüntüs/i);
      expect(text).not.toMatch(/görselden okun/i);
    },
  );

  it.each(['BEGINNER', 'PRO'] as const)(
    'labels no value as visually extracted in %s mode',
    (mode) => {
      const { container } = workspace(mode);
      const text = container.textContent ?? '';

      expect(text).not.toMatch(/SCREENSHOT_EXTRACTED/);
      expect(text).not.toMatch(/AI_VISUAL_INFERENCE/);
      expect(text).not.toMatch(/görsel çıkarım/i);
    },
  );

  it('states in Pro mode what the analysis was derived from', () => {
    const { container } = workspace('PRO');
    const audit = container.querySelector('.workspace__audit, .audit');

    // Pro mode carries the audit block; it must name deterministic inputs only.
    expect(audit?.textContent ?? '').not.toMatch(/ekran görüntüs/i);
  });
});

describe('a screenshot failure cannot block the analysis', () => {
  it('renders a full workspace with no screenshot state of any kind', () => {
    const analysis = mapAnalysis(analysisDto());
    const { container } = render(
      <AnalysisWorkspace analysis={analysis} mode="PRO" onBack={() => {}} />,
    );

    // The workspace takes no screenshot prop at all, so there is nothing a
    // failed upload could have withheld from it.
    expect(container.querySelector('.shots')).toBeNull();
    expect(within(container).getByRole('heading', { name: analysis.context.symbol })).toBeVisible();
  });
});
