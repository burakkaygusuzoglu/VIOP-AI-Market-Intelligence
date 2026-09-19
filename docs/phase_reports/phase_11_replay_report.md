# Phase 11 — Deterministic interactive market replay

Status: **IMPLEMENTED**. Awaiting human approval; Phase 12 not started.

Replay answers a question the rest of the system could not: *what did this look
like at the time, and what would I have done?* It does that without acquiring a
second opinion about anything. There is no replay indicator, no replay fill, no
replay P&L and no replay win rate. If a number appears on a replay screen, some
earlier phase computed it.

---

## 1. What was built

| Piece | Where | What it owns |
| --- | --- | --- |
| Availability rule | `app/domain/replay/availability.py` | `coverage_end = open_time + duration`; `available = coverage_end <= as_of` |
| Session cursor | `app/domain/replay/session.py` | forward-only `step`, bounded `advance`, `END_OF_DATASET` |
| Use cases | `app/application/replay/service.py` | ingest, step, feed observations, analyse, open a position, measure |
| Storage port | `app/application/replay/ports.py` | `ReplayStore` — datasets, sessions, links |
| Storage adapter | `app/adapters/persistence/replay_store.py` | bounded candle reads, conditional cursor update |
| Schema | `alembic/versions/0004_replay.py` | four tables, a candle-immutability trigger |
| HTTP surface | `app/api/routes/replay.py` | nine operations, `extra="forbid"` throughout |
| Workspace | `frontend/src/screens/Replay.tsx` | session list, chart, controls, analysis, positions, performance |

Four tables and **no financial record among them**: the money a replay makes
stays in the Phase 9 ledger, where one engine owns it.

## 2. The invariant

> At replay market time **T**, no consumer may see market information whose
> coverage ends after **T**.

The rule lives in one place and is expressed as SQL at the storage boundary:

```python
statement.where(ReplayCandleRow.open_time <= until - timedelta(minutes=tf.minutes))
```

An unrevealed candle is therefore never loaded. It cannot be hidden later, it
cannot reach a serialiser, and it cannot sit in a browser's memory behind a CSS
rule. The proofs put a candle with `high = 999999999` in the dataset and require
it to be absent from the store read, the chart window, the analysis prefix and
the whole HTTP payload — and then require it to appear once the replay reaches
it, so absence is the rule working rather than the fixture failing.

## 3. Dataset identity and immutability

`RD-<32 hex>` is a sha256 over **one canonical JSON document**, not over
concatenated fields:

```json
{"symbol": "<trimmed symbol>",
 "timeframes": [{"timeframe": "5M",
                 "rows": [["<open time, UTC ISO-8601>",
                           "<open>", "<high>", "<low>", "<close>", "<volume>"]]}]}
```

Committed to, in exactly this shape:

| In the identity | Not in the identity |
| --- | --- |
| the symbol, trimmed | the database row id |
| each timeframe label, partitions sorted by label | `created_at` / `updated_at` |
| every candle's market instant, normalised to UTC | the uploaded file name |
| every OHLCV amount, `Decimal.normalize`d | the order timeframes were supplied in |
| the number of rows in each partition (the list length) | decimal spelling (`100` = `100.00`) |
| | the offset a timestamp was written in |

**Why a document rather than a byte stream.** The first version fed the hash
the symbol, then each timeframe label, then each row. Concatenation hides its
own boundaries: a crafted symbol carrying the bytes of a timeframe label and a
row would have produced the same digest as a genuinely different two-timeframe
dataset. JSON quotes and delimits every value, so each part can be read only one
way. `test_no_symbol_can_absorb_a_timeframe_partition` writes that collision out
and requires the two ids to differ.

**Collision semantics.** A matching digest is supposed to mean identical market
data, and `save_dataset` *checks* that rather than assuming it: before reusing a
stored dataset it compares the stored symbol, row total and per-timeframe ranges
against what the request describes, and raises `DATASET_IDENTITY_CONFLICT`
(HTTP 409) on any disagreement. The branch is unreachable while the digest
commits to every identity fact — which is the point. It turns "the digest covers
everything" from an argument into a checked condition, so a future change that
narrowed the digest would be caught here instead of by serving one market's
candles under another market's name.

