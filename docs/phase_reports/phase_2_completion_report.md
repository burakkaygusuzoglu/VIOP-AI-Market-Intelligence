# PHASE 2 COMPLETION REPORT

Date: 2026-08-22 · Follows `phase_1_completion_report.md` · Phase 2 only

---

## 1. Baseline / Git state

Inspection before any file was touched:

| Check | Finding |
| --- | --- |
| Phase 1 committed | **Yes** — `34ea0bf` *Complete Phase 1 market data and technical engine* |
| Working tree | **Clean** |
| Branch / remote | `main`, synced with `origin/main` |
| Phase 1 really exists | 17 domain modules including the full `technical/` package |
| Phase 2 already present | **No** — the only matches for swing/BOS/regime were forward-looking docstrings in Phase 1 files |

Phase 2 scope read from master spec §103 (Phase 2), §12 market structure, §13
support/resistance, §14 regime, §27 false breakout, §28 retest.

---

## 2. Scope delivered

| Requirement | Status |
| --- | --- |
| Swing high / swing low | IMPLEMENTED |
| HH / HL / LH / LL | IMPLEMENTED |
| BOS | IMPLEMENTED |
| CHOCH | IMPLEMENTED |
| Support zones | IMPLEMENTED |
| Resistance zones | IMPLEMENTED |
| Breakout primitives | IMPLEMENTED |
| Breakdown primitives | IMPLEMENTED |
| False breakout primitives | IMPLEMENTED |
| Retest primitives | IMPLEMENTED |
| Market regime classification | IMPLEMENTED — all 11 regimes |
| Breakout Volume Confirmation *(Phase 1 deferral)* | IMPLEMENTED |
| Volume Divergence *(Phase 1 deferral)* | IMPLEMENTED |
| Historical Volatility *(Phase 1 deferral)* | IMPLEMENTED — unannualised |
| Time-of-Day Normalized Volume | **NOT STARTED**, as instructed — still blocked on verified session metadata |

---

## 3. Files added / modified

### Domain — `app/domain/structure/` (new package, stdlib only)

`swings.py` · `market_structure.py` · `events.py` · `zones.py` ·
`breakouts.py` · `divergence.py` · `regime.py` · `engine.py` · `__init__.py`

### Domain — `app/domain/technical/` (Phase 1, extended)

`volatility.py` — added `log_returns` and `historical_volatility`. Reuses the
existing `rolling_population_stdev`; no formula was duplicated.

### Tests

`tests/unit/structure/` — `test_swings.py`, `test_market_structure.py`,
`test_events.py`, `test_zones.py`, `test_breakouts.py`, `test_divergence.py`,
`test_regime.py`, `test_no_lookahead.py`, `test_engine.py`
`tests/unit/technical/test_historical_volatility.py`
`tests/factories.py` — added `pivot_series` and `prefix_of`
`tests/unit/test_architecture.py` — unchanged (its existing checks still cover
the new package through the contract set)

### Configuration

`backend/pyproject.toml` — one contract widened, one added. **No dependency
added.**

`docs/viop_master_spec.md` was **not** modified. No API route, no frontend
change, no database table, no migration.

---

## 4. Swing detection semantics

A candle `i` is a swing high when both windows fit and:

* `high[i] > high[j]` for every `j` in `[i-left, i-1]` — **strictly** above
  everything left;
* `high[i] >= high[j]` for every `j` in `[i+1, i+right]` — **at least**
  everything right.

Swing lows mirror it. Defaults `left = right = 2`, pivot price from the wick
(`use_wicks=True`), configurable.

**Two timestamps, and this is the whole phase.** A `SwingPoint` carries
`pivot_index`/`pivot_time` (where the extreme is) and
`confirmed_index`/`confirmed_time` (`pivot_index + right` — the first candle at
which it could have been recognised). Every downstream engine filters by
`confirmed_index` via `swings_known_at`.

**Equal highs.** The strict-left / permissive-right asymmetry is the
tie-break: on a plateau the *earliest* bar qualifies and every later bar is
rejected, so one plateau yields exactly one pivot at the bar that first reached
the level. Strict on both sides would erase double tops entirely; permissive on
both would mark every bar of a flat stretch.

