import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { AnalyzeMarket } from './AnalyzeMarket';

/**
 * The analyze screen's progress announcement (§12).
 *
 * Measured in a real browser before this existed: pressing Analyse changed the
 * submit button's label to "Analiz ediliyor..." and nothing else. That is a
 * visual cue only - a screen-reader user who had moved past the button heard
 * nothing at all for the whole wait, and nothing when it ended.
 *
 * The fix is a polite live region here for the start, and focus moving to the
 * result heading for the finish (see AnalysisWorkspace.test.tsx), so both ends
 * of the wait are spoken.
 */

function show(busy: boolean) {
  return render(
    <AnalyzeMarket onAnalyse={() => {}} onCancel={() => {}} busy={busy} error={null} />,
  );
}

describe('progress is announced, not only drawn', () => {
  it('says nothing while the form is idle', () => {
    show(false);

    expect(screen.getByRole('status')).toHaveTextContent('');
  });

  it('announces politely once analysis starts', () => {
    show(true);

    const status = screen.getByRole('status');

    expect(status).toHaveTextContent(/analiz ediliyor/i);
    expect(status).toHaveAttribute('aria-live', 'polite');
  });

  it('does not interrupt with an assertive announcement', () => {
    show(true);

    expect(screen.getByRole('status')).not.toHaveAttribute('aria-live', 'assertive');
  });

  it('keeps the announcement out of the visual layout', () => {
    show(true);

    expect(screen.getByRole('status')).toHaveClass('visually-hidden');
  });

  it('still labels the button for sighted users', () => {
    show(true);

    expect(screen.getByRole('button', { name: /analiz ediliyor/i })).toBeDisabled();
  });
});