Phase 11 ships **two** migrations: `0004_replay` for the four tables, and
`0005_replay_command_target` for the column the retry semantics needed. A second
migration rather than an edit to the first, because `0004` had already been
applied — to the development database and inside the running container — and a
database stamped at a revision never re-runs it. Amending it left those
databases without the column while Alembic reported them up to date, which is
exactly what happened when the change was first written into `0004`. Applied is
history, even before release.

`replay_candles` carries `replay_candles_no_update_or_delete`, a trigger that
raises on every UPDATE and DELETE. The dataset row is `ON DELETE RESTRICT` from
both the candles and the sessions, so a dataset a session walked cannot be
removed underneath it.

## 4. The virtual clock

`ReplayCursor.as_of` is the only market clock a replay has. The process clock is
injected and used for audit stamps only — when a session was created, when a
link was recorded. `tests/unit/replay/test_engine_reuse.py` scans the replay
packages and the replay API modules and refuses any direct clock read, because a
rule about behaviour is only as good as the seam it is observed through: a
module that reads `datetime.now()` has no seam at all.

Reopening a session months later returns the same cursor. A test freezes two
different wall clocks around the same session and requires `as_of` to be equal.

## 5. Warm-up and the replay start

Normalisation is **one-directional**: a typed moment is snapped to the driver
candle boundary at or before it, never forward. An effective start later than
the moment a person asked for would reveal candles they did not ask to see, so
the effective `replay_as_of` is always `<=` the requested `replay_start`.

| Requested | Effective `replay_as_of` |
| --- | --- |
| exactly on a boundary | that boundary |
| inside a candle | the previous boundary |
| between boundaries | the previous boundary |
| before the first candle closes | refused, `REPLAY_START_BEFORE_DATA` |
| after the last candle closes | refused, `REPLAY_START_AFTER_DATA` |

Normalisation is **disclosed, not silent**: the session keeps `plan.replay_start`
as the moment that was asked for and reports `cursor.replay_as_of` as what it
resolved to, so a client can see the two differ without inferring it.

Everything that had finished by the effective start is warm-up history and is
legitimately available — that is what gives the indicators something to read.
Nothing whose coverage ends after it is, which a test asserts across every
timeframe of a snapped session.

## 6. Stepping

One step reveals exactly one driver candle and moves `as_of` to that candle's
coverage end. `advance(n)` is implemented as repeated `step` — deliberately, not
as a jump — because the intermediate boundaries are what the paper engine needs
in order to see every bar in between. The equivalence is proven twice: on the
cursor, and end to end by running one session with `advance(12)` and another
with twelve single steps and comparing their ledgers.

The end of a dataset is `END_OF_DATASET`, a stated condition. Nothing repeats the
last candle and nothing is fabricated past it.

Replay is forward only. Reversing it would mean reversing paper fills, ledger
events and journal writes a person may already have acted on; to see an earlier
moment again, start another session over the same immutable dataset.

## 7. What a half-finished step leaves behind

A step is **not one transaction**, and the report does not claim it is. It
writes to the Phase 9 ledger one position at a time, each in its own unit of
work, and then writes the cursor. Those are different stores reached through
different ports; wrapping them in one transaction would mean giving replay a
transaction that spans the paper engine, which is exactly the coupling the
ports exist to prevent.

So the design is **recoverable rather than atomic**, and it is stated in three
rules, each with failure-injection tests behind it:

1. **Deliver, then commit.** A cursor that moved before delivery would leave a
   bar undeliverable for ever. A cursor that lags a delivered bar is corrected
   by the retry, because `apply_observation` is *idempotent for a bar already
   applied with identical prices* — it returns the position unchanged — and the
   dataset is immutable, so the prices cannot differ.
2. **One boundary, one commit.** An advance of ten is ten committed steps. A
   failure leaves the session at the last **completed** step, never inside one.
3. **A command records where it was going.** The key and the revealed count the
   command is working towards are written on every boundary it commits, so a
   retry can tell a finished command from one that stopped part way - and
   completes exactly the remainder.

   This was wrong when first written. The key was stored only by the final
   boundary, so a retry of `advance(6)` after two committed boundaries advanced
   six *more*, leaving the session eight candles on from a request for six.
   `command_target_revealed` (migration `0005`) fixed it; four parametrised
   cases and an HTTP-level test now require the retried cursor to land exactly
   where the original command aimed.

