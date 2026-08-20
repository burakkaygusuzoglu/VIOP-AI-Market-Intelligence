# PHASE 1 COMPLETION REPORT

Date: 2026-08-21 · Follows `phase_0_ci_fix_report.md` · Phase 1 only ·
**Corrected final report**

Baseline at start: `df96bd9`, clean tree, CI green, no Phase 1 code present.

Phase 1 was technically approved on review. This report is the approved record,
issued after a closeout that corrected the volume-capability accounting,
recorded Historical Volatility as explicitly deferred work, and brought the
`volume.py` module docstring into line with both. The only production-file
change in the closeout was that **docstring**; no executable logic was
modified, and every gate was re-run afterwards (§15).

---

## 1. Scope delivered

Master spec section 103, Phase 1 — *market data + technical engine*.

| Requirement | Status |
| --- | --- |
| OHLCV domain representation | IMPLEMENTED |
| Historical market-data validation | IMPLEMENTED |
| Data Quality Engine (§40) | IMPLEMENTED |
| CSV historical provider | IMPLEMENTED |
| Deterministic / mock provider | IMPLEMENTED |
| EMA 9 / 20 / 50 / 200 | IMPLEMENTED |
| SMA 20 / 50 / 200 | IMPLEMENTED |
| RSI 14 | IMPLEMENTED |
| MACD 12 / 26 / 9 | IMPLEMENTED |
| ATR 14 | IMPLEMENTED |
| VWAP (+ anchored-VWAP architecture) | IMPLEMENTED |
| ADX 14 | IMPLEMENTED |
| Bollinger Bands 20 / 2 | IMPLEMENTED |
| Volume capabilities (§11) | PARTIAL — **4 of 7 available**, by design (below) |
| Deterministic tests and validation | IMPLEMENTED |

**Volume capabilities — 4 of 7 available.** Section 11 lists seven volume
capabilities. **Four are available today:**

| Capability | Status | How it is provided |
| --- | --- | --- |
| Raw Volume | AVAILABLE | Already present as `Candle.volume` — exact `Decimal`, carried through the whole pipeline. No derived function is needed or wanted. |
| Volume Moving Average | IMPLEMENTED | `volume_moving_average()` |
| Relative Volume | IMPLEMENTED | `relative_volume()` |
| Volume Acceleration | IMPLEMENTED | `volume_acceleration()` |

**Exactly three are NOT STARTED**, and none of them is stubbed:

| Capability | Blocked on | Phase |
| --- | --- | --- |
| Breakout Volume Confirmation | support/resistance zones and market structure (§12, §13) | 2 |
| Volume Divergence | swing high/low structure (§12) | 2 |
| Time-of-Day Normalized Volume | verified exchange-session metadata — a §118 fact | the phase that obtains verified session data |

Inventing a session grid to make the third one compile is exactly the failure
§118 exists to prevent, so it was left out rather than faked.

Two further section 11 items are deferred and are **not** counted among the
seven volume capabilities: **Stochastic RSI** (marked *Optional* in §11) and
**Historical Volatility** — see §13, which records the latter as explicit
deferred work with the conditions for taking it up.

---

## 2. Files added / modified, by layer

### Domain — `app/domain/` (stdlib only)

**Added**
- `market/series.py` — `CandleSeries`, `ValidatedCandleSeries`, the
  Decimal→float boundary
- `market/quality.py` — the Data Quality Engine, severities, codes, policy,
  report
- `technical/types.py` — `IndicatorValues`, input guards, align/compact helpers
- `technical/smoothing.py` — `sma`, `ema`, `wilder`, rolling population stdev
- `technical/momentum.py` — `rsi`, `macd`
- `technical/volatility.py` — `true_range`, `atr`, `bollinger_bands`
- `technical/trend_strength.py` — `adx` (+DI, −DI, DX, ADX)
- `technical/vwap.py` — `vwap`, `daily_anchors`, `typical_prices`
- `technical/volume.py` — volume MA, relative volume, volume acceleration
- `technical/engine.py` — `TechnicalConfig`, `TechnicalSnapshot`,
  `compute_technicals`
- `technical/__init__.py`

**Modified**
- `market/__init__.py` — exports the new market types

`market/candle.py` and `common/` are unchanged from Phase 0.

### Application — `app/application/`

**Added**
- `use_cases/load_market_data.py` — `LoadValidatedCandles`

**Modified**
- `ports/market_data.py` — added `MarketDataFetch` and the optional
  `DiagnosticHistoricalMarketDataProvider`. The Phase 0
  `HistoricalMarketDataProvider` protocol is **unchanged**; the addition is
  purely additive.

