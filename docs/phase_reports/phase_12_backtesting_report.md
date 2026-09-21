# Phase 12 — Deterministic backtesting

**STATUS: IN PROGRESS — AWAITING HUMAN REVIEW**

Part 1, Part 2A and Part 2B (final verification) are implemented and measured.
Phase 12 is **not closed**: closure requires commit, push and a green CI run.

Phase 12 is **not closed**. It is uncommitted, unpushed, and has not been
through CI.

Part 1 covers the backend: the strategy-policy model, historical data
orchestration, no-lookahead correctness, reuse of the existing engines,
simulated execution, run identity and isolation, persistence, financial
correctness, and the tests that hold all of it in place.

Part 2A — the public API, the frontend workspace, Beginner/Pro presentation
and the focused API/frontend/PostgreSQL tests — is implemented; see section 30.

Part 2B — real-browser end-to-end validation, the mutation sweep and the final
security review — is complete; see section 31.

---

## 1. What this phase adds, in one paragraph

A backtest asks the same question Phase 11 asks a human — *what would I have
done here?* — several thousand times, without anybody watching. That is the
entire risk profile. A leak in a replay is noticed by the person stepping
through it; a leak in a backtest is reported as a profit. So Phase 12 adds
**no second engine of any kind**. It adds a rule model, a causal walk, and a
place to put the result.

## 2. Files added

| Path | What it holds |
| --- | --- |
| `app/domain/backtest/policy.py` | What a rule may see and may answer |
| `app/domain/backtest/strategies/ema_crossover.py` | The reference strategy |
| `app/domain/backtest/levels.py` | Making a derived level executable |
| `app/domain/backtest/run.py` | Run identity, statuses, resource bounds |
| `app/domain/backtest/fingerprint.py` | Configuration and result digests |
| `app/application/backtest/ports.py` | `BacktestStore` and its record types |
| `app/application/backtest/service.py` | `BacktestRunner` — the causal walk |
| `app/adapters/persistence/backtest_models.py` | Four tables |
| `app/adapters/persistence/backtest_store.py` | Atomic publication |
| `app/adapters/performance/backtest_source.py` | One run's outcomes, for Phase 10 |
| `alembic/versions/0006_backtest.py` | The migration |

## 3. Files modified, and why

| Path | Change | Reason |
| --- | --- | --- |
| `app/domain/paper/model.py` | `PositionOrigin.STRATEGY_BACKTEST` | A simulated trade must be distinguishable from one a person decided to take |
| `app/domain/instrument/policy.py` | `ProductPolicy.price_increment()` | A derived level has to be made placeable; "does this fit?" cannot tell a caller what to propose instead |
| `app/domain/futures/policy.py` | Implements it | Returns the contract's tick size with provenance intact, unjudged |
| `app/adapters/performance/paper_source.py` | `_fold` → `fold_ledger`, `_replace_unrealized` → `replace_unrealized` | Made public so Phase 12 reuses the fold instead of writing a second one |
| `app/domain/backtest/run.py` | Removed `max_decision_records` | Declared but never enforced, and unreachable: one record per boundary means `max_boundaries` already bounds it |
| `alembic/env.py` | Registers the new models | Same as every prior phase |
| `backend/pyproject.toml` | Three contracts | 28 → 31 |
| `tests/integration/paper_support.py` | `truncate` covers the new tables | A run references the dataset it read, so PostgreSQL refuses to truncate the dataset alone |
| `tests/factories_paper.py`, `tests/unit/multi_asset/test_instrument_boundary.py` | Stubs implement `price_increment` | mypy caught both — the protocol is enforced, not documentary |
| `tests/integration/test_paper_persistence.py`, `test_journal_persistence.py`, `test_replay_persistence.py` | Register Phase 12 models; expected table set extended | The established per-phase pattern for the schema-drift tests |

## 4. The strategy-policy model

`StrategyPolicy` is a `Protocol` with `identifier`, `version`, `warm_up_bars`,
`parameters()` and `decide(context)`. Implementations must not read a clock,
use randomness, perform input or output, call a model, or hold mutable state
between boundaries.

`StrategyContext` carries `as_of`, the symbol, the driver timeframe, the driver
bar that *finished* at `as_of`, `current` and `previous` readings, confirmed
higher-timeframe readings, `bars_available`, and whether the run already holds
an open position.

`StrategyDecision` is one of `NO_SIGNAL`, `WAIT`, `ENTRY_INTENT`, `EXIT_INTENT`
and always states a reason, which is recorded in the run's decision trace.

## 5. No-lookahead is a property of the type, not of care taken

`Readings` are scalars. There is no series in the context and therefore no
index for a policy to read past. A rule that wanted tomorrow's candle would
have to be handed one, and nothing hands it one.