**Exactly one refusal is skipped.** A position in a terminal state raises
`INVALID_TRANSITION` and genuinely has nothing to learn. Every other refusal —
out of order, conflicting prices, a bar that had not closed, the wrong
timeframe — means the bar was **not** delivered, and the step fails with
`OBSERVATION_NOT_DELIVERED` rather than moving the cursor past it. An earlier
version swallowed any `REFUSED` or `INVALID`, which would have let the cursor
claim a candle a position never received.

**Where the failure was injected**, and what the tests require afterwards:

| Injection point | After the failure | After the retry |
| --- | --- | --- |
| before the first position | cursor unmoved | every position has the bar once |
| after the first position | cursor unmoved | every position has the bar once |
| mid-way through seven positions | cursor unmoved | every position has the bar once |
| after every position, before the cursor write | cursor unmoved | every position has the bar once, version +1 |
| untyped fault (dropped connection) | cursor unmoved | — |

**The residual window, measured.** Deliver-then-commit means a failure can leave
positions holding the boundary the cursor has not yet reached. That window is
**at most one bar**, a test measures it, and the retry closes it. The invariant
that matters is untouched: nothing was delivered beyond the boundary the step
was authorised to reveal.

**A failed advance says where it got to.** The refusal carries `this advance
completed N of M steps`, and a test checks the claim against the stored cursor —
a client that asked for ten steps can tell whether it moved none of them or
nine without re-reading first.

**The observation window is checked, not filtered.** The candle read for a
boundary is bounded at both ends in SQL, so it should return exactly the bar
that boundary revealed. The service asserts that rather than quietly filtering:
a filter would hide a widened query, and a widened query is how a bar a step had
no authority to deliver would reach a position (`UNBOUNDED_OBSERVATION_WINDOW`).

## 8. Persistence, idempotency, concurrency

* **Persistent.** Sessions live in PostgreSQL; closing the browser loses nothing.
* **Create idempotency.** The same `Idempotency-Key` with the same request
  returns the same session (HTTP 200, `idempotent_replay: true`). The same key
  with a different request is `IDEMPOTENCY_CONFLICT` (409).
* **Step idempotency.** A retried advance carrying the same key returns the
  cursor that command already produced, and reveals nothing further.
* **Concurrency.** The cursor update is conditional on the version it read
  (`WHERE id = :id AND version = :expected`). Two tabs stepping at once mean one
  applies and the other is told `VERSION_CONFLICT` (409) — never a two-candle
  jump.

## 9. Engine reuse, proven rather than asserted

`tests/integration/test_replay_parity.py` builds the same input twice: once
through replay, once through the ordinary path, reconstructed from the uploaded
CSV **without touching replay's own helpers**. A parity test that serialised the
prefix with the same function replay uses would only prove that one function is
consistent with itself.

| Engine | Comparison | Result |
| --- | --- | --- |
| Phase 8 analysis | replay analysis vs `run_analysis` over the same prefix | identical |
| Phase 9 paper | replay position vs a direct position given the same bars in the same order | identical ledger, state and P&L |
| Phase 10 performance | session metrics vs `PerformanceService` over the same position ids | identical |

Each has a control that requires the two to **differ** when the input differs,
so two identically broken paths cannot pass.

Phase 10 grew one field for this: `OutcomeFilters.position_ids`. A session
narrows the existing engine; it does not get one of its own.

## 10. Product metadata trust boundary

An uploaded CSV symbol establishes nothing. Production composes no contract
metadata provider, so opening a position inside a replay is refused with
`PRODUCT_METADATA_UNAVAILABLE` — verified in the container stack, with no
override in place. Test-only verified metadata enters through explicit test
composition and through no request, query parameter, environment variable or
frontend flag.

The session response claims no product facts: no multiplier, tick size, point
value, margin or expiry, and no `VERIFIED_CURRENT_FACT` anywhere.

## 11. Session isolation and observation routing