**Flat market → no swings at all.** No bar is strictly above its neighbour.
That is the honest answer for a tape with no structure.

**Insufficient history.** Shorter than `left + right + 1` returns empty.
Nothing is inferred from a partial window.

Tested: uptrend, downtrend, range, equal highs, equal lows, flat, noisy,
insufficient history, price scales from 1e-4 to 1e6.

---

## 5. HH / HL / LH / LL semantics

Each swing is compared with the previous swing **of its own type**. A swing
high is never compared with a swing low.

`equal_tolerance` defaults to **exactly zero**. Exchange prices arrive on a
tick grid as exact `Decimal`s, so equality is a real observable event; any
non-zero default would be a guess about one instrument's tick size, which is
the class of assumption §118 exists to prevent. Configurable, and both settings
are tested.

Bias truth table:

| last high | last low | bias |
| --- | --- | --- |
| HH | HL | BULLISH |
| LH | LL | BEARISH |
| LH | HL | CONTRACTING |
| HH | LL | EXPANDING |
| EQH or EQL | any | AMBIGUOUS |
| fewer than two of either | | INSUFFICIENT |

CONTRACTING and EXPANDING are kept distinct from AMBIGUOUS because they are
informative — a narrowing range versus a widening one — while AMBIGUOUS means
the comparison genuinely did not resolve. **No ambiguous sequence is forced
into a direction.**

**Missing alternation** is handled rather than assumed away: detection can
produce two highs with no low between them. Labelling still works (same-type
comparison), and `MarketStructure.alternates` tells the reader it happened.

---

## 6. BOS / CHOCH semantics

> **Revised during human review.** The first version of this engine labelled a
> break from a non-directional structure as BOS. That was wrong, and §6a below
> records the review and the correction.

All three are the same physical event — a close beyond a confirmed swing — and
differ only in the bias that preceded it:

* break **with** a *directional* structure → **BOS**
* break **against** a *directional* structure → **CHOCH**
* break from a non-directional structure (CONTRACTING / EXPANDING / AMBIGUOUS /
  INSUFFICIENT) → **LEVEL_BREAK** — the level was taken out, and nothing
  beyond that is claimed.

`StructuralEventType.is_classified` is true only for BOS and CHOCH, so a
consumer can tell at a glance whether the label carried a directional reading.

**Confirmation:** `CLOSE` by default. A wick through a level is a test, not
structure — it is exactly what a stop run looks like. `WICK` is available and
explicit.

**Tolerance:** `breach_tolerance`, absolute `Decimal`, default 0 (strict
inequality). Deliberately *not* ATR-scaled: an ATR-scaled threshold would make
a structural level depend on a smoothed float estimate, so the same candle
could break a level on one run and not after an ATR period change.

**Invalidation:** a swing is consumed by the break that takes it out and never
fires again. Without this a holding trend emits an identical BOS every candle.

**Evidence on every event:** type, direction, broken level, origin swing
(*including its own confirmation index*), event index/time, confirmed
index/time, prior bias, confirmation method, breach price, and a reason string.

`event_index == confirmed_index` here, and that is deliberate: only closed
candles are consumed, so the candle that closes beyond a level both causes and
settles the break. Both fields exist because the breakout and retest engines
have genuine lag between them.

**None is a buy or sell signal.** Nothing in this phase emits a direction to
trade.

---

## 6a. BOS / CHOCH semantic review (human review, 2026-08-22)

The reviewer challenged the third rule: *can an event honestly be classified as
BOS when there was no directional structure to continue or reverse?*

**Finding: no. The original behaviour forced certainty. It has been fixed.**

**What the specification requires.** §12 lists BOS and CHOCH among the things to
detect; §103 names them in the Phase 2 scope. Neither passage requires that
*every* break be one of the two, and nothing in the spec forbids recording a
break that is neither. The spec constrains what must be detected, not that the
set is exhaustive.

**What consumes the label.** `structural_events` is read by nothing today — the
regime engine consumes `breakout_events`. The label is currently inert, which is
exactly why this was the moment to fix it: §16 evidence fusion is where a "BOS"
will be weighed as trend-continuation evidence, and it would inherit the false
premise from a field it does not re-derive.

