# Technical conventions — Phase 1

Companion to `viop_master_spec.md` sections 1 and 11. Where they disagree, the
master specification wins.

An indicator name does not determine a number. "RSI 14" describes a family of
series that differ by smoothing, by seed and by warm-up, and two of them can
disagree by several points for the first hundred bars. Every convention this
project uses is therefore written down here and pinned by a test, so that a
value can be reproduced, argued with, and compared against a chart.

---

## 1. Numerical authority

Deterministic Python computes every number. The LLM layer has no role in
producing an indicator value, a candle transformation, a data-quality verdict
or a volume metric, and never will — it interprets numbers it is given
(master spec section 1).

---

## 2. Precision policy

`Decimal` everywhere would be false precision; `float` everywhere would be a
correctness bug. The split is deliberate.

| Category | Type | Why |
| --- | --- | --- |
| Candle OHLC, volume, open interest | `Decimal` | Exact as received. Parsed straight from source text, never through `float`, so the input is not rounded before it is stored. |
| Money, P&L, margin, position size | `Decimal` | Phase 3 onward. Binary floating point is not acceptable for money or tick arithmetic. |
| Indicator mathematics | `float` | EMA, Wilder smoothing and standard deviation are irrational-valued recursions. `Decimal` would carry 28 digits of precision the mathematics does not have, cost roughly two orders of magnitude in speed, and still need a rounding decision at every step. |
| Indicator outputs | `float \| None` | An analytic quantity, not money. |

**Conversion boundary.** Exactly one: the `float_*()` accessors on
`ValidatedCandleSeries`. Nothing converts back. An indicator result is never
silently re-promoted to `Decimal`, because that would present a derived
estimate with the authority of an exact quantity. When a level computed here
later drives money arithmetic — an ATR-based stop, say — the caller converts
explicitly and quantizes to the verified tick size, which arrives with
`ContractMetadataProvider` in Phase 3.

**Rounding.** None inside the engine. Rounding early destroys information and
compounds through a recursion; presentation rounding belongs to the UI.

**Comparison.** No test asserts float equality by accident. Values that are
exact in binary — an SMA of small integers, a zero, a warm-up `None` — are
compared with `==` deliberately. Everything else uses `pytest.approx` or
`math.isclose` with a stated tolerance. The one place exact equality is
required rather than tolerated is the look-ahead test, where the identical
computation must produce identical bits.

**Non-finite values.** Cannot enter: the Data Quality Engine blocks NaN and
infinity, and each indicator re-checks its own inputs. Cannot be produced:
every division is guarded, and `test_numeric_safety.py` asserts it across
trending, falling, flat, zero-volume, single-candle and extreme-scale inputs.

---

## 3. Shared smoothing conventions

Three recursions, kept separate by name because confusing them is the most
common way to get a "correct-looking" wrong series.

| Name | Alpha | Used by |
| --- | --- | --- |
| `sma` | — (arithmetic mean of a trailing window) | Bollinger middle band, volume average |
| `ema` | `2 / (period + 1)` | EMA ribbon, MACD |
| `wilder` | `1 / period` | RSI, ATR, ADX |

**Seed.** Both recursive forms are seeded with the arithmetic mean of the first
`period` inputs, placed at index `period - 1`. Seeding from the first value
alone converges to the same series but differs measurably for hundreds of bars
— exactly the range a short backtest occupies.

**Summation.** Window sums use `math.fsum`, so an SMA does not drift with
window length and two runs agree bit for bit. VWAP accumulates with Neumaier
compensated summation, which keeps the pass linear rather than quadratic.

---

## 4. Indicator conventions

Every indicator returns a tuple the same length as its input, aligned
index-for-index with the candles. Warm-up is `None` — never zero, never NaN.

### EMA — periods 9, 20, 50, 200
`alpha = 2 / (period + 1)`. Seeded with the SMA of the first `period` closes at
index `period - 1`. First value at index `period - 1`.

### SMA — periods 20, 50, 200
Mean of `values[i - period + 1 .. i]`: trailing, inclusive of `i`, never
centred. First value at index `period - 1`. Insufficient history yields `None`,
not a partial-window average.

### RSI — period 14
Close-to-close changes, so the first change is at index 1. Gains are
`max(change, 0)`, losses `max(-change, 0)`. Both are **Wilder-smoothed**, seeded
with the mean of the first `period` changes. `RS = avgGain / avgLoss`,
`RSI = 100 - 100 / (1 + RS)`. First value at index `period` — one later than an
SMA of the same period.

Degenerate windows:

| Condition | Value | Reason |
| --- | --- | --- |
| No losses, some gains | `100.0` | The limit of the formula, and Wilder's instruction. |
| No gains, some losses | `0.0` | Same limit from the other side. |
| Neither — a constant price | `50.0` | `RS` is genuinely `0/0`. `100` would assert maximum bullish momentum for a market that has not moved; `None` would claim a warm-up that is over. `50` says there is no directional pressure, which is what happened. |