**Membership is established by creation, and by nothing else.** There is no
surface that attaches an existing position to a session: no request model has a
field naming one, and the only routes that take a `position_id` take it in the
*path*, carry no request body, and act on a position the session already owns.
`replay_position_links` is keyed by the **position**, so the database refuses a
second owner outright.

| Case | Result |
| --- | --- |
| an ordinary Phase 9 position | belongs to no session; invisible to every session's performance |
| acting on it through a replay | `POSITION_NOT_IN_SESSION` (404) |
| a position created in session A | linked to A, and only A |
| acting on A's position from session B | `POSITION_NOT_IN_SESSION` for close, breakeven and cancel |
| inserting a second link row | refused by the primary key |

**The session supplies the trade's identity.** `symbol`, `timeframe` and
`decision_time` come from the session — the dataset's symbol, the driver
timeframe, and the cursor's `replay_as_of` — and the request body has no field
for any of them. A client cannot open a position in one instrument's replay and
name another.

**Routing follows the position's own timeframe.** A replay position always
carries the driver timeframe, and a step delivers driver bars, so the two match
by construction: with a 5M driver there is no 15M position to mis-feed. The
Phase 9 rule stands behind that as an independent guard — `_validate_bar`
refuses a bar whose timeframe is not the position's, and the Phase 1 validator
refuses a 5M series presented as 15M before the engine is even reached. Both
refusals are tested directly.

Higher timeframes are **never resampled or aggregated**. A 1H candle is
revealed when the 1H candle the person uploaded reaches its coverage end, and
if a dataset supplies 5M but not 1H then 1H is simply absent.

**Simultaneous boundaries.** At the hour a 5M, a 15M and a 1H candle all
finish. Each timeframe's revealed count advances by exactly its own candle, and
candle order comes from `ORDER BY open_time` in SQL rather than from whatever
the table returns, so two reads of the same window are identical.

## 12. Bounds

| Collection | Bound | Why |
| --- | --- | --- |
| Rows per timeframe | 2 500 | the Phase 8 analytical ceiling |
| Rows per dataset | 6 000 | total ingest cost |
| Timeframes per dataset | 4 | the four the analysis stack has roles for |
| Chart candles | 400, newest first | the Phase 8 display policy |
| Advance steps | 50 | each step can feed observations to open positions |
| Positions per session | 50 | every open position is fed every revealed bar |
| Session list page | 25 | |
| Upload body | 1 MiB per timeframe | bounded before parsing |

A bounded chart **says so**: `revealed`, `dataset_total`, `window_limit` and
`truncated` are all in the response, and the screen reads "900 revealed, 400
drawn". Nothing authoritative is silently truncated.

## 13. Measurements

Through the real HTTP stack against real PostgreSQL. SQL statements counted with
an engine-level listener. Re-measured after the closeout, because committing
each boundary separately changed the cost of an advance.

| Dataset | Step | ms | queries | bytes |
| --- | --- | ---: | ---: | ---: |
| 100 driver candles | create session | 117 | 14 | 7 353 |
| | single step | 35 | 12 | 7 590 |
| | advance 10 | 104 | 39 | 9 008 |
| | advance 10, one open position | 243 | 109 | 10 204 |
| | session detail | 24 | 7 | 8 643 |
| | analysis at replay time | 29 | 5 | 67 884 |
| | session performance | 21 | 8 | 6 498 |
| 1 000 driver candles | create session | 138 | 14 | 48 160 |
| | single step | 41 | 12 | 48 279 |
| | advance 10 | 115 | 39 | 48 532 |
| | advance 10, one open position | 230 | 109 | 48 561 |
| | session detail | 25 | 7 | 48 167 |
| | analysis at replay time | 87 | 5 | 177 433 |
| | session performance | 20 | 8 | 6 498 |
| 2 500 driver candles (bound) | create session | 370 | 14 | 48 165 |
| | single step | 67 | 12 | 48 284 |
| | advance 10 | 164 | 39 | 48 537 |
| | advance 10, one open position | 258 | 109 | 48 561 |
| | session detail | 33 | 7 | 48 172 |
| | analysis at replay time | 213 | 5 | 242 853 |
| | session performance | 19 | 8 | 6 499 |