### Adapters — `app/adapters/`

**Added**
- `market_data/csv_provider.py` — `CsvHistoricalMarketDataProvider`
- `market_data/synthetic_provider.py` — `SyntheticHistoricalMarketDataProvider`
- `market_data/__init__.py`

### Tests — `backend/tests/`

**Added** — `factories.py`; `unit/test_candle_series.py`,
`unit/test_data_quality.py`, `unit/test_market_data_providers.py`,
`unit/test_load_market_data.py`; `unit/technical/` (`test_smoothing.py`,
`test_momentum.py`, `test_volatility.py`, `test_trend_strength.py`,
`test_vwap.py`, `test_volume.py`, `test_engine.py`, `test_golden_vectors.py`,
`test_no_lookahead.py`, `test_numeric_safety.py`); `fixtures/golden_indicators.json`;
`fixtures/market_data/` (5 CSV fixtures).

**Modified** — `unit/test_architecture.py` (two new independent checks).

### Configuration and docs

- `backend/pyproject.toml` — one new import-linter contract. **No dependency
  added.**
- `docs/technical_conventions.md` — **added**; the mathematical conventions.
- `docs/architecture.md` — updated to the now-implemented state.

`docs/viop_master_spec.md` was **not** modified. No API route, no frontend
change, no database table, no Alembic migration.

---

## 3. Market data model

**Candle.** Unchanged from Phase 0: `open_time` is the bar's opening instant
and must be timezone-aware; prices and volume are `Decimal`; `is_closed`
distinguishes a settled bar from a forming one; `open_interest` is optional and
stays `None` when absent rather than becoming zero.

**Timeframe.** `Timeframe` carries its own duration in minutes, so the expected
spacing is derived rather than hard-coded per call site.

**Two series types, and why.**

`CandleSeries` holds whatever a provider returned — possibly empty, unordered,
duplicated or corrupt. It enforces nothing, because rejecting bad data at
construction would leave the Data Quality Engine unable to explain *why* a
dataset is unusable, and §40/§41 require that explanation.

`ValidatedCandleSeries` enforces its invariants in `__post_init__`: every
candle closed, strictly ascending by `open_time`, timezone-aware, and sharing
the series' symbol and timeframe. The indicator engine accepts only this type,
so **handing raw provider output to a calculation is a type error**, not a
convention someone has to remember.

**Forming versus closed.** A forming candle is blocked from any historical
dataset by the Data Quality Engine, and cannot exist inside a
`ValidatedCandleSeries` at all. The distinction survives into Phase 13: nothing
in Phase 1 collapses the two states, and the CSV provider preserves an explicit
`is_closed=false` from a file rather than overriding it.

**Ordering and identity.** Candles are identified by `open_time` within a
symbol and timeframe. Duplicates are detected by timestamp, and an identical
repeat is reported distinctly from two different candles claiming the same
timestamp — the second is a worse provider fault, and collapsing them would
lose that.

---

## 4. Data Quality Engine

Two principles govern it.

**Nothing is silently repaired.** Candles are never sorted, deduplicated,
gap-filled or clamped. Each of those would destroy the evidence that a provider
produced bad data. The single normalization performed — converting
timezone-aware timestamps to UTC — preserves the exact instant, changes no
value, and is recorded as an explicit `INFO` finding rather than done quietly.

**Absence of proof is not proof of absence.** A gap warns and never blocks,
because distinguishing missing data from a closed market requires verified VIOP
session hours. Those are §118 facts and are not available; blocking on them
would mean inventing a trading calendar.

| Severity | Meaning |
| --- | --- |
| `INFO` | A deterministic, value-preserving normalization was applied |
| `WARNING` | Usable, but the analysis must disclose the caveat |
| `BLOCK` | Integrity failure; no calculation may run |

**Blocking** — `EMPTY_SERIES`, `NAIVE_TIMESTAMP`, `NON_FINITE_VALUE`,
`NEGATIVE_PRICE`, `ZERO_PRICE`, `INVALID_OHLC_RELATIONSHIP`, `NEGATIVE_VOLUME`,
`FORMING_CANDLE`, `SYMBOL_MISMATCH`, `TIMEFRAME_MISMATCH`, `DUPLICATE_CANDLE`,
`CONFLICTING_DUPLICATE`, `OUT_OF_ORDER`, `IRREGULAR_INTERVAL`, `MALFORMED_ROW`.

**Advisory** — `MISSING_CANDLES`, `ZERO_VOLUME`, `EXTREME_MOVE`,
`INSUFFICIENT_HISTORY`.

**Informational** — `TIMEZONE_NORMALIZED`.

