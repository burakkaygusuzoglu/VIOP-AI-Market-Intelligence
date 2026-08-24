# PHASE 4 COMPLETION REPORT

Date: 2026-08-24 · Follows `phase_3_completion_report.md` · Phase 4 only
(4A + 4B)

---

## 1. Baseline / Phase 4A recovery

Phase 4A was delivered in an earlier session and left uncommitted. Recovery
inspection before anything was modified:

| Check | Finding |
| --- | --- |
| HEAD | `f631c79` *Complete Phase 3 futures and risk engine* |
| Working tree | Phase 4A present and uncommitted — `app/domain/analysis/`, `tests/factories_analysis.py`, `tests/unit/analysis/`, plus a modified `pyproject.toml` |
| Phase 4A baseline | **930 tests** (926 + 4 integration), re-run and confirmed |
| Import contracts | 10, all kept |
| Broken intermediate state | **None** — everything imported, typed and passed |

### The §16 audit, and its answer

The instruction was to establish whether Phase 4A actually satisfied the
Evidence Fusion Engine or only generated and concatenated `EvidenceItem`s.

**It only collected.** `MultiTimeframeAnalysis` exposed a flat `evidence`
tuple, a `contract_evidence` tuple and the contradiction report. There was no
partition by direction, no grouping, no `reliability`, and therefore no way to
answer "what supports the bullish case" without every consumer re-deriving it.
Worse, a flat tuple lets one observation speak as many times as it happens to
be recorded — which is precisely the inflation §16's central engine exists to
prevent.

So fusion was built before any scoring was attempted, and Setup Quality was
written to consume it rather than the raw items. Everything else in Phase 4A —
roles, causality, contradictions, the pullback exemption — was preserved
unchanged; nothing was rewritten for style.

---

## 2. Scope delivered

| Requirement | Status |
| --- | --- |
| 1D / 1H / 15M / 5M roles, never averaged | IMPLEMENTED *(4A)* |
| Timeframe identity validation | IMPLEMENTED *(4A)* |
| Deterministic evidence model | IMPLEMENTED *(4A)* |
| Evidence generation from Phase 1-3 only | IMPLEMENTED *(4A, extended)* |
| Causality / `confirmed_at` semantics | IMPLEMENTED *(4A)* |
| Contradiction engine | IMPLEMENTED *(4A)* |
| Pullback vs true conflict | IMPLEMENTED *(4A)* |
| **Evidence Fusion Engine (§16)** | **IMPLEMENTED *(4B)*** |
| Correlated-evidence / double-counting handling | IMPLEMENTED *(4B)* |
| Setup Quality 0-100 with breakdown | IMPLEMENTED *(4B)* |
| Entry Quality, separate model | IMPLEMENTED *(4B)* |
| Scenario states incl. WAITING_FOR_CONFIRMATION | IMPLEMENTED *(4B)* |
| NO TRADE engine with reason codes | IMPLEMENTED *(4B)* |
| Bull / Bear / Neutral scenarios | IMPLEMENTED *(4B)* |
| Risk / suitability boundary | IMPLEMENTED *(4B)* |
| Global LONG / SHORT / WAIT synthesis | **NOT STARTED — out of phase by instruction** |
| Claude synthesis, Devil's Advocate, StrategyRouter | **NOT STARTED — later phases** |

---

## 3. Files added / modified

**Domain — `app/domain/analysis/`**: `timeframes.py`, `evidence.py`,
`generation.py`, `contradictions.py` *(all 4A)*; `fusion.py`, `quality.py`,
`entry.py`, `scenarios.py` *(new in 4B)*; `engine.py` and `__init__.py`
extended.

**Domain — `app/domain/suitability/`** (new): `no_trade.py`, `__init__.py`.

**Tests**: `tests/factories_analysis.py`; `tests/unit/analysis/` (9 modules,
4 new); `tests/unit/suitability/` (new).

**Configuration**: `backend/pyproject.toml` — 8 → **11** import contracts.
**No dependency added.**

**Docs**: `docs/architecture.md` updated. `docs/viop_master_spec.md` and the
Phase 0-3 reports **not** modified. No API route, no frontend change, no
database table, no migration.

---

## 4. Multi-timeframe model

Four named roles, bound to timeframes by a `TimeframeRolePolicy` that defaults
to §10's recommended hierarchy and is validated as a strict hierarchy — four
distinct timeframes, each faster than the role above. A policy putting 5M above
1H is refused, because it would silently invert every "higher timeframe"
statement downstream.

Input is validated before anything is computed, and **refused rather than
repaired**: a duplicated role, a duplicated timeframe, a view whose timeframe
is not the one its role expects, views describing different instruments, and an
internally inconsistent view (indicators computed from a different series) each
raise `MultiTimeframeError` with a distinct `MultiTimeframeIssue`. The
swapped-timeframe message states explicitly that nothing was reordered.