**Why the original justification failed.** The code argued that *"the market
broke a level" is a fact while "the character changed" is a claim*. The first
half is right; the second does not license BOS. **Break of Structure asserts
both that a structure existed and that this break continued it.** Applied to an
undecided market it writes a trend into the record that the swings never
showed — the same forced classification the phase already refuses at the
structure layer with `AMBIGUOUS` and `INSUFFICIENT`.

**The steelman, and why it lost.** One could argue BOS simply means "a level was
broken", with CHOCH as a subtype, and that consumers should read `prior_bias` to
disambiguate — which the original test did assert. But that makes the enum
uninformative about its own semantics and requires every future consumer to
remember to check a second field before trusting the first. That is precisely
the trap `ValidatedCandleSeries` was built to remove: the type should carry the
guarantee rather than delegate it to discipline.

**The fix.** A third member, `StructuralEventType.LEVEL_BREAK`. BOS and CHOCH
are now emitted *only* from a directional prior bias.

**Tests proving the label cannot misrepresent an unknown structure** (7 added,
`test_events.py` 14 → 21):

| Test | Guarantees |
| --- | --- |
| `test_a_classified_break_always_had_a_directional_bias` | Over a 200-candle noisy market: every BOS/CHOCH has a directional `prior_bias`, every `LEVEL_BREAK` has a non-directional one, and BOS-vs-CHOCH matches continuation-vs-opposition exactly |
| `test_the_market_produces_all_three_kinds_so_the_invariant_is_not_vacuous` | The invariant above is not passing on an empty or one-sided set |
| `test_a_break_from_an_undecided_structure_is_neither_bos_nor_choch` | The corrected behaviour on the original scenario |
| `test_every_non_directional_bias_produces_a_level_break` | All four non-directional biases, parametrised |
| `test_only_bos_and_choch_count_as_classified` | `is_classified` semantics |

**Not turned into a trade signal.** `LEVEL_BREAK` carries no direction to act
on; it is a strictly weaker statement than the labels it replaced.

**Behavioural blast radius: none on legitimate cases.** Re-running the 400-candle
synthetic review market produced 32 events, all still BOS, because that market
has genuinely bullish structure throughout. Only breaks from undecided structure
changed label.

---

## 7. Support / resistance zones

**Zones are spanned by prices the market actually paid.** `low = min(swing
prices in the cluster)`, `high = max(...)` — exact `Decimal`, every boundary a
price at which the market genuinely turned.

The rejected alternative was `level ± k × ATR`, which produces edges that are a
smoothed float estimate wearing the costume of a price: change the ATR period
and "support" moves though nothing in the market did. ATR appears **only** as
the clustering tolerance — a comparison, not a level.

**Clustering is complete linkage**, bounding a zone's total spread to
`cluster_atr_multiple × ATR`. This was a defect found and fixed during
development: single linkage chains, and a 300-candle uptrend produced *one*
resistance zone 14% of price wide with 25 touches — an impressive-looking
object describing nothing. Complete linkage produced 6 support and 6 resistance
zones on the same data, ~1.6 wide, 4 touches each.

**`min_touches = 2`.** One swing would give a zero-width band — the
"unrealistic single exact value" §13 tells us to avoid. Configurable.

**Strength score, 0–100 — a heuristic quality score, never a probability.**
Four components, each normalised to 0–1 and exposed individually on
`ZoneScoreBreakdown` so the number can be argued with rather than trusted:
touches (saturating at 5), recency (half-life decay), reaction magnitude
(ATR-normalised travel away from the zone), and Phase 1 relative volume at the
touch candles. Weights configurable and validated to sum to 1.

§13 also lists *timeframe importance*; that is multi-timeframe fusion, which is
explicitly out of Phase 2 scope and was not implemented.

---

## 8. Breakout / false breakout

Lifecycle, each stage its own event with its own confirmation index:

```
CHALLENGE   price enters the zone without closing beyond it
BREACH      a candle closes beyond the zone
  ├── FALSE_BREAKOUT   a close returns inside within failure_window
  └── CONFIRMED        the breach survived the whole window
```

`failure_window` (default 3) is *also* the confirmation lag, and necessarily
so: `breach + failure_window` is the first candle at which the breach is known
**not** to have failed.