Verdicts are `ACCEPTED`, `ACCEPTED_WITH_WARNINGS`, `BLOCKED`. Every issue
carries a stable code, a message, and the candle index and timestamp where it
was found, so the §41 data-quality panel and the §67 audit trail have real
evidence rather than a boolean. A blocked dataset reports **every** reason
found, not the first — otherwise fixing a corrupt file is a game of
whack-a-mole.

Provider-level findings travel in the same report: a malformed CSV row becomes
a `MALFORMED_ROW` issue and is assessed alongside the domain rules.

**Not implemented, and not claimed as covered:** *stale feed* needs a live
feed's last tick (§50, §63 — Phase 13); *wrong contract* and *expired contract*
need `ContractMetadataProvider` (§118 facts — Phase 3). Neither is stubbed.

Thresholds live in `DataQualityPolicy` — `minimum_candles=2`,
`extreme_move_ratio=0.20`, `flag_zero_volume=True`. All three are project
heuristics; none describes VIOP or any contract, so none is a §118 fact. The
`minimum_candles` default is deliberately the bare minimum: per-indicator
sufficiency is the indicator's own business, reported as warm-up `None`, not a
number the engine guesses on the caller's behalf.

---

## 5. Indicator conventions

Full detail, with the reasoning for each choice, is in
`docs/technical_conventions.md`. Summary:

| Indicator | Convention | Seed | First value | Degenerate case |
| --- | --- | --- | --- | --- |
| SMA | trailing inclusive mean, `fsum` | — | `period-1` | short history → `None` |
| EMA | `alpha = 2/(period+1)` | SMA of first `period` | `period-1` | — |
| Wilder | `alpha = 1/period` | mean of first `period` | `period-1` | — |
| RSI | close-to-close, Wilder-smoothed gains/losses | mean of first `period` changes | `period` (14) | no losses → 100, no gains → 0, **flat → 50** |
| MACD | `EMA(12) − EMA(26)`, each seeded independently | SMA per EMA | 25 | flat → 0 |
| signal | `EMA(9)` over MACD's **defined values only** | SMA of those | 33 | — |
| True Range | `max(H−L, |H−pC|, |L−pC|)` | — | **1** (index 0 is `None`) | — |
| ATR | Wilder of TR | mean `TR[1..period]` | `period` (14) | frozen market → 0 |
| Bollinger | SMA middle, **population** σ, ±2σ | — | `period-1` (19) | flat → three coincident bands |
| +DI / −DI / DX | Wilder of +DM, −DM, TR | mean of first `period` | `period` (14) | `smoothed(TR)=0` → DI 0; `DI sum = 0` → DX 0 |
| ADX | **second** Wilder of DX | mean of first `period` DX | **`2·period−1` = 27** | frozen market → 0 |
| VWAP | `Σ(TP·V)/ΣV` from anchor, `TP=(H+L+C)/3` | — | **0** (no warm-up) | zero volume → `None` |
| Volume MA | `SMA(volume)` | — | `period-1` | — |
| Relative volume | `volume / volume_ma` | — | `period-1` | zero average → `None` |
| Volume acceleration | `volume_ma[i]/volume_ma[i-1] − 1` | — | `period` | zero previous → `None` |

Three choices worth calling out explicitly:

**RSI of a constant price is 50, not 100.** `RS` is genuinely `0/0`. A naive
"no losses means maximum" branch returns 100, asserting maximum bullish
momentum for a market that has not moved — actively misleading. `None` would
claim a warm-up that is over. 50 says there is no directional pressure, which
is what happened. Pinned by `test_rsi_constant_price_is_neutral`.

**True Range is undefined at index 0.** There is no previous close. Substituting
`high − low` is the common shortcut and fabricates a value that then propagates
into the ATR seed. TA-Lib agrees — its `TRANGE` is also undefined there.

**ADX starts at index 27, not 14.** Two Wilder stages. Seeding the second from a
single DX would report an ADX thirteen bars early and overstate trend strength
through the whole warm-up.

**Numeric representation.** `Decimal` for candle OHLCV and all money; `float`
for indicator mathematics, because EMA, Wilder smoothing and standard deviation
are irrational-valued recursions where `Decimal` would carry 28 digits of
precision the mathematics does not have. One conversion point — the `float_*()`
accessors on `ValidatedCandleSeries` — and nothing converts back, because an
indicator result is an analytic quantity, not money. Quantizing a level to the
tick size waits for Phase 3, when the tick size is a verified fact.

**Warm-up is `None`.** Never zero, never NaN. A zero reads as a measurement; a
NaN propagates invisibly through every later comparison, because `nan > 0` is
`False` and a signal simply never fires.

