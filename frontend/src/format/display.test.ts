import { describe, expect, it } from 'vitest';
import type { NumericFact } from '../domain/models';
import {
  DISPLAY_FORMAT_VERSION,
  formatFact,
  formatScore,
  formatTimestamp,
  formatValue,
  MISSING_DISPLAY,
  roundDecimalString,
} from './display';

/**
 * Display formatting (§35).
 *
 * The property that matters most is the one that is easiest to lose: the raw
 * value must survive untouched. Everything else is presentation, and
 * presentation may be wrong without anyone losing money; a mutated
 * authoritative value is a different kind of bug.
 */

describe('raw value is never mutated', () => {
  it('leaves the fact it was given untouched', () => {
    const fact: NumericFact = {
      id: 'FACT-X',
      label: 'X',
      raw: '24.658334322196957',
      unit: 'indicator',
      source: 'CALCULATED',
    };
    const before = fact.raw;
    const formatted = formatFact(fact);

    expect(fact.raw).toBe(before);
    expect(formatted.raw).toBe('24.658334322196957');
    expect(formatted.display).toBe('24.66');
    expect(formatted.wasRounded).toBe(true);
  });

  it('reports when nothing was rounded', () => {
    const fact: NumericFact = {
      id: 'FACT-C',
      label: 'Kontrat',
      raw: '4',
      unit: 'contracts',
      source: 'CALCULATED',
    };
    expect(formatFact(fact).wasRounded).toBe(false);
  });
});

describe('rounding happens on the decimal string, never through a float', () => {
  it.each([
    ['0.1', 1, '0.1'],
    ['24.658334322196957', 2, '24.66'],
    ['24.654', 2, '24.65'],
    ['9.995', 2, '10.00'],
    ['9.99', 0, '10'],
    ['-3.456', 2, '-3.46'],
    ['1000.005', 2, '1000.01'],
    ['0.000000001', 2, '0.00'],
    ['123456789012345678901234567890.126', 2, '123456789012345678901234567890.13'],
  ])('rounds %s to %i places as %s', (raw, places, expected) => {
    expect(roundDecimalString(raw, places)).toBe(expected);
  });

  it('does not corrupt a value JavaScript floats cannot hold exactly', () => {
    // 0.1 + 0.2 is the classic float demonstration. The string path never
    // performs that addition, so the digits survive.
    expect(roundDecimalString('0.30000000000000004', 2)).toBe('0.30');
    expect(roundDecimalString('0.1', 20)).toBe('0.10000000000000000000');
  });

  it('does not render a negative zero', () => {
    // A value that rounds away to zero is not meaningfully negative, and
    // "-0.00" on screen reads as a rendering bug. The sign is kept whenever
    // any significant digit survives.
    expect(roundDecimalString('-0.004', 2)).toBe('0.00');
    expect(roundDecimalString('-0.006', 2)).toBe('-0.01');
  });

  it('returns non-decimal input unchanged rather than guessing', () => {
    expect(roundDecimalString('yaklaşık 61', 2)).toBe('yaklaşık 61');
    expect(roundDecimalString('6.127e1', 2)).toBe('6.127e1');
  });
});

describe('precision follows semantics, not the data (§6)', () => {
  it.each([
    ['contracts', '4', '4'],
    ['contracts', '4.0', '4'],
    ['score', '72', '72'],
    ['indicator', '61.2700000001', '61.27'],
    ['currency', '1000.005', '1000.01'],
    ['ratio', '2.4499', '2.45'],
    ['percentage', '61.27', '61.3%'],
  ] as const)('formats a %s value %s as %s', (unit, raw, expected) => {
    expect(formatValue(raw, unit)).toBe(expected);
  });

  it('does not invent price precision while tick size is unverified', () => {
    // A price needs tick semantics to round correctly, and tick size is an
    // exchange fact with a verification status. Until one arrives, the value
    // is shown as sent (§6).
    expect(formatValue('61.2700', 'price')).toBe('61.2700');
  });

  it('leaves an unknown unit alone', () => {
    expect(formatValue('1.23456', 'unknown')).toBe('1.23456');
  });
});

describe('missing is not zero (§15)', () => {
  it.each([null, undefined, '', '   '])('renders %s as a dash', (value) => {
    expect(formatValue(value, 'currency')).toBe(MISSING_DISPLAY);
  });

  it('never renders a missing value as 0', () => {
    expect(formatValue(null, 'contracts')).not.toBe('0');
  });

  it('renders a missing score as a dash, not zero', () => {
    expect(formatScore(null, 100)).toBe(MISSING_DISPLAY);
  });
});

