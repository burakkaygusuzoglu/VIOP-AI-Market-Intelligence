/**
 * Deterministic, versioned display formatting (§5, §6).
 *
 * This closes the Phase 7 carry-forward: **RAW VALUE != DISPLAY VALUE.** The
 * backend renders `24.658334322196957` because that is exactly what Phase 1
 * computed and rounding it there would silently alter a number the synthesis
 * layer does not own. Rounding it *here* is safe, because here is display.
 *
 * Three rules make that safe rather than merely convenient:
 *
 * 1. **The raw value is never mutated.** `format` returns a new string and the
 *    `NumericFact.raw` it was given is untouched, so an audit view, a tooltip
 *    and a debugging panel all still see what the engine produced.
 * 2. **Precision follows semantics, not data.** A contract count is an
 *    integer because contracts are indivisible, not because it happened to
 *    arrive without decimals. Eight decimals are never shown merely because
 *    the raw string had eight.
 * 3. **It is centralised and versioned.** No `.toFixed(2)` anywhere else in
 *    the codebase - an architecture test enforces that. When the policy
 *    changes, the version changes with it, so a stored screenshot of a number
 *    can be traced to the rules that produced it.
 *
 * ## No binary floating point
 *
 * The backend sends exact decimal *strings*. Parsing them into JavaScript
 * numbers to round them would reintroduce precisely the float error the whole
 * project avoids - `parseFloat("0.1") + parseFloat("0.2")` is the classic
 * demonstration. So rounding is done on the decimal string itself, digit by
 * digit, and a value is only converted to a `number` where the result is
 * already known to be exact and small.
 */

import type { NumericFact, NumericUnit, Provenance } from '../domain/models';

export const DISPLAY_FORMAT_VERSION = 'display-format/v1';

/**
 * Decimal places per semantic unit.
 *
 * `null` means "show the value as given" - used where the project has no
 * verified precision to apply and inventing one would be a fabricated exchange
 * fact (§6).
 */
const DECIMALS: Record<NumericUnit, number | null> = {
  // Prices need tick-size semantics to round correctly, and tick size is a
  // per-contract exchange fact that arrives through a provider with a
  // verification status. Until a verified tick reaches the frontend, a price
  // is shown as the backend sent it: no invented precision (§6).
  price: null,
  percentage: 1,
  // A heuristic score out of 100. Decimals would imply a precision the
  // weighting does not have.
  score: 0,
  ratio: 2,
  // Contracts are indivisible.
  contracts: 0,
  /*
   * Two decimals as a **generic display policy**, not a currency minor-unit
   * rule (§12).
   *
   * This distinction is easy to get wrong. A currency-aware formatter would
   * show 2 decimals for TRY and USD, 0 for JPY and 3 for KWD - and applying
   * "2" while calling it a currency rule quietly asserts the account is
   * denominated in a two-decimal currency. This project has no verified
   * currency registry, so it does not make that claim: two decimals is what a
   * money-shaped number is rounded to for display here, and the currency code
   * (when the user supplied one) is rendered beside the number rather than
   * driving its precision.
   */
  currency: 2,
  // Enough to be useful, few enough not to imply false precision. RSI 61.27,
  // not RSI 61.2700000000000102.
  indicator: 2,
  timestamp: null,
  unknown: null,
};

/**
 * Shown where a value is absent.
 *
 * A dash, never a zero. A zero is a number and would read as a measurement,
 * which is exactly the "missing becomes neutral" collapse §15 forbids.
 */
export const MISSING_DISPLAY = '—';

const UNIT_SUFFIX: Partial<Record<NumericUnit, string>> = {
  percentage: '%',
};

/**
 * A plain decimal this module is willing to reformat.
 *
 * Deliberately narrow. Scientific notation (`1e5`), a trailing point (`12.`),
 * `NaN`, `Infinity` and any prose fail it, and everything that fails it is
 * passed through untouched rather than guessed at.
 */
const PLAIN_DECIMAL = /^-?\d+(\.\d+)?$/;

/**
 * Round a decimal *string* to `places`, without going through a float.
 *
 * Half-up on the decimal digits, which is what a reader expects and what the
 * string representation supports exactly.
 */
export function roundDecimalString(raw: string, places: number): string {
  const trimmed = raw.trim();
  if (!PLAIN_DECIMAL.test(trimmed)) {
    // Not a plain decimal - scientific notation, a word, an empty string.
    // Returned unchanged rather than guessed at.
    return trimmed;
  }

  const negative = trimmed.startsWith('-');
  const unsigned = negative ? trimmed.slice(1) : trimmed;
  // The guard above proved the shape `\d+(\.\d+)?`, so there is always at
  // least one integer digit and the `'0'` default is unreachable. It is here
  // because `noUncheckedIndexedAccess` types a destructured element as
  // possibly undefined, and `${undefined}` would have produced the literal
  // string "undefined" inside the carry arithmetic below.
  const [whole = '0', fraction = ''] = unsigned.split('.');

  if (places === 0 && fraction === '') return trimmed;

  const keep = fraction.slice(0, places);
  const nextDigit = fraction.charCodeAt(places) - 48;
  const padded = keep.padEnd(places, '0');

  let result: string;
  if (nextDigit >= 5) {
    // Increment the kept digits as an integer string, so a carry across the
    // decimal point (9.99 -> 10.0) is handled without touching a float.
    const digits = `${whole}${padded}`;
    const bumped = (BigInt(digits === '' ? '0' : digits) + 1n)
      .toString()
      .padStart(digits.length, '0');
    const cut = bumped.length - places;
    const newWhole = places === 0 ? bumped : bumped.slice(0, cut) || '0';
    const newFraction = places === 0 ? '' : bumped.slice(cut);
    result = places === 0 ? newWhole : `${newWhole}.${newFraction}`;
  } else {
    result = places === 0 ? whole : `${whole}.${padded}`;
  }

  return negative && /[1-9]/.test(result) ? `-${result}` : result;
}

