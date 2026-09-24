# Phase 14 — Shadow Mode

**STATUS: PHASE 14 IN PROGRESS — AWAITING HUMAN REVIEW** (Parts 1, 2A and 2B implemented and validated; sections 39-49 are Part 2B)

Part 1 is the deterministic shadow observation engine and its decision journal.
Part 2A (sections 29-38) adds forward outcome observation, the REST API and the
research screen. Both are implemented and validated, and both are
**uncommitted and unpushed** and have not been through CI. Part 2B (real
Chromium end-to-end tests, the mutation sweep and the final security review) is
not started. Phase 15 is not started. Phase 14 is **not closed**.

Baseline: HEAD `3d5a71d` "Complete Phase 13 live market architecture", `main`
tracking `origin/main`, tree clean apart from this work. Backend before this
phase: 4,157 passing, 0 skipped, 35 import contracts, migration head
`0006_backtest`. Frontend: 448 passing, unchanged — Part 1 touches no frontend
file.

---

## 1. What Shadow Mode is, and what it is not

Master spec section 78: the system analyses market data and **"opens no paper or
real position automatically"** — it records the signal, the entry candidate, the
stop, the targets, the setup quality, the market regime and the later outcome.
The Phase 14 entry in the implementation plan says: run live analysis without
executing trades, collect real-time signal outcomes, create evaluation tools.

Part 1 delivers the first two thirds of that: **observation and the journal**.
"The later outcome" and the evaluation tools are Part 2, for the reason given in
section 3.

Shadow Mode is not paper trading:

| | Paper trading (Phase 9) | Shadow (Phase 14) |
| --- | --- | --- |
| What it holds | A position with a ledger | A record of what was decided |
| Fill model | Yes (`paper-sim/v1`) | **None** |
| P&L | Yes, from real fills | **None, and no field for one** |
| Created by | A person, explicitly | Nothing is created |
| Risk approval | Required before creation | Recorded as a verdict |

An `ENTRY_INTENT` in the journal is an intent. It is not a fill, not a trade and
not a result. A `REFUSED` entry is a refusal, not a losing trade. A `WAIT` is
not an unsuccessful trade.

## 2. No substantive conflict with the master specification

The task was checked against the spec before implementation. No conflict
requiring a stop was found. Two scope facts are recorded here so they are not
mistaken for omissions:

1. **The only stream is a simulation.** Phase 13 ships exactly one provider,
   `SIMULATED_HISTORICAL_STREAM`, replaying stored history; real exchange feeds
   are Phase 15. Shadow observes that stream and relabels nothing.
2. **"The later outcome" needs something Part 1 does not have.** Tracking what
   happened after a signal requires either verified product metadata (absent by
   design in this deployment) or an outcome-tracking pass over subsequent
   confirmed candles. Rather than fabricate a result, Part 1 records the
   decision and its evidence, and Part 2 adds outcome tracking and the
   evaluation tools the plan asks for.

## 3. Why the journal is persisted, not ephemeral

Section 78 asks for the later outcome of a recorded signal; the Phase 14 plan
asks for collected real-time signal outcomes and evaluation tools. All three are
questions about history, and a question about history cannot be answered by
state that dies with the process. So the journal is a real table, with a new
migration, and a test proves a run and its entries read back unchanged through a
fresh database connection.

Phase 13's live market state remains ephemeral. What is durable here is the
record of what was decided — not the market picture.

## 4. Files

| Path | What it holds |
| --- | --- |
| `app/domain/shadow/run.py` | Run status, end reasons, `ShadowLimits`, `ShadowError` |
| `app/domain/shadow/decision.py` | Outcome and financial vocabulary, evidence, entry sketch, `ShadowDecision` with its validation |
| `app/domain/shadow/eligibility.py` | Whether a boundary may be evaluated at all, and why not |
| `app/domain/shadow/identity.py` | Configuration fingerprint, input fingerprint, decision key |
| `app/application/shadow/ports.py` | `ShadowJournalStore`, `StoredShadowRun` |
| `app/application/shadow/service.py` | `ShadowRunner`: capture, eligibility, evaluation, the financial step, the journal |
| `app/application/strategy/context.py` | The shared policy-input builder, moved out of the backtest runner |
| `app/adapters/persistence/shadow_models.py` | The two tables |
| `app/adapters/persistence/shadow_store.py` | `SqlAlchemyShadowStore`, idempotent append |
| `alembic/versions/0007_shadow_mode.py` | The migration, its constraints and the append-only trigger |

Tests: `tests/unit/shadow/` (46) and `tests/integration/test_shadow_persistence.py`
(10, against real PostgreSQL).

## 5. It computes nothing of its own

Every number in a journal entry came from an engine that already existed.

| Fact recorded | Where it was computed |
| --- | --- |
| Confirmed candles, availability, continuity | Phase 13 `LiveSession` |
| Indicator readings | Phase 1, through the shared builder |
| The decision | A registered Phase 12 `StrategyPolicy` |
| Approved quantity, risk verdict | Phase 3 `size_for_product` |
| Regime, suitability, setup quality | Phase 8 `run_analysis` |

No `LiveEMA`, no `ShadowRSI`, no `ShadowATR`, no `ShadowCrossover`, no
`ShadowMTFScore`. No indicator is recalculated anywhere under `app/*/shadow/`.
To guarantee one code path, the four private helpers the Phase 12 backtest
runner used to prepare policy inputs (`readings_series`, `readings_at`,
`value_at`, `confirmed_higher`) moved verbatim to `app/application/strategy/`
and are now imported by both. The 199 backtest tests still pass unchanged.