describe('a score is rendered against its total (§9)', () => {
  it('never renders bare or with a percent sign', () => {
    const rendered = formatScore(72, 100);
    expect(rendered).toBe('72 / 100');
    expect(rendered).not.toContain('%');
  });
});

describe('timestamps', () => {
  it('formats an ISO instant for a Turkish reader', () => {
    expect(formatTimestamp('2026-03-02T12:00:00+00:00')).toContain('2026');
  });

  it('returns unparseable input unchanged rather than showing a wrong date', () => {
    expect(formatTimestamp('not a date')).toBe('not a date');
  });

  it('renders an absent timestamp as missing', () => {
    expect(formatTimestamp(null)).toBe(MISSING_DISPLAY);
  });

  it('names the zone, so a time can never be read as local', () => {
    // Regression. This rendered "2 Mar 2026 12:00" with no zone: three hours
    // off for a reader in Istanbul, with nothing on the page to contradict it.
    expect(formatTimestamp('2026-03-02T12:00:00+00:00')).toContain('UTC');
  });

  it('converts an offset timestamp to UTC rather than displaying it as given', () => {
    // 15:00+03:00 is 12:00 UTC. Showing "15:00 UTC" would be a wrong claim,
    // not a formatting choice.
    const shifted = formatTimestamp('2026-03-02T15:00:00+03:00');
    expect(shifted).toContain('12:00');
    expect(shifted).toContain('UTC');
  });

  it('does not append a zone to a value it could not parse', () => {
    expect(formatTimestamp('not a date')).not.toContain('UTC');
  });
});

describe('a unit suffix is only ever attached to a number', () => {
  // Regression. The suffix was appended unconditionally, so any unparseable
  // value became a fabricated measurement.
  it('does not turn prose into a percentage', () => {
    expect(formatValue('abc', 'percentage')).toBe('abc');
  });

  it('does not turn NaN or Infinity into a percentage', () => {
    expect(formatValue('NaN', 'percentage')).toBe('NaN');
    expect(formatValue('Infinity', 'percentage')).toBe('Infinity');
  });

  it('does not reformat or suffix scientific notation', () => {
    expect(formatValue('1e5', 'percentage')).toBe('1e5');
    expect(formatValue('1e5', 'indicator')).toBe('1e5');
  });

  it('does not suffix a malformed decimal', () => {
    expect(formatValue('12.', 'percentage')).toBe('12.');
  });

  it('still suffixes a real number', () => {
    expect(formatValue('12.34', 'percentage')).toBe('12.3%');
  });
});

describe('very large and very small values stay exact', () => {
  it('rounds an 18-digit value without float corruption', () => {
    // Beyond Number.MAX_SAFE_INTEGER. A float path would have lost digits.
    expect(formatValue('123456789012345678.987654321', 'indicator')).toBe('123456789012345678.99');
  });

  it('collapses a sub-precision value to zero without inventing a sign', () => {
    expect(formatValue('0.00000001', 'indicator')).toBe('0.00');
    expect(formatValue('-0.004', 'indicator')).toBe('0.00');
  });

  it('keeps a price exactly as sent, at any magnitude', () => {
    expect(formatValue('123456789012345678.987654321', 'price')).toBe(
      '123456789012345678.987654321',
    );
    expect(formatValue('0.00000001', 'price')).toBe('0.00000001');
  });
});

describe('a value the system says it does not have is never dressed up', () => {
  it('does not give MISSING provenance calculated precision', () => {
    // Regression. `MISSING` + a raw value is an incoherent payload, and it
    // rendered as `99.00` - the precision of a number we computed.
    const shown = formatFact({
      id: 'x',
      label: 'l',
      raw: '99',
      unit: 'indicator',
      source: 'MISSING',
    });
    expect(shown.display).toBe('99');
    expect(shown.wasRounded).toBe(false);
  });

  it('still formats every authoritative source', () => {
    for (const source of ['CALCULATED', 'STRUCTURED_DATA', 'USER_CONFIRMED'] as const) {
      expect(
        formatFact({ id: 'x', label: 'l', raw: '99', unit: 'indicator', source }).display,
        source,
      ).toBe('99.00');
    }
  });
});

describe('the policy is versioned', () => {
  it('states its version', () => {
    expect(DISPLAY_FORMAT_VERSION).toBe('display-format/v1');
  });
});
