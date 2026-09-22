# Phase 13 — Live market architecture

**STATUS: PHASE 13 IN PROGRESS — AWAITING HUMAN REVIEW**

Part 1 (streaming backend and market state, including its data-integrity
closeout, section 10), Part 2A (streaming API, SSE and the Live Intelligence
frontend, section 11) and Part 2B (final runtime, SSE, browser and security
validation, section 12) are implemented and validated. Phase 13 is **not
closed**: it is uncommitted and unpushed, and it has not been through CI.

Baseline: HEAD `53bdd24` "Complete Phase 12 deterministic backtesting". The
tree was clean at the start. The backend had 3,812 passing tests, 0 skipped,
and 31 import contracts.

---

## 1. What Part 1 adds

Part 1 gives the backend a way to take in candle events and decide three
things:

- which candles are **confirmed facts**
- whether that picture is **complete**
- whether it is **current**

It does this without a second analysis engine and without any trade path.
Receiving data never triggers analysis, never calls Claude, and never opens a
position. Live analysis is `run_analysis` over confirmed candles, as in
replay and backtesting.

## 2. Files

| Path | What it holds |
| --- | --- |
| `app/domain/live/events.py` | Provenance, candle states, stream identity, raw (untrusted) events, provider signals, validated observations |
| `app/domain/live/validation.py` | `validate()`: an event becomes either an `Observation` or a coded `Rejection` |
| `app/domain/live/book.py` | `CandleBook`: confirmed candles for one timeframe, the forming candle, integrity, duplicates and conflicts, trimming |
| `app/domain/live/state.py` | Connection state machine, freshness, availability, snapshots |
| `app/domain/live/alerts.py` | Data-integrity alert candidates (stateless) |
| `app/domain/live/limits.py` | `LiveLimits`: every resource bound in one place |
| `app/application/live/ports.py` | `LiveMarketDataProvider`, `LiveSubscription`, `BackfillCapable` |
| `app/application/live/buffer.py` | `BoundedEventBuffer` for push providers |
| `app/application/live/session.py` | `LiveSession`: pull loop, recovery, on-demand confirmed analysis |
| `app/application/live/registry.py` | `LiveSessionRegistry` (max 8 sessions) |
| `app/adapters/live/mock_stream.py` | Mock pull, backfill and push providers; `ManualClock`; scripted steps |
| `app/application/analysis/serialisation.py` | `candles_to_csv`, moved out of replay so replay and live share it |

Modified:
- `app/application/replay/service.py` now uses the shared serialiser; its
  behaviour is unchanged.
- `backend/pyproject.toml` gains three import contracts.
- `docs/architecture.md` gains a Phase 13 part 1 section.

Tests: `tests/unit/live/` has `support.py` plus five files, 189 tests in total:

| File | Tests |
| --- | ---: |
| `test_validation.py` | 49 |
| `test_market_state.py` | 36 |
| `test_session.py` | 25 |
| `test_live_boundary.py` | 37 |
| `test_integrity_closeout.py` (closeout) | 42 |

## 3. Design decisions

**Provenance.** There is one provenance value, `SIMULATED_HISTORICAL_STREAM`,
and it is a constant on the provider. Raw events carry no provenance field. No
code can create `EXCHANGE_VERIFIED`, `LIVE_EXCHANGE_FEED` or `BROKER_VERIFIED`.

**Time.** There are three clocks:
- Event time: market time, taken from the provider.
- Receive time: from the injected clock.
- Audit time: when analysis was requested.

Freshness uses receive time. Analysis runs as of the latest confirmed coverage
end, which is market time. The three are never merged.

**Candle lifecycle.** A candle is either FORMING or CLOSED. A forming candle
is held separately and never enters `confirmed()` or analysis. It is dropped
on disconnect. An invalid event is refused and never stored, clamped or
re-timed.

**Completeness.** This was corrected in the closeout; see section 10. Three
separate facts are tracked: provider sequence continuity, interval coverage,
and exchange session boundaries. The last is never known, because there is no
verified calendar.
- Sequenced streams must follow the contract "one consecutive number per
  closed grid interval", and it is checked against time on every candle.
  - A number jump that matches the time jump means GAPPED. The missing
    candles are identified and can be filled exactly.
  - A time jump longer than the number jump is a TEMPORAL_GAP, which makes the
    book DISCONTINUOUS.
  - A number jump longer than the time jump is a SEQUENCE_MISMATCH, which
    makes the book DISCONTINUOUS.
- Unsequenced streams: any time jump is a TEMPORAL_GAP. A late candle is
  refused.
- A forming candle more than one interval ahead makes the book UNVERIFIED.
- No jump is ever called a session break.

**Duplicates and corrections.**
- An identical repeat is DUPLICATE. It is a no-op and does not refresh
  freshness.
- A conflicting value is CONFLICT. It is quarantined, the stored candle is not
  rewritten, and the timeframe becomes CONFLICTED.

**Connection.** The states are:
- INITIALIZING
- CONNECTED
- DISCONNECTED
- RECOVERING
- TERMINATED, with a reason: END_OF_STREAM, RECONNECT_LIMIT, OVERLOADED,
  PROVIDER_ERROR or CANCELLED.