Query counts are **flat across dataset size** — 12 for a step, 39 for an advance
of ten, 7 for a detail, 8 for performance — so there is no N+1 and no O(N²) over
candles. Response size flattens at the 400-candle window (48 KB at both 1 000
and 2 500 rows), which is the bound doing its job.

**What the recovery design costs, measured.** Committing each boundary
separately took an advance of ten from 12 queries to 39, and from 19 to 109 with
one open position — the extra are that position's own ledger read and write, one
per bar, which is what makes a partial failure recoverable to a single boundary.
It is linear in boundaries × positions and bounded by the advance and
positions-per-session limits, never in candles.

**One fix came out of measuring it.** Feeding a boundary was fetching the whole
revealed prefix to find one candle, which showed as a step costing four times
more at the row bound than at a hundred rows. `ReplayStore.candles` now takes a
lower bound as well as the availability bound, so the query returns the one bar;
the advance at the bound fell from 469 ms to 258 ms and is now flat across
dataset size.

## 14. Frontend

A focused workspace: session list and create, a chart of revealed candles only,
Step / advance N / Play-Pause / speed, Analyse, linked positions, session
performance, and Beginner/Pro.

* **No future data reaches the browser.** The screen contains no filtering step,
  and must not: a bar that had to be hidden would already be in the page.
* **Play is a scheduler.** It issues the same single step on a timer. Speed
  changes the delay between commands and is not sent anywhere — the request body
  is exactly `{steps, expected_version}`, asserted by key count.
* **A late answer cannot move the clock backwards.** A response is accepted only
  if its cursor version is newer than the one on screen.
* **Beginner and Pro render the same replay**; Pro adds audit facts (version,
  dataset id, start), not different numbers.

Accessibility was **measured in a real browser**, not argued from CSS. A
headless Chromium (the Chrome already installed on the machine) was driven over
the DevTools Protocol against the container stack through nginx. No dependency
was added: the harness lives outside the repository and uses the `websockets`
package the backend venv already carries.

**68 / 68 checks passed** at 1280 / 768 / 390 / 320 px. Each viewport was
measured for:

| Measured in the live page | How |
| --- | --- |
| no horizontal page overflow | `documentElement.scrollWidth` vs `clientWidth` — equal at every width |
| Step reachable by keyboard | Tab until `document.activeElement` is the control |
| focus is visible | computed `outline` on the focused control: `solid 2px` |
| Step operable by keyboard | Enter **and** Space each advance the rendered replay clock |
| Advance control focusable | both step buttons have a non-negative `tabIndex` |
| Play/Pause | one control present; clicking Play renders Pause |
| session selection | opening a listed session renders its workspace |
| timeframe switching | `aria-selected` moves to the chosen tab |
| analysis action | Analyse renders an analysis stamped with the replay moment |
| chart text alternative | every `<svg>` carries a `<title>` |
| counts in words | "N mum açıklandı, M mum veri kümesinde var" |
| provenance visible | simulated fills and disabled execution stated on screen |
| named regions | five sections with accessible names |

At desktop width, on a session already at the end of its dataset: the end is
announced through `role="status"`, and Step and Play are both disabled.
Beginner and Pro render the same replay clock.

One "failure" in the first run was the harness, not the product: CDP
`rawKeyDown` does not perform a control's default activation, so the probe was
not really pressing Enter. Diagnosed by comparing a scripted `.click()` against
both key dispatch forms, then fixed — the button had always been operable.

## 15. Mutation probes

Thirty probes. Each broke one guarantee, ran the tests meant to notice, and the
file was restored byte-identically — verified by sha256 across all 442 source
files after the full run.

**30 / 30 detected** (A–T from the build, U–AD from the closeout), with all
442 source files restored byte-identically after the full run.

| Probe | What it broke |
| --- | --- |
| U | the dataset digest ignores the symbol |
| V | the dataset digest ignores the timeframe partition |
| W | the cursor commits although a linked position failed |
| X | a retry after partial delivery re-feeds from the wrong boundary |
| Y | an ordinary paper position can be attached to a replay |
| Z | a replay A position can be acted on from replay B |
| AA | a replay position takes a symbol other than the dataset's |
| AB | a driver bar is fed to a position of another timeframe |
| AC | the replay start snaps forward, revealing more than was asked for |
| AD | a partial advance reports a cursor beyond the last completed step |

