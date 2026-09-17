import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { EvidenceItem } from '../domain/models';
import { evidence } from '../test/fixtures';
import { EvidencePanel } from './EvidencePanel';

/**
 * Evidence must stay disaggregated.
 *
 * The failure this guards against is not a crash: it is a future refactor that
 * "helpfully" nets the two sides into one score, which reads as certainty and
 * throws away what disagrees.
 */

function panel(items: readonly EvidenceItem[] = evidence, showRaw = false) {
  return render(<EvidencePanel evidence={items} showRaw={showRaw} />);
}

describe('for and against stay separate', () => {
  it('renders a bullish and a bearish column', () => {
    panel();
    expect(screen.getByText(/Yükseliş yönünde/)).toBeInTheDocument();
    expect(screen.getByText(/Düşüş yönünde/)).toBeInTheDocument();
  });

  it('places each item under its own direction', () => {
    const { container } = panel();
    const bull = container.querySelector('.evidence__column--bullish');
    const bear = container.querySelector('.evidence__column--bearish');

    expect(within(bull as HTMLElement).getByText('EMA dizilimi yukarı')).toBeInTheDocument();
    expect(within(bear as HTMLElement).queryByText('EMA dizilimi yukarı')).toBeNull();
  });

  it('states outright that the sides do not cancel out', () => {
    panel();
    expect(screen.getByText(/birbirini götürmez/)).toBeInTheDocument();
    expect(screen.getByText(/Tek bir puana indirgenmez/)).toBeInTheDocument();
  });

  it('shows no net or aggregate score', () => {
    const { container } = panel();
    const text = container.textContent ?? '';

    expect(text).not.toMatch(/net (skor|puan)/i);
    expect(text).not.toMatch(/toplam puan/i);
    expect(text).not.toMatch(/\d+\s*%/);
  });
});

describe('strength is a word, never a weight', () => {
  it('renders the strength label in Turkish', () => {
    panel();
    expect(screen.getAllByText('güçlü').length).toBeGreaterThan(0);
  });

  it('does not render strength as a number', () => {
    const { container } = panel();
    expect(container.textContent).not.toMatch(/güç(lü)?\s*[:=]\s*\d/);
  });
});

describe('provenance travels with each reason', () => {
  it('labels a calculated item as calculated', () => {
    const { container } = panel();
    const first = container.querySelector('.evidence__item');
    expect(within(first as HTMLElement).getByText('Hesaplanan')).toBeInTheDocument();
  });

  it('marks a model-inferred reason as not authoritative', () => {
    const inferred: EvidenceItem = {
      id: 'EV-TEST-0000000000',
      direction: 'BEARISH',
      strength: 'WEAK',
      category: 'PATTERN',
      reason: 'Modelin grafikten çıkardığı bir izlenim',
      timeframe: '15M',
      confirmation: 'FORMING',
      source: 'AI_INFERENCE',
    };
    panel([inferred]);
    expect(screen.getByText(/yetkili değil/)).toBeInTheDocument();
  });
});

describe('empty states are honest', () => {
  it('renders nothing at all when there is no evidence', () => {
    const { container } = panel([]);
    expect(container.firstChild).toBeNull();
  });

  it('does not call an empty side a clean bill of health', () => {
    const onlyBull = evidence.filter((item) => item.direction === 'BULLISH');
    const { container } = panel(onlyBull);
    const bear = container.querySelector('.evidence__column--bearish');

    expect(
      within(bear as HTMLElement).getByText('Bu yönde kayıtlı kanıt yok.'),
    ).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/risk yok|sorun yok|temiz/i);
  });
});

describe('audit references are a Pro detail', () => {
  it('hides reference ids in beginner mode', () => {
    panel(evidence, false);
    expect(screen.queryByText('EV-BULL-8F2A1C9D0B')).toBeNull();
  });

  it('shows them in pro mode', () => {
    panel(evidence, true);
    expect(screen.getByText('EV-BULL-8F2A1C9D0B')).toBeInTheDocument();
  });
});