Repeated signals are idempotent. A provider's CONNECTED signal does not end
recovery. Only a new newest closed candle does. Its placement records any gap
or jump since the last candle. A late fill of an older hole does not end
recovery (corrected in the closeout).

**Freshness and stale data.**
- Freshness is per timeframe (NO_DATA, FRESH or STALE), set by an explicit
  `FreshnessPolicy`. It measures *transport* freshness only.
- Market currency is separate. `MarketCurrency` comes from provenance
  alone, and for the simulated stream it is always HISTORICAL. Snapshots and
  analyses carry it and refuse any currency that does not follow from the
  provenance.
- There is no MARKET_CLOSED state because there is no verified calendar.
- Stale data keeps its original timestamps.
- `last_analysis()` recomputes `current` on every call. It returns
  `current=False` once its inputs are no longer the set that is connected and
  available now. `current` describes the stream, not the market.

**Availability.** A timeframe is available only when all of these hold:
- the connection is CONNECTED or RECOVERING
- the timeframe is FRESH
- its integrity is COMPLETE
- it has at least one closed candle

When a timeframe is unavailable, every reason is listed, not just the first.

**Reconnect and backfill.** A `BackfillCapable` provider is asked for the
closed candles after the last sequence number, up to `backfill_limit`. They
are validated like live data, and they are placed under the same
sequence-against-time rule as live candles. With no backfill, the timeframe
stays UNVERIFIED until the next new closed candle proves or disproves
continuity.

**Backpressure.** Subscriptions are pulled. Push providers go through
`BoundedEventBuffer`. On overflow, the buffer emits an OVERFLOW signal and the
session ends with TERMINATED(OVERLOADED). Events are never dropped silently.

**Bounds.** Defaults:
- 2,500 closed candles per timeframe
- 500 missing sequences tracked
- 200 recorded rejections
- 20 reconnects
- 32-character symbol
- price at most 1e9, volume at most 1e15, decimal scale at most 10
- 5 s clock skew
- 8 sessions per registry

**Trimming.** Completeness is a claim about the retained window. An
unresolved issue leaves the record only once it lies wholly before the oldest
retained candle:
- missing numbers at or below the oldest retained sequence
- an overflowed gap, only when its unenumerated ceiling is at or below that
  floor
- a discontinuity, only when the candle after it is the oldest retained

Each issue that leaves this way is counted in `unresolved_trimmed`.

**Analysis integration.** `confirmed_analysis()` is on demand only. It
analyses the available timeframes, calls `run_analysis` with the market-time
clock, and caches the result against a fingerprint made of:
- the available timeframes and their book versions
- the account
- the risk policy

It reports excluded timeframes with their reasons. It never resamples.

**Synthesis and Vision.** Neither is wired in. The session imports neither
the synthesis nor the Vision module, and never imports Anthropic.

**Metadata trust.** The injected `ContractMetadataProvider` is passed through;
production passes none. The result reports `contract_metadata_verified =
False`.

**Aggregation.** Aggregation is **not implemented**, deliberately. The mock
emits native candles for each timeframe. Real aggregation needs verified
window and session conventions, which do not exist yet. Missing timeframes are
reported and never synthesised.

**Paper and backtest isolation.** Import contracts forbid live code from
reaching paper, backtest, replay or execution modules, and the boundary tests
confirm it. Live code has no path that can create a position.

**Alerts.** `alert_candidates(snapshot)` returns data-integrity candidates
only:
- DATA_STALE
- PROVIDER_DISCONNECTED
- RECOVERY_PENDING
- DATA_GAP
- DATA_DISCONTINUITY
- DATA_CONFLICT
- CONTINUITY_UNPROVEN (a forming candle is ahead of the confirmed candles;
  added in the closeout)
- INVALID_OBSERVATIONS
- STREAM_OVERLOADED
- STREAM_ENDED

Market-setup alerts belong to Part 2.

**Failure behaviour.** A provider failure ends the session with
TERMINATED(PROVIDER_ERROR). The log carries only the symbol, the stage and the
error type. Analysis failures raise a typed `LiveAnalysisUnavailableError`
with `from None`. Tests confirm that no secret, exception message or traceback
reaches a log or an error.

**Restart.** State is ephemeral and held in memory: nothing is persisted and
there is no migration. After a restart the session is back to INITIALIZING
with empty books.

## 4. Golden cases

| Case | Where |
| --- | --- |
| A / O clean confirmed stream | `test_market_state.py::TestAAndOAClosedCandleIsConfirmed`, `test_session.py::TestAACleanStream` |
| N forming candle never confirmed | `test_market_state.py::TestNAFormingCandleIsNeverConfirmed` |
| B identical duplicate | `TestBIdenticalDuplicate` |
| C conflicting duplicate | `TestCConflictingDuplicate` |
| D missing candle | `TestDAMissingCandle` |
| E / F late and out-of-order | `TestEAndFLateAndOutOfOrder` |
| G one timeframe stale, another available | `TestGStaleTimeframeWhileAnotherStaysAvailable` |
| H connected, no valid data | `TestHConnectedButNoValidData` |
| I disconnect | `TestIDisconnect` |
| J reconnect without backfill | `test_session.py::TestJReconnectWithoutBackfill` |
| K reconnect with complete backfill | `TestKReconnectWithCompleteBackfill` |
| L invalid OHLC | `test_validation.py::TestLInvalidOhlcIsRefusedNotClamped` |
| M huge or unrepresentable decimal | `TestMHugeOrUnrepresentableDecimals` |
| P multi-timeframe availability | `test_session.py::TestPMultiTimeframeAvailability` |
| Q bounded queue overflow | `TestQBoundedQueueOverflow` |
| R cancellation | `TestRCancellation` |
| S end of mock stream | `TestSEndOfMockStream` |
| T deterministic replay | `TestTDeterministicReplay` |