**The breach event is never rewritten.** A false breakout is a separate event
stamped at the candle that revealed the failure. A reader asking "what did we
know at the breach?" gets a breach and nothing more. This is the single most
important guarantee in the module — labelling the breach candle "false
breakout" would let a backtest decline every losing break using information
from its own future.

An unresolved breach at the end of the data emits **neither** outcome, because
neither is known yet.

**Breakout volume confirmation** reads Phase 1 `relative_volume` at the breach
candle: `CONFIRMED` at or above `volume_confirmation_multiple` (default 1.5x),
`WEAK` below, `UNAVAILABLE` during warm-up. `UNAVAILABLE` exists so a
measurement that was never taken cannot read as a measurement of zero. It never
becomes LONG or SHORT — the enum has exactly three members and a test pins that.

---

## 9. Retest logic

A retest attaches **only** to a `CONFIRMED` breakout and scanning starts at
`confirmed_index + 1`, so it can never precede the breakout it belongs to.
Discovering one later never edits the breakout event.

`TOUCHED` when price re-enters the broken zone within `retest_window` (20).
Then `HELD` if a close leaves in the breakout direction, `FAILED` if a close
goes back through, both within `retest_resolution_window` (5). Neither → the
retest is reported as `TOUCHED` and nothing more, which is an honest "not yet
known".

Each event carries the source zone, the breakout's confirmation index, its own
event and confirmation timestamps, the price and a reason.

---

## 10. Volume divergence / breakout volume confirmation

**Volume divergence** compares two **confirmed** swings of the same type:

* **Bearish** — higher high, lower volume.
* **Bullish** — lower low, lower volume.
* Rising volume into a new extreme is *confirmation*, not divergence, and
  produces no event.

The volume measure is Phase 1 `volume_moving_average` at the pivot index, not
the raw volume of the single pivot candle: one candle's volume is noisy enough
that the comparison would flip on a single large print, while the 20-candle
average describes the participation behind the move.

Price equality uses the same `equal_tolerance` as the structure labels, so
"higher high" means one thing across the phase. Volume uses a *relative*
`volume_tolerance` (default 5%), so a 0.1% difference is not a signal.

A divergence is dated by its **later pivot's confirmation** — that is when it
became knowable. If either pivot falls in the volume warm-up, no event is
emitted; the comparison was not possible, and a zero would claim it was.

Breakout volume confirmation is covered in §8.

---

## 11. Historical Volatility decision

**Implemented, and deliberately not annualised.**

* Returns are `ln(close[i] / close[i-1])` — log rather than simple, because
  they are additive over time and symmetric in direction.
* The value is the **population** standard deviation over `period` returns, the
  same convention as the Bollinger bands in the same module, so the two
  volatility measures cannot silently disagree.
* First value at index `period` — one later than an SMA, because index 0 yields
  no return.
* Constant price → exactly `0.0`. Constant *proportional* growth → `0.0` too,
  which is correct.

**No annualisation constant was invented.** Periods-per-year is a property of
the VIOP trading calendar and therefore a §118 fact this project does not hold.
The failure mode is uniquely dangerous because it is invisible: multiply by
`sqrt(252)` for an instrument trading a different number of sessions and the
answer is still a plausible-looking percentage, merely wrong. The widely copied
252 describes US equities.

So the number returned is per candle, as a fraction — `0.012` means 1.2% per
candle of the series' own timeframe. `test_the_value_is_per_candle_and_not_annualised`
fails the moment anyone multiplies by a trading-calendar constant.

---

## 12. Regime engine

All eleven §14 regimes, evaluated in order; first match wins and the reason
records which rule fired:

1. ADX or EMA stack warming up → `UNCERTAIN`
2. Confirmed breakout within `breakout_recency` → `BREAKOUT` / `BREAKDOWN`
3. Directional structure **and** ADX ≥ `strong_adx` **and** agreeing EMA stack
   → `STRONG_UPTREND` / `STRONG_DOWNTREND`
4. Structure and EMA stack agree, ADX has not confirmed → `WEAK_UPTREND` /
   `WEAK_DOWNTREND`
5. High volatility **and** no trend strength **and** EXPANDING structure →
   `CHAOTIC`
6. No trend strength and non-directional structure, split by volatility state →
   `LOW_VOLATILITY_RANGE` / `RANGE` / `HIGH_VOLATILITY_RANGE`
