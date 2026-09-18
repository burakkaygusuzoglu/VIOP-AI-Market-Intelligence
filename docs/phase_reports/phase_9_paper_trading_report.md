# Phase 9 Report — Generic Paper Trading Core

**Status: IMPLEMENTED, validated, awaiting review. Not committed.**

Phase 9 adds a deterministic, replayable, persistent **simulation** of a
person's own trade plan against bars they supply. It is not trading. No code
path can place, route or acknowledge an order; there is no broker, no Midas
integration, no credential and no live data.

Baseline: `9479a9c Add multi-asset architecture foundation` on `main`, tracking
`origin/main`. Backend 2 541 passed / 0 skipped, frontend 274 passed, 19 import
contracts, Docker stack healthy, no paper-trading code present.

After Phase 9 and its human-review closeout: backend **2 821 passed /
0 skipped**, frontend **309 passed**, **22** import contracts kept.

## 1. Architecture

    Generic paper core   app/domain/paper          rules · model · engine
            ↓
    Instrument / policy  app/domain/instrument     ProductPolicy
            ↓
    Product              app/adapters/products     FuturesProductResolver,
                                                   FuturesSnapshotCodec
            ↓
    Futures domain       app/domain/futures        FuturesProductPolicy (Phase 8.5)

`app.application.paper.PaperTradingService` owns the use cases and reaches
products only through `ProductResolver` and `ProductSnapshotCodec` ports.
Persistence is `SqlAlchemyPaperStore` behind `PaperStore`. The API is
`/api/paper/positions` (eight operations on seven paths). The frontend adds one screen.

The domain is not futures-shaped: nothing is called `ViopPaperPosition`, the
paper domain cannot import `app.domain.futures` (contract 20), and there is no
`if asset_class == FUTURES` in it. Gross P&L is `pnl_for_product`; sizing is
`size_for_product`. No Phase 3 or 8.5 formula is duplicated.

## 2. Domain model

| Type | Role |
| --- | --- |
| `PositionSpec` | The plan: symbol, direction, integer quantity, intended entry, stop, 1–5 targets (strictly ordered, allocation ≤ quantity), timeframe, aware decision time, `SimulationPolicy`, note ≤ 280. Structural validation only. |
| `RiskApproval` | The risk engine's verdict, built only from a `SizingResult`. |
| `SimulationPolicy` | `rules_version`, `SameBarPolicy`, `SlippagePolicy`, `FeePolicy`. Immutable, persisted with the position. |
| `PaperEvent` | One ledger fact: sequence, type, market time, string-only data. |
| `PaperPosition` | The state reproduced from events: state, remaining, entry fill, stop, targets, mark, realized gross, fees, bars applied. |

No separate order or fill entity: a fill is an event, and an entry intent is
the spec. Adding either would have modelled exchange concepts Phase 9 does not
simulate.

## 3. Lifecycle

`PENDING_ENTRY → OPEN → PARTIALLY_CLOSED → CLOSED`, with `REJECTED` (the entry
fill would be at or beyond the stop or first target), `CANCELLED` (only before
entry) and `AMBIGUOUS_HALTED` (same-bar ambiguity under `HALT`). Terminal:
`CLOSED`, `CANCELLED`, `REJECTED`. Every other transition raises
`PaperRefusalError(INVALID_TRANSITION)`; nothing is silently corrected.

## 4. Identity

* **Command identity**: the client's `Idempotency-Key` (16–128 chars).
* **Position identity**: `PP-` + first 24 hex of SHA-256(key) — deterministic,
  so a retry and a replay name the same position. No UUID in any domain path.
* **Event identity**: `(position_id, sequence)`, gapless from 1.
* Audit timestamps (`created_at`, `updated_at`, `recorded_at`) are never part
  of identity or of any financial result.

## 5. Simulation rules — `paper-sim/v1`

| | |
| --- | --- |
| Entry | `NEXT_BAR_OPEN`: open of the first closed bar at or after the decision time. Only entry model supported; no LIMIT/STOP/STOP-LIMIT/MARKET semantics are claimed. Intended entry, simulated fill and mark are separate fields. |
| Stop | `STOP_PRICE_OR_GAPPED_OPEN`: the stop price, or the open when the bar opens through it (`gap: true`). Trigger and fill are separate event fields. Protects the **remaining** quantity only. |
| Target | `TARGET_PRICE_NO_IMPROVEMENT`: the target price even on a favourable gap. Targets fill in order; each has its own quantity. |
| Manual close | Open of the next closed bar. |
| Breakeven | Stop → entry fill, only while the mark is favourable. |
| Same bar | `STOP_FIRST` (default: exit remainder at stop, touched targets named) or `HALT` (no fill, `AMBIGUOUS_HALTED`). Both emit `SAME_BAR_AMBIGUITY`. No optimistic ordering exists. |
| Slippage | `ZERO` (default, stated) or `FIXED_POINTS`; adverse; market-style fills only (entry, stop, manual close). |
| Fees | `NOT_MODELLED` (fees and net are `null`, not zero) or `USER_DEFINED_PER_UNIT` (all-in, per unit, per fill). No VİOP commission is invented. |