## 6. Causality: evidence is frozen at the boundary

The observer callback runs inside the stream. It captures, synchronously: the
boundary, the connection state, the provenance, the market currency, the
per-timeframe availability and integrity, and the **confirmed prefix** of every
book — only candles whose coverage ended at or before that boundary. Evaluation
then runs off that capture.

The consequence is the property the phase needs: a consumer that falls behind
decides exactly what it would have decided on time. Lag changes *when* an entry
is written, never *what it says*.

This was found as a real defect by its own test. The first implementation read
`session.snapshot()` at consumption time, which let a later disconnect mark an
earlier boundary `UNAVAILABLE`, and let a candle that arrived later reach back
into a prefix that had already closed. Both are now impossible by construction.

## 7. Five clocks, never collapsed

- **Market event time** — when the market says the candle happened.
- **Candle coverage end** — where confirmed evidence stops. Decisions are
  attributed here, and analysis runs `as of` here.
- **Receive time** — the injected clock, when the event arrived. Freshness is
  measured from this and nothing else.
- **Shadow decision time** — when the policy was asked.
- **Audit/persistence time** — `recorded_at`, when the entry was written.

A decision's `market_boundary` is market time. Replaying March history in
September is honest because of this separation: the data just arrived, and it is
still March data.

## 8. When an evaluation happens — and when it does not

Exactly once per genuinely new confirmed driver boundary.

Produces no evaluation: a duplicate record, a forming update, a heartbeat, a
reconnect with no new evidence, a replayed notification, an additional SSE
subscriber, a browser tab. There is no worker per subscriber and no automatic
Claude call per candle — analysis runs only beside a proposed entry, and only
when the run asked for analysis evidence.

A late candle behind the head creates no boundary. It is recorded as an
operational fact (`LATE_FILL`), as is a duplicate that disagrees with a candle
already held (`CONFLICTING_CORRECTION`).

## 9. The availability gate

A boundary is evaluated only when the capture shows the stream connected **and**
fresh **and** the required timeframes available with usable integrity, and the
warm-up bar count met. `CONNECTED` alone is not sufficient; `FRESH` alone is not
sufficient. A temporal gap, a sequence mismatch, an unresolved conflict or a
stale required timeframe each block evaluation, and the journal records
`UNAVAILABLE` with the reasons rather than silence. The policy is never called.

**An empty journal is never filled by manufacturing a decision**, and "no
signal" is kept strictly apart from "could not see".

## 10. Decision vocabulary

`NO_SIGNAL` · `WAIT` · `ENTRY_INTENT` · `EXIT_INTENT` · `UNAVAILABLE` ·
`REFUSED`, with operational entries kept in a separate kind (`RUN_OPENED`,
`RUN_ENDED`, `PROVIDER_DISCONNECTED`, `PROVIDER_RECOVERING`, `DATA_GAP`,
`CONFLICTING_CORRECTION`, `LATE_FILL`, `OBSERVATION_REFUSED`,
`DECISION_SUPERSEDED`, `EVALUATION_FAILED`).

`ShadowDecision.__post_init__` enforces four rules: a decision states an
outcome; an operational entry states what happened; only a proposed entry
carries levels; an approval belongs to a proposed entry.

## 11. Evidence recorded with every decision

Strategy id and version · configuration fingerprint · market boundary ·
included timeframes · excluded timeframes with per-timeframe reasons ·
availability, freshness, integrity and confirmed counts · bars available ·
indicator readings as text · provenance and market currency · connection state ·
and, beside a proposed entry, the Phase 8 regime, suitability and setup quality.
The input fingerprint is stored on the entry.

No AI-written text is stored as a number. There is no LLM in this path at all.

## 12. Two trust levels, never blurred

Observing a signal needs only confirmed candles. Calling a decision
*financially executable* needs verified product facts **and** an independent
risk approval. This deployment has no verified futures metadata provider, so the
financial half of a decision is an explicit state rather than a silence:

- `NOT_APPLICABLE` — the decision proposes no entry.
- `NOT_CONFIGURED` — the run was opened without an account and risk policy.
- `METADATA_UNAVAILABLE` — no verified contract provider, or it could not answer.
- `APPROVED` / `REFUSED` / `UNDETERMINED` — the risk engine's own verdict.

A missing fact is never an approval. An entry recorded with
`METADATA_UNAVAILABLE` carries **no approved quantity, no risk amount, no margin
and no executable plan**.

## 13. The risk veto is independent and final

When risk refuses, `REFUSED` replaces the outcome outright and no approved
quantity is written: an intent cannot talk past the risk engine. There is no
path that lowers a quantity or widens a stop to obtain an approval, no
symbol-based inference of a contract, no fabricated multiplier or tick size, and
the instrument label alone establishes nothing — a test asserts this.

## 14. Nothing is created and nothing is executed

No broker order, no Midas call, no `PaperPosition`, no `ReplayPosition`, no
`BacktestRun`. There is no fill model in the shadow package, so a recorded entry
has no result, and the journal has no column for a fill, a P&L or a return. An
integration test counts rows in `paper_positions`, `paper_position_events`,
`backtest_runs`, `backtest_positions` and `replay_sessions` after a run that
recorded entry intents: all zero.