A **missing** role is not an error. It stays absent through evidence
generation, contradiction detection, scoring and the veto, and is reported as
an unavailable role — never substituted with a neutral reading.

---

## 5. Timeframe role semantics

1D → REGIME, 1H → BIAS, 15M → SETUP, 5M → ENTRY, ordered by `rank` so the
engine can say "higher timeframe" without hard-coding 1D.

Roles are **not equal and never averaged**. A shared role-share table gives
REGIME and BIAS 0.35 each, SETUP 0.20 and ENTRY 0.10, and both the alignment
and the coverage component read it, so the two always describe the same
hierarchy. Empirically: 1D+1H bearish against 15M+5M bullish scores the bear
case 39 and the bull case 22 — the lower pair cannot outvote the higher.

---

## 6. Evidence model

`EvidenceItem` carries source, category, direction, strength, reason,
timeframe, role, `confirmed_index`, `confirmed_time`, `point_in_time` and
provenance.

* `EvidenceDirection` — BULLISH / BEARISH / **NEUTRAL** / **UNAVAILABLE**, the
  last two permanently distinct.
* `EvidenceStrength` — WEAK / MODERATE / STRONG, a `StrEnum` with an explicit
  `rank`. Deliberately **not** an `IntEnum`: `sum()` raises `TypeError`, so the
  arithmetic that would produce "78% chance of LONG" is unavailable rather than
  merely discouraged. A test pins that, including the one operator that fails
  quietly (`+` concatenates to `'WEAKSTRONG'`).
* `EvidenceReliability` — §16's reliability field, added in 4B and **derived,
  never stored**: CONFIRMED for a completed event, PROVISIONAL for a reading of
  the latest candle, UNVERIFIED_SOURCE when provenance is not authoritative
  under §118. It follows from `point_in_time` and `provenance`, so it cannot
  drift out of step with them.

---

## 7. Evidence generation

Thirteen sources, every one a translation of finished Phase 1-3 output:
EMA alignment, market structure, structural events (BOS / CHOCH /
LEVEL_BREAK), market regime, **momentum**, **VWAP relationship**, support and
resistance, breakout, breakout volume, retest, volume divergence, basis, open
interest.

Momentum and the VWAP relationship were added in 4B so that the corresponding
quality components read real data instead of being permanently unavailable.
Both are translations: RSI, the MACD histogram and VWAP are computed once, by
Phase 1, and only read here. RSI and MACD must **agree** to produce a
direction; where they disagree the item is NEUTRAL and names both readings,
rather than casting a deciding vote. VWAP evidence carries
`DEVELOPMENT_DEFAULT` provenance, because Phase 1 anchors VWAP to the UTC
calendar day in the absence of verified session hours — so it reports
`UNVERIFIED_SOURCE` reliability and the caveat travels with the number.

**Basis and open interest remain NEUTRAL.** §32 and §33 both state their
readings are context and not direction, and Phase 3 tests enforce it. Promoting
PREMIUM to BULLISH would smuggle in a claim two earlier phases explicitly
refused to make. That Phase 4A decision was preserved deliberately.

Unresolved states produce nothing: a zone under challenge, a breach still
inside its failure window, a retest that has only been touched. They are not
yet facts.

---

## 8. Evidence Fusion Engine

`FusedEvidence` provides the partition §16 asks for — `bullish`, `bearish`,
`neutral`, `unavailable` — with every item intact, plus the contradictions
carried through rather than folded in. Contract evidence is kept separately and
reaches the neutral partition.

The second half is **grouping**, and it is the anti-double-counting device.
`EvidenceGroup` collects every observation of one category on one timeframe
into one voice:

* its `direction` requires the directional items to agree; a category holding
  both bullish and bearish observations is NEUTRAL and the disagreement is
  reported as a contradiction, because resolving it by majority would let the
  count of records decide a market question;
* its `strength` is the **maximum** among agreeing items, never a total;
* its `reliability` is the **weakest** among the items that set the direction;
* `item_count` stays visible so a reader can see the repetition that was
  collapsed — and is an input to nothing.

Every downstream consumer reads groups, not items.

---

## 9. Correlated-evidence / double-counting handling

Two distinct problems, two deterministic devices, no statistical modelling —
which would be false precision here.

**Repetition inside a category** is solved by fusion. Seven divergences across
seven swings are one group, so the record count never reaches the arithmetic.
Proven mechanically: duplicating the entire evidence set 5× and 8×, and
repeating one supporting item 30×, all leave the score bit-identical (71 → 71).

**Shared inputs across categories** is solved by component caps. The regime is
derived partly from the EMA stack and partly from the structure bias, so TREND,
STRUCTURE and REGIME are genuinely not three independent confirmations. Each is
capped at its own weight, and `REGIME_SUITABILITY` is weighted **below** both
of the others (8 against 16 and 16) precisely because most of what it knows has
already been counted. A test asserts that ordering, so a future re-weighting
cannot silently undo the reasoning.