The adversarial cases are hostile symbols, forged future events, naive
timestamps, floats, NaN and Infinity, invalid sequences, duplicate floods,
leaking secrets in provider failures, and every combination of signals.

## 5. Performance

These numbers come from a script run in the scratchpad (not in the repo) on
Windows 11 with Python 3.14.3, using the mock pull provider and a 5M stream.
The script was re-run after the closeout; both runs are shown.

| Measurement | Before the closeout | After (run 1 / run 2) |
| --- | --- | --- |
| Validation only (20,000 events) | 5.55 µs/event | 7.31 / 6.71 µs/event |
| Ingest 100 events | 33.1 µs/event, peak 33 KiB | 43.9 / 42.3 µs/event, peak 34 / 31 KiB |
| Ingest 1,000 events | 31.1 µs/event, peak 258 KiB | 44.5 / 40.9 µs/event, peak 253 / 239 KiB |
| Ingest 2,500 events (the cap) | 31.3 µs/event, peak 598 KiB | 46.0 / 45.5 µs/event, peak 593 / 592 KiB |
| Ingest 5,000 events | 35.2 µs/event, peak 614 KiB | 52.2 / 54.1 µs/event, peak 601 / 598 KiB |
| Duplicate handling | 6.7 µs each | 9.3 / 9.6 µs each |
| Confirmed analysis, 288×5M + 96×15M + 24×1H | 17.7 ms first call, 0.033 ms cached | 23.9 / 23.6 ms, 0.060 / 0.080 ms cached |

The validation code did not change in the closeout, yet it measured 21–32%
slower. That means part of the slowdown is noise on this machine. The ingest
path does add one divide per new candle (the sequence-against-time check), so
the two cannot be fully separated here. At the cap, ingest takes under
55 µs per event. Memory stops growing at the cap: 5,000 events peak at
598–601 KiB against 592–593 KiB for 2,500. At every size, 2,500 candles are
kept and one analysis is run.

## 6. Quality gates

| Gate | Result |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 435 files already formatted |
| `mypy --platform linux` | No issues, 427 source files |
| `mypy --platform win32` | No issues, 427 source files |
| `lint-imports` | 34 kept, 0 broken (31 before Phase 13, plus 3 new) |
| `pytest` against PostgreSQL `viop_test` | **4,001 passed, 0 skipped** |

The 4,001 are the 3,812 baseline tests plus 189 live tests. The live tests are
147 from Part 1 plus 42 from the closeout.

The 18 warnings are all Python 3.16 deprecation notices about the
selector-loop policy. They come from `app/core/runtime.py` and
`tests/unit/test_runtime.py`, both Phase 0 files that Part 1 did not change.
No warning comes from live code.

The frontend is unchanged in Part 1 and was not re-run. The last recorded
frontend baseline is 403 tests, from Phase 12.

## 7. Known limitations

1. There is no aggregation. A timeframe the stream does not supply natively
   is unavailable.
2. Without a verified calendar, every unexplained time jump is a
   TEMPORAL_GAP, on sequenced and unsequenced streams alike. That includes
   every real session break. A real multi-session feed stays unavailable
   until the break leaves the 2,500-candle window, or until a verified session
   source exists (a later phase).
3. Freshness is based on receive time and the thresholds are explicit
   configuration. Nothing knows whether the market is open.
4. An overflowed gap (more than 500 missing numbers) stays GAPPED until every
   number it could not enumerate lies before the retained window. Numbers
   beyond the enumerated 500 cannot be late-filled.
5. Missing numbers that appear mid-stream (not after a reconnect) are filled
   only by late live events. Backfill is requested only after a reconnect.
6. Open-time grid alignment is only checked for timeframes up to 1H. 4H and
   1D boundaries depend on venue conventions, which are not verified. A 4H or
   1D jump that is not a whole number of intervals is a TEMPORAL_GAP.
7. There are no market-setup alerts. They belong to Part 2.
8. State is ephemeral and does not survive a restart.
9. At the end of Part 1 nothing was exposed over HTTP. Part 2A adds the API
   and SSE for local development only; production still composes no session
   (section 11).

## 8. Remaining Part 2 work (as planned at the end of Part 1)

Part 2A delivered the first six items below; see section 11. The last two
belong to Part 2B.


- Streaming API and WebSocket/SSE transport
- Composition-root wiring
- Live Intelligence frontend
- Setup cards
- Timeline
- Alerts UI and market-setup alerts
- Browser end-to-end tests
- Final mutation sweep and security review

## 9. Phase boundary