## 15. Run identity and the decision key

A `ShadowRun` freezes its configuration at open: source identity, instrument
label, provenance, strategy id and version, strategy parameters, driver
timeframe, subscribed timeframes, required timeframes, risk policy and account.
The fingerprint (`SC-…`) is a digest over that canonical form.

A decision key is a digest of the run id, the configuration fingerprint, the
boundary and a fingerprint of the exact policy inputs (bar OHLCV, current and
previous readings, higher-timeframe readings, bars available). **No random UUID
protects against a duplicate semantic decision** — appending the same
observation twice is a no-op at the database level, through
`ON CONFLICT DO NOTHING` on `(run_id, decision_key)`.

A browser tab id is not part of any identity and carries no authority.

## 16. Corrections, gaps and superseding

A published entry is never edited. A trigger on `shadow_journal` refuses
`UPDATE` and `DELETE` at the storage level, so a decision the rules did make
cannot be turned into one they did not.

Invalidation is therefore additive. A conflicting correction is recorded as
`CONFLICTING_CORRECTION`, and — because every decision from that boundary
onward read the contested candle in its confirmed prefix — a second entry,
`DECISION_SUPERSEDED`, names the earliest affected boundary and how many
published decisions rest on contested evidence. The decisions themselves stand
exactly as published. A correction does not make it untrue that the rules
decided what they decided; what a reader needs is to know the ground moved, and
that is a new fact, not an edit.

This was tightened during validation: `DECISION_SUPERSEDED` existed in the
vocabulary but nothing emitted it, which would have been a placeholder reported
as a capability. It is now emitted and tested.

## 17. Restart and recovery

An SSE reconnect, a browser tab reopen and a provider reconnect are all
distinguished from a backend restart. A reconnect with no new evidence produces
no decision. A run is not resumed across an unverified data gap, and no missed
decision is ever invented: the gap itself is what gets recorded.

## 18. Bounds, and honesty when one is reached

`ShadowLimits`: 4 concurrent runs · 5,000 observations per run · 512 pending
records · 100 journal entries per page · 8 excluded reasons · 300 characters per
reason · 8 evidence timeframes.

A run that reaches its observation bound ends with `OBSERVATION_LIMIT` and is
never described as `COMPLETE`. A consumer that cannot keep up (512 records
behind) ends the run with a stated reason rather than writing a journal with a
hole in it — an incomplete record that admits it is better than a complete-
looking one that lies. Reason text is truncated to the bound before storage.

## 19. Determinism and performance

Measured on the development machine (Windows 11, Python 3.14.3, PostgreSQL in
Docker), 250 observations per run:

| Measurement | Result |
| --- | --- |
| Per confirmed boundary (capture, eligibility, policy, journal) | **1.64 ms** median |
| The same run with an identical duplicate after every candle | 422 ms vs 411 ms; **no extra entries** |
| Peak memory, 250 observations | 0.96 MiB peak, 0.73 MiB retained |
| Append 100 entries (real PostgreSQL) | 42 ms |
| Append one entry (real PostgreSQL) | 5.5 ms median |
| Re-append the same 100 entries | 41 ms, **0 written** |
| Read a 100-entry page | 13 ms |

Determinism: three runs over identical observations with the same frozen
configuration produced identical sequences of kind, sequence, decision key,
outcome, boundary and input fingerprint, and one configuration fingerprint.
Only the wall-clock audit fields differ. There is no `random`, no `datetime.now()`
and no `utcnow()` anywhere in the shadow packages.

## 20. Failure handling

A policy that raises ends the run with `EVALUATION_ERROR` and a partial record
that says so. The exception message is never stored, returned or logged as text:
the log carries the run id and the exception *type* only. A test raises a policy
error containing a Windows path and an API-key-shaped string and asserts neither
appears in the journal, the run record or the captured logs. Recoverable
operational facts (a disconnect, a gap, a late fill) are recorded and observed
through; terminal ones end the run with a reason.

## 21. Product metadata trust

Streaming candles establish no product fact. There is no `TEST_FIXTURE`
provider in this path, no hardcoded contract table, no request-controlled
metadata authority and no symbol-based inference. Without a configured verified
resolver the answer is `METADATA_UNAVAILABLE`, which is this deployment's
default and is recorded as such.

## 22. No profitability claim anywhere

No leaderboard, no best-strategy selector, no parameter optimization, no grid
search, no walk-forward, no guaranteed return, no AI profit prediction, and no
financial performance reporting — because no simulation ran. The Phase 12 ban on
optimisation vocabulary continues to pass, and it now also reads the shadow
packages.

## 23. Tests

56 new tests: 46 unit, 10 integration against real PostgreSQL. Zero skipped.