Contradictions are charged **once per distinct conflict** — keyed on (type,
roles) — rather than once per evidence record, so a fixture that generates a
divergence at every swing cannot flatten the score through repetition either.

---

## 10. Evidence causality

Phase 4A's rule, unchanged: every item is dated to when it became *knowable*,
never to when the price action happened. A swing pivoting at candle 100 and
confirming at 102 produces evidence dated 102.

`point_in_time` separates the two kinds of observation. Event evidence carries
a confirmation index and accumulates; point-in-time evidence — a regime, a
structure bias, the nearest level — describes the final candle only and is
legitimately different at every candle. Conflating them would make the
no-look-ahead argument untestable.

---

## 11. Contradiction engine

Five named conflicts, each a specific comparison rather than a score threshold:
`HIGHER_TIMEFRAME_CONFLICT` (MAJOR), `SETUP_AGAINST_BIAS` (MODERATE),
`LOWER_TIMEFRAME_REVERSAL` (MINOR), `INTRA_TIMEFRAME_CONFLICT` (MODERATE) and
`EVIDENCE_CONFLICT` (MINOR). Each exposes the involved evidence, categories,
roles, timeframes, a reason and `confirmed_at` — the latest confirmation among
its evidence, since a conflict is only as old as its newer half.

A per-timeframe `DirectionalReading` is derived from exactly two Phase 2 facts,
the regime and the structure bias, and only where they agree; disagreement
yields NEUTRAL plus an intra-timeframe contradiction rather than a casting
vote. Where either is UNAVAILABLE and neither is directional, the reading is
UNAVAILABLE, not NEUTRAL.

---

## 12. Pullback vs reversal semantics

The distinction §17 and §10 draw together, and the rule most likely to be
eroded by a later refactor — hence the dedicated regression module.

A lower timeframe opposing higher ones **that agree** is a `Pullback`, reported
separately and explicitly not a contradiction. The exemption is revoked by
either:

1. a **strong opposing trend regime** on the entry timeframe — a retracement
   does not classify as a strong trend on its own timeframe; or
2. an opposing **change of character *and* a confirmed opposing breakout**,
   both within the lookback.

A CHOCH alone is deliberately **not** enough. On the entry timeframe a change
of character is what a pullback *is* structurally, so escalating on it would
revoke the exemption on every healthy trend and make the distinction
meaningless. Requiring a level to give way as well asks for structure and price
to agree first.

Where the higher timeframes do not agree among themselves the exemption never
applies — there is no consensus to be retracing within.

---

## 13. Setup Quality — exact scoring model

**0-100, labelled `HEURISTIC SETUP QUALITY`, method version
`setup-quality/1`.** It measures *how coherent the evidence for a direction is*
and says nothing about what price will do.

| Component | Weight | Reads |
| --- | --- | --- |
| TIMEFRAME_ALIGNMENT | 16 | whether the readable roles agree |
| TREND_ALIGNMENT | 15 | TREND |
| MARKET_STRUCTURE | 15 | STRUCTURE |
| SUPPORT_RESISTANCE | 10 | LEVEL, BREAKOUT, RETEST |
| MOMENTUM | 8 | MOMENTUM, INTRADAY |
| VOLUME | 8 | VOLUME, DIVERGENCE |
| TIMEFRAME_COVERAGE | 8 | how much of the hierarchy exists |
| REGIME_SUITABILITY | 8 | REGIME |
| CONTRADICTION_BURDEN | 8 | distinct contradictions |
| DATA_AVAILABILITY | 4 | coverage of the other components |
| **Total** | **100** | |

**These are DEFAULT POLICY, not market fact.** Every weight is a project
convention, overridable through `QualityWeights`; §28 records the closeout
evidence that a custom profile actually reaches the arithmetic rather than
being accepted and ignored.

**Alignment and coverage are separate components**, and that split was made
during the human-review closeout — see §27, issue 1.

Each component is capped at its weight in both directions. A supporting group
earns a share of the weight by strength (WEAK 0.4, MODERATE 0.7, STRONG 1.0);
each *additional* group of a **different** category adds 0.15, capped at 1.0;
opposing groups subtract. Repetition of the same category adds nothing, because
fusion already made it one group.

`CONTRADICTION_BURDEN` is scored rather than subtracted from the total, so it
is visible as its own line and cannot drive the result negative — verified by
setting the per-conflict cost to 1000 and confirming the component floors at 0
and the total stays ≥ 0.

**Normalisation with missing components.** An unavailable component is excluded
from **both** numerator and denominator, so absent data is not scored as
failure — and `awarded` is `None`, not `0`, because zero reads as measured and
bad. The denominator actually used is published as `available_weight`, with
`total_weight` and `coverage` alongside. Missing data is not free either:
`DATA_AVAILABILITY` scores the coverage of the other components.