`tests/unit/live/test_live_boundary.py` checks for the absence of the
following. All 37 checks pass.
- real provider or network code
- exchange provenance claims
- broker, order, Midas or position creation
- paper, backtest or replay services
- a second indicator engine
- synthesis, Vision or Anthropic wiring
- fixture metadata
- persistence or a migration
- live routes or schemas
- composition-root wiring

No Part 2 or Phase 14 artifact existed at the end of Part 1. Part 2A replaced
the two assertions about live routes and composition with Part 2A's own
boundary (section 11.5). No Part 2B or Phase 14 artifact exists.

## 10. Final data-integrity closeout

A targeted review before Part 2 audited four claims in this report. Three of
them were wrong or incomplete, and one hid a real gap. The corrections below
are the smallest changes that make each claim true. No refactor was needed.

| Claim audited | What the code actually did | Correction |
| --- | --- | --- |
| "A time jump with contiguous sequence numbers is a session break" | Called the book COMPLETE across any time jump whose numbers were contiguous. That asserted a session break with no evidence, and could hide a missing 5M candle. | Sequence numbers are now checked against time on every new candle. A time jump the numbers do not explain is a `TEMPORAL_GAP`. Numbers that jump further than time are a `SEQUENCE_MISMATCH`. Both make the book DISCONTINUOUS. Nothing is ever called a session break. |
| "A missing sequence number means GAPPED" | Recorded "missing candles" even where the grid had no interval for them, so they could never be filled. A late fill could land anywhere between its neighbours. | A gap is recorded only when the number jump matches the time jump. A late fill must land in exactly the interval its number names. |
| Reconnect continuity is "proven by the next contiguous closed candle" | *Any* closed candle ended the wait, including a late fill of an old hole. That fill says nothing about the time since the disconnect. | Only a new newest candle ends the wait. |
| (not claimed) | A forming candle several intervals ahead of confirmed history refreshed freshness while the book stayed COMPLETE. That hid closed intervals that were never received, and could make a cached analysis read as current again. | `forming_ahead` makes the book UNVERIFIED and raises a `CONTINUITY_UNPROVEN` alert, until the confirmed history catches up. |
| "`missing_overflowed` is sticky until trimming scrolls the gap out" | The flag was never cleared at all. That was safe but unrecoverable. A naive clear would have been unsafe, because the numbers it could not record are the top of the range and can still sit inside the window after every recorded one has gone. `list(range)` also allocated the whole jump. | The ceiling of the unenumerated numbers is tracked, and the overflow retires only once the ceiling is before the window. The range is sliced, never listed. `unresolved_trimmed` discloses every unresolved issue that leaves the window. |
| Freshness vs currency | Freshness measured receive time correctly, but nothing in the model separated "still arriving" from "describes the market now". | `MarketCurrency` (only `HISTORICAL`) comes from provenance alone. It is carried on snapshots and analyses, which refuse any other value. Candle times are never compared with a wall clock. |
| Cached analysis "current" | Correct for a later receive time, CONNECTED, duplicates, trims, conflicts and ended streams. It was open only through the forming-ahead gap above. | Covered by the forming-ahead fix. `current` is recomputed on every call, and a test now covers each transition. |

One golden assertion was inverted, and it is reported here rather than
buried. It was `test_a_session_break_with_contiguous_sequences_is_not_a_gap`,
now `test_contiguous_sequences_across_a_time_jump_are_not_a_session_break`.
It encoded the wrong claim.

One test changed its input without changing its assertion:
`test_a_huge_sequence_jump_is_reported_without_enumerating_it`. It used a
jump of a million numbers across a single interval, which is now correctly a
SEQUENCE_MISMATCH. It now uses a jump that is consistent with time.

The ten mutation probes of the closeout's own fixes were all killed:
- late fill ends continuity
- contiguous numbers treated as a session break
- overflow cleared once its enumerated record is empty
- forming-ahead ignored
- late fill accepted anywhere in its gap
- `current` from the connection alone
- temporal-gap reason dropped
- currency unchecked on the snapshot
- currency unchecked on the analysis
- trimming without disclosure

Each probe was applied to the real source, run against the live suite, and
restored. The files ending in CRLF were matched in CRLF.

## 11. Part 2A — Streaming API and Live Intelligence frontend

Part 2A is implemented and validated. Part 2B (real-browser end-to-end tests,
the final mutation sweep and the final security review) has **not started**.
Nothing is committed.

### 11.1 What was added

| Path | What it holds |
| --- | --- |
| `app/application/live/records.py` | `StreamRecord`: what the state did with each item, written after it was applied |
| `app/application/live/catalog.py` | `SimulatedSourceCatalog` port, `OpenedSource`, `PlaybackPace`, source errors |
| `app/application/live/workspace.py` | `LiveWorkspace`: session tasks, bounded timeline, bounded subscribers, limits, analysis serialisation, shutdown |
| `app/adapters/live/dataset_playback.py` | `ReplayDatasetCatalog` (stored replay datasets as sources) and `PacedPlaybackProvider` |
| `app/api/schemas/live.py`, `live_projection.py` | Strict request bodies, literal-typed responses, the `viop.live.v1` SSE envelope |
| `app/api/routes/live.py` | Nine routes (below) |
| `frontend/src/api/live.ts` | Zod schemas and the only `EventSource` |
| `frontend/src/domain/live.ts` | Pure client guards and Turkish labels |
| `frontend/src/screens/Live.tsx`, `components/Live.css` | The Live Intelligence workspace |

