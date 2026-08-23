# PHASE 3 COMPLETION REPORT

Date: 2026-08-23 · Follows `phase_2_completion_report.md` · Phase 3 only

**Revised 2026-08-23 after human review.** Five financial-correctness issues
were raised, all five were confirmed on audit, and all five were fixed. The
decisions, fixes and evidence are in **§24**; §7–§9, §13, §14, §18–§21, §23 and
§26 were updated to describe the code as it now behaves rather than as it did
at first submission.

---

## 1. Baseline / Git state

Inspection before any file was touched:

| Check | Finding |
| --- | --- |
| Phase 2 committed | **Yes** — `a8a3ccb` *Complete Phase 2 market structure and regime engine* |
| Working tree | **Clean** |
| Branch / remote | `main`, `origin/main` at the same SHA |
| CI baseline | Corresponds to the committed Phase 2 state |
| Phase 3 already present | **No** — `domain/futures/`, `domain/risk/` and the contract adapter directories were all absent |

Spec read: §31 futures engine, §32 basis, §33 open interest, §42 risk engine,
§44 margin safety, §45 adaptive warnings, §46 P&L, §47 what-if, §48 (calculation
parts only), §103 Phase 3, §118, §119, §120. Existing `VerificationStatus`,
`VerifiedValue`, `ClockPort`, market and structure domains inspected before
design.

---

## 2. Scope delivered

| Requirement | Status |
| --- | --- |
| FuturesContract | IMPLEMENTED |
| Contract metadata | IMPLEMENTED |
| ContractMetadataProvider (port) | IMPLEMENTED |
| ManualContractMetadataProvider (adapter) | IMPLEMENTED |
| Multiplier / tick size / tick value / margin metadata | IMPLEMENTED |
| LONG P&L / SHORT P&L | IMPLEMENTED |
| Basis / basis percent | IMPLEMENTED |
| Open-interest context | IMPLEMENTED |
| Position sizing, FIXED and PERCENTAGE modes | IMPLEMENTED |
| Risk limits | IMPLEMENTED |
| Risk / reward | IMPLEMENTED |
| Margin safety | IMPLEMENTED |
| Notional exposure / effective leverage | IMPLEMENTED |
| Used / free margin | IMPLEMENTED |
| What-if simulation | IMPLEMENTED |
| Contract validation (tick grid, tick value, expiry) | IMPLEMENTED |
| Annualised basis | **DEFERRED** — §10 below |
| §45 adaptive volatility warnings | **DEFERRED** — §23 below |
| Contract/quote identity binding | IMPLEMENTED — §24 |
| Tick-grid participation in sizing safety | IMPLEMENTED — §24 |
| Typed transaction-cost completeness | IMPLEMENTED — §24 |
| Margin-deficit preservation | IMPLEMENTED — §24 |
| Provenance-completeness validation | IMPLEMENTED — §24 |

No UI. No paper trading. No order execution.

---

## 3. Files added / modified

**Domain — `app/domain/futures/`** (new): `contract.py`, `validation.py`,
`basis.py`, `open_interest.py`, `__init__.py`

**Domain — `app/domain/risk/`** (new): `pnl.py`, `sizing.py`, `margin.py`,
`reward.py`, `whatif.py`, `__init__.py`

**Domain — `app/domain/common/`**: `arithmetic.py` (new) — pinned-context
Decimal division, floor division, zero normalisation

**Application**: `ports/contract_metadata.py` (new)

**Adapters — `app/adapters/contract_metadata/`** (new): `manual_provider.py`,
`__init__.py`

**Tests**: `tests/factories_futures.py`, `tests/unit/futures/` (4 modules),
`tests/unit/risk/` (5 modules) — the fourth and fifth added by the hardening
pass in §24

**Configuration**: `backend/pyproject.toml` — one contract widened, two added.
**No dependency added.**

**Docs**: `docs/architecture.md` updated. `docs/viop_master_spec.md` **not**
modified. No API route, no frontend change, no database table, no migration.

---

## 4. FuturesContract model

§31 lists sixteen fields together. They are modelled as **two** types, because
they are two kinds of thing:

* **`FuturesContract`** — what the contract *is*: symbol, underlying, name,
  multiplier, tick size, tick value, initial and maintenance margin, expiry,
  settlement, session, valuation model.
* **`FuturesQuote`** — what the market is *doing*: futures price, spot price,
  open interest, volume, `observed_at`.

Folding both into one object forces a choice between a mutable contract - so a
level computed from it goes stale unnoticed - and building a new "contract" on
every tick, which destroys the idea that a contract has an identity. Keeping
them apart costs one parameter at call sites and makes staleness visible.

**`days_to_expiry` is deliberately not a stored field.** It is a function of
the expiry and the current time, so storing it guarantees it will be wrong. It
is derived on demand from a `ClockPort`, and a test asserts it is not in
`FuturesContract.__slots__`.