**Not a probability.** No field named probability, win_rate, success_chance,
calibrated_probability, expected_return, edge, confidence, odds or likelihood
exists on any quality type, enforced by a test over `__dataclass_fields__` and
an AST scan of the module.

**Bull and bear are independent.** Computed separately, never normalised.
Observed sums across three markets: **88, 101, 75** — the 101 is itself the
proof, since a normalised pair could not exceed 100.

---

## 14. Entry Quality

A **separate** model — `HEURISTIC ENTRY QUALITY`, `entry-quality/1` — because
setup coherence and entry timing routinely disagree, and a model that averaged
them would hide exactly that. Components: ENTRY_STRUCTURE 20, LEVEL_PROXIMITY
16, BREAKOUT_RETEST 16, EXTENSION 16, VOLUME_CONFIRMATION 12, MOMENTUM_CONTEXT
12, VWAP_RELATIONSHIP 8.

It reads **only** entry-timeframe groups; reading a higher timeframe would
re-award what Setup Quality already counted. `EXTENSION` measures the close's
distance from EMA20 in ATR (§29), both read off the finished Phase 1 snapshot,
and is direction-agnostic — 4 ATR extended is a poor place to join whichever
way the move went.

With no entry timeframe supplied the score is **`None`**, not 0: §13's rule
that no entry trigger is not a bad entry. No entry price, stop, target or
trigger is fabricated anywhere.

**Risk/reward is not a component of either quality model.** §43 separates setup
quality from trade suitability, and the Phase 4A contract keeps
`app.domain.analysis` clear of `app.domain.risk`. Risk/reward is a Phase 3
result about a *proposed trade*, so it is consumed by the suitability layer
instead — see §15.

---

## 15. Risk / trade-suitability boundary

The Phase 4A contract forbidding `app.domain.analysis` → `app.domain.risk` was
**preserved, not relaxed**. An account balance must never reach a technical
score, or the same chart would grade differently for two users.

A veto genuinely needs both halves, so the dependency is resolved one level up
in a new `app/domain/suitability/` package:

```
  analysis (technical, account-blind) â”€â”
                                       â”œâ”€â–¶ suitability â”€â–¶ NO TRADE veto
  risk     (money, analysis-blind) â”€â”€â”€â”€â”˜
```

Both arrive as finished typed results; no Phase 3 formula is reimplemented, and
an AST test asserts that no Phase 3 engine function is called. A third contract
forbids anything beneath suitability from importing it — a veto is the end of
the chain, not an input to what it vetoes.

Proven empirically: an excellent setup (quality 71) with sizing that permits
zero contracts yields `no_trade = True` with `RISK_NOT_PERMITTED`, **and the
quality stays 71** across account states.

---

## 16. NO TRADE engine

A deterministic veto with typed reason codes, returning
`no_trade: bool | None` — three states, because "we could not tell" must not
collapse into "go ahead".

Evaluable today: `CONFLICTING_TIMEFRAMES`, `CHAOTIC_REGIME`, `HIGH_VOLATILITY`,
`MIDDLE_OF_RANGE`, `EXTENDED_MOVE`, `NO_CONFIRMATION`, `UNCLEAR_STRUCTURE`,
`INSUFFICIENT_DATA`, `BAD_DATA`, `POOR_RISK_REWARD`, `RISK_NOT_PERMITTED`,
`RISK_UNDETERMINED`.

**Every finding carries a typed `FindingSeverity`** — BLOCKING, PENDING or
CAUTION — added during the closeout so the future WAIT vs NO TRADE distinction
survives Phase 4. See §27, issue 3.

`sizing`, `risk_reward` and `data_quality` are optional Phase 1-3 results.
Supplying them enables the reasons that depend on them; omitting them records
the gap in `missing_requirements`. **Absence never counts as a pass** — no risk
input is not risk allowed. `SizingOutcome.UNDETERMINED` does not block but does
prevent a clean pass, because an unknown constraint is not a satisfied one.

**Reasons that cannot be evaluated are declared, not faked.**
`DeferredNoTradeReason` enumerates `LOW_LIQUIDITY`, `EVENT_RISK`, `NEWS_RISK`,
`ORDER_BOOK_IMBALANCE` and `CORRELATED_EXPOSURE` — §25 reasons with no
authoritative data source in this repository. The two enums share no member, so
a deferred reason cannot reach a finding even by accident, and a test proves
none ever fires. Implementing them against invented inputs would produce a veto
nobody could audit.

---

## 17. Bull / Bear / Neutral scenarios

Structured views of evidence that already exists — no prose, no invented price,
no order. Each carries supporting and counter evidence groups, contradictions,
requirements, invalidation conditions, unavailable groups, its own Setup
Quality and Entry Quality, and a state.