/**
 * The display string for a raw value of a given unit.
 *
 * ## A unit suffix is a claim, so it is only added to a number
 *
 * This appended the suffix unconditionally, which turned any unparseable
 * value into a fabricated measurement: `formatValue('abc', 'percentage')`
 * returned `"abc%"`, and `'NaN'`, `'1e5'` and `'12.'` were dressed the same
 * way. A `%` after a word is a reading the backend never sent.
 *
 * Anything that is not a plain decimal now passes through as its own text,
 * with no rounding and no suffix. It is still shown — suppressing it would
 * hide that the backend sent something unexpected — but it is never decorated
 * into looking like a measurement.
 */
export function formatValue(raw: string | null | undefined, unit: NumericUnit): string {
  if (raw === null || raw === undefined || raw.trim() === '') return MISSING_DISPLAY;

  const trimmed = raw.trim();
  if (!PLAIN_DECIMAL.test(trimmed)) return trimmed;

  const places = DECIMALS[unit];
  const rounded = places === null ? trimmed : roundDecimalString(trimmed, places);
  const suffix = UNIT_SUFFIX[unit] ?? '';
  return `${rounded}${suffix}`;
}

/** A score, always rendered against its total so it cannot read as a percent (§9). */
export function formatScore(score: number | null, outOf: number): string {
  if (score === null) return MISSING_DISPLAY;
  return `${score} / ${outOf}`;
}

export interface FormattedFact {
  readonly label: string;
  /** What the user sees. */
  readonly display: string;
  /** What the engine produced. Shown in tooltips and audit views. */
  readonly raw: string;
  /** True when rounding changed the text, so the UI can offer the exact value. */
  readonly wasRounded: boolean;
  readonly source: Provenance;
  readonly unit: NumericUnit;
  /** The user-supplied account currency, or `null` when none was given. */
  readonly currency?: string | null;
}

/**
 * Sources whose values this project did **not** compute.
 *
 * A screenshot reading is text a model saw on a chart. Running it through
 * numeric formatting would put digits in the screenshot's mouth: an observed
 * `99` displayed as `99.00` claims a precision the picture never showed, which
 * is the false precision §6 forbids - and it makes an unverified reading look
 * more machine-like than the calculated value beside it.
 *
 * `MISSING` belongs here too. A fact whose provenance is `MISSING` while
 * carrying a raw value is an incoherent payload, and the safe reading of it is
 * "not ours to format": it rendered as `99.00`, dressing a value the system
 * says it does not have in the precision of one it calculated.
 *
 * So these render exactly as recorded. Formatting is for numbers this project
 * computed.
 */
const NOT_CALCULATED_HERE: ReadonlySet<Provenance> = new Set<Provenance>([
  'VISION_READ',
  'AI_INFERENCE',
  'UNVERIFIED',
  'MISSING',
]);

/**
 * Format one fact, keeping its raw value and provenance attached.
 *
 * ## The currency code is displayed, never inferred
 *
 * A money value shows its currency **only** when the user told us what the
 * account is denominated in. With no code the number is shown bare - no `₺`,
 * no symbol guessed from the locale, and none assumed because VİOP is a
 * Turkish exchange. An unlabelled amount is ambiguous; a wrongly labelled one
 * is false, and the second is worse.
 *
 * The code is appended as text and kept out of `raw`, so the exact value the
 * engine produced is still exactly what an audit view shows.
 */
export function formatFact(fact: NumericFact): FormattedFact {
  const base = NOT_CALCULATED_HERE.has(fact.source)
    ? fact.raw.trim() || MISSING_DISPLAY
    : formatValue(fact.raw, fact.unit);

  const code = fact.currency?.trim();
  const display =
    fact.unit === 'currency' && code && base !== MISSING_DISPLAY ? `${base} ${code}` : base;

  return {
    label: fact.label,
    display,
    raw: fact.raw,
    wasRounded: display !== fact.raw && display !== MISSING_DISPLAY,
    source: fact.source,
    unit: fact.unit,
    currency: code || null,
  };
}

/**
 * A timestamp for display.
 *
 * ISO in, readable out, with the original preserved. Locale is pinned to
 * `tr-TR` because the product is Turkish-first.
 *
 * ## The zone is always named
 *
 * The rendered time is UTC, and it **says UTC**. It did not, and an empirical
 * review of the rendered dashboard caught it: "Analiz verisi: 2 Mar 2026
 * 12:00" reads as local time to a trader in Istanbul, who is three hours
 * ahead. A three-hour error in "when was this measured" is the difference
 * between an analysis of the current session and one from before the open,
 * and nothing else on the page would have contradicted it.
 *
 * Rendering in the viewer's local zone instead was rejected: the backend
 * timestamps everything from a clock port in UTC, audit digests quote UTC, and
 * a screenshot of the UI should be comparable to a log line without knowing
 * which machine took it.
 */
export function formatTimestamp(iso: string | null): string {
  if (!iso) return MISSING_DISPLAY;
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  const formatted = new Intl.DateTimeFormat('tr-TR', {
    dateStyle: 'medium',
    timeStyle: 'short',
    timeZone: 'UTC',
  }).format(parsed);
  return `${formatted} UTC`;
}