---

## 6. Providers

Both implement `HistoricalMarketDataProvider`. Neither computes anything — an
import-linter contract now forbids `app.adapters` from importing
`app.domain.technical`, and the contract was verified to actually break when
violated.

**CSV** (`CsvHistoricalMarketDataProvider`). One file per symbol and timeframe
at `{root}/{symbol}/{timeframe}.csv`. Prices parse straight from source text to
`Decimal`, never through `float`, so the input is not rounded before it is
stored. Timestamps must be ISO-8601; a naive timestamp takes the configured
`default_tz`, and when that is `None` the row is reported as malformed rather
than guessed at as UTC — a wrong timezone silently shifts every candle in the
file. Rows it cannot parse become `MALFORMED_ROW` findings that travel with the
candles; a missing column or missing file raises `CsvSchemaError` instead,
because there is no dataset to weigh up. It does not sort, deduplicate,
gap-fill or clamp.

**Synthetic** (`SyntheticHistoricalMarketDataProvider`). Deterministic by
construction: prices come from a closed-form function of the candle index, not
from `random`. The waveform is an exact triangle computed in `Decimal` rather
than a sine, because `math.sin` may differ by an ulp between platforms — which
would make "deterministic" true only on one machine, and this project has
already paid for one Windows-versus-Linux discrepancy local tests could not
see. Candle *n* is identical whether one candle or a thousand is requested,
which is the property a replay harness needs. It makes no network call and
invents no exchange fact: the symbol is visibly synthetic (`SYNTH-MOCK`) and
nothing encodes a multiplier, tick size, margin, session hour or expiry. It can
also inject defects on purpose — gap, duplicate, out-of-order, forming last
bar, zero volume — so the Data Quality Engine is exercised against a provider
rather than only against hand-built fixtures.

**The seam.** `LoadValidatedCandles` composes provider → Data Quality Engine →
`ValidatedCandleSeries`. It never raises for bad market data; a blocked dataset
is a normal outcome the caller must be able to display with its reasons. Adding
a live, replay or vendor provider later cannot introduce an unvalidated path
without deliberately rewriting this use case.

---

## 7. Numerical verification

Reference values come from **TA-Lib 0.7.1** — the C implementation most
platforms embed — generated in a throwaway virtual environment and frozen into
`backend/tests/fixtures/golden_indicators.json`. TA-Lib is **not** a project
dependency, does not run in CI, and is never the runtime numerical authority.
It is a witness, not a component.

**Eleven of thirteen families match to floating-point noise:**

```
       indicator  ref@  our@    n      max abs      max rel
          sma_20    19    19  131    5.684e-14    6.266e-16
          sma_50    49    49  101    7.105e-14    7.607e-16
           ema_9     8     8  142    0.000e+00    0.000e+00
          ema_20    19    19  131    0.000e+00    0.000e+00
          ema_50    49    49  101    1.421e-14    1.592e-16
          rsi_14    14    14  136    2.132e-14    6.258e-16
      true_range     1     1  149    0.000e+00    0.000e+00
          atr_14    14    14  136    1.332e-15    6.241e-16
   bb_upper_20_2    19    19  131    1.191e-11    1.280e-13
  bb_middle_20_2    19    19  131    5.684e-14    6.266e-16
   bb_lower_20_2    19    19  131    1.202e-11    1.359e-13
```

Warm-up indices agree exactly for all eleven.

**Two families differ, by initialization only — and this is proven, not
assumed.** For each, the test suite rebuilds the value using TA-Lib's own
seeding and shows it then matches to 1e-12, which isolates the difference to
the seed and demonstrates the formula agrees.

*MACD.* TA-Lib starts the fast EMA at index `slow − fast` = 14 so both EMAs are
seeded over the same number of bars. We seed each EMA independently from the
start of the data — the textbook reading of "EMA(12) − EMA(26)", and what
TradingView computes. Applying TA-Lib's alignment to our own EMA reproduces its
output with **max abs difference 0.000e+00**.

*Directional (+DI, −DI, DX, ADX).* TA-Lib seeds the +DM/−DM/TR smoothing with
the sum of the first `period − 1` bars, decays it once, then adds the
`period`-th. Wilder's published method — and **TA-Lib's own ATR, which we match
exactly** — seeds with the first `period` bars. TA-Lib is internally
inconsistent here; we follow Wilder, which keeps our ADX consistent with our
ATR and with TradingView's `ta.rma`. Reproducing TA-Lib's seed gives its first
+DI to **1.8e-15**.

**Both differences decay geometrically**, which is the load-bearing half of the
argument: a seeding difference must shrink as the smoothing forgets it, while a
wrong alpha, window or sign stays wrong forever.