States: `UNAVAILABLE` → `INACTIVE` → `FORMING` → `WAITING_FOR_CONFIRMATION` →
`CONFIRMED`, decided in a fixed order with unavailability checked **first** so
a case that cannot be judged never falls through to INACTIVE and reads as
measured-and-absent.

Six named requirements, each deterministically evaluable and each distinguishing
`UNMET` from `UNKNOWN`. `outstanding_requirements` is the set the state machine
reads — unmet *and* unknown — so a case can never confirm itself on evidence
nobody could see, and the state can never disagree with the requirement list.

Invalidations are stated as **conditions on future observations**, never
prices: "a confirmed opposing change of character on the bias timeframe" is
something the engines can later report, while "invalidation at 104.20" would be
a number nobody computed. A test asserts no invalidation description contains a
digit.

The **neutral** case is deliberately not scored by §18's model, which weighs
the coherence of a *directional* argument; applying it to "no direction" would
answer the wrong question. It is described by the readings instead.

Contradictions are shown on **both** directional cases — §17 forbids hiding a
conflict from whichever case it inconveniences.

---

## 18. Missing-data behaviour

Every honest-uncertainty rule holds, and each has a test:

| Situation | Behaviour |
| --- | --- |
| Missing timeframe | absent role; reading is `None`; never neutral |
| Warm-up not finished | `UNAVAILABLE`, never `NEUTRAL` |
| No volume data | `UNAVAILABLE`, never low volume |
| Basis / OI unmeasurable | `UNAVAILABLE`, never FLAT / UNCHANGED |
| Component with no evidence | `awarded = None`, excluded from the denominator |
| No entry timeframe | entry score `None`, never 0 |
| No risk input | recorded in `missing_requirements`, never "allowed" |
| Requirement unevaluable | `UNKNOWN`, never `MET` |

**The normalisation convention, stated plainly**: unavailable components leave
both numerator and denominator; `available_weight` is published on every
result; and `DATA_AVAILABILITY` charges for the missing coverage so exclusion is
not the same as free.

---

## 19. No-look-ahead proof

Two series are built from the same index-driven generator, one 150 candles and
one 220. Because the generator depends only on the candle index, the shorter is
byte-for-byte a prefix of the longer — asserted directly, since without it
every other assertion would pass vacuously. Both are truncated to the same cut
point (70, 95, 120, 145) and analysed in full.

Everything Phase 4 concludes must be identical, because the only difference is
candles that had not happened at the cut. Covered at every cut point:

* the entire `MultiTimeframeAnalysis` (a frozen dataclass all the way down, so
  equality compares evidence, fusion, contradictions, scenarios and every
  component score of both quality models);
* fused evidence, all four partitions and all groups;
* the contradiction report;
* Setup Quality — score, components and denominator;
* Entry Quality — score and components;
* all three scenarios — state, requirements and supporting evidence;
* the NO TRADE assessment, including §14's specific worry that a later
  breakout must not reach back and change an earlier veto.

Two guards keep the proof honest: evidence must **accumulate** across cut
points (confirmed events never vanish), and the analysis must **differ**
between the first and last cut, so the equalities cannot be holding trivially.

---

## 20. Determinism proof

Same inputs, identical outputs — asserted for evidence, fused evidence,
contradictions, setup components and total, entry components and total,
scenario contents and states, and the NO TRADE verdict.

No randomness, no LLM, no global mutable state, and no ambient clock: a test
asserts every evidence timestamp is drawn from a candle's own `open_time`, so
two runs separated in wall-clock time are identical.

---

## 21. Critical empirical review findings

Run as **15 empirical probes** against real calls, not by reading. All 15 pass.

timeframes weighted not averaged · lower timeframes cannot outvote the higher
pair (bull 16, bear 34) · routine retracement stays a pullback · a strong
opposing entry trend is not exempted · 8× duplicated evidence cannot move the
score (71 = 71) · contradictions charged once per conflict · a missing role is
absent not neutral · unavailable components are `None` with the denominator
published · bull + bear are not normalised (88, 101, 75) · risk NOT_PERMITTED
vetoes while quality stays 71 · no LLM or network import · no StrategyRouter ·
no final-action synthesis · no fabricated trigger/entry/stop/target · no Phase
1-3 engine re-run.

**One real defect was found by probing and fixed.** `TIMEFRAME_ALIGNMENT`
originally normalised over the roles *supplied* rather than the whole
hierarchy, so a single daily view scored **20/20** for agreeing with itself and
pushed the total to **74** on a quarter of the evidence. One timeframe is not
alignment. It now scores against the full hierarchy: the same lone view earns
7/20 and a total of 60, while four agreeing timeframes still earn 20/20. Two
tests pin the behaviour, and the reasoning is recorded in the function's
docstring.