Indicators are computed **once** over the whole driver series and indexed by
boundary. That is valid only because Phase 1's values are causal — value *i*
derives from candles *0..i*. The runner does not assume this;
`tests/unit/backtest/test_indicator_causality.py` proves the equality against
recomputation over every prefix, for every indicator the reference strategy
reads. An indicator that stopped being causal fails a test rather than quietly
leaking the future.

## 6. Next-bar entry is enforced by the engine, not remembered by the runner

The position is created with `decision_time = T`, the boundary. Phase 9 fills
an entry on the first bar opening *at or after* the decision time. The bar that
closed at `T` opened before it; the next bar opens exactly at `T`. So a signal
cannot enter on the candle that produced it, and this holds because of where
the timestamp is set rather than because of a check somebody has to maintain.

`test_backtest_golden.py::TestBNextBarEntry` pins the fill price to the next
bar's open, and `test_backtest_causality.py` asserts it again with a fixture
where the next open is *worse* than the signal close.

## 7. Higher timeframes appear only once closed

A forming 1H candle is **absent** from `context.higher`, not present with
partial values. A partial higher-timeframe reading is the most plausible-looking
leak available, because the number it produces is nearly right.

## 8. No second financial engine

| Quantity | Owner |
| --- | --- |
| Which candles had finished | Phase 11 `coverage_end` |
| Indicators | Phase 1 `compute_technicals` |
| Position size, and whether to allow it at all | Phase 3 `size_for_product` |
| Entry, stop, target and manual fills | Phase 9 `open_position` / `apply_observation` |
| Realized and unrealized P&L | Phase 9 |
| Outcomes from a ledger | Phase 9 `fold_ledger`, imported not copied |
| Metrics | Phase 10 `summarise` |

The runner performs no arithmetic on prices or money. The one calculation this
phase introduces is grid alignment (section 10), which is `Decimal` rounding
over values the caller supplies and touches no market fact.

## 9. Proven, not asserted: the ledgers are the same ledger

`test_backtest_parity.py` opens one trade twice — once through the runner, once
through the Phase 9 paper service with the same levels, account, risk and
simulation policy — and asserts the two financial ledgers are **equal event for
event, including every data field**. It then asserts the Phase 10 summary of
the run equals the Phase 10 summary of the paper population: same realized
gross, same wins, same losses, same expectancy.

## 10. Grid alignment: the one new rule, and why it only ever hurts

An ATR-derived stop lands wherever the arithmetic lands and almost never on the
exchange's price grid. Phase 3 refuses an off-grid level rather than snap it,
which is correct for a level a *person* typed and which would make a derived
level impossible to trade at all. This was not theoretical: the first end-to-end
run produced **zero positions**, with all four crossovers refused as
`entry 111.2, stop 112.25000000000000060 is not a whole number of 0.25 ticks`.

The runner therefore aligns derived levels, under three rules:

1. **Only protective levels move.** The intended entry is a price the market
   printed. An off-grid entry means the dataset and the product disagree about
   what this instrument is — surfaced as a refusal, not rounded away.
2. **Always away from the entry.** The stop moves further out and the target
   moves further out. Risk per unit grows, so the position sizes *smaller*; the
   reward becomes harder to reach. Rounding to the nearest tick would sometimes
   shrink the measured risk distance and silently inflate the size.
3. **Never onto an unconfirmed grid.** Alignment requires a verified increment.
   Without one the levels pass through untouched, and Phase 3 then declines the
   sizing because it cannot confirm they are placeable — so the run records a
   refusal rather than a trade at invented levels. That is stricter than
   aligning would have been, and it is the right way round: rounding to a grid
   nobody verified would manufacture the very fact that is missing.

The strategy is not told the tick size — an import-linter contract forbids it.
Alignment happens in the runner, the one place holding the frozen product. Every
alignment that moved something says so in the decision trace.

`tests/unit/backtest/test_levels.py` sweeps the property directly: for a range
of prices either side of the entry, the aligned distance is never smaller than
the original, and alignment is idempotent.

## 11. Metadata trust is preserved

The product is resolved **once** and frozen for the whole run. Nothing re-reads
the provider afterwards, so changing contract metadata cannot alter a result
already computed. With no metadata provider composed — which is what production
composes — a run is refused with `PRODUCT_METADATA_UNAVAILABLE` rather than run
on assumed specifications. No price, multiplier, margin, tick size or expiry is
fabricated anywhere in this phase. Every fixture contract is `TEST_FIXTURE`.

## 12. Run identity

`BC-<32 hex>` is the configuration fingerprint: dataset id, symbol, driver
timeframe, interval, strategy identifier, version and parameters, simulation
policy, risk policy, account, the frozen product snapshot and the runner rules
version — over one canonical JSON document, for the reason Phase 11 learned
(concatenation hides its own boundaries).

`BR-<24 hex>` derives the run id from the configuration and the caller's
attempt key.

Two runs of one configuration under different keys produce the same
`configuration` and the same `result_digest`; a repeated key returns the first
run without recomputing anything. Both are asserted.

## 13. Persistence semantics — stated, not implied