### MACD — 12 / 26 / 9
`macd = EMA(12) - EMA(26)`, each EMA seeded independently from the start of the
data. First defined at index `slow - 1` = 25, where both inputs exist.

`signal = EMA(9)` of the MACD line, computed over its **defined values only**
and seeded with their SMA. First at index 33. Feeding the warm-up positions in
as zeros — a common shortcut — drags the signal toward zero for dozens of bars.

`histogram = macd - signal`, defined only where both exist.

> TA-Lib differs here: it starts the fast EMA at index `slow - fast` so both
> EMAs are seeded over the same number of bars. Our reading is the textbook one
> and matches TradingView. The difference is initialization only — see §6.

### True Range and ATR — period 14
`TR = max(high - low, |high - prevClose|, |low - prevClose|)`.

**`TR` is `None` at index 0.** There is no previous close, so the true range is
unknown. Substituting `high - low` is widespread and fabricates a value that
then propagates into the ATR seed. TA-Lib agrees: its `TRANGE` is also
undefined at index 0.

`ATR` is Wilder smoothing of TR, seeded with the mean of `TR[1..period]`. First
value at index `period`. Gap handling needs no special case — it is already
inside the True Range definition.

### Bollinger Bands — period 20, multiplier 2
Middle band is a **simple** moving average. Deviation is the **population**
standard deviation (divide by `N`) over the same window; the sample form would
widen every band by `sqrt(N/(N-1))` — about 2.6% at period 20, enough to move a
band touch across a decision boundary. Upper and lower are
`middle ± multiplier * sigma`. All three defined from index `period - 1`. A
flat window gives `sigma = 0` and three coincident bands, which is correct
rather than degenerate.

### ADX — period 14
`up = high[i] - high[i-1]`, `down = low[i-1] - low[i]`.
`+DM = up` when `up > down and up > 0`, else `0`; `-DM` symmetrically. An inside
bar produces zero on both sides, and the two are never both positive.

`+DM`, `-DM` and `TR` are each Wilder-smoothed over `period`, seeded with the
mean of their first `period` defined values.
`+DI = 100 * smoothed(+DM) / smoothed(TR)`, likewise `-DI`; first at index
`period`.
`DX = 100 * |+DI - -DI| / (+DI + -DI)`; same index.
`ADX` is a **second** Wilder smoothing of DX, seeded with the mean of the first
`period` DX values — first at index `2 * period - 1` = **27**.

Degenerate: `smoothed(TR) == 0` (a market with no range at all) gives both DI
values `0.0`; `+DI + -DI == 0` gives `DX = 0.0`.

> TA-Lib differs here too: it seeds the DM/TR smoothing with the sum of the
> first `period - 1` bars, decays it once, then adds the `period`-th. Wilder's
> published method — and TA-Lib's *own* ATR, which we match exactly — seeds
> with the first `period` bars. We follow Wilder, which keeps our ADX
> consistent with our ATR. See §6.

### VWAP
Price basis is the typical price `(H + L + C) / 3`.
`VWAP[i] = sum(TP * V) / sum(V)` from the most recent anchor through `i`
inclusive. Cumulative within a window, not rolling, so it has **no warm-up**:
the first candle of a window is its own VWAP.

Anchors are passed as start indices, which is the whole architecture for
anchored VWAP (master spec section 11): `(0,)` gives a whole-series VWAP,
`daily_anchors(...)` gives a session VWAP, and a future anchor at a swing high
or an event is the same function with a different argument.

**Session anchoring is a development default.** `daily_anchors` breaks on the
UTC calendar date. A true session VWAP restarts at the exchange's session open,
and VIOP session hours — including evening-session eligibility — are mutable
exchange facts that section 118 forbids assuming from memory. When they are
obtained from Borsa İstanbul, only `daily_anchors` changes.

Zero volume: while cumulative volume since the anchor is zero, the result is
`None`. Carrying the previous VWAP forward, or emitting the typical price,
would both present a fabricated level as a traded average.

### Volume capabilities — period 20

Section 11 lists seven volume capabilities. **Four are available**: Raw Volume,
plus the three derived metrics below. **Three are deferred.**

Raw Volume needs no function — it is `Candle.volume`, already exact as
`Decimal` and carried unchanged through the whole pipeline.

| Derived metric | Formula | Undefined when |
| --- | --- | --- |
| Volume moving average | `SMA(volume, period)` | fewer than `period` candles |
| Relative volume | `volume[i] / volume_ma[i]` | warm-up, or the average is zero |
| Volume acceleration | `volume_ma[i] / volume_ma[i-1] - 1` | warm-up, first bar after it, or the previous average is zero |

Relative volume's denominator **includes candle `i`**, matching how the measure
is read on a chart. It is therefore slightly self-damping; this is stated so
that nobody later "fixes" it into a forward-looking window.