`ValuationModel` names `LINEAR`, `INVERSE` and `QUANTO`; **only `LINEAR` is
supported**. The other two exist so a non-linear contract can be *represented*
and then *refused* — a fail-closed path that is real and testable rather than
an implicit assumption that every future is linear. They are generic derivative
conventions and imply no VIOP fact.

---

## 5. ContractMetadataProvider

**Port** `app/application/ports/contract_metadata.py` — `get_contract(symbol)`
returning `FuturesContract | None`, and `list_symbols()`. Phase 0 deferred this
port because its return type did not exist; it exists now, and it is the only
way a mutable exchange fact enters the system.

**Adapter** `ManualContractMetadataProvider` — §31's fallback for "no live
provider exists initially", which is exactly where this project is.

What it does: serves precisely what a human registered, and reports the
metadata issues at registration so a caller learns immediately that a margin is
unverified.

What it must not do, and is tested not to:

* **Never upgrades provenance.** A contract registered with an `UNVERIFIED`
  multiplier is served with one. There is no code path that raises a status.
* **Never replaces silently.** Registering a symbol twice raises, because
  otherwise which specification is in force would depend on import order.
* **No network, no credentials.** Verified by parsing the module's imports with
  `ast` — its only imports are `collections`, `app` and `__future__`. (Grepping
  the text was tried first and flagged the docstring that *explains* no
  credentials are needed; the AST check is the honest one.)
* **Ships empty.** No production VIOP contract exists in this repository.

---

## 6. Verification / provenance model

The existing Phase 0 primitives are used unchanged — no parallel system was
invented. Every §118 fact is a `VerifiedValue[Decimal]` carrying
`VerificationStatus` (`VERIFIED_CURRENT_FACT`, `DEVELOPMENT_DEFAULT`,
`TEST_FIXTURE`, `MOCK_DATA`, `UNVERIFIED`), a source and an optional `as_of`.

**Only `VERIFIED_CURRENT_FACT` is released into money arithmetic.**
`authoritative_multiplier()` and `authoritative_initial_margin()` return `None`
for every other status, and `calculate_contract_pnl` / `simulate_contract`
raise `UnverifiedFinancialFactError` rather than computing.

**Two absences are kept distinct.** `None` means the provider never supplied
the fact; a present value with `UNVERIFIED` status means it supplied a number
that should not be relied on. Collapsing them would lose the difference between
a gap in the data and a number somebody is unsure about — and they produce
different issue codes (`MISSING_MARGIN` vs `UNVERIFIED_MARGIN`).

Source-tree guards, run as tests:

* No VIOP instrument code (`XU030`, `XU100`, `BIST30`, …) appears anywhere in
  `app/`.
* No module defines `DEFAULT_MULTIPLIER`, `DEFAULT_TICK_SIZE`,
  `DEFAULT_MARGIN`, `DEFAULT_COMMISSION`, `TRADING_DAYS_PER_YEAR` or similar.
* No calendar constant (`= 252`, `= 365`, `= 360`) is assigned in
  `domain/futures/` or `domain/risk/`.

Test fixtures live in `tests/factories_futures.py`, default to `TEST_FIXTURE`
status, and the module says in its own docstring that nothing in it describes a
VIOP contract. A test needing verified status must ask for it explicitly, which
makes the pretence visible at the call site.

---

## 7. Contract validation / expiry handling

Structural invariants raise at construction: multiplier > 0, tick size > 0,
tick value > 0 when supplied, initial margin > 0 when supplied, maintenance
margin ≥ 0, prices > 0, open interest and volume ≥ 0, `observed_at`
timezone-aware, all values finite.

Provenance gaps are **reported, not raised** — an unverified margin still
leaves everything that does not need margin perfectly usable.

**Provenance completeness** (added by the §24 hardening pass): a value claiming
`VERIFIED_CURRENT_FACT` is checked for the evidence that claim implies.
`UNSOURCED_VERIFIED_FACT` is **blocking** — an unattributable claim of currency
is a guess wearing the right status. `UNDATED_VERIFIED_FACT` is a **warning** —
"current" cannot be evaluated without knowing when it was current, but a
missing date is a gap in the record rather than a contradiction in it. No
freshness duration or TTL is invented in either case; see §24, issue 5.

**Expiry** (`contract_state`, using an injected `ClockPort`, never
`datetime.now()`):

| Situation | State |
| --- | --- |
| Expiry missing or unverified | `UNKNOWN` |
| Evaluation date after expiry date | `EXPIRED` |
| Evaluation date before expiry date | `ACTIVE` (not known expired) |
| **Evaluation date == expiry date, no verified last-trading time** | **`UNKNOWN`** |
| Verified last-trading timestamp available | decided against it |

The fourth row is the §118 point. A contract stops trading at a specific moment
on its final day, and that moment is an exchange fact this project does not
hold. Assuming "the close" would invent a session hour to make the answer look
decisive. This closes the Phase 1 debt, which deferred wrong/expired-contract
checks until this provider existed.

---

## 8. Tick-size / tick-value semantics