| Case | Covered by |
| --- | --- |
| A confirmed candle → exactly one evaluation | `test_each_confirmed_boundary_is_evaluated_exactly_once` |
| B duplicate → none | `test_an_identical_duplicate_produces_no_second_evaluation` |
| C forming → none | `test_a_forming_candle_produces_no_evaluation` |
| D heartbeat → none | `test_the_observer_has_no_heartbeat_to_react_to` (a heartbeat is an SSE concern and is not in `RecordKind` at all), `test_repeated_stream_signals_produce_no_decision` |
| E reconnect without new evidence → none | same |
| F no future candle leak | `test_a_decision_sees_only_candles_closed_at_its_boundary`, `test_a_late_arriving_candle_cannot_reach_an_earlier_decision` |
| G temporal gap blocks | `test_a_temporal_gap_blocks_evaluation_and_says_why` |
| H conflicting correction not silently accepted | `test_a_conflicting_correction_is_recorded_and_changes_nothing`, `test_a_correction_names_every_decision_that_read_the_candle` |
| I stale cannot claim current | `test_a_stale_required_timeframe_blocks_the_decision` |
| J unknown strategy version refused | `test_rules_this_build_does_not_have_are_refused` |
| K missing metadata ≠ approval | `test_without_a_resolver_an_entry_is_recorded_as_metadata_unavailable`, `test_the_instrument_label_alone_establishes_no_contract` |
| L risk refusal cannot be overridden | `test_a_risk_refusal_is_the_outcome_and_approves_nothing` |
| M no paper position | `test_an_approved_entry_still_creates_no_position_or_run`, `test_shadow_creates_no_position_and_no_backtest_run` |
| N no broker order | `test_no_shadow_module_can_create_or_execute_anything` |
| O no backtest run | `test_shadow_creates_no_position_and_no_backtest_run` |
| P no duplicate journal record after retry | `test_writing_the_same_observation_twice_records_it_once` |
| Q deterministic on identical observations | `test_identical_observations_produce_identical_decisions` |
| R run isolation | `test_two_runs_over_one_stream_keep_separate_journals`, `test_two_runs_never_share_a_journal` |
| S run bounds | `test_the_run_stops_at_its_observation_bound_and_says_so`, `test_the_number_of_concurrent_runs_is_bounded`, `test_a_reason_is_stored_bounded` |
| T interruption / failure semantics | `test_a_failing_strategy_ends_the_run_and_leaks_nothing`, `test_a_partial_run_is_never_described_as_complete` |
| U historical mock provenance preserved | `test_the_run_and_every_decision_keep_the_simulated_provenance` |
| V no fabricated financial result | `test_an_entry_intent_carries_no_result_and_no_approval`, `test_the_journal_has_no_field_for_a_fill_or_a_profit` |
| W Phase 9–13 preserved | The full suite: 4,212 passing, 0 skipped |

Persistence additionally proves: restart/read consistency through a fresh
`Database`, the append-only trigger refusing both `UPDATE` and `DELETE`, the
`ended_runs_state_why` constraint, per-run key scoping, run-id reuse refusal,
ordered pagination, and exact `Decimal` round-tripping.

## 24. Architecture contracts

39 kept, 0 broken (35 before). Four added:

1. *The shadow domain observes; it knows no transport, storage, money or
   positions* — no FastAPI, SQLAlchemy, broker or frontend transport.
2. *Nothing beneath shadow depends on it* — no Phase 1–13 module may import the
   shadow packages, so an engine cannot behave differently under observation.
3. *Shadow orchestration observes and records; it cannot trade or fabricate a
   result* — forbids `app.domain.paper`, the P&L and what-if modules, and the
   individual indicator routines.
4. *The shared strategy context builder prepares inputs and computes nothing.*

No earlier contract's meaning was altered or weakened.

## 25. Prior-phase tests that needed updating, and why

Five, all guards that now see the new tables — none weakened:

- Three schema-drift tests (`paper`, `journal`, `replay`) register the model
  modules by import; `shadow_models` was added to each, exactly as each earlier
  phase did. This exposed a real defect: migration `0007` created three check
  constraints the models did not declare, so the drift check was correct to
  fail. The constraints are now declared on the models too, and the drift check
  is silent because the two genuinely agree.
- `test_no_future_phase_table_exists` gained `shadow_runs` and `shadow_journal`
  with a comment stating what they hold and, importantly, what they do not.
- `test_no_execution_capability_exists_anywhere_in_the_app` listed `shadow_mode`
  while Phase 14 was a future phase. That marker moved with the approved phase
  boundary, following this file's own precedent, and the comment records why.
  What it was guarding — that nothing in the application can place an order — is
  guarded by the remaining entries, which read every module under `app/`,
  including the shadow package.

## 26. Validation

All gates run from `backend/`, PostgreSQL from `docker compose`.

| Gate | Result |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 466 files already formatted |
| `mypy --platform linux` | No issues, 457 source files |
| `mypy --platform win32` | No issues, 457 source files |
| `lint-imports` | **39 kept, 0 broken** |
| `pytest` (real PostgreSQL) | **4,212 passed, 0 skipped** |
| Targeted Phase 8–13 regressions | 1,740 passed |
| `alembic downgrade 0006_backtest` then `upgrade head` | Clean both ways; head `0007_shadow_mode` |

Frontend: not run and not needed — Part 1 changes no frontend file. The previous
baseline of 448 passing stands unchanged.

## 27. Deliberately not built in Part 1

No real exchange provider. No broker order path and no Midas. No automatic paper
position. No invented futures metadata. No Shadow API and no Shadow screen. No
optimisation, grid search or walk-forward. No profitability claim. No AWS or
Azure. No outcome tracking — that is Part 2.

## 28. What Part 2 must add

Outcome tracking over subsequent confirmed candles (what happened after a
recorded signal, stated as market facts rather than as P&L), the evaluation
tools the plan asks for, and the API and screen. It must keep every property
above: no second engine, no fabricated result, no financial claim without
verified inputs.

---

# Part 2A — Outcome tracking, API and research workspace