Gross, fees and net are separate throughout, from event to screen.

## 6. Quantity, partial exits, P&L

Futures quantity is a whole number (`StrictInt` at the API; a string `"4"` is
422). Remaining quantity can only fall, through target fills of their allocated
quantity or a full exit of the remainder; it can never go negative or exceed the
opened quantity. Partial exits are **target-driven**; manual partial closes are
not offered.

Realized gross accumulates per fill from `pnl_for_product(entry fill, exit
fill, quantity)` and is never recomputed from the mark. Unrealized gross uses
the last mark and the remaining quantity; it is `null` before entry and exactly
`0` on every terminal state.

## 7. Ledger and replay

Input events: `POSITION_CREATED`, `OBSERVATION_APPLIED`, `CLOSE_REQUESTED`,
`STOP_MOVED_TO_BREAKEVEN`, `POSITION_CANCELLED`. Derived: `ENTRY_FILLED`,
`ENTRY_REJECTED`, `TARGET_FILLED`, `STOP_FILLED`, `SAME_BAR_AMBIGUITY`,
`MANUAL_EXIT_FILLED`, `POSITION_CLOSED`.

`rebuild(spec, approval, product, events)` replays the inputs and demands the
identical ledger (`REPLAY_DIVERGED` otherwise). The service rebuilds from the
stored ledger, the stored policy and the frozen product snapshot before every
write.

**Observations.** A bar must be closed by the server clock, at or after the decision
time and at least one timeframe after the last applied bar. An identical
re-sent bar is a no-op (no event, no fill); the same timestamp with different
prices is `CONFLICTING_OBSERVATION`; an earlier bar is `OUT_OF_ORDER`. Input is
never sorted.

## 8. Risk and analysis boundaries

Creation runs `size_for_product` first. Anything but `ALLOWED`, or a quantity
above `allowed_units`, is refused and nothing is written. Missing or unverified
multiplier/tick metadata is refused through `require_product_calculable`.
Analysis never creates a position: there is no link from the analysis API, the
application layer may not import the orchestrator (contract 22), and every
position is `USER_CREATED`. WAIT or NO_TRADE therefore cannot become a trade.

## 9. Persistence and concurrency

Migration `0002_paper_trading`: `paper_positions` (plan, approval, product
snapshot, projection, `version`, unique idempotency key + fingerprint, CHECK
constraints on quantity, remaining and state) and `paper_position_events`
(PK `(position_id, sequence)`, JSONB data, `timestamptz`). Money is `NUMERIC`.
A PL/pgSQL trigger refuses `UPDATE` and `DELETE` on events.

Writes: one transaction → `SELECT … FOR UPDATE` → rebuild → append → update
conditional on `version`. Idempotency: same key + payload returns the stored
position (200); same key + different payload is 409; a concurrent duplicate
insert re-reads the winner. No distributed lock.

## 10. API and bounds

`POST /paper/positions`, `GET /paper/positions`, `GET /{id}`, `GET /{id}/events`,
`POST /{id}/observations`, `/close`, `/stop/breakeven`, `/cancel`. Request
models are `extra="forbid"` with no field for state, fill, P&L, fees, event
type, provenance, risk approval or product implementation. Decimal text:
≤ 40 chars, finite, |x| < 10¹². Bounds: 50 positions/page, 200 events/page,
500 rows and 256 KiB per upload, 5 000 bars per position, 5 targets, 280-char
note. Refusals are typed 4xx; an unreachable store is 503.

## 11. Frontend

`PaperTrading` screen reached from a secondary dashboard control. Persistent
banner "KAĞIT İŞLEM — SİMÜLASYON" and a SİMÜLASYON state tag. Detail shows
quantity/remaining, planned entry, simulated fill, initial/current stop, mark,
realized gross, fees, net, unrealized, targets table, key events. Beginner
"Ne oldu ve neden" turns ledger fields into Turkish sentences; Pro adds rules
version, policies, fill models, risk approval, provenance and the paged raw bar
events. Both views read one DTO; numbers are the server's decimal strings,
shown verbatim. The create form reuses one idempotency key per plan until the
plan is edited.

## 12. Provenance and time

Fills are `SIMULATED`; market data `USER_SUPPLIED_HISTORICAL_BARS`; origin
`USER_CREATED`; the point value keeps its own `VerificationStatus` and source
from the frozen snapshot. Simulation never upgrades a fact's status. Financial
semantics use bar `open_time` only; the server clock is used only to refuse
unclosed bars and future decision times, and for audit timestamps.