**The grid is reported, never repaired.** `check_tick_grid(105.037, 0.05)`
returns `on_grid=False`, the untouched price, the remainder and both
neighbouring ticks. It does not return `105.05`. A stop silently moved by a
tick is a stop in a different place than the one the risk calculation used; a
caller that genuinely wants to snap can, having been told.

**The grid now participates in sizing safety** (§24, issue 2). It is no longer
an offered check a caller may forget: `size_position` classifies the entry and
stop against the grid and reports the verdict as `TickFeasibility`, and an
off-grid level yields `SizingOutcome.INVALID` with the original values
preserved in the reason. See §13 and §18.

**Tick value is cross-checked only where the identity holds.** Under linear
valuation `tick_value == tick_size × multiplier`, and a mismatch is a blocking
issue. That identity is *not* universal — an inverse contract's tick value
depends on price — so for any non-linear valuation the check reports
`TICK_VALUE_UNCHECKABLE` rather than pretending, and the calculation engines
refuse the contract outright.

**And that check is now read.** `require_calculable()` re-runs the blocking
issues at the entry to sizing, P&L, margin assessment and what-if, so a
contract whose tick value contradicts its own tick size and multiplier raises
`ContractValidationError` instead of flowing into money arithmetic. Before the
hardening pass `check_tick_value` was computed once at provider registration
and read by nothing.

`implied_tick_value()` derives the value when both facts are verified, so a
caller can display a clearly-derived figure instead of the engine inventing one
and storing it as metadata.

---

## 9. P&L engine

    LONG  : (exit - entry) × multiplier × contracts
    SHORT : (entry - exit) × multiplier × contracts

All `Decimal`. Hand-calculated tests: long profit 500, long loss −100, short
profit 500, short loss −500, break-even, and 1/3/7 contracts scaling linearly.
Long and short are asserted to be exact mirrors, so the same move cannot be
profitable in both directions.

Rejected rather than guessed: contracts ≤ 0, non-positive prices, non-positive
multiplier, `NEUTRAL` direction, non-finite values. A zero-contract "trade"
returning zero would look like a break-even result rather than the input error
it is.

**Gross is always computable; net is not.** Commission, fees and slippage are
broker- and execution-specific §118 facts, so there is **no default commission
anywhere in the codebase**. Reporting net == gross would tell a user their
scalp was profitable when the round trip may not have been. Slippage is an
explicit modelling input.

**Cost completeness is typed** (§24, issue 3). `CostCompleteness` records
whether every component was actually supplied, and `net` is populated **only**
under `COMPLETE`:

| Costs supplied | Completeness | `net` | `net_upper_bound` |
| --- | --- | --- | --- |
| none | `UNKNOWN` | `None` | `None` |
| commission only | `PARTIAL` | `None` | gross − 12 |
| commission + fees | `PARTIAL` | `None` | gross − 15 |
| all three | `COMPLETE` | gross − 20 | same |
| all three, explicitly `0` | `COMPLETE` | gross | same |

An explicit `Decimal("0")` counts as supplied — a user stating a zero-commission
account is giving information — while a `None` does not. That distinction is the
whole reason both are representable. Under `PARTIAL` the figure offered is named
`net_upper_bound`, because the unsupplied components are costs and the true net
can only be lower; `missing_components` names which ones. The sum itself is
`known_total`, deliberately not `total`, which is what it was called before: that
name invited exactly the subtraction that misreports a partial figure as a net.

Derived measures: return on account, return on margin, R multiple — each
returning `None` on a zero denominator rather than infinity or a misleading
zero.

---

## 10. Basis engine

`basis = futures − spot`; `basis_ratio = basis / spot`.

**Convention pinned by name and test:** `basis_ratio` is a fraction (`0.02`),
`basis_percent` is percentage points (`2`). Both are exposed under names that
say which is which, because confusing them is the most reliable source of
factor-of-100 errors in financial code.

Contexts: `PREMIUM`, `DISCOUNT`, `FLAT`, `UNAVAILABLE`. A missing price or a
zero spot gives `UNAVAILABLE`, never `FLAT` — which would claim a measurement
nobody took. The flat tolerance is a configurable project heuristic defaulting
to exact equality. **Nothing returns a direction**, per §32.