Part 2A was interrupted once by a usage limit and resumed from the filesystem.
The recovery found one break (an import used in `main.py` but never added) and
one prior-phase guard that correctly saw the two new tables; both were fixed.
No placeholder, skip, debug output or merge marker was found.

## 29. Test-count reconciliation for Part 1

Section 26 reported 4,157 before the phase and 4,212 after, beside 56 new tests.
That arithmetic does not reconcile, and section 25 explained why without saying
so in numbers: exactly one collected test was removed — the `shadow_mode`
parameter of `test_no_execution_capability_exists_anywhere_in_the_app`, a
Phase 12 "not started yet" marker that Phase 14 made obsolete. So
**4,157 − 1 + 56 = 4,212**. Verified by diffing the modified test files: that is
the only removal. Its intent (nothing in the app can place an order) is still
checked by the remaining nine entries over every module, and by eleven
execution and position bans that scan the shadow packages specifically. No
coverage was lost, and no test was changed to make the numbers fit.

Part 2A: **4,212 + 107 = 4,319** collected and passed, 0 skipped. Part 1's 56
shadow tests are unchanged.

## 30. What "later outcome" means here

Spec section 78: record "later outcome" to "evaluate system behavior under live
conditions". It asks for **observed market development** — not a hypothetical
fill (which needs an execution model) and not P&L (which needs a verified
multiplier and tick value this deployment does not have). No conflict with the
spec; nothing was routed through the Phase 9 financial engine because nothing
asks for it. The full rules are in `architecture.md`.

| State | Meaning |
| --- | --- |
| `NOT_EVALUATED` | The decision proposed no levels |
| `PENDING` | The observation window is still open |
| `OBSERVED` | A level was touched, or the window closed with none |
| `UNAVAILABLE` | A gap, or the data ended; always with a reason |
| `INVALIDATED` | A correction contested evidence the decision read |

Events: `STOP_LEVEL_TOUCHED`, `TARGET_LEVEL_TOUCHED`,
`BOTH_LEVELS_TOUCHED_SAME_BAR` (ambiguous, never ordered), `NONE_REACHED`,
`NOT_OBSERVED`. No win, loss, fill or profit word exists at any layer.

## 31. Files added or changed in Part 2A

| Path | What it holds |
| --- | --- |
| `app/domain/shadow/outcome.py` | Forward-only development engine and `ShadowOutcomeRecord` |
| `app/domain/shadow/identity.py` | `outcome_key`; the configuration now includes the evidence rules |
| `app/domain/shadow/run.py` | Outcome bounds; `EndReason.INTERRUPTED` |
| `app/application/shadow/service.py` | Follow-ups, invalidation, `begin`, `follow`, `configuration_for` |
| `app/application/shadow/workspace.py` | `ShadowWorkspace` lifecycle; `recover_interrupted_runs` |
| `app/application/shadow/ports.py` | Outcome, attempt and reconciliation operations; `AttemptKeyHeldError` |
| `app/application/live/workspace.py` | `attach_reader`, `detach_reader`, `session_object`, `session_ended` |
| `app/adapters/persistence/shadow_*.py` | Two new tables; atomic run-and-key creation |
| `alembic/versions/0008_shadow_outcomes.py` | Forward migration (0007 was already applied) |
| `app/api/routes/shadow.py`, `app/api/schemas/shadow*.py` | Seven routes, strict schemas, a rename-only projection |
| `app/main.py` | Composition, restart reconciliation, shutdown order |
| `frontend/src/api/shadow.ts`, `domain/shadow.ts`, `screens/Shadow.tsx`, `components/Shadow.css` | The research workspace |
| `frontend/src/App.tsx`, `screens/Dashboard.tsx` | One screen and one button |

## 32. Two Part 1 behaviours changed, deliberately

* **The configuration fingerprint now includes the evidence rules.** A run with
  analysis evidence and one without record different things, so they are not
  one configuration. Found when the attempt-key conflict test passed for the
  wrong reason.
* **Creation writes the run before it answers**, in one transaction with its
  attempt key. Previously the run row was written inside the observing task.

## 33. Defects found during Part 2A

1. The attempt key was claimed before the run row existed, and the foreign key
   rejected it. The in-memory journal could not see this; the PostgreSQL test
   did. Fixed by claiming and creating in one transaction.
2. A retry at capacity was refused `SHADOW_CAPACITY` by the load it had itself
   created. Found by measurement; the key is now consulted first. Pinned by a
   regression test.
3. A clean shutdown labelled runs `CANCELLED`. It now says `SHUTDOWN`.
4. Finished runs stayed in the workspace's map. They are now released.
5. A run left `OBSERVING` by a crashed process stayed apparently active. It is
   now closed `INTERRUPTED` at the next start, from its own journal.

## 34. Tests added in Part 2A (107 backend, 33 frontend)

| File | Tests | What it pins |
| --- | --- | --- |
| `unit/shadow/test_outcome.py` | 23 | Forward-only causality, touch not fill, ambiguity, gaps, windows, vocabulary |
| `unit/shadow/test_outcome_tracking.py` | 12 | Which decisions are followed, no lookahead via the runner, idempotence, bounds, invalidation |
| `unit/shadow/test_workspace.py` | 19 | One stream many readers, no retrospective signals, idempotency, retry at capacity, subscriber is not a run, cancel, bounds |
| `integration/test_shadow_api.py` | 42 | Capability, production disabled, Host/Origin, 17 forged fields, strategy-as-path, workflow to completion, no position or backtest rows, isolation, pagination, cancel, restart |
| `integration/test_shadow_outcomes_persistence.py` | 11 | Round trip, write-once, append-only trigger, forward-only and reason constraints, newest-answer query, eight-way concurrent creation, restart reconciliation |