Five findings came out of probing rather than from the code review:

* **L** (build) passed for the wrong reason — a forged `cursor` field was
  refused only because the probe sent an integer. Fixed by pinning each request
  model's field set exactly.
* **F** (build) and **G**, **O** (closeout) never mutated anything: their
  anchors no longer matched the code. Re-pointed, then detected.
* **AD** was first written as a no-op — `revealed` and `boundaries` can never
  differ — so the guarantee was made observable instead: a failed advance now
  reports how many steps it completed, and the probe flips that number.
* Hand-checking **R** found the advance request had two shapes and only one was
  exercised; `expectedVersion` is now required in the client, leaving one shape.
* **W** is the probe that justifies the narrowed refusal allow-list: with the
  old blanket `continue`, a position that genuinely refused a bar was silently
  skipped and the cursor advanced anyway.

## 16. Quality gates

| Gate | Result |
| --- | --- |
| `ruff check .` | pass |
| `ruff format --check .` | pass (375 files) |
| `mypy --platform linux` | pass (368 files) |
| `mypy --platform win32` | pass (368 files) |
| `lint-imports` | 28 contracts kept, 0 broken |
| `pytest` (real PostgreSQL) | 3 372 passed, 0 skipped |
| `vitest run` | 377 passed |
| `npm run typecheck` / `lint` / `prettier --check` | pass |
| `npm run build` | pass |
| `docker compose config -q` | pass |
| Docker + nginx end-to-end | 24 / 24 checks |
| Adversarial cases against the running stack | 35 / 35 typed refusals |
| Real-browser accessibility (Chromium, 4 viewports) | 68 / 68 checks |

Three new import contracts (26–28): the replay domain knows only market time;
nothing beneath replay depends on it; the replay application layer orchestrates
engines and computes nothing (direct imports only — reaching them *indirectly*
is the point).

Served frontend assets are byte-identical to the tested build
(`index-Cxbnq-WT.js`, sha256 `831e5a47…4759`).

## 17. Phase boundary

Phase 12 has not begun. Mechanically absent from the replay packages and the
replay API: backtest, walk-forward, Monte Carlo, optimisation, parameter sweep,
shadow mode, strategy runner, WebSocket/SSE, live feed, broker, Midas, order
placement. Execution remains disabled (master spec §120).

## 18. Known limitations

* **A 1D candle is "finished" 24 hours after it opens.** The duration comes from
  the timeframe, exactly as Phase 9 derives it. That is a conservative
  convention, not an exchange session calendar — this project has no verified
  session metadata and will not invent one.
* **Replay is forward only.** Rewinding is not a missing feature; it is a
  deliberate refusal (see §6).
* **Two uploads differing only in trailing zeros are one dataset**, and the
  first one stored wins. The values are numerically identical; the second
  upload's spelling is not preserved. Reuse is verified rather than assumed —
  a stored dataset whose identity facts disagreed with the request would be a
  `DATASET_IDENTITY_CONFLICT`, not a silent substitution.
* **A step is recoverable, not atomic.** Ledger writes and the cursor write are
  separate transactions in separate stores. A failure can leave a position
  holding one bar the cursor has not reached; a retry closes it, and the tests
  measure that the window is exactly one bar. Nothing beyond the authorised
  boundary is ever delivered.
* **Higher timeframes are not derived.** If a dataset supplies 5M but not 1H,
  the 1H timeframe is simply absent from the analysis rather than aggregated —
  building it would be this system inventing candles.
* **The Docker lifecycle proof stops at the position refusal**, because
  production composes no metadata provider — that refusal *is* the production
  behaviour, verified in the container. The fill-bearing half is a separate
  test: over the same HTTP surface and the same PostgreSQL, with an explicit
  test-only provider, a position opens, its entry fills, a target closes half of
  it, a manual close exits the rest, and the Phase 10 engine measures the
  result. The two are deliberately described as two tests, because they are.
* **Replay analysis can call the synthesis provider** when one is configured, on
  the same user-triggered path as `POST /api/analysis`. Nothing automatic runs
  one: no step, no page load and no timer.