## 13. Verification

| Evidence | Result |
| --- | --- |
| Golden scenarios (hand-derived values, LONG and SHORT: target, stop, partial then stop, gap, same-bar STOP_FIRST/HALT, rejection, manual close, slippage + fees) | 26 passed |
| Lifecycle, refusals, adversarial domain inputs | 82 passed |
| API boundary (forged fields, adversarial values, bounds, route inventory, 503) | 65 passed |
| PostgreSQL persistence (schema diff empty, append-only, constraints, rollback, races, idempotency, Decimal/timezone round-trip) | 26 passed |
| HTTP lifecycle in-process (DTO == DB row == replay) | 15 passed |
| Futures parity (Phase 8.5 fixtures) | 103 passed; futures, risk and instrument domain unchanged vs HEAD |
| Mutation probes A–O | **15/15 detected**, files restored (SHA-256 verified) |
| Phase 8.5 mutation probes | 15/15 detected |
| Runtime smoke through nginx (production composition) | 26/26: honest `PRODUCT_METADATA_UNAVAILABLE`, forged fields 422, no broker routes, served assets == `dist` |
| Runtime lifecycle (real uvicorn + PostgreSQL container + production bundle in Chrome) | 25/25: DB row == API DTO == rendered values; trigger refuses UPDATE |
| Browser accessibility, 1280/390/320 px | no horizontal overflow, 0 unnamed controls, 0 targets < 24 px, 0 contrast failures, no heading jumps, refusal in `role="alert"`, focus on detail heading |
| Phase 8 regressions (E2E, adversarial, vision separation) | 23/23, 16/16, analysis ids identical |

Mutations: A paper imports futures · B SHORT uses LONG formula · C duplicate
bar exits twice · D out-of-order accepted · E ambiguity picks target · F gap
stop fills at trigger · G partial reduces wrong quantity · H closed keeps
unrealized · I fee counted twice · J calculability check removed · K client
sends realized P&L · L client sends state · M veto ignored · N retry duplicates
· O stored policy ignored.

**Found and fixed during validation.** The browser pass found a populated
position widening the page to 1 021 px at 390 px: grid items kept their nowrap
table's min-content width, and the visually-hidden column label escaped its
unpositioned scroll wrapper. Both fixed in `PaperTrading.css` and re-measured.

**Performance** (host, PostgreSQL container): the engine applies 5 000 bars in
402 ms and rebuilds them in 432 ms; a 500-bar upload takes 78 ms on a new
position and 693 ms at the 5 000-bar cap (each write replays the ledger, so cost
is linear and bounded by the cap). Reading one position verifies it against the
ledger: 4.3 ms (p50) for a typical position, 567 ms for one at the cap. Detail
responses are 2.8 KiB (key events only); a 200-event page is 63 ms / 46 KiB;
the list is 7 ms in two queries (count and page) with no per-row query. Through
nginx: list p50 6 ms, p95 31 ms.

## 14. Quality gates

Backend: ruff check clean · ruff format 321 files · mypy linux and win32 clean
(317 files) · lint-imports 22 kept / 0 broken · pytest 2 821 passed, 0 skipped
(18 pre-existing Python 3.14 asyncio deprecation warnings).
Frontend: `npm ci --dry-run` ok · vitest 309 passed (18 files) · typecheck ·
lint · prettier · build.
Docker: `docker compose config -q` · backend and frontend rebuilt · migration
applied in container (`0002_paper_trading (head)`) · postgres, backend,
frontend healthy · served JS/CSS byte-identical to the tested build.

## 15. Human-review closeout: causality and trust

The review withheld approval pending temporal-causality and trust-boundary
proofs. Auditing them found most invariants already held, one real defect, and
one weak proof of my own.

**Already held, now proved explicitly.**