Frontend, `components/shadow.test.tsx` (33): provenance, financial
unavailability, no P&L text, history, decision detail, evidence, pending,
observed, unavailable, invalidated and ambiguous developments, superseded
decisions, empty journal, interrupted and partial runs, Beginner/Pro parity,
out-of-order run switching, page merging and bounds, the create body and its
retry key, labels, keyboard tabs, announcements, cancel, and source checks for
financial arithmetic.

## 35. Measurements

Development machine, PostgreSQL in Docker, through the composed application.

| Measurement | Result |
| --- | --- |
| Run creation (POST) | 27 ms cold; 30 ms median for four concurrent observers |
| Idempotent retry | 59 ms; answers the original run |
| Fifth run beyond capacity | 409 `SHADOW_CAPACITY` |
| Run detail | 6 ms |
| Journal page of 100, with evidence and developments | 25 ms, 140 KiB |
| Outcome page of 100 | 12 ms |
| Run list of 25 | 7 ms |
| SQL statements per journal page | **4, at page size 10 and at 100** — no N+1 |
| SQL statements per outcome page | 3 |
| Cancellation | 58 ms median, 80 ms max |
| After cancelling all four | 0 runs held in memory, 0 runner slots |

A 288-candle run produced 287 observations and 289 journal entries.

## 36. Validation

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | Passed / 477 files formatted |
| `mypy --platform linux` / `win32` | No issues, 467 source files each |
| `lint-imports` | **40 kept, 0 broken** (one added: the shadow API cannot trade, value or narrate) |
| `pytest`, real PostgreSQL | **4,319 passed, 0 skipped** |
| `npm ci --dry-run` | Passed |
| `npx vitest run` | **481 passed** (448 + 33) |
| `npm run typecheck` / `lint` / `npx prettier --check .` | Passed |
| `npm run build` | Built, with the existing chunk-size warning |
| Docker | Rebuilt; `alembic upgrade head` ran 0007 and 0008; all three services healthy on loopback; Shadow answers `SHADOW_DISABLED` without the opt-in |

## 37. Known limitations

* The observation window is a fixed 24 driver candles per decision.
* Only `ENTRY_INTENT` is followed; a refused signal's levels are not.
* The screen refreshes on request; it does not poll an observing run.
* One strategy is registered (`ema-crossover-atr` 1.0.0), as before.
* The Starlette test client cannot close an infinite SSE response, so "a closing
  subscriber does not end a run" is tested at the workspace level against the
  real `subscribe()` and `close()`, not over HTTP.
* The layout was designed for 1280/768/390/320 but not measured in a browser.

## 38. Part 2B, not started

Real-Chromium end-to-end tests of the Shadow workflow, responsive and
accessibility measurement in a browser, the mutation sweep, and the final
security review of the new routes.

---

# Part 2B — Final runtime, browser, outcome integrity and security validation

Part 2B validated Parts 1 and 2A against the composed system: the Docker
backend, real nginx, real PostgreSQL, real HTTP, stored replay datasets played
through the live workspace, and the built frontend in headless Chrome 153 over
CDP. It fixed what that exposed. Where this part contradicts sections 29-38,
this part is current.

## 39. Test-count reconciliation, verified

Re-derived from collection rather than reports: Phase 13 closed at 4,157; Part 1
added 56 and removed one parameter (`shadow_mode` in the Phase 12 execution
guard, a "not started yet" marker), so **4,157 − 1 + 56 = 4,212**; Part 2A added
107 (4,319); Part 2B added 54 (**4,373**). Nothing else was removed or renamed.
Coverage of the removed parameter's intent is kept by nine execution bans over
every module and eleven over the shadow packages.

## 40. Defects found and fixed

| # | Found by | Defect | Fix |
| --- | --- | --- | --- |
| 1 | Causality audit | Outcome eligibility was "coverage ends after `T`": a candle opening before `T` and closing after it counted in full, pre-decision range included. | Eligible means **opened at or after `T`**. |
| 2 | Causality audit | Continuity was checked only between later candles, so a missing interval right after `T` read as continuous. | The gap check starts at `T`. |
| 3 | Correction audit | Corrections of a candle a *development* read (the touch, or any candle in its window) were ignored; the touch stood as observed. | Open and published follow-ups that read it get an appended `INVALIDATED` record naming the candle. |
| 4 | Correction audit | Corrections of any non-driver timeframe were ignored, though the policy reads higher-timeframe readings. | Every subscribed timeframe is followed; decisions that read it are superseded. |
| 5 | Review | `shutdown` did not take the creation lock: a creation in flight could start an observer after shutdown returned (shutdown itself then raised). | Shutdown closes, then takes the lock; creation re-checks under it. |
| 6 | Adversarial E2E | `after=99999999999999999999` reached PostgreSQL and came back as `503 SHADOW_STORE_UNAVAILABLE` - a false outage, and a 5xx. | Cursors are bounded at the 32-bit sequence limit; a larger one is a 422. |
| 7 | Browser journey L | Run history could not page past its newest 25 runs. | Paged, bounded at 200 held, and says when it stops. |
| 8 | Browser journey M | Runs of the same rules were indistinguishable in history. | Pro shows the run id on each history row. |
| 9 | Docker gate | `sqlalchemy>=2.0` let a fresh image resolve SQLAlchemy 2.1, which no longer installs `greenlet`: the backend could not start. | `sqlalchemy[asyncio]>=2.0,<2.1` - the extra the code uses, on the tested line. |
| 10 | Mutation probe B | No test delivered a higher-timeframe candle before the driver candles it spans, so the prefix filter could be deleted unnoticed. | Test added; probe B now detected. |
| 11 | Bounds review | The 512 pending-record backlog bound had no test. | Test added: the run ends `PENDING_OVERFLOW`, never `COMPLETE`. |