* The run row is created **before** the walk, so an interrupted run is visible
  as `PENDING` rather than invisible.
* Positions, events and decisions are written **only at publication**, in one
  transaction. The two possible outcomes are "nothing" and "everything".
* A check constraint refuses a `COMPLETED` run with no result, and another
  refuses a `FAILED` run with no code — enforced by the database, independently
  of the code that is supposed to maintain it.
* The event table carries Phase 9's append-only trigger. UPDATE and DELETE are
  both refused, and both are tested.
* A retry with the same attempt key returns the run that key produced —
  including a `PENDING` or `FAILED` one. Re-running is a new attempt under a
  new key. **The key identifies the request, not the intention.**

There is no claim of atomicity beyond this. Publication is atomic; the run
lifecycle is not a single transaction, and the report does not pretend it is.

## 14. Isolation

A run's positions live in `backtest_positions`, never in `paper_positions`. The
performance source is scoped to one run **at construction**, not by a filter
argument, so contamination is not a question of remembering to pass the right
parameter. Tested: a run leaves the paper population empty, a second run does
not change the first one's answer, a failed run contributes nothing, and the
two tables' counts are asserted directly.

## 15. Resource bounds

2,500 boundaries, 200 positions, 500 warm-up candles. Every limit is **refused**
with `RESOURCE_LIMIT` and a message naming the number and the remedy. Running
the first 2,500 boundaries of a larger request and reporting it as the requested
range would be a false statement about what was tested.

`max_decision_records` was declared in the first draft and never enforced. It
was removed rather than given a check: one record is written per boundary, so
`max_boundaries` already bounds the trace, and a second number would read like
a protection while protecting nothing.

## 16. Architecture

31 import-linter contracts, all kept. Three are new:

* **A strategy policy decides, and knows nothing about products, money or
  storage** — `app.domain.backtest` may not import products, instruments, risk,
  paper, performance, journal, replay, the application layer, adapters or the
  API.
* **Nothing beneath backtest depends on it** — no engine may ask whether it is
  running inside a backtest.
* **The backtest runner orchestrates engines and computes nothing** — the
  individual indicator routines and P&L primitives are forbidden, as is
  `app.domain.futures`: the runner works through the generic product boundary.

## 17. A schema drift the tests caught

The first migration named the positions unique constraint
`uq_backtest_positions_one_position_per_ordinal`, while the shared naming
convention derives `uq` names from the table and first column and therefore
resolves the model's declaration to `uq_backtest_positions_run_id`. The three
`TestSchema` drift tests failed on it.