Changed:
- `app/application/live/session.py` gained an optional `observer` and
  records every applied item. A cancellation before the subscription opens
  now terminates the state as CANCELLED; previously it escaped with the
  state still INITIALIZING.
- `app/main.py` composes the workspace outside production and shuts it
  down before the database.
- `backend/pyproject.toml` adds contract 35: "The live API can stream and
  analyse but cannot trade or narrate".
- Frontend: `App.tsx` and `Dashboard.tsx` gain the entry point;
  `capabilities.ts` gains `simulated-live-stream` (AVAILABLE_NOW);
  `live-analysis`, meaning a real exchange feed, stays NOT_IMPLEMENTED.

### 11.2 Routes

| Route | Purpose |
| --- | --- |
| `GET /api/live/capability` | What can be streamed and what is never claimed. Says DISABLED in production. |
| `GET /api/live/sources` | Stored datasets as sources; no candles are read |
| `POST /api/live/sessions` | Start a playback. Body: `source_id`, `timeframes`, `window_candles`, `pace`. `extra="forbid"`. |
| `GET /api/live/sessions` | Sessions in this process (at most 8) |
| `GET /api/live/sessions/{id}` | The authoritative snapshot |
| `POST /api/live/sessions/{id}/cancel` | End the stream and keep the session. Repeatable. |
| `DELETE /api/live/sessions/{id}` | End if running, and release the slot |
| `GET /api/live/sessions/{id}/timeline` | A bounded page (at most 100) of what happened, in `seq` order |
| `POST /api/live/sessions/{id}/analysis` | Phase 8 analysis over confirmed, available candles. On request only; never narrated. |
| `GET /api/live/sessions/{id}/events` | Server-Sent Events |

### 11.3 Decisions

- **SSE over WebSocket.** The data flows one way, and SSE passes through the
  existing nginx proxy unchanged. This was checked against the running
  containers: frames arrived at 1 per second with a `text/event-stream`
  content type.
- **Source.** Stored datasets only, so no prices are invented. The adapter
  publishes closed candles only; forming states would be fabricated. The
  replay store keeps candles without a symbol, and the adapter attaches the
  dataset's label. That gap was found by the integration test: every event
  had been honestly refused as INVALID_SYMBOL.
- **Identity.** Stream `LS-<24 hex>` from `secrets`, the dataset `RD-…`, the
  provider `STORED_DATASET_PLAYBACK`, the instrument label, and a contract
  identity `NOT_ESTABLISHED`. These five are never merged.
- **Times.** Market time (`market_*`), receive time (`received_at`, freshness
  thresholds) and server audit time (`created_at`, `recorded_at`,
  `server_time`, `snapshot_at`) have separate names. The browser's clock is
  used only for its own transport log.
- **Currency.** Every response carries provenance
  `SIMULATED_HISTORICAL_STREAM` and currency `HISTORICAL` as single literals,
  on both backend and frontend. A relabelled response fails validation. The
  UI shows "AKIŞ TAZE" next to "GEÇMİŞ VERİ — güncel fiyat değil".
- **Envelope.** `protocol`, `session_id`, `event_id`, `kind` (STATE,
  TIMELINE, RESYNC_REQUIRED, END, HEARTBEAT), `server_time`, `cursor`,
  `entry`, `session`, `reason`.
- **Heartbeat.** It carries no `id:` line, touches no state, and carries the
  cursor so a client can detect a gap.
- **Resync.** Replay is bounded by the retained timeline (500 entries).
  Beyond it, or for a cursor from before a restart, the server sends
  `RESYNC_REQUIRED`. The client never lets `EventSource` retry by itself: it
  re-reads the snapshot and timeline, then reopens from the snapshot's cursor.
- **Backpressure.** Each subscriber queue holds 64. On overflow it is cleared
  and replaced by one `RESYNC_REQUIRED`. The candle books are untouched.
- **Transport vs provider.** A lost browser connection is logged and shown as
  the browser's, next to the provider's own connection state, which it does
  not change.
- **Freshness for a playback.** The threshold is three times the average
  receive-time gap between a timeframe's candles, with a 10-second minimum.
  It is receive-clock only and not a market fact.
- **Analysis.** On request, one at a time per session, and 2 at a time
  across the workspace. A concurrent duplicate is served from the Part 1
  cache. Synthesis is `NOT_APPLICABLE`: no Claude call. Nothing available
  gives a typed 409 with reasons.
- **Security scope.** Local development only. Production composes no
  workspace and every live route answers `503 LIVE_DISABLED`. There is no
  authentication, CORS is unchanged, and no credentials were added.

### 11.4 Bounds