7. Anything else → `UNCERTAIN`

**No single indicator decides.** Every branch requires agreement between at
least two independent sources. Pinned by tests: raising the ADX bar demotes a
strong trend to weak, and a bullish EMA stack alone does not produce an uptrend
when structure disagrees.

**Volatility is measured against the instrument's own history** — the current
ATR-to-price ratio against its trailing median over 100 candles, classified
`LOW` / `NORMAL` / `HIGH` by configurable multiples. An absolute threshold like
"ATR above 3% of price" would be an instrument-specific constant; the trailing
median needs no exchange fact and cannot look ahead.

Every threshold is explicit, configurable, deterministic and documented as a
**project heuristic**. None describes VIOP, a contract or a session, so none is
a §118 fact. **None is a probability.**

`RegimeEvidence` records every input actually used — structure bias, EMA stack,
ADX, ATR, ATR ratio and its median, volatility state, historical volatility,
range width, recent breakout — so a reader can check the reasoning rather than
trust the label.

`UNCERTAIN` and `CHAOTIC` are first-class outputs, and every one of the eleven
is produced by a market that deserves it in the test suite. **No regime is a
trade recommendation**; a test asserts the assessment carries no field named
for an action.

---

## 13. No-look-ahead proof

The Phase 2 property is stricter than Phase 1's, and stated differently.

Phase 1 asked whether an indicator *value* could change when later candles
arrived. Phase 2 must allow later candles to reveal **new** structure — a pivot
at candle 100 genuinely does not exist until 102 — while forbidding any claim
that it was known earlier. So the assertion is:

> everything the full series says was knowable at candle N
> == everything the N-candle prefix reports at all

`tests/unit/structure/test_no_lookahead.py` applies this over a 140-candle
prefix of a 220-candle market to **swings, HH/HL/LH/LL, BOS/CHOCH, zones,
breakouts, false breakouts, retests, divergence, historical volatility and the
regime**, with guards that the two runs are the same market, that the longer
run really does discover more, and that every engine produced something.

**It found two genuine defects**, both fixed:

1. **Zone boundaries were computed from the whole series.** A breach at candle
   40 was measured against a band whose edges were fixed partly by swings
   confirming at candle 200 — a breach that could not have been detected in
   real time. Fixed by giving `build_zones` an explicit `as_of` parameter that
   restricts the swings, the reference ATR and the recency measurement, and by
   scanning breakouts against zones rebuilt at each swing-confirmation
   checkpoint.

2. **A band could inherit an old confirmation date.** The clustering tolerance
   is an ATR multiple, so a later checkpoint with a different ATR can group
   long-confirmed swings into a band that had never existed before. Dating it
   by its touches claimed it was available hundreds of candles early. Fixed by
   stamping each band with the checkpoint that produced it.

Neither was visible by reading the code. Both were caught by the test.

Supporting guarantees: every swing satisfies `confirmed_index == pivot_index +
right` and reports `known_at` false one candle earlier; a later swing never
reclassifies an earlier break (the prior bias is frozen on the event); a
growing window is checked at 80, 120, 160 and 200 candles as well as the main
cut; and analysis is bit-for-bit reproducible.

---

## 14. Tests and exact counts

**575 collected, 575 passed** with PostgreSQL. Phase 1 ended at 350, so Phase 2
adds **225** (218 in the build, 7 more from the §6a semantic review).

| Module | Tests |
| --- | --- |
| `structure/test_swings.py` | 33 |
| `structure/test_no_lookahead.py` | 28 |
| `structure/test_regime.py` | 28 |
| `structure/test_market_structure.py` | 26 |
| `structure/test_breakouts.py` | 25 |
| `structure/test_events.py` | 21 |
| `structure/test_zones.py` | 21 |
| `technical/test_historical_volatility.py` | 19 |
| `structure/test_divergence.py` | 14 |
| `structure/test_engine.py` | 10 |

Scenario coverage as required: uptrend, downtrend, sideways range,
low-volatility range, high-volatility range, breakout, breakdown, false
breakout, successful retest, failed retest, equal highs, equal lows, flat
market, chaotic/noisy market, insufficient data, repeated calculation and
determinism, price scales 1e-4 to 1e6.