Because Phase 12 is unreleased and `0006_backtest` is this phase's own
migration, it was corrected in place rather than patched by a follow-up
revision — and then verified the strong way: `alembic downgrade base` followed
by `alembic upgrade head`, after which all three drift tests pass. (The Phase 11
lesson about never amending an *already-shipped* migration stands; that case was
a previous phase's revision running in a container.)

## 18. Tests

| Suite | Count | What it holds down |
| --- | --- | --- |
| `tests/unit/backtest/test_indicator_causality.py` | 9 | Value *i* equals the prefix value, for every indicator read |
| `tests/unit/backtest/test_strategy.py` | 36 | The reference rule, plus the shipped parameters pinned against tuning |
| `tests/unit/backtest/test_levels.py` | 78 | Alignment moves levels away from the entry, never toward it |
| `tests/integration/test_backtest_golden.py` | 29 | Scenarios A–U, expectations derived by hand from the fixture's OHLC |
| `tests/unit/backtest/test_atr_precision.py` | 10 | The one float-to-money crossing, pinned |
| `tests/unit/backtest/test_metadata_trust.py` | 16 | Test composition cannot become authority |
| `tests/integration/test_backtest_entry_grid.py` | 27 | No position at a price the product could not quote |
| `tests/integration/test_backtest_result_honesty.py` | 5 | A run can report a loss, a breakeven and an unknown cost |
| `tests/integration/test_backtest_causality.py` | 10 | Nothing unfinished reaches a decision or a fill |
| `tests/integration/test_backtest_parity.py` | 7 | The run's ledger *is* the paper ledger; the metrics *are* the Phase 10 metrics |
| `tests/integration/test_backtest_isolation.py` | 13 | A run's trades stay in that run |
| `tests/integration/test_backtest_recovery.py` | 15 | An interruption costs the answer, never produces a wrong one; explicit terminalisation |
| `tests/integration/test_backtest_bounds.py` | 8 | Limits refuse rather than truncate |

The golden scenarios drive a **scripted** strategy rather than the EMA rule: an
EMA crossover is a poor instrument for asking "does an entry decided at 10:05
fill at the 10:05 open", and the reference rule has its own unit tests. Every
golden expectation is arithmetic a reader can check against the bars written a
few lines above the assertion.

## 19. Gates

All run locally against real PostgreSQL, on Python 3.14.3 / Windows 11.

```
ruff check .                 All checks passed
ruff format --check .        all files formatted
mypy --platform linux        Success: no issues found
mypy --platform win32        Success: no issues found
lint-imports                 Contracts: 31 kept, 0 broken
pytest                       (see section 20)
```

## 20. Test run

```
3714 passed, 0 failed, 0 skipped        (backend, real PostgreSQL, 126s)
  377 passed                            (frontend baseline, unchanged by this phase)
```

3,567 at the end of Part 1; 3,635 after the financial closeout (section 20d);
3,714 after the specification audit (section 20e).

No test is skipped, none is marked expected-failure, and none is marked xfail.
The frontend gates are listed because CI runs them; this phase changed no
frontend file.

## 20a. Cost, measured rather than estimated

A maximum-size run on this machine, against real PostgreSQL:

```
dataset   3,682 candles across M5/M15/H1, stored in 0.32s
run       2,500 boundaries, 62 positions, 1.00s  (0.40 ms/boundary)
metrics   Phase 10 summary over the run: 0.04s
```

Indicators are computed once for the whole series rather than per boundary,
which is what keeps this linear. The 2,500-boundary ceiling therefore costs
about a second of compute, and the limit exists to bound the *request*, not
because the walk is slow.

**The trade figures from that run are meaningless as evidence.** The fixture is
a deterministic sawtooth built to exercise a crossover, so it produces 62 wins
and no losses. It says nothing about the strategy and is reported here only as
a measurement of throughput.

## 20b. Two injected defects, to show the tests are not decorative

A full mutation sweep belongs to Part 2. Two targeted defects were injected
here because they are the two this phase exists to prevent:

| Defect injected | Result |
| --- | --- |
| `away_from` rounds *toward* the entry instead of away | 65 tests fail, including `test_levels.py`'s distance sweep and `TestUGridAlignment` |
| The position is stamped `decision_time = bar.open_time` instead of the boundary — hindsight entry | The same run fails; `TestBNextBarEntry` and the causality suite both catch it |

Both were reverted and the suite re-run green.

## 20c. The container

The running image predated this phase, so the stack could not migrate to Phase
12's head. It was rebuilt:

```
docker compose build backend && docker compose up -d backend
docker compose exec -T backend alembic upgrade head
    0004_replay -> 0005_replay_command_target
    0005_replay_command_target -> 0006_backtest
docker compose config                      valid
backend / frontend / postgres              all healthy
GET /api/health                            200
```

The container's constraint names match the models', which is the same check the
three `TestSchema` drift tests make locally — so a green local run and a green
container mean the same thing.

## 20d. Final financial closeout (second pass)

A narrow financial review after Part 1 was written found one real gap and
produced four hardening changes. None of them altered a historical dataset, and
none introduced a rounding that makes an invalid price valid.

### The gap: three prices, only one of which was checked

A simulated entry involves the price the strategy **proposed**, the price
Phase 9 actually **fills** at - the next bar's open - and the **protective
levels**. Phase 3 has always checked the proposed entry, so an off-grid plan
was already refused. Nothing checked the executed one. A dataset one tick out
of step with the product would therefore have opened, stopped and closed
positions at prices that product cannot quote, and every figure derived from
them would have looked perfectly ordinary.

Closed by refusing the run, not by moving the candle:

* `DATASET_OFF_PRODUCT_GRID` - the driver series is checked against the
  *verified* increment before anything is evaluated, and the refusal names the
  first offending candle. No run row is created, and the dataset is untouched.
* `SLIPPAGE_OFF_PRODUCT_GRID` - a fixed slippage that is not a whole number of
  ticks is applied to an on-grid market price and would put every market-style
  fill off the grid. Phase 9's rules are unchanged; this is an additional
  precondition a *backtest* requires.

An unverified increment convicts nothing. The run proceeds, and Phase 3 then
declines every entry because executability cannot be confirmed either - the
refusal comes from the engine that owns the question.

### Sizing follows the final stop

Alignment happens before `size_for_product`, never after. The test uses numbers
where the two answers differ: entry 100, planned stop 97.10, aligned stop
97.00, multiplier 10, risk budget 290. The plan would approve **ten** units
(2.90 x 10 = 29.00 each, 290/29 = 10); the aligned stop approves **nine**
(3.00 x 10 = 30.00 each, 290/30 = 9.67). Sizing on the plan would have risked
300 against a 290 budget.

### The ATR numeric boundary

`Decimal(str(atr))`, which is the conversion policy this repository already
uses at every float-to-money crossing and is justified in
`app/application/synthesis/context.py`. It is exact with respect to the float
that was computed. `Decimal(float)` would drag in the full binary expansion;
quantising to chosen places would discard a digit the indicator produced. The
multiplication is then performed in `Decimal`, so the float error stops at one
step - `app/domain/analysis/generation.py` multiplies in float first, and the
strategy deliberately does not. No validation was weakened to hide a tail:
`tests/unit/backtest/test_atr_precision.py` asserts the tail survives into the
level, and that the resulting level is *not* on the grid until the runner
aligns it.

### Metadata trust, in three parts

| | | |
| --- | --- | --- |
| **A** | Verified product facts the engine requires | Only `VERIFIED_CURRENT_FACT` is authoritative; everything else is refused |
| **B** | Test-only composition supplying controlled authoritative facts | A `ManualContractMetadataProvider` built in tests. A deliberate lie, confined to the test process |
| **C** | `TEST_FIXTURE` provenance | Not a weaker fact - a non-fact wearing a label that says so |

The risk is not that (B) exists; it is that (B) could be reachable from a
deployment. It is not: nothing in `app/` constructs the provider, `main.py`
sets `product_resolver = None` categorically rather than as the false branch of
a condition, `config.py` contains no flag whose name touches contract metadata,
and the dependency type-checks whatever it finds on app state. Sixteen tests in
`tests/unit/backtest/test_metadata_trust.py` hold each of these, including a
guard that fails if the checked file list ever silently empties.

### Result honesty

* A loss is reported as a loss; a **breakeven is kept separate from a win**,
  because collapsing them inflates a win rate by exactly the trades that earned
  nothing.
* An unmodelled fee yields **no net figure at all**, not a net equal to gross.
* A stated fee makes the win smaller, never larger.
* The shipped strategy parameters are **pinned by a test**, so tuning one to
  flatter a fixture is a visible edit that has to be argued for. They were not
  changed during this phase.
* The `trending` fixture *was* changed during Part 1 - from 0.40 to 0.50 steps
  and 0.15 to 0.25 wicks - to put it on the fixture contract's tick grid. That
  changes its P&L as a side effect. It was a grid fix, not a tuning, and no
  strategy parameter moved with it.
* **Simulated historical results do not guarantee future returns.** No
  document, route or frontend file in this repository claims the reference
  strategy is profitable.

### Interrupted-run recovery

`BacktestRunner.abandon(run_id)` terminalises a PENDING run as FAILED with the
code `INTERRUPTED`. The workflow is deliberately two explicit steps - abandon,
then ask again under a new attempt key - because a new key is a decision a
person makes rather than one a retry loop makes on their behalf using financial
output nobody checked. There is no automatic resumption: the walk's
intermediate state is never written anywhere, so a resumption point invented
afterwards would publish a result computed partly before the interruption and
partly after. A COMPLETED run is refused both by the runner and by the store,
at the statement that would actually destroy the result. Abandoning twice is
harmless. No job scheduler was added.

### A documentation inconsistency left alone

`README.md`'s phase table lists **every** phase from 1 to 16 as "Not started",
including the eleven that are complete and committed. Row 12 is therefore
wrong, but so are rows 1-11. Editing only row 12 would imply the others are
current, and repairing the whole table spans phases this review does not cover,
so it is reported here rather than changed. It makes no profitability claim.

## 20e. Specification audit (third pass)

The Part 1 specification was re-read against the implementation, section by
section. Most requirements were already met by Part 1 and the financial
closeout. Four genuine gaps were found and closed, and one wrong claim was
found in my own code.

### The wrong claim

`app/domain/backtest/strategies/__init__.py` stated that a strategy "is
registered by identifier and version" and that "an unknown identifier or
version is refused". **No code implemented either sentence.** A docstring
describing a guard that does not exist is worse than no docstring, because it
is what a reviewer reads instead of the code.

`app/domain/backtest/registry.py` now implements it: a read-only table of
identifier to supported versions, checked **before any market is read**, with
two distinct refusals. Nothing imports by name, evaluates a string, or
constructs a class from client input - the caller still supplies the policy
object. The table is injectable the same way `RunBounds` is, so tests can drive
a scripted policy; production passes nothing and receives the shipped table.

### The other three gaps

| Gap | Section | Closed by |
| --- | --- | --- |
| Failure injection covered publication only | 26 | Four more stages - at the first boundary, after an entry intent, after a fill, near the end - plus a store that refuses to create the run at all |
| The coverage comparison was never tested at its edge | 31 | One microsecond either side of `coverage_end`, and a window exactly one boundary wide |
| Identical symbols with different candles were untested | 34 | Same symbol, one changed candle: different digest, different configuration |
| The phase boundary was claimed, not checked | 39 | Mechanical absence tests for optimisation, grid search, walk-forward, Monte Carlo, ranking, broker/order capabilities, backtest routes and schemas, LLM imports, randomness and wall-clock shortcuts |

### Two of my own assertions were wrong, and the code was right

* I asserted a partial exit produces three fills. Phase 10 counts **exit**
  fills, so it is two, and the position remains one sample. The test now says
  so explicitly.
* I asserted two single-trade runs would differ in drawdown. Phase 10 measures
  peak-to-trough with the peak starting at the first point, so a single trade
  has **no** drawdown by definition. The test now builds a genuine two-trade
  curve (+60 then -30 → drawdown 30) and separately asserts that neither
  single-trade run can show the other's fall.

Both were corrected against Phase 10's actual definitions rather than by
adjusting Phase 10.

### One test of mine was too naive to keep

An absence test banned the *words* "midas" and "broker" anywhere in `app/`. It
failed - because `core/config.py` says "there is deliberately no broker
configuration" and the manual provider says it "does not scrape Midas". Banning
the word would have deleted the documentation of the prohibition. The test now
bans the *shape of a capability* (`def place_order`, `import midas`,
`BrokerPort`, ...) and a companion test asserts the prohibition is still
written down.

### Known behaviour, documented rather than changed

An unexpected exception from a strategy (a defect, not a refusal) propagates
out of `run()` untyped, leaving the run PENDING with nothing persisted. The
financial guarantee holds - no positions, no events, no decisions, never
COMPLETED - and the operator terminalises it with `abandon`. Part 2's API layer
will need to map such an exception to a response; that is noted as Part 2 work
rather than papered over here with a broad `except`.

## 30. Part 2A — the API and the workspace

### What the surface is

Ten operations under `/api/backtest`, following the existing routing
conventions:

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/capability` | Whether this deployment can price a simulated trade |
| GET | `/strategies` | The registry, described |
| GET | `/datasets` | Immutable datasets a run may be pointed at |
| POST | `/runs` | Create (201) or replay (200) one run |
| GET | `/runs` | Bounded run list |
| GET | `/runs/{id}` | Identity, configuration, status, totals |
| POST | `/runs/{id}/abandon` | Terminalise an interrupted run |
| GET | `/runs/{id}/trace` | Paginated decision trace |
| GET | `/runs/{id}/positions` | Simulated positions, replayed from their ledgers |
| GET | `/runs/{id}/positions/{pid}/events` | Paginated ledger |
| GET | `/runs/{id}/performance` | Phase 10's metrics for this run |

### Two correctness gaps Part 2A closed

Part 1's idempotency returned the earlier run for **any** repeat of an attempt
key, including one carrying a different configuration — which would answer a
question nobody asked. The fingerprint is now computed first and a mismatch is
a 409 `ATTEMPT_KEY_REUSED`.

Part 1's `publish` would also complete a run that had already been abandoned,
turning a terminal FAILED into COMPLETED after somebody had been told
otherwise. Both guards now sit **inside** the transaction that would do the
damage: `TerminalRunError` on publish, `CompletedRunError` on fail. A guard
above the write is one two concurrent callers can both pass.

### The read model

`store.get` hydrates every decision and every position's ledger. Part 2A added
`head` (the row plus two `count(*)`), `trace_page` and `events_of`, so a detail
response is a fixed size regardless of how long the run was.

### The workspace

Five sections — Kur, Geçmiş, Sonuçlar, Karar izi, Pozisyonlar — as an ARIA tab
list, Turkish-first, reusing the existing design language. Beginner explains
what a backtest is, why a run can be refused, and why an unknown fee is not a
zero; Pro adds the dataset digest, the configuration fingerprint, the result
digest, the simulation policy fields and the risk refusal reasons. Both show
the same financial truth.

**Nothing in the browser computes money.** An architecture test forbids
`parseFloat`, `Number(` and `.reduce((` in the backtest frontend files.

### One test of mine was passing for the wrong reason

The frontend architecture test that asserted "no backtest concept exists"
(the Part 1 boundary) still passed after Part 2A added the whole workspace —
because it banned the string `backtestRun` and the code says `BacktestRun`. A
guard that passes by an accident of capitalisation is indistinguishable from
one that works. It now bans optimisation concepts, which is the boundary that
actually still holds, and the backend boundary test moved the same way.

### Measurements

A 1,091-boundary run (1,091 trace rows, 27 positions), measured through the
composed application against real PostgreSQL, counting the SQL each request
issued:

```
endpoint              ms   SQL  bytes
run list             6.5     3    467
run detail           5.3     3  2,253
trace page           9.5     5  5,276
position page       16.8     5 15,736
event page          18.2     7  2,666
performance         32.7    11  9,865
datasets             5.2     2    628
strategies           1.8     0  1,104
capability           1.6     0    440
```

**No N+1, and no endpoint grows with the length of the run.** The detail cost
is a row and two counts whatever the run did; the trace and the ledger are
pages. The only endpoint that reads the whole population is `performance`, and
that is deliberate: Phase 10 computes complete-population aggregates, and a
partial population would produce a confidently wrong average. It is bounded by
the runner's own 200-position ceiling rather than by a page size, and that
trade — correctness over a smaller read — is the one worth making.

## 31. Part 2B — final runtime, browser and financial verification

### The internal-defect path, audited rather than assumed

Part 2A reported that an unexpected strategy defect propagated untyped and left
the run PENDING. Measured over real HTTP with an exception carrying a fake
connection string and an absolute path, the response was a bare
`500 Internal Server Error`: **nothing leaked** - no secret, no path, no
traceback, no module name - but nothing was *said* either, and the run sat
PENDING with no way for the caller to find it.

Now the runner catches the defect, logs it server-side with
`logger.exception`, terminalises the run as FAILED with the code
`INTERNAL_ERROR`, and returns a 500 whose body is a **fixed sentence plus the
run id**. The exception's own text is never used. Six tests pin this, including
one parametrised over five things that must not appear in the body.

It is deliberately not disguised: the kind is `INTERNAL`, distinct from every
refusal, and the message says *nothing about the request needs changing* - so a
programming bug never sends somebody looking for contract metadata.

### An inconsistency the browser run exposed

`GET /capability` reported availability from `get_contract_metadata` while the
runner refused on `get_product_resolver` - two sources for one fact, agreeing
only because production composes neither. The browser harness composed one and
the two answers diverged. `capability` now reads the same dependency the runner
refuses on.

### Production Docker E2E — refusal

Rebuilt images, `0006_backtest` at head, all three services healthy, through
the real nginx frontend and the real API:

```
/api/health 200 · frontend / 200 · capability 200
financial_execution_available = False   refusal_code = PRODUCT_METADATA_UNAVAILABLE
registered parameters configurable = {False}
create run over a REAL dataset -> 422 PRODUCT_METADATA_UNAVAILABLE   (twice)
runs recorded after those refusals -> 0
unknown strategy version 422 · forged status+PnL 422 · backwards interval 422
short idempotency key 422 · unknown run 404 · malformed run id 422 · limit=5000 422
?fixtures=1 and X-Test-Provider header change nothing: still unavailable
```

The dataset was created through the production replay endpoint - storing
candles is not pricing a trade - so the refusal is about metadata and not about
a missing dataset.

### Test-composed E2E — success, and separately

A single-origin harness served the **built** `frontend/dist` in front of the
real application, with a product provider composed through the dependency seam.
The application's own wiring was not edited; production still sets
`product_resolver = None`, which the run above demonstrates.

```
run 301 boundaries, 301 decisions, 7 positions, COMPLETED
database vs API: status, boundaries, decisions, positions, digest - all equal
paper_positions = 0                       (isolation)
trace: NO_SIGNAL 170, ENTERED 5, HOLDING 25, ordered ascending
fills all on the 0.25 grid; realized_gross db == api, e.g. 30.00 per position
ledger: POSITION_CREATED, ENTRY_FILLED, TARGET_FILLED, POSITION_CLOSED
Phase 10: closed 7, realized_gross AVAILABLE 210.00, realized_net UNAVAILABLE,
          fee_coverage 0/7
cross-run ledger read -> 404 · same key+config -> 200 same run
same key, different config -> 409 ATTEMPT_KEY_REUSED · abandon completed -> 409
```

`decision_time` and `entry_time` print as the same clock minute, and that is
the next-bar rule working rather than hindsight: the decision is stamped with
the boundary, which *is* the moment the next bar opens.

**The 7 wins and 0 losses are a property of the sawtooth fixture, not of the
strategy.** Nothing in the product presents them as a return.

### Real Chromium

Headless Chrome 153, driven over CDP with `websockets` - no new dependency.

```
width   1280   768   390   320
overflow  0px   0px   0px   0px      tabs 5 at every width

tablist 1 · tabpanel 1 · live region 1 · fieldset legends 4
backtest controls 9, all with explicit label[for]
the only two controls without one are the Beginner/Pro radios, which are
wrapped in <label> - a valid technique this probe did not recognise
focus outline 3px · Tab order reaches every tab · Enter activates a tab
pinned parameter chips 8 · editable parameter inputs 0
history rows 2 · status chip "Tamamlandı" · performance rendered
trace page 25 of 301, "301 karar kaydından 26–50 arası" after paging
position rows 7 · "komisyon modellenmedi" shown as text · ledger 10 entries
no fake 0% win rate · no profitability claim anywhere in the DOM
Beginner and Pro totals identical; Beginner shows no fingerprint
```

**No WCAG certification is claimed.** These are measurements, not an audit.

### Mutation sweep — 19/19

Each probe edits production code, runs a bounded suite, and restores the file
from its original bytes with a digest check.

Two probes initially reported NOT DETECTED and the investigation found **real
test gaps**, not mutation failures: the API never asserted a position's
`realized_gross` against its own ledger, and never asserted trace ordering.
Both are now asserted. A third (`O`) exposed a weak guard - the metadata test
checked that `product_resolver = None` *exists*, which appending a second
assignment leaves true; it now asserts the name is assigned exactly once.

A fourth pair silently never applied at all: multi-line anchors written with
`
` could not match files stored with CRLF, so the probes touched nothing and
would have been counted as passes. The runner now translates line endings, and
reports NOT APPLIED rather than treating it as detection - a probe that never
reached the code proves nothing.

Every production file was restored byte-identically; anchors verified afterwards.

## 32. Final approval check

### The server log was leaking, and the previous report called that a feature

Section 31 said the defect was "logged server-side with `logger.exception`" as
if that were the safe half of the design. A captured-log probe using the
application's own JSON formatter found the injected connection string, its
password, the exception message and the full traceback in the server log -
while every HTTP assertion was passing. The earlier probe's "absolute path:
False" was also not trustworthy: it matched the raw path against JSON output
that doubles every backslash.

The boundary now logs `code`, `run_id`, `strategy`, `strategy_version` and
`error_type` (the exception's *class name*), with no message, no `exc_info`,
and the chain cut with `from None`. Re-probed with JSON-escaped path matching:
HTTP body and server log both clean on all five checks. Eight permanent tests
read `caplog` directly, and mutation probe U - which restores
`logger.exception` with the message - is detected by them.

### The mutation manifest was 19 of 20

"19/19" accounted for nineteen identifiers. **P** (the result endpoint trusts
forged client PnL) had no probe. It now mutates the request schema's
`extra="forbid"` to `extra="ignore"` - the guard that turns a forged financial
field into a refusal - and the forgery tests detect it.

| ID | Target | Applied | Outcome | Restored |
| --- | --- | --- | --- | --- |
| A | strategy context gets the next candle | yes | DETECTED | yes |
| B | decision stamped with the signal candle's open | yes | DETECTED | yes |
| C | higher timeframe gated on open time, not coverage end | yes | DETECTED | yes |
| D | risk refusal branch disabled | yes | DETECTED | yes |
| E | off-grid dataset check disabled | yes | DETECTED | yes |
| F | unmodelled fee projected as net = gross | yes | DETECTED | yes |
| G | a position counted once per fill | yes | DETECTED | yes |
| H | cross-run ownership check disabled | yes | DETECTED | yes |
| I | unknown version check disabled | yes | DETECTED | yes |
| J | existing-key check disabled | yes | DETECTED | yes |
| K | publish guard against a terminal run disabled | yes | DETECTED | yes |
| L | fail guard against a completed run disabled | yes | DETECTED | yes |
| M | `results_are_final` always true | yes | DETECTED | yes |
| N | frontend multiplies a PnL string | yes | DETECTED | yes |
| O | production composes a resolver | yes | DETECTED | yes |
| P | request schema ignores forged fields | yes | DETECTED | yes |
| Q | trace ordered descending | yes | DETECTED | yes |
| R | projection doubles realized gross | yes | DETECTED | yes |
| S | ledger fold drops the last event | yes | DETECTED | yes |
| T | workspace copy claims profitability | yes | DETECTED | yes |
| U | boundary logs exception text and traceback | yes | DETECTED | yes |

21 of 21 applied and detected. None NOT_APPLIED; none counted as detected
without applying. Every target file is consistently CRLF with zero bare LF
after the sweep.

### Next-bar causality, to the timestamp

`tests/integration/test_backtest_next_bar.py` writes the timeline out with
three deliberately different prices:

```
10:05  signal candle opens at 100.00           (the hindsight price)
10:10  signal candle ends, closed at 101.00    (the intended entry)
10:10  decision_time                           (== signal candle's coverage_end)
10:10  next candle opens at 102.00             -> ENTRY_FILLED
       market_time 10:10, fill_price 102.00, reference_price 102.00
```

`decision_time` equals `entry_time` because the decision is stamped with the
boundary, and the boundary *is* the moment the next candle opens. The fill is
neither 100.00 nor 101.00. The run's ledger then equals, entry for entry
(type, market time, every data field), a direct Phase 9 `open_position` +
`apply_observation` replay from the same spec, approval, frozen product and
candles.

## 21. What is explicitly NOT done

* No HTTP route, request schema or response schema.
* No frontend file of any kind.
* No browser or end-to-end validation, and therefore **no accessibility claim**.
* No mutation-testing sweep.
* No security review.
* No API-level rate limiting or authorisation for backtest runs.
* Only one reference strategy exists. There is no strategy registry, no
  user-supplied strategy code path, no `eval`, and no dynamic import from a
  strategy name — and Part 2 must not add one.

## 22. Honest limitations

* The reference strategy is a demonstration of the *interface*, not a
  recommendation. It is an EMA crossover with an ADX filter and ATR-derived
  levels. Nothing in this repository claims it is profitable, and a backtest of
  it is evidence about the past only.
* Slippage and fees are modelled only when the caller states them. Absent cost
  data is unknown cost, never zero cost, and an unmodelled fee produces no net
  figure at all rather than a net that equals the gross.
* A bar gives four prices and no sequence. When one bar reaches both the stop
  and a target, the run takes the stop (`STOP_FIRST`) and flags the fill as
  ambiguous, naming every target also touched. `HALT` is available and decides
  nothing. Neither option is optimistic.
* Every price used in a test is a fixture. No number in this phase is presented
  as a current exchange specification.