| Family | index 33 | index 50 | index 75 | index 100 | index 125 | index 149 |
| --- | --- | --- | --- | --- | --- | --- |
| MACD | 5.540e-03 | 3.237e-04 | 4.970e-06 | 7.631e-08 | 1.172e-09 | 2.126e-11 |
| ADX | 3.056e-01 | 1.562e-01 | 4.898e-02 | 1.108e-02 | 2.665e-03 | 6.329e-04 |
| +DI | 6.916e-02 | 3.151e-02 | 3.709e-03 | 5.607e-04 | 2.731e-04 | 1.782e-06 |

TA-Lib covers no VWAP and none of the volume metrics. Those are verified by
hand-computed examples small enough to check on paper — the VWAP of two bars
weighted 100/300, the volume MA of `[10,20,30,40]` at period 2 — plus
invariants (VWAP always lies within the range of typical prices seen so far).

---

## 8. Look-ahead protection

At index `i` an indicator uses only candles `0..i`. No centred window, no
backfill, no normalization against whole-dataset statistics, no future candle
in any seed.

Proven structurally rather than case by case, in
`tests/unit/technical/test_no_lookahead.py`:

1. Compute the **entire** indicator set over a 90-candle prefix.
2. Compute it again over the full 150 candles.
3. Require every one of the first 90 values to be **bit-identical** — including
   requiring that a value which was `None` is still `None`.

A leak anywhere in any indicator changes a historical value and fails there.
The test also walks a growing window (40, 60, 95, 120, 150 candles) and checks
that the newest value at each step equals what the full run produces at that
index — the way live mode will consume the engine, and the strongest available
statement of §74 parity without a live feed.

Two supporting guards: the comparison uses exact equality, not a tolerance,
because identical computation must produce identical bits; and a companion test
asserts the two datasets genuinely differ and that every indicator produced
real values in the prefix, so the comparison cannot pass vacuously.

*During development this test failed*, and the failure was in the test's own
market generator — its trend reversed at `size // 2`, so the two runs were
different markets. Fixed by pinning the reversal to a fixed index. Worth
recording: a no-look-ahead test whose data depends on the requested length
proves nothing.

---

## 9. Tests

**350 collected, 350 passed with PostgreSQL** (346 passed + 4 named skips
without a database). Phase 0 had 78; Phase 1 adds **272**.

| Module | Tests | Kind |
| --- | --- | --- |
| `unit/test_data_quality.py` | 36 | every code, every blocking rule, evidence, determinism |
| `unit/technical/test_golden_vectors.py` | 33 | independent TA-Lib verification, seeding proofs, convergence |
| `unit/test_market_data_providers.py` | 28 | CSV parsing, malformed rows, synthetic determinism, injected defects |
| `unit/technical/test_numeric_safety.py` | 27 | NaN/inf absence, alignment, bounds, price-scale invariance |
| `unit/technical/test_smoothing.py` | 25 | hand-computed SMA/EMA/Wilder, alpha recovery, population σ |
| `unit/technical/test_vwap.py` | 18 | hand-computed, anchoring, zero volume, timezone handling |
| `unit/technical/test_volume.py` | 18 | hand-computed, zero-average handling |
| `unit/technical/test_momentum.py` | 16 | RSI hand-computed + degenerate cases, MACD warm-up and identities |
| `unit/technical/test_volatility.py` | 15 | TR gap capture, ATR seed, Bollinger population σ |
| `unit/technical/test_trend_strength.py` | 15 | DM rules, warm-up scaling, frozen/inside-bar markets |
| `unit/technical/test_engine.py` | 13 | alignment, config, anchoring, phase-boundary check |
| `unit/test_candle_series.py` | 12 | series invariants, Decimal→float boundary |
| `unit/test_load_market_data.py` | 9 | the validation seam, full pipeline, two-provider parity |
| `unit/technical/test_no_lookahead.py` | 5 | future-leakage property, reproducibility, growing window |
| `unit/test_architecture.py` | 5 | +2 new: adapters never compute, technical stays pure |

By kind: **hand-computed reference values** (SMA, EMA, Wilder, RSI, TR, ATR,
Bollinger, VWAP, all volume metrics); **frozen golden vectors** (TA-Lib, 150
bars, 18 series); **edge cases** (empty, one candle, two candles, flat,
constant price, zero volume, gaps, duplicates, unsorted, malformed OHLC, naive
timestamps, extreme scales from 1e-4 to 1e6); **property/invariant tests**
(RSI and DI bounded 0–100, Bollinger ordered and symmetric, VWAP within the
range of typical prices, histogram identity, price-scale invariance, warm-up is
a leading run with no holes); **regression baselines** (our own frozen output
for the two families TA-Lib cannot pin); **look-ahead** (5).

