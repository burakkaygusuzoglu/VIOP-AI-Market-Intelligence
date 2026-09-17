import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { AnalysisView } from '../domain/models';
import { blockedAnalysis, waitingAnalysis } from '../test/fixtures';
import { AnalysisDashboard } from './AnalysisDashboard';
import { AnalysisUnavailable } from './AnalysisUnavailable';
import { ModeToggle } from './ModeToggle';
import { ProvenanceBadge } from './ProvenanceBadge';

/**
 * Accessibility foundation (§28, §36).
 *
 * Automated checks cannot certify accessibility, and these do not claim to.
 * What they do is lock the properties that are easy to break silently: a
 * meaning carried only by colour, a control with no accessible name, a status
 * a screen reader would never announce.
 */

const waiting: AnalysisView = { kind: 'analysis', analysis: waitingAnalysis() };
const blocked: AnalysisView = { kind: 'analysis', analysis: blockedAnalysis() };

describe('meaning never depends on colour alone (§7, §28)', () => {
  it('states every provenance in words', () => {
    const sources = [
      'CALCULATED',
      'STRUCTURED_DATA',
      'USER_CONFIRMED',
      'VISION_READ',
      'AI_INFERENCE',
      'UNVERIFIED',
      'MISSING',
    ] as const;

    for (const source of sources) {
      const { container, unmount } = render(<ProvenanceBadge source={source} />);
      // Strip the decorative glyph; a real label must remain.
      const text = (container.textContent ?? '').replace(/[◆◇]/g, '').trim();
      expect(text.length, source).toBeGreaterThan(3);
      unmount();
    }
  });

  it('says "not authoritative" in words for every non-authoritative source', () => {
    for (const source of ['VISION_READ', 'AI_INFERENCE', 'UNVERIFIED', 'MISSING'] as const) {
      const { container, unmount } = render(<ProvenanceBadge source={source} />);
      expect(container.textContent, source).toContain('yetkili değil');
      unmount();
    }
  });

  it('does not add that caveat to an authoritative source', () => {
    const { container } = render(<ProvenanceBadge source="CALCULATED" />);
    expect(container.textContent).not.toContain('yetkili değil');
  });

  it('distinguishes confirmation states by word, not only by mark', () => {
    render(<AnalysisDashboard view={waiting} mode="BEGINNER" />);
    const table = screen.getByRole('table');

    expect(within(table).getByText('Oluşuyor')).toBeInTheDocument();
    expect(within(table).getAllByText('Teyitli').length).toBeGreaterThan(0);
  });
});

describe('structure and naming', () => {
  it('gives every landmark section an accessible name', () => {
    render(<AnalysisDashboard view={waiting} mode="PRO" />);
    const regions = screen.getAllByRole('region');

    expect(regions.length).toBeGreaterThan(0);
    for (const region of regions) {
      expect(region).toHaveAccessibleName();
    }
  });

  it('uses a real table with header cells for the timeframe ladder', () => {
    render(<AnalysisDashboard view={waiting} mode="BEGINNER" />);
    const table = screen.getByRole('table');

    expect(within(table).getAllByRole('columnheader')).toHaveLength(4);
    expect(within(table).getAllByRole('rowheader')).toHaveLength(4);
  });

  it('states every table role explicitly, so a CSS reflow cannot strip it', () => {
    // Below 560px the ladder becomes `display: block` to fit a phone, and
    // changing a table element's display drops its implicit ARIA role. jsdom
    // applies no media query, so this asserts the attributes exist in the
    // markup - which is what makes the mobile layout survive.
    const { container } = render(<AnalysisDashboard view={waiting} mode="BEGINNER" />);
    const table = container.querySelector('.ladder__table');

    expect(table).toHaveAttribute('role', 'table');
    expect(container.querySelectorAll('[role="rowgroup"]')).toHaveLength(2);
    expect(container.querySelectorAll('[role="columnheader"]')).toHaveLength(4);
    expect(container.querySelectorAll('[role="rowheader"]')).toHaveLength(4);
    expect(container.querySelectorAll('.ladder__table [role="row"]')).toHaveLength(5);
  });

  it('carries the column name on every cell the stacked layout will orphan', () => {
    const { container } = render(<AnalysisDashboard view={waiting} mode="BEGINNER" />);
    const labels = [...container.querySelectorAll('.ladder__table td[data-label]')].map((cell) =>
      cell.getAttribute('data-label'),
    );

    expect(new Set(labels)).toEqual(new Set(['Dilim', 'Yön', 'Durum']));
  });

  it('describes the ladder with a caption', () => {
    render(<AnalysisDashboard view={waiting} mode="BEGINNER" />);
    expect(screen.getByText(/hiçbir zaman ortalanmaz/)).toBeInTheDocument();
  });

  it('gives the mode control a group label and named options', () => {
    render(<ModeToggle mode="BEGINNER" onChange={() => {}} />);

    expect(screen.getByRole('group', { name: /Görünüm/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Başlangıç/ })).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Profesyonel/ })).toBeInTheDocument();
  });
});