## 41. Correction dependencies, as now implemented

| Case | Result |
| --- | --- |
| A candle the decision read (driver or subscribed higher timeframe) | Decisions from that boundary superseded; their follow-ups invalidated |
| A later candle that established a target touch | The published touch stands; an `INVALIDATED` record is appended |
| A later candle that established a stop touch | The same |
| A candle inside an open window (continuity) | The open follow-up is invalidated |
| A candle after a development's last observed candle | That development is untouched |
| A timeframe the run never subscribed to | Not recorded by this run |

All append-only; every invalidation names the dependency and the candle.

## 42. Runtime end to end (62/62 checks)

Stored datasets, disclosed: `dataset(288)` (contiguous 5M/15M/1H), the same 5M
rows with three candles removed, and `five_minute(600)` - the repository's own
factories, untuned. Through nginx:

* Each played to its end, `COMPLETE`, `SIMULATED_HISTORICAL_STREAM` /
  `HISTORICAL` on the run and every decision; one decision per boundary.
* Contiguous: 288 decisions - 5 entry intents, 255 no-signal, 8 wait, 20
  unavailable (warm-up). Every intent's development was `STOP_LEVEL_TOUCHED`.
  Long: 600 decisions, 5 intents, the same development. These are what the
  factory prices produce; nothing was tuned toward a touched target.
* Gapped: 205 decisions after the gap recorded `UNAVAILABLE` (the book stays
  `GAPPED`); no development claims a window across it.
* Every development's first candle opens exactly at its decision boundary.
* No intent approved; every intent `NOT_CONFIGURED` or `METADATA_UNAVAILABLE`.
* Database decisions, developments and run rows equal the API's, in order.
* `UPDATE` and `DELETE` on the journal, and `UPDATE` on outcomes, refused by
  the database triggers.
* A backend restart leaves every journal and outcome list byte-identical.
* A `docker compose kill` mid-run: at the next start the run is `INTERRUPTED`,
  its 32 decisions unchanged, one `RUN_ENDED` appended, nothing invented, its
  live session gone.
* 8 concurrent identical POSTs: one run, one attempt row. Same key, other
  settings: typed 409. Six different keys racing at capacity: exactly the free
  slots filled, the rest 409 `SHADOW_CAPACITY`. A retry at capacity answers the
  original run.
* No cross-run leakage through journal, outcomes or cursors.
* Cancel twice is idempotent; cancelling a complete run leaves it complete.
* No paper position, ledger event or backtest run; uploaded datasets
  byte-identical afterwards.

## 43. API security through nginx (79/79)

Seventeen forged fields (decision, outcome, development, approval, quantity,
risk verdict, contract metadata, product, currency, provenance, status,
completeness, P&L, fill, journal entry, candles, strategy code) → 422.
Unsupported version, module-path strategy, unknown and malformed sessions, half
risk configurations, invalid numbers, unsubscribed driver, wrong types, bad
JSON and a 3 MB body → typed 4xx. Traversal, SQL, unicode, overlong and
uppercase run ids on every route → 404/422. Invalid pagination → 422; a cursor
past the end → an empty page naming its run. PUT, PATCH and DELETE on runs and
journals, and POST to journal or outcomes → 405. Foreign Origin and rebinding
Host → 403. **No 5xx.** No path, secret, traceback or driver message in any
response, nor in the backend log for the run.

## 44. Deployment fail-closed

Composed exactly where the live workspace is (31 tests over the environment
matrix): `APP_ENV` unset + opt-in → composed (the documented default is
development); development or test without the opt-in, production with or
without it → not composed, every route `SHADOW_DISABLED`. Empty, misspelled,
padded or differently cased `APP_ENV`, and an opt-in of `""`, `maybe`, `2` or
`yes please`, refuse to start. Docker publishes 127.0.0.1 and [::1] only. The
stack is left in its default configuration, where Shadow is off. None of this
is authentication.

## 45. Real Chromium (32/32 journey checks, 8/8 viewport sets)

Chrome 153 headless, the built frontend through nginx, real backend data:

* **A** opening Shadow creates no run. **B/C** an explicit live session over a
  stored dataset, and a run created through the form. **D/18** Refresh shows
  newly persisted entries (1 → 50 rows); nothing appears without it. **N** the
  UI cancel ends the run `CANCELLED` and disables the button.
* **19** run id, strategy version, provenance and currency equal the API;
  journal order and decision outcomes in the DOM equal the API's (5/5 intents,
  20/20 unavailable). **L** the journal pages to the 500-entry bound and says
  "602 kaydın ilk 500 tanesi gösteriliyor".