Integration tests are unchanged at 5 — Phase 1 added no persistence, so there
was nothing new to integrate against a database.

---

## 10. Quality gates

Every command ran and passed.

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `ruff check .` | **PASS** |
| Format | `ruff format --check .` | **PASS** — 87 files |
| Types (Linux) | `mypy --platform linux` | **PASS** — 85 source files |
| Types (Windows) | `mypy --platform win32` | **PASS** — 85 source files |
| Architecture | `lint-imports` | **PASS** — **5** contracts kept, 0 broken |
| Backend tests | `pytest` (with PostgreSQL) | **PASS** — **350 passed, 0 skipped**, 10.0 s |
| Backend tests | `pytest` (no database) | **PASS** — 346 passed, 4 named skips |
| Frontend typecheck | `npx tsc -b` | **PASS** |
| Frontend lint | `npm run lint` | **PASS** |
| Frontend format | `npm run format:check` | **PASS** |
| Frontend tests | `npm test` | **PASS** — 12 passed |
| Frontend build | `npm run build` | **PASS** — 694 ms |
| Compose | `docker compose config -q` | **PASS** |
| Backend image | `docker compose build backend` | **PASS** |

No gate was weakened. `mypy` remains strict with `warn_unreachable`; the
import-linter contract count went **up**, not down.

**Alembic: not run, and correctly so.** Phase 1 adds no persistence. The
section 93 `Candle` table is not created, because a table for a feature nothing
writes to is fake implementation (§105) — the same reasoning applied in Phase 0.

**Frontend: not modified.** Phase 1 has no UI; §103 assigns none. Its gates were
run as a regression check only.

---

## 11. Architecture review

Dependency direction holds: `api → application → domain`, adapters implement
ports, the domain depends on nothing outside the standard library.

**Five contracts, up from four.** The new one — *"Adapters never compute
indicators"* — forbids `app.adapters` from importing `app.domain.technical`.
Indicator mathematics is the numerical authority (§1) and stays where it is
tested and type-checked as such; an adapter computing an average would move a
financial calculation outside that guarantee. The contract was **verified to
actually break** by temporarily introducing the import (`4 kept, 1 broken`),
then restoring. Two independent source-scan tests back it up, so the rule
survives a change to the linter configuration.

**No contract was weakened.** The domain purity contract still holds over the
new `technical/` package, which imports nothing but `math`, `datetime`,
`decimal`, `dataclasses` and `enum`.

**The port boundary was preserved, not changed.** The Phase 0
`HistoricalMarketDataProvider` protocol is byte-identical. The additive
`DiagnosticHistoricalMarketDataProvider` is an optional capability a provider
may implement; `LoadValidatedCandles` falls back to `get_candles` when it is
absent, so a provider that only satisfies the original contract works
unchanged. This was the alternative to two worse options: an adapter silently
dropping a malformed row, or raising an exception that aborts an otherwise
usable dataset.

**Live/replay/backtest reuse (§74).** `compute_technicals` takes a
`ValidatedCandleSeries` and nothing else. It has no clock, no I/O, no global
state, and no knowledge of which provider produced the candles — demonstrated
by a test showing two different provider implementations delivering the same
candles produce identical indicator values. Only the provider changes.

---

## 12. Dependencies

**None added.** Not to the runtime, not to the dev extras, not to the frontend.
`backend/pyproject.toml` changed only to add an import-linter contract.

TA-Lib and pandas were installed in a **throwaway virtual environment outside
the project** to generate reference vectors, which are frozen into a JSON
fixture. That keeps genuine independent verification while leaving CI install
time, the dependency surface and the runtime numerical authority untouched.
`docs/technical_conventions.md` §6 records how to regenerate them.

Everything in Phase 1 is standard library: `math`, `decimal`, `datetime`,
`dataclasses`, `enum`, `csv`, `asyncio`, `pathlib`.

---

## 13. Technical debt and known limitations