Three Phase 4A architecture tests failed after 4B landed and were **updated,
not deleted**: they asserted the absence of setup quality, entry quality and
scenarios, which is correct for 4A and wrong afterwards. Their boundary moved
to Phase 5 — no StrategyRouter, no Devil's Advocate, no synthesis, no final
action — and a new test forbids any module from defining a LONG / SHORT / WAIT
constant. The probability-field test kept every likelihood word and dropped
only `quality` and `score`, which Phase 4B legitimately produces.

---

## 22. Tests and exact counts

**1100 collected, 1100 passed, 0 skipped** with PostgreSQL. Phase 3 ended at
817; Phase 4A added 113 (930), Phase 4B a further 141 (1071), and the
human-review closeout in §27 a further **29**. Phase 4 total: **283**.

| Module | Tests |
| --- | --- |
| `analysis/test_quality.py` | 42 |
| `analysis/test_phase4_causality.py` | 37 |
| `suitability/test_no_trade.py` | 35 |
| `analysis/test_evidence.py` | 32 |
| `analysis/test_entry_and_scenarios.py` | 25 |
| `analysis/test_timeframes.py` | 22 |
| `analysis/test_contradictions.py` | 19 |
| `analysis/test_causality.py` | 17 |
| `analysis/test_fusion.py` | 16 |
| `analysis/test_hierarchy_regression.py` | 14 |
| `analysis/test_analysis_architecture.py` | 13 |
| `analysis/test_engine.py` | 11 |

The hierarchy regressions cover all six required scenarios (A-F) plus the CHOCH
exemption boundary in both directions.

---

## 23. Quality gates

Freshly run, all of them:

| Gate | Result |
| --- | --- |
| `ruff check .` | **PASS** |
| `ruff format --check .` | **PASS** — 161 files |
| `mypy --platform linux` | **PASS** — 159 source files |
| `mypy --platform win32` | **PASS** — 159 source files |
| `lint-imports` | **PASS** — **11** contracts kept, 0 broken |
| `pytest` (with PostgreSQL) | **PASS** — **1100 passed, 0 skipped**, 20.7 s |
| Frontend `tsc -b --noEmit` | **PASS** *(not re-run in the §27 closeout — see below)* |
| Frontend ESLint | **PASS** *(not re-run)* |
| Frontend Prettier | **PASS** *(not re-run)* |
| Frontend tests | **PASS** — 12 passed *(not re-run)* |
| Frontend build | **PASS** — 768 ms *(not re-run)* |
| `docker compose config -q` | **PASS** *(not re-run)* |

The backend gates in this table are from the closeout run. The frontend and
Docker rows date from the Phase 4B run and are labelled as such rather than
restated as fresh: the closeout changed backend domain code and backend tests
only, touching no runtime version, configuration, dependency, Dockerfile or
compose file, and no frontend file exists that it could have affected.

No gate was weakened; the contract count went **up**, 8 → 11. No dependency was
added, so no Docker image rebuild was required.

Six mypy errors in new test code were fixed with **real annotations**, not
suppressions — the same discipline used in Phase 2.

---

## 24. Architecture review

`api → application → domain`; adapters implement ports; the domain imports
nothing outside the standard library.

Inside the domain the order is now explicit and enforced:

```
  market â”€â–¶ technical â”€â–¶ structure â”€â”
                                    â”œâ”€â–¶ analysis â”€â”
                        futures â”€â”€â”€â”€â”˜             â”œâ”€â–¶ suitability
                        futures â”€â–¶ risk â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”˜
```

**Three contracts added (8 → 11):**

* *Nothing beneath the analysis layer depends on it* — technical, structure,
  futures, market and common may not import `app.domain.analysis`.
* *The analysis layer never depends on the risk engine* — §43's separation,
  made mechanical.
* *Nothing beneath the suitability layer depends on it* — including analysis
  and risk themselves.

The adapters contract was widened to include `app.domain.analysis`.

Every Phase 4 engine is a pure function over explicit inputs with no clock, no
I/O and no global state, so live, replay, backtest and shadow mode share them
without duplication.

---

## 25. Technical debt / limitations

| Item | Impact | When |
| --- | --- | --- |
| Component weights are unvalidated project heuristics | Expected — §19 forbids calibration claims until outcomes are recorded | Phase 12+, with backtesting |
| `EVIDENCE_CONFLICT` fires on most trending fixtures | Low — MINOR, charged once per conflict, and honest; the zigzags produce a divergence at nearly every swing | revisit with real candles |
| Neutral case has no quality score | Deliberate — §18's model scores a directional argument | — |
| Risk/reward absent from both quality models | Deliberate — §43 boundary; consumed by suitability instead | — |
| §25 liquidity / event / news reasons unevaluable | Expected — no authoritative source; declared in `DeferredNoTradeReason` | Phase 12+, 15 |
| VWAP anchoring still UTC-calendar | Carried from Phase 1 — surfaces as `UNVERIFIED_SOURCE` reliability | with verified session data |
| Extension measured from EMA20 only | Low — one documented reference, configurable | — |
| Phase 2 O(candles × swings) rebuild loops | Carried | before the backtest phase |
| Deprecated event-loop policy API | Carried from Phase 0 | before Python 3.16 |
| Turkish text unreviewed by a native speaker | Carried | Phase 5 |