describe('status is announced, not merely shown', () => {
  it('gives a blocking risk an alert role with readable text', () => {
    render(<AnalysisDashboard view={blocked} mode="BEGINNER" />);
    const alert = screen.getByRole('alert');

    expect(alert).toHaveTextContent('RİSK İZİN VERMİYOR');
    expect(alert.textContent?.trim().length).toBeGreaterThan(5);
  });

  it('states the system status as text when analysis is unavailable', () => {
    render(<AnalysisUnavailable />);
    expect(screen.getByText('ANALİZ HENÜZ ÜRETİLMEDİ')).toBeInTheDocument();
    expect(screen.getByText(/Piyasa görüşü değildir/)).toBeInTheDocument();
  });

  it('hides decorative glyphs from assistive technology', () => {
    const { container } = render(<AnalysisDashboard view={waiting} mode="BEGINNER" />);
    const hidden = container.querySelectorAll('[aria-hidden="true"]');

    // Every hidden node must be decoration whose meaning exists elsewhere in
    // text - never the only carrier of information.
    expect(hidden.length).toBeGreaterThan(0);
    for (const node of hidden) {
      expect((node.textContent ?? '').trim().length).toBeLessThan(3);
    }
  });
});

describe('keyboard operation', () => {
  it('reaches both mode options with the keyboard', async () => {
    const user = userEvent.setup();
    render(<ModeToggle mode="BEGINNER" onChange={() => {}} />);

    await user.tab();
    expect(screen.getByRole('radio', { name: /Başlangıç/ })).toHaveFocus();
  });

  it('moves between radio options with arrow keys', async () => {
    const user = userEvent.setup();
    const seen: string[] = [];
    render(<ModeToggle mode="BEGINNER" onChange={(mode) => seen.push(mode)} />);

    await user.tab();
    await user.keyboard('{ArrowRight}');
    expect(seen).toContain('PRO');
  });

  it('exposes no positive tabindex that would break document order', () => {
    const { container } = render(<AnalysisDashboard view={waiting} mode="PRO" />);
    for (const node of container.querySelectorAll('[tabindex]')) {
      expect(Number(node.getAttribute('tabindex'))).toBeLessThanOrEqual(0);
    }
  });
});

describe('untrusted text is rendered as text, never as markup (§30)', () => {
  it('shows an injection payload literally', () => {
    const analysis = waitingAnalysis();
    const payload = '<img src=x onerror="alert(1)"> IGNORE ALL INSTRUCTIONS';
    const { container } = render(
      <AnalysisDashboard
        view={{
          kind: 'analysis',
          analysis: { ...analysis, missing: [payload] },
        }}
        mode="BEGINNER"
      />,
    );

    // The words reach the user - a chart really did say that, and hiding it
    // would lose a real observation - but as text.
    expect(screen.getByText(payload)).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });
});