| Item | Impact | When |
| --- | --- | --- |
| **Three** §11 volume capabilities not implemented — breakout confirmation, volume divergence, time-of-day normalized volume (Raw Volume and the three derived metrics are available, so 4 of 7 are covered) | Expected — each needs Phase 2 structure or verified session metadata | 2, and the session-data phase |
| **Historical Volatility not implemented** — named in §11, absent from the §103 Phase 1 list | Medium — Phase 2 regime work will want a volatility measure. Deliberately deferred rather than guessed; see the note below this table | reconsider **before or during Phase 2** |
| **VWAP session anchoring uses the UTC calendar date** — `DEVELOPMENT_DEFAULT`, not an exchange-correct fact | Medium — a real session VWAP restarts at the exchange session open, which is a §118 fact this project does not yet hold. Any session-aware VWAP or time-of-day logic must wait for verified exchange-session metadata; only `daily_anchors` changes when it arrives | with the verified session data |
| Data quality cannot detect a stale feed | Expected — needs a live feed's last tick | 13 |
| Data quality cannot detect a wrong or expired contract | Expected — needs `ContractMetadataProvider` | 3 |
| MACD and ADX differ from TA-Lib by initialization | Low, fully characterized and decaying — but anyone comparing against a TA-Lib-derived platform will see it in the first ~100 bars | documented, no action |
| Golden fixture is 122 KiB | Low — a large file in the repo, the price of frozen verification | — |
| Indicator functions accept raw float sequences | Low, deliberate — they are independently testable and defend their own contracts; only `compute_technicals` requires a validated series | — |
| Bollinger σ could overflow at absurd price scales (≫1e150) | Very low — not a plausible price; tested to 1e6 | — |
| `Decimal`-context independence rests on quantizing after division in the synthetic provider | Low — verified, but a subtle property | — |
| Deprecated event-loop policy API, removed in Python 3.16 | Medium — carried over from Phase 0, unchanged | before adopting 3.16 |
| Turkish text still unreviewed by a native speaker | Low — carried over | 5 |

### Deferred: Historical Volatility

Recorded explicitly so it is not lost between phases.

* Master spec **§11 does include Historical Volatility**, under VOLATILITY,
  alongside ATR and Bollinger Bands.
* Master spec **§103 does not name it** in the Phase 1 scope, which lists
  OHLCV, data validation, EMA, SMA, RSI, MACD, ATR, VWAP, ADX, Bollinger Bands,
  volume metrics and tests. §103 is the phase authority, so it was **not**
  implemented in Phase 1.
* This is an **intentional deferral, not an oversight**, and it was not stubbed,
  scaffolded or partially built.
* It **must be reconsidered before or during Phase 2**, where regime
  classification (§14) needs a volatility measure and is the natural owner of
  the decision.

When it is taken up, one constraint applies from the start: **no market-specific
annualization factor or session assumption may be invented.** A historical
volatility figure is meaningless without stating the return definition (close-to-close,
log or simple), the window, and — if annualized — the number of trading periods
per year. That last number is a property of the VIOP trading calendar: a mutable
exchange fact under §118. Until it is obtained from an authoritative source, the
measure must either be reported **unannualized in per-candle terms**, or carry a
`VerifiedValue` whose status is `UNVERIFIED`. Hard-coding "252 trading days" or
any session count would be precisely the fabrication §118 forbids — and it would
be invisible, because the resulting number looks perfectly plausible.

The same constraint already governs VWAP anchoring and Time-of-Day Normalized
Volume above. All three are blocked on the same missing input: **verified
exchange-session metadata.**

**No placeholder was marked complete.** Everything above is either
IMPLEMENTED and tested, or explicitly NOT STARTED with the phase that owns it.

---

## 14. Phase boundary verification

No Phase 2+ work was implemented, scaffolded, or prepared for. Explicitly
**absent**:

market structure engine · HH/HL/LH/LL · BOS/CHOCH · swing detection ·
support/resistance · regime detection · strategy routing · setup scoring ·
evidence fusion · contradiction engine · risk engine · position sizing ·
margin · P&L · futures contract metadata · basis · open interest analysis ·
screenshot vision · any LLM or Claude call · news · live feeds · WebSockets ·
paper trading · backtesting · replay · shadow mode · broker integration ·
Midas · `OrderExecutionPort` · real execution.

Verified mechanically: no `app/domain/structure/`, `futures/`, `risk/`,
`setups/`, `strategies/`, `trading/` or `backtest/` package exists; no
speculative empty package was created; `TechnicalSnapshot` is asserted by test
to carry no field named `trend`, `regime`, `bias`, `signal`, `score`,
`quality`, `setup`, `decision` or `recommendation`, so an interpretation layer
cannot have leaked in.

The Phase 0 test asserting no broker, execution or Midas module exists in
`ports/` still passes. Section 120 holds: no order is sent, and nothing was
built that could send one.

**Next phase would be PHASE 2 — MARKET STRUCTURE + REGIME:** swings, HH/HL/LH/LL,
BOS/CHOCH, support/resistance zones, and market regime classification, built on
the Phase 1 indicators.