**No placeholder was marked complete.** Everything above is either IMPLEMENTED
and tested, or explicitly deferred with a reason and an owning phase.

---

## 26. Phase-boundary verification

Explicitly **absent**, verified mechanically rather than by inspection:

Claude synthesis · Devil's Advocate (§24) · StrategyRouter (§15) · the global
LONG / SHORT / WAIT final-action engine · Beginner/Pro UI · educational
tooltips · Why Engine · Pre-Trade Checklist · Claude Vision · paper trading ·
journal · backtesting · replay · shadow mode · live feed · WebSockets · news ·
broker integration · Midas · `OrderExecutionPort` · real order execution.

Checked by test: no module in `analysis/` or `suitability/` defines
`strategy_router`, `devils_advocate`, `synthesise`, `final_action` or `decide`;
no module assigns a `LONG`/`SHORT`/`WAIT` constant; no `ScenarioState` or
`NoTradeReason` value is a trade instruction; `MultiTimeframeAnalysis`,
`Scenario`, `ScenarioSet` and `NoTradeAssessment` carry no action, decision,
verdict, recommendation or signal field; no LLM, network, framework or database
import exists anywhere in either package.

§120 holds: no order is sent, no position is created, and nothing was built
that could send one.

---

## 27. Human-review semantic closeout

Three issues were raised in human review after the report above was written.
**All three were audited against the actual code, and all three were real**
— one a semantic error, one a genuine gap in an otherwise working mechanism,
and one a missing distinction that a later phase depends on.

### Issue 1 — timeframe alignment with insufficient roles

**Finding: confirmed, and my own earlier fix was itself wrong.** The report
recorded a lone 1D view scoring 7/20 for `TIMEFRAME_ALIGNMENT`. The reviewer's
point is correct and sharper than the fix I had made: *one timeframe cannot
demonstrate alignment with another*. Both of my earlier drafts measured a
relation that does not exist — the first by scoring it 20/20 (full agreement
with itself), the second by scoring it 7/20 (partial agreement with itself).
A partial mark is a smaller error of the same kind, not a correction of it.

The second draft also committed the error the reviewer names directly: it used
the alignment component to penalise coverage, which is *pretending coverage is
alignment*.

**Fix — the two facts are now two components.**

* `TIMEFRAME_ALIGNMENT` requires at least `minimum_roles_for_alignment`
  (default **2**) readable readings. Below that it is `UNAVAILABLE` with
  `awarded = None`, and the reason says: *"alignment is a relation between
  timeframes and needs at least 2 readable readings â€¦ this is neither
  agreement nor disagreement."* Above it, it scores agreement **over the
  readable roles only**, so absent roles are neither agreement nor
  disagreement, and the reason names which roles agreed and which disagreed.
* `TIMEFRAME_COVERAGE` (new, weight 8) scores how much of the hierarchy exists,
  weighted by role, and names the missing ones.

**The denominator trap was explicitly guarded.** Making alignment UNAVAILABLE
removes 16 points of denominator, which on its own would *raise* a lone
timeframe's normalised score — exactly what the review warned against.
`TIMEFRAME_COVERAGE` offsets it, and a test asserts the net effect rather than
the reasoning: a lone view scores **62**, a complete agreeing hierarchy **72**.

Observed across the required matrix:

| Input | Alignment | Coverage | Score |
| --- | --- | --- | --- |
| only 1D | **UNAVAILABLE** | 3/8 | 62 |
| 1D + 1H aligned | 16/16 | 6/8 | 72 |
| 1D + 1H conflicting | 8/16 | 6/8 | **26** |
| all four aligned | 16/16 | 8/8 | 72 |
| missing 15M | 16/16 | 6/8 | 71 |
| missing 5M | 16/16 | 7/8 | 72 |

A missing 15M costs more coverage than a missing 5M (6 vs 7), because §10
forbids treating timeframes equally.

### Issue 2 — configurable Setup-Quality weights

**Finding: configurability was real; the proof of it was not, and a related
gap was.** `QualityWeights` was already a frozen, validated, injectable
dataclass, and the weights genuinely reached the arithmetic. But the only test
asserted that a custom `total_weight` *differed* — which would have passed even
if the weight were ignored everywhere it mattered. And **`EntryWeights` had no
validation at all**: no `__post_init__`, so a negative entry weight would have
been accepted silently.