**Exactly three are not implemented, and none is stubbed**, with the phase that
owns each:

| Section 11 capability | Blocked on | Phase |
| --- | --- | --- |
| Breakout Volume Confirmation | support/resistance and market structure | 2 |
| Volume Divergence | swing high/low structure | 2 |
| Time-of-Day Normalized Volume | verified exchange-session metadata (§118) | the phase that obtains it |

Two further section 11 items are deferred and are **not** counted among the
seven volume capabilities:

* **Stochastic RSI** — marked *Optional* in §11.
* **Historical Volatility** — named in §11 under VOLATILITY, but not in the
  §103 Phase 1 scope. Intentionally deferred, to be reconsidered before or
  during the Phase 2 regime work. When it is taken up, **no market-specific
  annualization factor or session assumption may be invented**: the number of
  trading periods per year is a property of the VIOP calendar and therefore a
  §118 fact. Until it is verified, such a measure must be reported
  unannualized in per-candle terms, or carry a `VerifiedValue` marked
  `UNVERIFIED`. Hard-coding "252 trading days" would be a fabrication that
  looks entirely plausible.

---

## 5. Look-ahead protection

At index `i` an indicator uses only candles `0..i`. No centred window, no
backfill, no normalization against whole-dataset statistics, no future candle
in a seed.

`tests/unit/technical/test_no_lookahead.py` proves it structurally rather than
case by case: it computes the entire indicator set over a 90-candle prefix and
again over the full 150 candles, and requires every historical value to be
bit-identical. It also walks a growing window and checks that the newest value
at each step equals the value the full run produces at that index — the way
live mode will consume the engine.

---

## 6. Independent verification

Reference values come from **TA-Lib 0.7.1**, the C implementation most
platforms embed, run in a throwaway virtual environment and frozen into
`backend/tests/fixtures/golden_indicators.json`. TA-Lib is not a project
dependency, does not run in CI, and is never the runtime numerical authority.
It is a witness.

Eleven indicator families reproduce TA-Lib to floating-point noise — worst
relative difference `1.4e-13`, at the Bollinger bands where a square root
compounds rounding. Warm-up indices agree exactly for all of them.

Two families differ, both by **initialization only**, and both differences are
proven rather than assumed: the test suite rebuilds each value using TA-Lib's
own seeding and shows it then matches to `1e-12`, which isolates the difference
to the seed and demonstrates the formula agrees. It also shows the difference
decaying geometrically — a seeding difference must shrink, while a wrong alpha,
window or sign stays wrong forever.

| Family | Cause | Difference at index 33 | At index 100 | At index 149 |
| --- | --- | --- | --- | --- |
| MACD | TA-Lib shifts the fast EMA start | 5.5e-3 | 7.6e-8 | 2.1e-11 |
| ADX | TA-Lib seeds DM/TR with `period - 1` bars | 3.1e-1 | 1.1e-2 | 6.3e-4 |

### Regenerating the fixture

Only needed if a convention changes. In a virtual environment **outside the
project**:

```bash
python -m venv refvenv
./refvenv/Scripts/python.exe -m pip install pandas TA-Lib
./refvenv/Scripts/python.exe gen_reference.py     # writes talib_reference.json
```

Then, in the project venv, rebuild `golden_indicators.json` from it. Record the
TA-Lib version in the fixture's `reference_source` field — a golden file whose
origin is unknown is not evidence of anything.

---

## 7. Data Quality Engine

See `app/domain/market/quality.py` for the rule set. Two principles govern it:

**Nothing is silently repaired.** Corruption is reported and blocks the
dataset. Candles are never sorted, deduplicated, gap-filled or clamped — each
of those would destroy the evidence that a provider produced bad data. The one
normalization performed, converting timezone-aware timestamps to UTC, preserves
the exact instant, changes no value, and is recorded as an explicit `INFO`
finding.

**Absence of proof is not proof of absence.** A gap warns and never blocks,
because telling missing data apart from a closed market requires verified
session hours (§118). Blocking on them would mean inventing a trading calendar.

| Severity | Meaning |
| --- | --- |
| `INFO` | A deterministic, value-preserving normalization was applied |
| `WARNING` | Usable, but the analysis must disclose the caveat |
| `BLOCK` | Integrity failure; no calculation may run |

Blocking: empty series, naive timestamp, non-finite value, negative price, zero
price, invalid OHLC relationship, negative volume, forming candle, symbol
mismatch, timeframe mismatch, duplicate candle, conflicting duplicate,
out-of-order candle, irregular interval, malformed row.

Advisory: missing candles, zero volume, extreme move, insufficient history.

Not implemented, and not claimed: **stale feed** needs a live feed's last tick
(Phase 13); **wrong or expired contract** needs `ContractMetadataProvider`
(Phase 3).

The guarantee that no provider can bypass this: indicators accept only
`ValidatedCandleSeries`, and structural invariants are enforced in its
constructor. Handing raw provider output to the engine is a type error.