| Bound | Value |
| --- | --- |
| Sessions | 8 (ended and unwatched sessions may be evicted; running or watched never) |
| Subscribers | 4 per session, 16 in total |
| Queue per subscriber | 64 |
| Timeline retained | 500 per session |
| Timeline page | 100 |
| Creations | 12 per minute |
| Concurrent analyses | 2 in the workspace, 1 per session |
| Window | 50–1,000 candles (Part 1's 2,500 still applies) |
| Session duration | 30 minutes |
| Request body | 48 MiB (unchanged) |

### 11.5 Tests

| Suite | Tests |
| --- | ---: |
| `tests/unit/live/test_workspace.py` | 40 |
| `tests/unit/live/test_live_sse.py` (route frames read one by one) | 8 |
| `tests/integration/test_live_api.py` (real PostgreSQL) | 47 |
| `frontend/src/components/live.test.tsx` | 38 |
| Frontend architecture: `EventSource` only in `api/live.ts` | 1 |

Two Part 1 boundary assertions were replaced, because Part 2A is the approved
phase for them. The changes are reported rather than hidden:
- "no live route or schema exists" became "the live API can stream but cannot
  trade" (no WebSocket, positions, orders, paper, backtest or synthesis).
- "the composition root wires no live session" became "it wires only the
  stored-dataset playback, and not in production".

The Phase 8 frontend rule banning `EventSource(` everywhere became "exactly
one module may open one". `WebSocket(` stays banned.

Before the reports, the new client guards were probed by mutation. Removing
each of these made a test fail (5 of 5): the session guard, the generation
guard, the cursor-gap check, the heartbeat-resync check, and the stale
open-response guard.

### 11.6 Performance

Measured in-process against real PostgreSQL with controlled mock inputs.
These numbers are not a claim of production throughput.

| Measurement | Result |
| --- | --- |
| Create session (288×5M + 96×15M + 24×1H window) | median 17.8 ms, p95 32.2 ms; 4 SQL statements (dataset, then 3 bounded candle reads) |
| Snapshot `GET` | median 1.23 ms, p95 1.49 ms; 0 SQL; 3.9 KiB |
| Timeline page (100 entries) | median 1.45 ms, p95 1.77 ms; 0 SQL; 27.1 KiB |
| Analysis | cold 30.4 ms; cached median 4.87 ms; 0 SQL; `analyses_run` stays 1 |
| Cancel | 1.73 ms; 0 SQL |
| Ingest and fan-out per event (0 / 1 / 4 / 16 readers) | 28.2 / 33.1 / 33.3 / 38.1 µs |
| A slow reader over 5,000 events | queue never above 64; 78 overflows, each followed by RESYNC_REQUIRED; memory flat at about 2.4 MiB after the books reached their cap |

### 11.7 Remaining (Part 2B and beyond)

Part 2B:
- Real-browser end-to-end tests: Chromium, keyboard, 390 px layout, and SSE
  reconnect across a real network drop.
- The final mutation sweep.
- The final security review.

Beyond this phase:
- Authorisation and per-user isolation, before any public deployment.
- Market-setup alerts. Only the data-quality alerts from Part 1 exist.
- A verified exchange calendar: every real session break in a dataset still
  blocks analysis as a TEMPORAL_GAP.
- A licensed real-time provider (Phase 15).

### 11.8 Part 2A quality gates

| Gate | Result |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 446 files already formatted |
| `mypy --platform linux` / `win32` | No issues, 438 source files each |
| `lint-imports` | 35 kept, 0 broken (34 before, plus 1) |
| `pytest` against PostgreSQL `viop_test` | **4,096 passed, 0 skipped** (4,001 + 95) |
| `npm ci --dry-run` | OK |
| `npx vitest run` | **442 passed** in 22 files (403 at the Phase 12 baseline, plus 38 live tests and 1 architecture rule) |
| `npm run typecheck`, `npm run lint`, `npx prettier --check .` | clean |
| `npm run build` | built |
| Docker | `docker compose config` valid; images rebuilt; all three services healthy; `/api/health/ready` ok; `/api/live/capability` and SSE verified through nginx |

The 18 backend warnings are the pre-existing Python 3.16 selector-policy
deprecations (section 6). The Part 1 gate table in section 6 describes the
state at the end of Part 1 and is kept as it was.

## 12. Part 2B — Final runtime, SSE, browser and security validation

Part 2B validated Parts 1 and 2A against the real system. That meant the
Docker backend, real nginx, real PostgreSQL, real HTTP and SSE, headless
Chrome 153 over CDP, and the built frontend. It also fixed the defects that
validation exposed.

Where this section contradicts sections 11.3–11.4, this section is current.

### 12.1 Defects found and fixed

| # | Found by | Defect | Fix |
| --- | --- | --- | --- |
| 1 | Configuration audit | An unset `APP_ENV` defaults to `development`, which composed the unauthenticated live workspace. Ambiguous configuration failed *open*. | Explicit opt-in `LIVE_SIMULATION_ENABLED` (default false), honoured only in development or test and ignored in production (the ignore is logged). Empty, misspelled or differently cased `APP_ENV` already refused to start. |
| 2 | Port audit | Postgres, the backend and nginx were published on `0.0.0.0` and `[::]`: the unauthenticated API was reachable from the LAN. | Every port is published on `127.0.0.1` and `[::1]` only. Verified refused on all four host IPv4 addresses, including the LAN address. |
| 3 | Code review | No `Origin` check, so a cross-site `text/plain` or form POST, which needs no CORS preflight, could cancel a session. | State-changing live requests with a foreign `Origin` → `403 LIVE_ORIGIN_REFUSED`. |
| 4 | Code review | No `Host` check, so DNS rebinding could reach the live API. | Live requests with a non-loopback, non-configured `Host` → `403 LIVE_HOST_REFUSED`. |
| 5 | Code review | A creation in flight during shutdown would start a task after shutdown returned, leaving an orphan. | Shutdown closes, then takes the creation lock; creation re-checks after reading its source. |
| 6 | Code review | An undescribed source failure escaped as a 500, with its message and traceback in the server log. | Typed `SOURCE_OPEN_FAILED` (503), logged by exception type only. |
| 7 | Real HTTP E2E | A reader catching up past its 64-slot queue, or overflowing while live, got `RESYNC_REQUIRED` followed by a tail of entries that skipped ahead. | An overflowing reader is closed after its resync demand. An oversized catch-up gets `RESYNC_REQUIRED(CATCH_UP_TOO_LARGE)` plus `STATE`, with no partial replay. |
| 8 | Real HTTP E2E | The loopback-only binding made `localhost` stall about 2 s per connection on Windows (a failed `::1` attempt first). | Also publish on `[::1]`. |
| 9 | Chromium journey I | The timeline could not be paged in the browser, and the client kept 200 of the server's 500 retained entries. | A "Daha eski kayıtları yükle" control over the existing paged API; the client bound is 500. |
| 10 | E2E design | The 15 s heartbeat could never be observed over real HTTP before a playback ended. | `LIVE_HEARTBEAT_SECONDS` (0.5–300, default 15). |

### 12.2 Deployment boundary (precise)

| Question | Answer |
| --- | --- |
| Enables mock streaming | `LIVE_SIMULATION_ENABLED=true` with `APP_ENV` `development` or `test`, or with `APP_ENV` unset (it defaults to `development`) |
| Refuses streaming | Any production configuration, and any configuration without the opt-in |
| Refuses to start at all | `APP_ENV` or `LIVE_SIMULATION_ENABLED` empty, misspelled or unknown |
| Bind addresses | uvicorn `0.0.0.0:8000` *inside* its container; nginx `:80` inside its container |
| Published ports | `127.0.0.1` and `[::1]` only, for 5173 (nginx), 8000 (backend) and 5432 (postgres) |
| External reachability | None; refused on every non-loopback host address tested |
| Browser protection | Host allow-list and foreign-Origin refusal on state changes; CORS unchanged (`http://localhost:5173`) |
| Authentication | **None.** Any local process can drive the API. |

### 12.3 Real-stack validation

**Production composition**, with `APP_ENV=production` and the opt-in
deliberately set. Everything was checked through nginx:
- 3/3 services healthy; migration `0006_backtest (head)`; `/api/health`
  live and ready both 200.
- The capability endpoint reports DISABLED. The sources, sessions, create and
  events routes all answer `503 LIVE_DISABLED`, including with `?fixtures=1`
  and an `X-Test-Provider` header.
- Forged provenance, currency, metadata and status fields get 422.
- Backtesting still refuses with `PRODUCT_METADATA_UNAVAILABLE`.
- The backend logged "live simulation opt-in ignored".

**Local simulated composition** over real HTTP and SSE through nginx
(`live_e2e.py`, 31/31 checks). Three datasets were uploaded through the real
replay API:
- 288×5M with 96×15M and 24×1H, contiguous;
- 288×5M with a real 15-minute hole;
- 600×5M.

These are TEST_FIXTURE OHLCV; nothing was edited in the database afterwards.
The checks covered:
- the snapshot/subscription race: 35 entries replayed in order, then STATE;
- confirmed analysis (200, `HISTORICAL`, market time before request time,
  synthesis `NOT_APPLICABLE`);
- 421 strictly consecutive timeline ids, and 408 confirmations with no
  duplicate;
- progressive delivery through nginx (first frame to last frame spread over
  about 39 s), with market time kept separate from server time;
- END closing the stream;
- timeline pagination;
- an oversized catch-up (`CATCH_UP_TOO_LARGE`) and an evicted cursor
  (`CURSOR_NOT_RETAINED`, gap reported);
- `Last-Event-ID` resume replaying identical entries and no new observation;
- cancel twice, delete twice;
- heartbeats: 6 in 6 s at SLOW pace, with no `id:`, no entry, the current
  cursor, and no candle created;
- the gap dataset: DISCONTINUOUS, a typed 409 with reasons, and a
  "no jump is assumed to be a session break" alert;
- the stored datasets byte-identical afterwards (SQL md5), and no paper
  position or backtest run created.

**Real Chromium** (`live_browser.py`, 27/27 checks, plus
`live_browser_close.py`, 3/3 checks):
- **Journeys A–O all pass.** They include a real nginx restart under an open
  stream: shown as the browser's connection, with the provider still
  connected, then a resync while the book kept growing (14 → 41). They also
  include a real backend restart: the session is reported gone and not
  resurrected. Session switching under 1.5 s of network latency showed the
  new session, with server readers A=0 and B=1.
- Closing a tab releases its reader and the session keeps running.
  Navigating away during a pending reconnect reopens nothing.
- The analysis DOM matches the backend's `last_market_as_of` exactly.
- Viewports 1280, 768, 390 and 320 in both modes and all five tabs: overflow
  0 px, nothing clipped, 0 unreadable timestamps, 0 unnamed buttons, 0
  unlabelled inputs, 0 buttons under 24 px. The polite status region was
  present at every width.
- Arrow, End and Home move selection and focus across the tabs; the focus
  outline is solid 2 px.

No WCAG certification is claimed.

**Adversarial**, through nginx (`live_adversarial.py`, 33/33): no 500s. There
were no paths, secrets or tracebacks in any response, and none in the backend
log for the run.

### 12.4 Mutation sweep A–V (24 probes, all DETECTED)

Each probe edited production code, translating to the file's line endings.
Every anchor matched exactly once, so nothing was NOT_APPLIED. The suite
failed on the intended test, and the original bytes were restored and
verified by SHA-256. The working tree was unchanged afterwards.

| Probe | Mutation | Detected by |
| --- | --- | --- |
| A | future candle accepted | `test_a_forged_future_event_is_refused` |
| B | forming candle analysed | `test_no_future_or_forming_candle_reaches_confirmed_analysis` |
| C | temporal gap complete | `test_contiguous_sequences_cannot_hide_a_missing_5m_interval` |
| D | trimmed gap retired early | `test_an_overflowed_gap_is_not_retired_while_any_of_it_is_in_the_window` |
| E1 | browser accepts any provenance | "refuses a response that calls itself an exchange feed" |
| E2 | snapshot relabelled | `test_a_snapshot_cannot_be_relabelled_as_current` |
| F | FRESH shown as current | "labels a fresh stream as historical, never as a current price" |
| G | CONNECTED hides unavailable | `test_invalid_events_do_not_make_a_stream_fresh` |
| H | duplicate refreshes freshness | `test_a_duplicate_cannot_refresh_a_stale_analysis` |
| I | heartbeat refreshes a candle | `test_polling_records_staleness_and_changes_no_candle` |
| J | heartbeat advances the cursor | `test_a_heartbeat_is_transport_only` |
| K1 | browser accepts a skipped cursor | "a skipped notification triggers a resync from the snapshot" |
| K2 | server resumes an unretained cursor | `test_an_unusable_cursor_is_told_to_resync` |
| L | old generation paints the new session | "ignores an older stream generation" |
| M | overflow without resync | `test_the_stream_ends_after_an_overflow_resync` |
| N | a reader leaving cancels the session | `test_the_last_reader_leaving_keeps_the_session_running` |
| O | cancel leaves the subscription open | `test_a_cancelled_session_says_so_and_closes_its_subscription` |
| P | session cap bypassed (both guards) | `test_registration_beyond_capacity_is_refused` |
| Q | ambiguous APP_ENV enables live | `test_only_an_explicit_opt_in_outside_production_composes_it[None-None-False]` |
| R | live enables fixture metadata | `test_the_production_metadata_boundary_is_unchanged` (PostgreSQL) |
| S | analysis on every candle | `test_no_event_runs_an_analysis` |
| T | live imports Paper trading | import contract "The live API can stream and analyse but cannot trade or narrate" |
| U | exception message logged | `test_an_undescribed_source_failure_is_typed_and_leaks_nothing` |
| V | Phase 12 refusal bypassed | `test_the_production_metadata_boundary_is_unchanged` (PostgreSQL) |

### 12.5 Performance (local mock, not exchange-feed throughput)

| Measurement | Result |
| --- | --- |
| Create session via nginx | median 31.0 ms, p95 33.0 ms |
| First SSE frame after opening (retained entries replayed) | 18.8 ms |
| Snapshot | median 2.3 ms, p95 26.8 ms |
| Timeline page (100) | median 2.5 ms, p95 23.3 ms |
| Analysis (cold) | 8.2 ms |
| Cancel | 2.6 ms |
| Ingest and fan-out per event (1 / 4 / 16 readers) | 36.5 / 47.2 / 37.5 µs |
| A slow reader over 3,000 events | 1 overflow, then closed; the fast reader got all 3,004 with 0 overflows; book capped at 2,500; timeline at 500; about 2.4 MiB traced |
| Maximum load (8 sessions × 2,500 candles, 16 readers) | 18.4 MiB traced; shutdown 0.6 ms; 0 orphan tasks |

### 12.6 Gates and counts

| Gate | Result |
| --- | --- |
| ruff check · ruff format --check | clean · 448 files formatted |
| mypy linux / win32 | clean, 440 files each |
| lint-imports | 35 kept, 0 broken |
| pytest against PostgreSQL | **4,157 passed, 0 skipped** (4,096 + 61 new live unit tests) |
| Frontend | `npm ci --dry-run` ok · vitest **448 passed** in 22 files · typecheck, lint, prettier clean · build ok |
| Docker | config valid · both images built · 3/3 healthy · migration at head · `/api/health` 200 · served `index.html`, JS and CSS are byte-identical (SHA-256) to the tested `dist` |

The stack was left running in its default configuration, where live is
DISABLED.

### 12.7 Limitations that remain

- There is no authentication. The loopback binding and browser checks are
  not user isolation, and the in-process caps are not distributed limits.
- Any real session break in a dataset still blocks analysis as a
  TEMPORAL_GAP.
- Sessions are ephemeral.
- There are no market-setup alerts.
- Resume is bounded by the 500 retained entries and the 64-slot queue.

Phase 14 has not begun.