Integration tests unchanged at 5 — Phase 2 added no persistence.

---

## 15. Quality gates

Every command ran and passed.

| Gate | Result |
| --- | --- |
| `ruff check .` | **PASS** |
| `ruff format --check .` | **PASS** — 107 files |
| `mypy --platform linux` | **PASS** — 105 source files |
| `mypy --platform win32` | **PASS** — 105 source files |
| `lint-imports` | **PASS** — **6** contracts kept, 0 broken |
| `pytest` (with PostgreSQL) | **PASS** — **575 passed, 0 skipped**, 9.7 s |
| Frontend tsc / eslint / prettier | **PASS** |
| Frontend tests | **PASS** — 12 passed |
| Frontend build | **PASS** — 679 ms |
| `docker compose config -q` | **PASS** |
| `docker compose build backend` | **PASS** |

No gate was weakened. The import-linter contract count went **up**, from 5 to
6. mypy remains strict with `warn_unreachable`. 77 mypy errors from untyped
test helpers were fixed by adding real annotations, not by suppression.

**All backend gates above were re-run after the §6a semantic change**, which
touched executable code. Frontend and Docker gates were **not** re-run at that
point and their figures are from the build pass — the semantic change is
confined to `app/domain/structure/events.py` and its tests, and touched no
frontend file, dependency or runtime configuration.

---

## 16. Architecture review

Dependency direction holds: `api → application → domain`, adapters implement
ports, the domain depends on nothing outside the standard library.

**Six contracts, up from five:**

* *"Adapters never compute indicators"* was **widened** to
  *"Adapters never compute indicators or market structure"*, adding
  `app.domain.structure` to the forbidden set. No adapter may compute swings,
  BOS, CHOCH, zones, breakouts, retests, divergence or regime.
* **New:** *"The technical engine does not depend on market structure"*. The
  dependency runs `structure → technical` and never back, so Phase 1 indicators
  stay usable on their own and an indicator cannot reach for structure and
  create a circular definition.

**No Phase 1 formula was duplicated.** ATR, ADX, EMA, relative volume and the
volume average all arrive from `TechnicalSnapshot`; historical volatility reuses
`rolling_population_stdev`. A test greps the structure engine, regime and zone
modules for indicator definitions and fails if one appears.

**`ValidatedCandleSeries` is reused**, so structure can only be computed on
candles the Data Quality Engine cleared — the Phase 1 type guarantee carries
forward unchanged.

**No LLM touches any of it.** The whole package is stdlib-only deterministic
domain code.

---

## 17. Technical debt / limitations

| Item | Impact | When |
| --- | --- | --- |
| Time-of-Day Normalized Volume still not implemented | Expected — blocked on verified exchange-session metadata (§118) | the phase that obtains it |
| VWAP session anchoring still uses the UTC calendar date, `DEVELOPMENT_DEFAULT` | Carried from Phase 1, unchanged | with the verified session data |
| `detect_structural_events` rebuilds the structure at every candle — O(candles × swings) | Low now (220 candles is instant), noticeable at ~10k candles | before the backtest phase |
| `_historical_zones` rebuilds zones at each swing confirmation | Same shape of cost, same horizon | before the backtest phase |
| Retests attach to zone breakouts only; BOS levels are not separately retested | Medium — a BOS level that is also a zone *is* covered, an isolated one is not | Phase 4, when setups need it |
| One challenge and one breach outcome per zone | Low, deliberate — after a genuine break the zone is no longer that level | — |
| Zone strength weights are unvalidated heuristics | Medium — plausible, never measured against outcomes | Phase 12 backtest can calibrate |
| Multi-timeframe fusion absent, so §13 "timeframe importance" is not in the score | Expected — explicitly out of Phase 2 scope | Phase 4 |
| Equal-level tolerance defaults to exact equality, so `EQH`/`EQL` are rare on noisy data | Low, deliberate and configurable | — |
| Deprecated event-loop policy API, removal in Python 3.16 | Carried from Phase 0 | before adopting 3.16 |
| Turkish text still unreviewed by a native speaker | Carried | Phase 5 |

**No placeholder was marked complete.** Everything above is either IMPLEMENTED
and tested, or explicitly NOT STARTED with the phase that owns it.