**Fix.** `EntryWeights` gained the same validation as `QualityWeights`. The
weights docstrings now lead with **DEFAULT POLICY, NOT MARKET FACT** in those
words. Six tests replace the weak one: the default profile is pinned; a custom
weight is proven to change the component's `awarded` *and* the total; a custom
profile is proven to reach the score **through the whole engine** via
`AnalysisConfig → ScenarioConfig → QualityConfig`, not only by calling
`score_setup` directly; negative and all-zero profiles are rejected; a custom
profile is deterministic; and the defaults are asserted to keep
`REGIME_SUITABILITY` below both `TREND_ALIGNMENT` and `MARKET_STRUCTURE`, so
the correlated-evidence protection cannot be re-weighted away by accident.

Observed: default `REGIME 8/8` → total 72; `QualityWeights(regime_suitability=60)`
→ `REGIME 60/60`, total 82.

### Issue 3 — no-confirmation vs hard no-trade

**Finding: confirmed.** `NoTradeFinding.blocking` was a bare `bool`, which
collapses two genuinely different conditions into one non-blocking bucket:
*"this may resolve on the next candle"* and *"this cannot be resolved by
waiting at all"*. Phase 7 must eventually separate WAIT from NO TRADE, and it
cannot do that from information Phase 4 destroyed.

**Fix — a typed `FindingSeverity`**, with `blocking` kept as a derived property
so the two can never disagree:

| Severity | Meaning | Reasons |
| --- | --- | --- |
| `BLOCKING` | waiting does not help | CONFLICTING_TIMEFRAMES, CHAOTIC_REGIME, UNCLEAR_STRUCTURE, BAD_DATA (blocked), POOR_RISK_REWARD, RISK_NOT_PERMITTED, INSUFFICIENT_DATA (case unjudgeable) |
| `PENDING` | a future candle genuinely could resolve it | NO_CONFIRMATION, MIDDLE_OF_RANGE, EXTENDED_MOVE |
| `CAUTION` | disclosed, neither blocking nor pending | HIGH_VOLATILITY, RISK_UNDETERMINED, BAD_DATA (warnings), INSUFFICIENT_DATA (missing roles) |

The classification rule, applied to each reason individually: *can a future
candle resolve this?* A missing confirmation and a mid-range price can; zero
risk allowance, a timeframe conflict, chaos and corrupt data cannot.

`NoTradeAssessment` gained `pending`, `cautions` and a descriptive
`is_waitable`. **Phase 4 still makes no decision**: a pending finding never
vetoes, and `no_trade is False` alongside a non-empty `pending` is two facts
deliberately kept apart — nothing blocks, and something has not happened yet.
No `FindingSeverity` value is WAIT, NO_TRADE, LONG or SHORT, and a test asserts
it.

Observed, and this is the pair the review asked for:

```
good setup, no confirmation   no_trade=False  waitable=True
                              blocking=[]  pending=[NO_CONFIRMATION]
good setup, risk max 0        no_trade=True   waitable=False
                              blocking=[RISK_NOT_PERMITTED]  pending=[NO_CONFIRMATION]
```

BAD_DATA is `BLOCKING` while NO_CONFIRMATION is `PENDING`, so a corrupt
dataset is not "ordinary waiting for a 5M confirmation".

### Closeout validation

All gates freshly re-run; **1100 passed, 0 skipped** (was 1071, so the closeout
adds 29). Phase 4 subtotal **283**. Both probe suites re-run: the 15 original
critical-review checks still pass with **no defects**, and a new closeout probe
covers the six alignment cases, the weight matrix and the severity pair.

Frontend and Docker were **not** re-run, and that is accurate rather than
carried over: the closeout changed backend domain code and backend tests only —
no runtime version, configuration, dependency, Dockerfile or compose file was
touched, and no frontend file exists that could be affected.

Two documents were updated because semantics genuinely changed:
`docs/architecture.md` (the alignment/coverage split and the severity model)
and this report (§13, §16, §22, §23 and this section).

---

## 28. Final Git status

```
HEAD:  f631c79  Complete Phase 3 futures and risk engine  (main, origin/main)
```

**Nothing was committed or pushed by Claude.** `HEAD` is unchanged from the
start of Phase 4A; the git-write hook remained active throughout.

**Modified (2)** — `git diff --stat`: 2 files changed, 136 insertions, 13 deletions
`backend/pyproject.toml`
`docs/architecture.md`

**Untracked (6)**
`backend/app/domain/analysis/`
`backend/app/domain/suitability/`
`backend/tests/factories_analysis.py`
`backend/tests/unit/analysis/`
`backend/tests/unit/suitability/`
`docs/phase_reports/phase_4_completion_report.md` *(this file)*

`docs/viop_master_spec.md` and the Phase 0-3 reports are unmodified.

---

# STOP

Phase 4 is complete and validated. Phase 5 has not been started, scaffolded, or
prepared for. No Phase 5 dependency was installed. Nothing was committed or
pushed.

Awaiting human review.