Two Phase 1 deferrals fall due there and should be decided explicitly at its
start rather than drifting: **Historical Volatility** (§13 above), and the two
Phase 2-blocked volume capabilities — **Breakout Volume Confirmation** and
**Volume Divergence** — which become computable once swing structure and
support/resistance exist.

---

## 15. Git state

```
HEAD:   df96bd9  Update GitHub Actions to Node 24 runtime  (main, origin/main)
```

**Nothing was committed or pushed by Claude.** `HEAD` is unchanged from the
start of Phase 1; the git-write hook remained active throughout.

**Modified (5)** — `backend/app/application/ports/market_data.py`,
`backend/app/domain/market/__init__.py`, `backend/pyproject.toml`,
`backend/tests/unit/test_architecture.py`, `docs/architecture.md`

**Untracked (13)** — `backend/app/adapters/market_data/`,
`backend/app/application/use_cases/load_market_data.py`,
`backend/app/domain/market/quality.py`, `backend/app/domain/market/series.py`,
`backend/app/domain/technical/`, `backend/tests/factories.py`,
`backend/tests/fixtures/`, `backend/tests/unit/technical/`,
`backend/tests/unit/test_candle_series.py`,
`backend/tests/unit/test_data_quality.py`,
`backend/tests/unit/test_load_market_data.py`,
`backend/tests/unit/test_market_data_providers.py`,
`docs/phase_reports/phase_1_completion_report.md` *(this file)*

`docs/viop_master_spec.md` is unmodified.

### Post-approval closeout

Applied after technical approval, in two steps. It corrected documentation and
one module docstring; **no executable logic changed anywhere.**

| File | Change | Kind |
| --- | --- | --- |
| `docs/phase_reports/phase_1_completion_report.md` | This corrected final report | documentation |
| `docs/technical_conventions.md` | Volume section restated as 4 of 7 available / 3 deferred; Historical Volatility deferral and its §118 annualization constraint recorded | documentation |
| `backend/app/domain/technical/volume.py` | **Module docstring only** — the volume-capability accounting restated | production file, docstring only |

**The one production-file change.** `backend/app/domain/technical/volume.py`
carried the same miscount in its module docstring, describing the split as
"three implemented, four not". Correcting it required editing a production
file, so it was done as a separate, explicitly scoped step rather than folded
into the documentation pass.

The edit is confined to the module docstring. **No executable logic, import,
function signature, formula, constant, default or behaviour changed** — the
diff touches only lines inside the opening `"""..."""` block, and the test
count is unchanged at 350, which a behavioural change could not leave
untouched.

The pre-approval draft `docs/phase_reports/phase_1_report.md` was superseded by
this file and removed, so the resume protocol in `CLAUDE.md` finds exactly one
Phase 1 report and it is the corrected one. It was untracked and had never been
committed. Phase 0 reports were **not** touched.

### Volume accounting now agrees everywhere

The inconsistency recorded in earlier drafts is resolved. All three places that
state the volume position now say the same thing:

| Source | States |
| --- | --- |
| `backend/app/domain/technical/volume.py` module docstring | 4 of 7 available, exactly 3 deferred |
| `docs/technical_conventions.md` §4 | 4 of 7 available, exactly 3 deferred |
| This report, §1 and §13 | 4 of 7 available, exactly 3 deferred |

Available: **Raw Volume** (as `Candle.volume`), **Volume Moving Average**,
**Relative Volume**, **Volume Acceleration**.
Deferred: **Breakout Volume Confirmation**, **Volume Divergence**,
**Time-of-Day Normalized Volume**.

No open documentation inconsistency remains in Phase 1.

### Final validation after the docstring correction

Re-run in full after the last edit, so these results describe the tree as it
now stands rather than an earlier state.

| Gate | Result |
| --- | --- |
| `ruff check .` | **PASS** |
| `ruff format --check .` | **PASS** — 87 files |
| `mypy --platform linux` | **PASS** — 85 source files |
| `mypy --platform win32` | **PASS** — 85 source files |
| `pytest` (with PostgreSQL) | **PASS** — **350 passed**, 0 skipped |
| `lint-imports` | **PASS** — 5 contracts kept, 0 broken |

Identical to the pre-closeout results in §10, which is the expected outcome of
a docstring-only edit: the test count did not move, the contract count did not
move, and both mypy platform targets stayed clean.

---

# STOP

Phase 1 is complete, validated and technically approved. The post-approval
closeout is finished and re-validated: documentation, the module docstring and
this report now agree, and every gate passes on the tree as it currently
stands. Phase 2 has not been started, scaffolded, or prepared for. No Phase 2
dependency was installed. Nothing was committed or pushed.

Awaiting explicit approval to proceed to Phase 2.