---

## 18. Phase-boundary verification

No Phase 3+ work was implemented or scaffolded. Explicitly **absent**:

`ContractMetadataProvider` implementation · futures multiplier · tick size ·
margin · P&L · position sizing · Risk Engine · multi-timeframe fusion ·
evidence fusion · contradiction engine · setup scoring · LONG/SHORT
recommendation · Beginner/Pro UI · Claude Vision · Claude synthesis · news ·
paper trading · replay · backtesting · shadow mode · live feeds · WebSockets ·
broker integration · Midas · `OrderExecutionPort`.

Verified mechanically:

* A test greps the entire `app/domain/structure/` package for `multiplier`,
  `tick_size`, `margin`, `position_size`, `ContractMetadata` and
  `OrderExecution` — none appears.
* A test asserts `StructureSnapshot` carries no field named `signal`, `setup`,
  `score`, `recommendation`, `decision`, `entry`, `stop`, `target`,
  `position_size` or `risk`.
* A test asserts `RegimeAssessment` has exactly the fields `regime`,
  `evidence`, `reason`, `config`.
* No `app/domain/futures/`, `risk/`, `setups/`, `strategies/`, `trading/` or
  `backtest/` package exists; no speculative empty package was created.
* The Phase 0 test asserting no broker, execution or Midas module exists in
  `ports/` still passes.

Section 120 holds: no order is sent, and nothing was built that could send one.

### Critical review performed

The review the phase prompt requires was run as 19 empirical probes against 400
candles of generated data — not by reading. All 19 pass. Three of the fourteen
named risks were **real defects, found and fixed**:

| Risk | Found by | Fix |
| --- | --- | --- |
| Overfitted support/resistance zones | smoke run during development | Complete-linkage clustering (§7) |
| Future-dependent false-breakout labels | the no-look-ahead suite | `as_of` zone construction and checkpoint dating (§13) |
| **False BOS** | **human review** | **`LEVEL_BREAK` third event type (§6a)** |

The remaining eleven — pivot look-ahead, backdated swings, off-by-one
confirmation, incorrect HH/HL/LH/LL comparison, equal-level ambiguity, false
CHOCH, wick/close ambiguity, future-dependent retests, divergence on
unconfirmed pivots, forced regime classification, duplicated Phase 1
formulas — were checked and were clean.

Worth recording: the false-BOS defect survived my own critical review because
the probe I wrote asked only whether *CHOCH* required a directional bias. It
did. I never asked the same question of BOS, because I had already convinced
myself the fallback was defensible. The reviewer asked it, and the answer was
no. A self-review that only tests the properties the author already believes is
weaker than it looks.

---

## 19. Final Git status

```
HEAD:  34ea0bf  Complete Phase 1 market data and technical engine  (main, origin/main)
```

**Nothing was committed or pushed by Claude.** `HEAD` is unchanged from the
start of Phase 2; the git-write hook remained active throughout.

**Modified (4)**
`backend/app/domain/technical/volatility.py`
`backend/pyproject.toml`
`backend/tests/factories.py`
`docs/architecture.md`

**Untracked (4)**
`backend/app/domain/structure/`
`backend/tests/unit/structure/`
`backend/tests/unit/technical/test_historical_volatility.py`
`docs/phase_reports/phase_2_completion_report.md` *(this file)*

`docs/viop_master_spec.md` is unmodified.

### Human-review closeout (2026-08-22)

| File | Change |
| --- | --- |
| `backend/app/domain/structure/events.py` | Added `StructuralEventType.LEVEL_BREAK` and `is_classified`; BOS/CHOCH now require a directional prior bias; docstrings rewritten |
| `backend/tests/unit/structure/test_events.py` | One test corrected, six added (14 → 21) |
| `docs/architecture.md` | Contracts 4–6 updated for Phase 2; `structure/` marked implemented; three cross-cutting decisions recorded |
| `docs/phase_reports/phase_2_completion_report.md` | This report, persisted and corrected |

Phase 0 and Phase 1 reports were **not** touched.

---

# STOP

Phase 2 is complete and validated. Phase 3 has not been started, scaffolded, or
prepared for. No Phase 3 dependency was installed. Nothing was committed or
pushed.

Awaiting human review.