* **F** evidence states the decision's own financial state and "Onaylanmadı".
  **J** a run given an account and risk policy: sizing asked for, refused for
  want of verified metadata, nothing approved, regime evidence shown.
* **G/H** developments listed as touches, never results; unavailable ones say
  why. **I** the disclosed correction fixture (the real runner, a real live
  session and the real store, over a scripted stream, because replay never
  produces a correction): superseded decisions flagged and shown as published;
  the touch and its later invalidation both listed.
* **K** Beginner and Pro state the same financial truth; Pro adds identifiers.
  **M** under 1.5 s of network latency the run chosen last stays on screen.
  **O/P** history read back after a reload and after a backend restart.
  **Q** no paper position, ledger event or backtest run.
* Keyboard: arrows, End and Home move selection and focus across the five tabs;
  focus outline solid 2 px.
* 1280, 768, 390 and 320 px, Beginner and Pro, the list screen and all five
  tabs: overflow 0, nothing clipped, 0 unreadable timestamps, 0 unnamed buttons,
  0 unlabelled inputs, 0 buttons under 24 px, polite status region present.

No WCAG certification is claimed.

## 46. Mutation sweep A-AD

Each probe edited production code (anchors translated to the file's line
endings), ran the targeted tests and restored the bytes, verified by SHA-256;
the working tree was unchanged afterwards.

| Probe | Mutation | Result |
| --- | --- | --- |
| A | forming candle decides | DETECTED |
| B | future candle in an earlier prefix | DETECTED (after the new test; first SURVIVED) |
| C | duplicate evaluated twice | DETECTED |
| D | heartbeat evaluates | INAPPLICABLE - no heartbeat `StreamRecord` exists; the transport is outside what Shadow may import |
| E | reconnect re-evaluates | DETECTED |
| F | gap treated as continuous | DETECTED |
| G | stale treated as current | DETECTED |
| H | currency becomes current | DETECTED |
| I | missing metadata approved | DETECTED |
| J | risk refusal overridden | DETECTED |
| K | shadow imports paper trading | DETECTED by the import contract |
| L | order-placing function added | DETECTED |
| M | decision updated on retry | DETECTED (the database trigger) |
| N | outcome updated on retry | DETECTED (the database trigger) |
| O | outcome reads a candle at/before `T` | DETECTED |
| P | straddling candle wholly subsequent | DETECTED |
| Q | same bar picks a winner | DETECTED |
| R | touch named as a fill | DETECTED |
| S | decision-evidence correction ignored | DETECTED |
| T | outcome-evidence correction ignored | DETECTED |
| U | unrelated correction invalidates | DETECTED |
| V | duplicate create makes a run | DETECTED |
| W | changed config reuses a key | DETECTED |
| X | production composes Shadow | DETECTED |
| Y | foreign Origin passes | DETECTED |
| Z | old run response overwrites the UI | DETECTED (vitest) |
| AA | run active after its session ends | DETECTED |
| AB | exception message logged | DETECTED |
| AC | outcome crosses a gap | DETECTED |
| AD | history lost after restart | DETECTED |

The first pass also had T and U `NOT_APPLIED` (formatting had reflowed their
anchors) and F mis-aimed; all three were re-anchored and re-run, not counted
until detected.

## 47. Performance (local, simulated history)

| Measurement | Result |
| --- | --- |
| Run creation | 20 ms; first decision 0.18 s later |
| 600-candle stream at 0.1 s/event | 600 of 600 boundaries evaluated, keeping pace |
| Run detail / run list (25) | 6.5 ms / 9.0 ms median |
| Journal page of 100 (with evidence and developments) | 27.5 ms; 28.0 ms at a deep cursor |
| Outcome page of 100 | 9.9 ms |
| SQL per journal page | 4, independent of page size (Part 2A measurement, path unchanged) |
| Cancellation of an observing run | 18 ms |
| Restart reconciliation, 4 orphaned runs | all `INTERRUPTED`; backend healthy 6.5 s after start |
| Container memory afterwards | backend 129 MiB, postgres 99 MiB, nginx 17 MiB |

## 48. Validation

| Gate | Result |
| --- | --- |
| ruff check / format --check | passed / formatted |
| mypy linux / win32 | no issues |
| lint-imports | 40 kept, 0 broken |
| pytest, real PostgreSQL | **4,373 passed, 0 skipped** (4,373 collected) |
| npm ci --dry-run, typecheck, lint, prettier, build | passed |
| vitest | **484 passed** (448 + 36) |
| Docker | config valid; both images built; 3/3 healthy; `0008_shadow_outcomes`; readiness 200 |
| Served frontend | `index-DFB5iF6K.js` and `index-BG75_Ptl.css` byte-identical to the local build (SHA-256) |

## 49. Known limitations

* The database enforces "observed after the decision", not "opened at or
  after it"; the straddling rule is the domain's (probe P).
* Corrections reach a run only through the live stream; a replayed dataset
  never produces one, so the browser shows invalidation through a disclosed
  fixture.
* The screen refreshes on request and does not poll.
* The outcome window is a fixed 24 driver candles; only entry intents are
  followed.
* SQLAlchemy is held below 2.1 until 2.1 is tested.
* No authentication exists; the local-only boundary is not authorization.

**PHASE 14 IN PROGRESS — AWAITING HUMAN REVIEW.** Not closed, not committed,
not pushed. Phase 15 not started.