**Annualised basis is deferred.** §32 lists it as optional, and it needs a
verified last-trading timestamp and a day-count convention for the exchange's
calendar — both §118 facts. The usual shortcuts (365, 360, 252, "assume the
close") each produce a different, confident, unfalsifiable percentage. A test
asserts no `annualised_basis` function exists.

---

## 11. Open-interest context

The four §33 readings, each prefixed "possible" in the specification and
treated that way here: `NEW_LONG_PARTICIPATION`, `SHORT_COVERING`,
`NEW_SHORT_PARTICIPATION`, `LIQUIDATION`.

Plus two the specification does not enumerate but the data demands:

* `UNCHANGED` — price or open interest did not move beyond tolerance, with the
  reason naming which stood still. Forcing a flat reading into one of the four
  cells would invent a direction from a market that did not move.
* `INSUFFICIENT_DATA` — an observation is missing. Not the same as
  `UNCHANGED`: one means nothing happened, the other means nobody looked.

Tolerances are explicit, configurable, deterministic project heuristics
defaulting to zero. A test asserts no context value is `LONG`, `SHORT`, `BUY`
or `SELL`.

---

## 12. Risk modes

`RiskMode.FIXED` takes a money amount; `RiskMode.PERCENTAGE` takes
`risk_ratio` as a **fraction** (`0.03` is 3%), validated to `(0, 1]` so nobody
passes `3` and risks 300% of the account. Incoherent policies are rejected at
construction.

Every threshold in `RiskPolicy` — including the margin-utilisation and leverage
warning limits — is a **user or project policy, never an exchange rule**.
Nothing in the engine adjusts them; §45 forbids automatically changing a user's
configured real-money risk.

---

## 13. Position sizing

0. `require_calculable()` — refuse a contract carrying a blocking metadata
   contradiction before any arithmetic runs.
1. Validate direction, prices, stop orientation, equity, and a **verified**
   multiplier.
2. Classify entry and stop against the tick grid (§8).
3. `risk_amount` from the policy.
4. `loss_per_contract = stop_distance × multiplier`.
5. `maximum_by_risk = floor(risk_amount / loss_per_contract)`.
6. `maximum_by_margin = floor(available_for_new_positions / initial_margin)` —
   **only** when the margin is a verified current fact.
7. Final allowance = min of whatever is actually known, including the user's
   contract cap.

**The mandatory §42 case.** Account 2,500, max risk 75, entry 105, stop 104,
multiplier 100 → loss per contract 100 → `maximum_by_risk = 0`,
`allowed_contracts = 0`, outcome `NOT_PERMITTED`. Tested verbatim.

**Floor, never ceil.** 0.75 contracts is zero. Rounding up would breach the
user's stated limit by a third on the very first trade, and it produces a
tradeable-looking answer, which is what makes it a tempting bug. Boundaries at
0.99, 1.0, 1.99 and 2.0 contracts are all tested.

**Stop orientation is enforced, not absolute-valued.** A long's stop must be
below entry, a short's above. `abs(entry - stop)` would happily size a long
whose "stop" sits above the entry — a position that cannot lose the amount the
calculation claims. Wrong orientation and `stop == entry` both give `INVALID`.

**Execution feasibility is part of the answer** (§24, issue 2). A stop that
cannot be placed where the risk calculation assumed it is not a stop, so
`PositionSizing` carries a `TickFeasibility`:

| Tick size | Levels | Feasibility | Outcome |
| --- | --- | --- | --- |
| verified | on grid | `ON_GRID` | ordinary sizing |
| verified | off grid | `OFF_GRID` | **`INVALID`**, values preserved, never snapped |
| unverified | — | `UNVERIFIED` | **`UNDETERMINED`** — `maximum_by_risk` still reported |
| absent | — | `MISSING` | **`UNDETERMINED`** |

The unverified case is the deliberate half: the arithmetic is sound, so the
risk-sized maximum is published, but `allowed_contracts` is `None` because
claiming a full allowance would assert that the levels are placeable when a
critical execution constraint has not been verified. The one exception is a
risk-sized maximum of **zero**, which stays `NOT_PERMITTED` — zero contracts
are placeable on any grid, so the answer remains knowable.

---

## 14. Margin safety

The §44 panel: account equity, used margin, free margin, margin utilisation,
notional exposure, effective leverage, risk to stop.

**Free margin is `equity − used` and is allowed to be negative** (§24,
issue 4). It was clamped at zero before the hardening pass, which destroyed the
single most important fact about a breached account: 2,500 of equity against
2,700 of committed margin is 200 in deficit, and clamping made it
indistinguishable from a fully committed healthy one. The economic value and
the sizing question are now separate readings:

| equity | used | `free_margin` | `margin_deficit` | `available_for_new_positions` |
| --- | --- | --- | --- | --- |
| 2,500 | 500 | 2,000 | 0 | 2,000 |
| 2,500 | 2,500 | 0 | 0 | 0 |
| 2,500 | 2,700 | **−200** | **200** | 0 |

Sizing consumes `available_for_new_positions`, so a deficit funds no new
contracts (`maximum_by_margin = 0`, outcome `NOT_PERMITTED`) while the −200
stays observable on the account and on the panel.

Warnings: `HIGH_MARGIN_UTILIZATION`, `EXCESSIVE_EFFECTIVE_LEVERAGE`,
`RISK_LIMIT_EXCEEDED`, `MARGIN_DEFICIT`, plus `MARGIN_UNKNOWN`. Each carries the observed value
**and the threshold that produced it**, so a reader can see it is a configured
policy; the message says so in words too.

**Margin is not maximum loss.** A futures position can lose more than the
margin behind it. `MarginAssessment` has no field a reader could take for a
loss cap — a test enumerates the forbidden names — and the exposure figure sits
next to the margin figure so the gap is visible: 250 posted against 10,500
controlled.

**Missing information stays missing.** With margin absent or unverified,
`required_margin`, `resulting_used_margin`, `margin_utilisation` and
`remaining_free_margin` are all `None`, and `MARGIN_UNKNOWN` fires. Exposure
and leverage still compute, because the multiplier is a separate fact.

---

## 15. Notional exposure / effective leverage

`notional = entry × multiplier × contracts` — 105 × 100 × 1 = 10,500.
`leverage = notional / equity` — 10,500 / 2,500 = 4.2.

The denominator is **equity, not margin**. Notional over margin measures the
contract's margin rate; notional over equity measures how exposed *this
account* is, which is what §44 asks. Both hand-calculated in tests, including
the contrast (10,500/250 = 42) that shows the two are different questions.

---

## 16. Risk / reward

`reward / risk` in price distance. Orientation enforced on both sides: a long's
target above entry and stop below, a short's reversed. A wrong-side target
returns `ratio = None` with a reason — never a positive number derived from
absolute distances, because a "target" below a long's entry is not a target, it
is a second stop.

Multiple targets are supported and evaluated independently, so one badly placed
level produces one invalid result rather than discarding the others.

**It is arithmetic, not quality and not probability** (§43, §19). A test
asserts `RiskReward` carries no field named `probability`, `confidence`,
`quality`, `score`, `win_rate` or `edge`.

---

## 17. What-if simulator

Given direction, entry, multiplier, contracts, equity and a list of
hypothetical prices, returns per scenario: gross P&L, account ratio and
percent, and R multiple **only when the initial risk was supplied** — a
scenario cannot be expressed in R if nobody said what R was.

**No market scenario is hard-coded.** §44's −1/−2/−3/−5% are examples in a
document; the caller supplies whatever prices it wants and a later UI can offer
those as a preset. Passing an empty tuple returns no scenarios.

No trade is placed and no position is created. §120 holds.

---

## 18. Missing / unverified metadata behaviour

The single most important behaviour in the phase, and it has its own outcome
state.

| Situation | Answer |
| --- | --- |
| Risk allows 5, margin verified and allows 3 | `ALLOWED`, 3 contracts |
| Risk allows 0 | `NOT_PERMITTED`, 0 — definite, since zero satisfies any margin |
| Risk allows 5, **margin missing or unverified** | **`UNDETERMINED`** — risk-sized maximum 5, margin feasibility reported, `allowed_contracts = None` |
| Risk allows 5, **tick size missing or unverified** | **`UNDETERMINED`** — risk-sized maximum 5, tick feasibility reported, `allowed_contracts = None` |
| Account in margin deficit | `NOT_PERMITTED`, 0 — with the deficit amount preserved |
| Entry or stop off a **verified** grid | `INVALID` — original values preserved, never snapped |
| Contract's tick value contradicts its tick size × multiplier | raises `ContractValidationError` before any arithmetic |
| Quote describes a different symbol than the metadata | raises `QuoteMismatchError` |
| Wrong stop side, zero equity, unverified multiplier | `INVALID` |

Saying "5 contracts are allowed" when the margin is unknown would present half
an analysis as a whole one. `maximum_by_margin` is `None` — never a large
placeholder that an unknown constraint could read as a satisfied one. The same
reasoning now covers the tick grid: an unverifiable execution constraint yields
`UNDETERMINED`, not a confident full allowance.

---

## 19. Financial correctness proof

The §21 review was run as **23 empirical probes** against real calls, not by
reading. All 23 pass:

multiplier applied not dropped · LONG/SHORT signs mirror · §42 mandatory case
gives zero · 7.5 floors to 7 · loss-per-contract arithmetic · long stop above
entry refused · long target below entry refused · notional dwarfs margin ·
free margin arithmetic · leverage divides by equity · missing margin →
UNDETERMINED · unverified margin → unknown · no costs → net None · off-grid
price never mutated · expiry day UNKNOWN without verified last-trading time ·
no annualised basis · no OI context is a direction · basis reports context only
· every monetary value is `Decimal` · sizing independent of the ambient
`decimal` context · break-even renders `0` not `-0` · unsupported valuation
refused.

One probe was **withdrawn and inverted** by the hardening pass: *"free margin
never negative"* asserted the clamping that §24 issue 4 identifies as a defect.
Its replacement asserts that a genuine deficit survives. The hardened
behaviours were re-probed empirically after the fix — see §24.

**Decimal guarantees.** Division runs inside `localcontext` with pinned
precision and rounding, so a risk answer cannot change because a caller altered
the global context — probed by re-running a sizing call under `prec = 6` and
asserting an identical result. Contract counts use `ROUND_FLOOR`. Zero
denominators return `None` everywhere. `-0` is normalised. A test greps
`domain/futures/` and `domain/risk/` for `float(` and `: float` and fails on
either.

---

## 20. Tests and exact counts

**817 collected, 817 passed** with PostgreSQL. Phase 2 ended at 575, so Phase 3
adds **242** — 192 in the original pass and **50 more** from the §24 hardening
review.

| Module | Tests | of which hardening |
| --- | --- | --- |
| `futures/test_contract.py` | 51 | +3 provenance completeness |
| `risk/test_pnl.py` | 40 | +7 cost completeness |
| `risk/test_sizing.py` | 31 | +5 margin deficit |
| `futures/test_basis_and_open_interest.py` | 25 | — |
| `risk/test_margin.py` | 24 | — |
| `risk/test_tick_safety.py` | 23 | **new module** |
| `risk/test_reward_and_whatif.py` | 20 | — |
| `futures/test_metadata_provider.py` | 16 | — |
| `futures/test_quote_identity.py` | 12 | **new module** |

Coverage against the §20 checklist: P&L long/short profit and loss, break-even,
multiple contracts · FIXED and PERCENTAGE risk · one contract exactly fits ·
one contract exceeds → zero · floor never ceil · invalid long and short stops ·
stop == entry · zero and negative account · zero and negative multiplier ·
risk-limited, margin-limited and equal constraints · insufficient free margin ·
missing margin · unverified margin · existing used margin · margin utilisation
· notional · leverage · risk/reward long, short, invalid side, multiple targets
· basis premium, discount, flat, missing spot, zero spot · all four OI
combinations plus unchanged, missing and ambiguous · metadata verified,
unverified, fixture, invalid multiplier, invalid tick, invalid margin, expiry
states, provenance preservation · what-if multiple prices, long and short,
account percentage, determinism.

Integration tests unchanged at 5 — Phase 3 added no persistence.

---

## 21. Quality gates

Freshly re-run after the §24 hardening changes:

| Gate | Result |
| --- | --- |
| `ruff check .` | **PASS** — all checks passed |
| `ruff format --check .` | **PASS** — 134 files already formatted |
| `mypy --platform linux` | **PASS** — 132 source files |
| `mypy --platform win32` | **PASS** — 132 source files |
| `lint-imports` | **PASS** — **8** contracts kept, 0 broken |
| `pytest` (with PostgreSQL) | **PASS** — **817 passed, 0 skipped**, 9.7 s |
| Empirical financial probes | **PASS** — all five hardened behaviours observed directly (§24) |

**Frontend and Docker were not re-run**, and this is a deliberate accurate
statement rather than a carried-over result. The hardening pass changed backend
domain code and backend tests only: no runtime version, no configuration, no
dependency, no Dockerfile and no compose file was touched, and no frontend file
exists that could be affected. Their last recorded results — frontend tsc /
eslint / prettier PASS, frontend tests 12 passed, frontend build PASS,
`docker compose config -q` PASS — date from the original Phase 3 pass and are
reported here as such.

No gate was weakened. The contract count went **up**, 6 → 8. No dependency was
added.

One lint finding is worth recording: `bandit` rejected two `assert` statements
used for type narrowing. They were replaced with real `raise` guards rather
than suppressed — `python -O` strips assertions, and a risk budget silently
becoming `None` under an optimised interpreter is not a failure mode worth
leaving open.

---

## 22. Architecture review

`api → application → domain`; adapters implement ports; the domain imports
nothing outside the standard library.

`ContractMetadataProvider` is a **port** in `app/application/ports/`;
`ManualContractMetadataProvider` is an **adapter** in `app/adapters/`.

**Eight contracts, up from six:**

* *"Adapters never compute indicators or market structure"* **widened** to add
  `app.domain.risk` — no adapter may compute a P&L or a position size.
* **New:** *"The risk engine depends only on contract facts, never on
  analysis"* — `app.domain.risk` may not import `app.domain.structure` or
  `app.domain.technical`. This keeps a money figure from being coupled to an
  analysis opinion, and mechanically preserves the §45 deferral.
* **New:** *"The futures domain does not depend on analysis engines"* —
  contract facts are inputs to analysis, never outputs of it.

Risk, P&L and futures calculations are pure functions over explicit inputs with
no clock, no I/O and no global state, so live, replay, backtest, paper trading
and shadow mode can share them without duplication.

---

## 23. Technical debt / limitations

| Item | Impact | When |
| --- | --- | --- |
| **§45 adaptive volatility warnings deferred** | Expected — see note below | Phase 4 |
| Annualised basis deferred | Expected — needs a verified expiry timestamp and day-count convention (§118) | when the calendar is verified |
| Only `LINEAR` valuation supported | Low — `INVERSE`/`QUANTO` are representable and refused, not silently mis-computed | when such a contract is needed |
| No live contract metadata source | Expected — §31's stated fallback is in place | Phase 15 |
| Maintenance margin stored but unused | Low — no margin-call logic exists yet | Phase 9, with paper positions |
| `trading_session` is an opaque verified string | Low — no session parsing until hours are verified | with the session data |
| ~~Partial cost sets sum what is present~~ | **RESOLVED** in §24 — `CostCompleteness`; a partial set can no longer surface as a net | — |
| ~~Tick-grid validation is offered but not enforced at sizing~~ | **RESOLVED** in §24 — `TickFeasibility` participates in the sizing outcome | — |
| Risk/reward applies no tick grid | Low, deliberate — `risk_reward` takes no contract, so it is pure geometry; the contract-aware sizing path is where execution feasibility is decided | Phase 4, if targets need placeability |
| Provenance completeness checks existence, not freshness | Expected — deciding a verified fact is *stale* needs an exchange revision schedule (§118) | when that schedule is verified |
| Phase 2 O(candles × swings) rebuild loops | Carried | before the backtest phase |
| VWAP session anchoring still UTC-calendar | Carried | with verified session data |
| Deprecated event-loop policy API | Carried from Phase 0 | before Python 3.16 |
| Turkish text unreviewed by a native speaker | Carried | Phase 5 |

**Why §45 was deferred rather than forced in.** Its two behaviours are
"HIGH_VOLATILITY → reduced suggested risk" and "EXTREME_VOLATILITY → NO TRADE
candidate". The first modifies sizing, which §45 itself forbids doing without
user control; the second is the NO TRADE engine, explicitly Phase 4+.
Implementing only a passive volatility warning would duplicate the regime
signal Phase 2 already produces, for no consumer, while coupling the risk
engine to the analysis engines — which the new contract now forbids. The
deferral is recorded here and mechanically enforced.

**No placeholder was marked complete.** Everything above is either IMPLEMENTED
and tested, or explicitly deferred with the reason and the owning phase.

---

## 24. Human-review financial hardening closeout

Five financial-correctness issues were raised in human review after the
original report. **All five were audited against the actual code and all five
were confirmed as real defects.** Each was fixed; none was argued away.

### Issue 1 — wrong-contract / quote identity

**Finding: confirmed.** `FuturesQuote` carried a `symbol`, but nothing compared
it to the contract's. `read_open_interest` took two quotes and compared them
without checking they described the same instrument — a front-month roll would
have read as mass liquidation followed by mass new participation, a perfectly
plausible reading of a change that never happened.

**Fix.** `QuoteMismatchError`, `require_matching_quote(contract, quote, op)` and
`require_same_instrument(first, second, op)` in `contract.py`. New
contract-aware entry points `calculate_contract_basis` and
`read_contract_open_interest` take the metadata and enforce the pairing;
`read_open_interest` enforces quote-to-quote identity.

**No VİOP symbol rule was invented.** Comparison is exact string equality after
`.strip()` — nothing else. Surrounding whitespace is tolerated because it is a
transport artefact; **case folding is not**, because "are VİOP symbols
case-insensitive?" is an exchange convention this project has not verified. A
case difference is therefore a mismatch, and a test pins that as deliberate.

Observed: `basis calculation: metadata describes 'PROBE_A' but the observation
describes 'PROBE_B'`.

### Issue 2 — the tick grid must participate in risk safety

**Finding: confirmed, and worse than deferred.** The original report listed
tick-grid enforcement as technical debt. The audit found a second, unrecorded
hole: `check_tick_value` was computed by the provider at registration and
**read by nothing**. A contract asserting a tick value that contradicted its own
tick size and multiplier flowed straight into P&L, sizing, margin and what-if.

**Fix.** `TickFeasibility` (§13) plus `require_calculable()` at the entry to
`size_position`, `calculate_contract_pnl`, `assess_margin` and
`simulate_contract`.

**Off-grid levels are refused, never rounded** — the reason string carries the
original values and the words *"the levels are preserved and were not rounded to
the grid"*, and `stop_distance` is left unset so no downstream arithmetic can
proceed on a level the exchange would not accept.

**Missing or unverified tick size is decided explicitly**, and the decision is
`UNDETERMINED` rather than "mathematically sized but execution unknown"
presented as an allowance: `maximum_by_risk` is published, `allowed_contracts`
is `None`. Claiming a full allowance would assert placeability that has not been
verified.

Boundary tests use exact `Decimal` values on a 0.05 grid: `105.00`, `105.05`,
`105.10`, `99.05` accepted; `105.01`–`105.049` and `105.037` refused; the same
`105.037` accepted on a 0.001 grid.

### Issue 3 — partial transaction costs

**Finding: confirmed.** `TradeCosts.total` summed whatever was present and
`PnLResult.net` subtracted it, so supplying only a commission produced a figure
labelled *net* that silently valued the unsupplied fees and slippage at zero.

**Model B was chosen** — a typed completeness state — over model A (net only
when complete, nothing otherwise). Model A discards genuinely useful
information: knowing the net cannot be better than *gross − 15* is worth having,
provided it can never be mistaken for the net itself. See the table in §9.

`total` was **renamed** to `known_total` rather than kept; the old name invited
exactly the subtraction that caused the defect. **No commission, fee or slippage
default was invented anywhere.** The explicit-zero versus missing distinction is
tested in both directions.

### Issue 4 — negative free margin / deficit

**Finding: confirmed.** `AccountState.free_margin` returned
`max(equity − used, 0)`, so an account 200 in deficit reported 0 free margin —
identical to a fully committed healthy account.

**Fix.** `free_margin` is now the raw economic value and may be negative;
`margin_deficit`, `is_in_deficit` and `available_for_new_positions =
max(free_margin, 0)` are separate readings. Sizing consumes the last of these,
so the clamping that sizing legitimately needs happens at the question that
needs it rather than in the stored value. `RiskWarningCode.MARGIN_DEFICIT` and
`MarginAssessment.margin_deficit` surface it. See the table in §14.

### Issue 5 — `VERIFIED_CURRENT_FACT` freshness audit

**Finding: confirmed, in the narrow form.** The Phase 0 primitive already
carries `source` and `as_of`, but nothing required them, so a value could claim
to be a current exchange fact with neither attribution nor a date.

**The Phase 0 primitive was not redesigned.** The check was added at the Phase 3
validation layer, where a contract is assembled: `UNSOURCED_VERIFIED_FACT`
(blocking) and `UNDATED_VERIFIED_FACT` (warning), reported by
`provenance_issues()`. The asymmetry is deliberate — an unattributable claim of
currency is a guess wearing the right status, whereas a missing date is a gap in
the record.

**No freshness duration or exchange-specific TTL was invented.** Nothing here
decides a value is *stale*; that would need an exchange revision schedule this
project does not hold. The check requires only that the provenance exists, so a
human or a later phase can judge it. Values carrying a non-verified status are
exempt: they already tell the caller not to rely on them.

**Meaning for Phase 3, documented and tested.** `source` is where the claim came
from; `as_of` is when it was observed to be true; `VERIFIED_CURRENT_FACT` means
a human confirmed it against an authoritative publication at `as_of` — it does
not mean the system checked, and it does not expire on its own.

### Issue 6 — report accounting

The original §25 said **"Untracked (7)"** above a list of **nine** paths. Miscount
corrected: there are **nine** untracked entries. See §26.

### Empirical re-verification

All five were re-probed by direct calls after the fix, not by reading:
identity mismatch refused in the basis engine, the OI engine and quote-to-quote ·
case difference refused · off-grid `INVALID` with values preserved · unverified
tick `UNDETERMINED` with `maximum_by_risk` still 100 · contradictory tick value
refused by sizing, P&L and margin · six cost combinations producing the §9 table
· deficit −200 preserved with `available_for_new_positions` 0, sizing
`NOT_PERMITTED`, `MARGIN_DEFICIT` raised · unsourced verified fact blocking,
undated verified fact warning, unverified fact exempt.

**Nothing here became a trade signal.** Every addition is a refusal, a typed
uncertainty state, or a preserved measurement.

---

## 25. Phase-boundary verification

No Phase 4+ work was implemented or scaffolded. Explicitly **absent**:

multi-timeframe fusion · 1D/1H/15M/5M orchestration · Evidence Fusion ·
Contradiction Engine · setup quality · entry quality · NO TRADE engine ·
Bull/Bear/Neutral synthesis · StrategyRouter · Beginner/Pro UI · Claude Vision
· Claude synthesis · paper trading · journal · backtesting · replay · shadow
mode · live feed · WebSockets · news · broker integration · Midas ·
`OrderExecutionPort` · real order execution.

Verified mechanically: no `app/domain/setups/`, `strategies/`, `trading/` or
`backtest/` package exists; `RiskReward` carries no quality or probability
field; `MarginAssessment` carries no loss-cap field; the Phase 0 test asserting
no broker, execution or Midas module exists in `ports/` still passes; the
manual provider's imports are `collections`, `app`, `__future__` and nothing
else.

§120 holds: no order is sent, no position is created, and nothing was built
that could send one.

---

## 26. Final Git status

```
HEAD:  a8a3ccb  Complete Phase 2 market structure and regime engine  (main, origin/main)
```

**Nothing was committed or pushed by Claude.** `HEAD` is unchanged from the
start of Phase 3 and from the start of the §24 hardening pass; the git-write
hook remained active throughout.

**Modified (2)** — `git diff --stat`: 2 files changed, 78 insertions, 14 deletions
`backend/pyproject.toml`
`docs/architecture.md`

**Untracked (9)**
`backend/app/adapters/contract_metadata/`
`backend/app/application/ports/contract_metadata.py`
`backend/app/domain/common/arithmetic.py`
`backend/app/domain/futures/`
`backend/app/domain/risk/`
`backend/tests/factories_futures.py`
`backend/tests/unit/futures/`
`backend/tests/unit/risk/`
`docs/phase_reports/phase_3_completion_report.md` *(this file)*

`docs/viop_master_spec.md` is unmodified.

---

# STOP

Phase 3 is complete and validated. Phase 4 has not been started, scaffolded, or
prepared for. No Phase 4 dependency was installed. Nothing was committed or
pushed.

Awaiting human review.