* *Entry causality.* A bar opening before the decision time is refused
  (`OBSERVATION_BEFORE_DECISION`), so its open can never become the entry price.
  Proved for a decision at a bar's open, one microsecond after it, inside a
  5-minute bar (the reviewer's 10:05/10:07 example), at a bar's coverage end,
  between bars, and for LONG and SHORT alike.
* *Wall clock.* `app.domain.paper` contains no clock; the use cases read time
  only through the injected `ClockPort`. Both are now enforced by package scans.
  A stored position read under clocks in 2026 and in 2099 returns identical
  ledgers, projections and DTOs.
* *Intake versus replay.* Bar closedness is checked when a bar is submitted and
  never re-asked afterwards: a position replays identically under a clock
  earlier than its own last bar, while an unclosed bar is still refused at
  intake.
* *Frozen product snapshot.* Changing the metadata provider's multiplier from 10
  to 50 leaves an existing position's P&L at 40.00; a later write on that
  position still uses 10 (80.00 on target 1, not 400.00); a new position uses 50.
* *Persisted policy.* A position stored with `HALT` and per-unit fees keeps both
  though the current defaults are `STOP_FIRST` and `NOT_MODELLED`.
* *Ledger growth.* Duplicate close and breakeven commands append nothing, a
  re-sent bar appends nothing, and one bar can append at most 3 + MAX_TARGETS
  events - so the 5 000-bar cap bounds the whole ledger.

**Defect found and fixed: the projection was trusted on reads.** `GET
/paper/positions/{id}` returned the `paper_positions` row without consulting the
ledger. Writes rebuilt and repaired it, but in between, a row edited outside the
application would have been served as financial history. The detail read now
replays the stored inputs under the stored policy and frozen snapshot and
refuses with `PROJECTION_DIVERGED` (503) if the row disagrees - proved in
PostgreSQL for a corrupted amount, state and quantity, and at runtime through
HTTP. The list endpoint still serves unverified summaries, which is now stated.

**Semantics made explicit in the ledger.** `CLOSE_REQUESTED` and
`STOP_MOVED_TO_BREAKEVEN` now record `effective_after_bar`, the last applied bar
at the moment of the request. The behaviour did not change - a request has
always taken effect on the next observed bar - but the ledger now states it
rather than implying it from sequence order.

**A weak proof of my own.** Mutation probe Q (replay consults the wall clock)
was *not* detected on its first run: my mutation read the real process clock,
which no clock-injecting test can reach. Rather than weaken the probe I added
the package scan above and split it, so both variants now fail: Q1 (replay
consults the injected clock) and Q2 (replay reads the process clock directly).

**Closeout probes.** P, Q1, Q2, R, S, T, U - all detected, all files restored
byte-identically (SHA-256 verified). A–O re-run: 15/15 still detected.

| New evidence | Result |
| --- | --- |
| `tests/unit/paper/test_temporal_causality.py` | 30 passed |
| `tests/unit/paper/test_metadata_trust_boundary.py` | 17 passed |
| `tests/integration/test_paper_temporal_trust.py` (real PostgreSQL) | 18 passed |
| Runtime lifecycle E2E (now including the close anchor and a corrupted projection) | 31/31 |
| Runtime smoke through nginx | 26/26 |
| Phase 8 regressions · adversarial · multi-asset | 23/23 · 16/16 · 189 passed |

**Append-only, stated precisely.** No application path edits or deletes an
event, and the PostgreSQL trigger refuses `UPDATE` and `DELETE` (re-proved
through the running container). This is append-only by application and database
rule, not cryptographic immutability: a superuser can drop the trigger, and
`TRUNCATE` is not a row-level delete. Privileged database access can still
rewrite history.

**Idempotency identity.** `PP-` + 24 hex of SHA-256(key) is an identity scheme,
not an authorization token: it is unauthenticated, and anyone who can reach the
API can read any position - as in every earlier phase of this single-user
application. The raw idempotency key appears in no response body, no error
message and no log line; the conflict for a reused key with a different payload
names neither the key nor the other payload.

## 16. Known limitations

1. **No contract metadata provider is composed**, so the deployment refuses
   every create. Deliberate; lifting it needs a verified metadata source.
2. One entry model (`NEXT_BAR_OPEN`); no limit/stop entries, no volume-limited
   partial fills, no manual partial close.
3. Bars are user-supplied CSV; no feed.
4. Fees are one all-in per-unit number; no commission schedule, tax or
   exchange fee.
5. No linkage from an analysis snapshot to a position (analyses are not
   persisted); positions are `USER_CREATED`.
6. The decision-time input is entered and labelled as UTC.
7. Refusal messages from the server are shown verbatim and are English, as on
   the other screens.
8. Each write replays the full ledger, and so does each detail read: linear
   cost, bounded by the 5 000-bar cap (4.3 ms typical, 567 ms at the ceiling).
9. The list endpoint serves projections without verifying them against the
   ledger; only the detail read verifies. A person acting on numbers should be
   looking at a position, which is the verified path.
10. Append-only holds through the application and the database trigger, not
    cryptographically: privileged database access can still rewrite history.
11. No authentication or per-user ownership of positions (single-user local
    application, as in earlier phases). The position id is an identity, never
    an authorization token.
12. Coverage end is derived as `open_time + timeframe duration`; no exchange
    session calendar is modelled, so a bar is conservatively considered closed
    only after a full timeframe has elapsed.

## 17. Phase boundary

No journal analytics, performance dashboard, backtest, replay feature, shadow
mode, live feed, WebSocket/SSE, broker, Midas or order execution exists. The
only new domain package is `app/domain/paper`; the only new tables are
`paper_positions` and `paper_position_events`. No dependency was added. The
master spec and Phase 0–8.5 reports are unchanged. Nothing was committed or
pushed. Phase 10 has not begun.
