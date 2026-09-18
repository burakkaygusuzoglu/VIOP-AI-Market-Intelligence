# Phase 10 Report — Journal + Performance Intelligence

**Status: IMPLEMENTED, validated, awaiting review. Not committed.**

Phase 10 reads the Phase 9 paper ledger and answers how the simulated trades
went, without acquiring a second opinion about what happened. It adds no
financial calculation of its own: every amount it reports was written by the
simulation engine, and every metric is an aggregate of those amounts computed in
one pure module.

Baseline: `3c332bb Complete Phase 9 generic paper trading core` on `main`,
tracking `origin/main`, tree clean. Backend 2 821 passed / 0 skipped, frontend
309 passed, 22 import contracts, migration head `0002_paper_trading`, all three
containers healthy. No replay, backtest, live feed or broker capability existed,
and none does now.

After Phase 10 and its human-review closeout: backend **3 027 passed /
0 skipped**, frontend **345 passed**, **25** import contracts, migration head
`0003_paper_journal`.

## 1. Architecture

    adapters/performance/paper_source.py   folds the append-only ledger into
            │                              authoritative PositionOutcome records
            ▼
    application/performance                bounds the request, hands records to
            │  ports: PerformanceSource ·  the engine, owns the journal use cases
            │         JournalStore
            ▼
    domain/performance   populations · outcome basis · metrics · drawdown ·
                         streaks · breakdowns   (pure, stdlib + domain.common)
    domain/journal       what a person may write, and its bounds

The engine imports no simulator and no product: contract 23 forbids
`app.domain.paper`, `app.domain.futures` and `app.domain.risk`, so a future
replay or backtest can produce the same `PositionOutcome` records and reuse every
metric rather than duplicating one.

## 2. Authoritative analytics source

Phase 9 established that `paper_positions` is a projection, not financial
authority. Phase 10 reads the **ledger** and classifies every input:

| Input | Class | Where it comes from |
| --- | --- | --- |
| symbol, asset class, direction, quantity, timeframe, fee mode | A: append-only ledger fact | `POSITION_CREATED`, frozen at open |
| each fill's gross and fee | A | `TARGET_FILLED` · `STOP_FILLED` · `MANUAL_EXIT_FILLED` |
| realized gross, fees, net, terminal market time | A | `POSITION_CLOSED` |
| never entered | A | `ENTRY_REJECTED` · `POSITION_CANCELLED` |
| current mark of a still-open position | C: verified rebuild | a batched replay of the open positions' own ledgers, each checked against its stored row and refused on disagreement (§15) |
| anything from `paper_positions` columns | **D: never used** | — |

Bar observations are excluded from the read, so analytics cost does not follow
bar count. Proven: a folded record equals what a full verified rebuild produces;
corrupting every projection column (money, state, direction, quantity) changes
no reported number; and a position with 499 extra bars carries the same three
financial events.

## 3. One position is one trade, and realized money is counted separately

A position that exited through two targets and a stop produced three fills and
**one** sample. Fill counts are reported separately and never reach a trade
count, a win-rate denominator, a streak or an expectancy sample. Proven for the
prompt's LONG example and its SHORT mirror, in the pure engine and through the
database.

A position that has realized a partial exit and is *still open* is a separate
case, added in the closeout (§17): its realized money appears in
`realized_accounting`, and it contributes to no trade statistic.

## 4. Populations

| Population | Completed sample? | Counted as |
| --- | --- | --- |
| `CLOSED` | **yes** | the only trade sample |
| `OPEN`, `PARTIALLY_CLOSED`, `AMBIGUOUS_HALTED` | no | entered, still exposed |
| `PENDING_ENTRY`, `CANCELLED`, `REJECTED` | no | never entered |

Every response carries all seven counts plus `entered`, `open_exposure` and
`never_entered`, so a reader can see each denominator.

## 5. Outcome, PnL basis and fee coverage

Outcome is `WIN` / `LOSS` / `BREAKEVEN` on a stated basis; exact zero is
breakeven and stays in the denominator. The basis is chosen from the selection's
own fee coverage:

* every completed position modelled fees → `REALIZED_NET`;
* otherwise → `REALIZED_GROSS`, with a sentence naming the coverage.

A net *total* over a partly costed population is `PARTIAL_COVERAGE` with **no
value** — never the sum of the costed part, never with the unknown treated as
zero. Gross still covers every completed position, and the fees that were
modelled are reported with their own coverage. A user-defined fee of **0** is
modelled, and is a different state from `NOT_MODELLED`.

## 6. The metrics

| Metric | Definition | Unavailable when |
| --- | --- | --- |
| Win rate | wins ÷ completed entered positions, with numerator and denominator | nothing completed |
| Average win / average loss | means of the winning and losing amounts, as separate magnitudes | there are none of that kind |
| Profit factor | gains ÷ losses | no losses (no finite value — never `Infinity`); nothing completed |
| Expectancy | mean realized result per completed position, with sample size | nothing completed |
| Max drawdown | largest peak-to-trough fall of the cumulative realized curve, in simulated money | nothing completed |
| Unrealized (open) | mark-to-market of exposed positions, reported apart from realized | no open position, or a mark missing |

Deliberately unavailable, each with its reason in the response: percentage
drawdown (no capital timeline), realized R expectancy (risk budget, planned stop
distance and realized entry-to-stop distance are three different quantities;
choosing would be a guess), MAE and MFE (bar OHLC records no order inside a bar,
so excursions are not determinable without assuming an intrabar path), Sharpe,
Sortino and annualised return (no capital base, no return series, no sampling
convention). None of these is implemented as an approximation.

## 7. Time, ordering, filters

Three rules, one per population — never `created_at`, `updated_at` or request
time:

* **completed trades** belong to a range by the market time of their closing fill;
* **realized accounting** selects individual *fills* by their own market time;
* **open exposure and unrealized P&L** are as of each position's last observed
  bar and are never removed by a date range.

Ordering is `(terminal market time, position id)`, so equal timestamps still
order deterministically. The same parsed filters (range, direction, symbol,
timeframe, tag) drive the summary, the breakdowns, the timeline and the journal
list, and a test asserts they move together.

The curve is called **cumulative realized P&L**, not account equity, and nothing
from an open position is inserted into it.

## 8. Journal

One new table, `paper_journal_annotations`: a note (≤ 4 000 characters), up to
12 tags (≤ 32 characters, normalised, de-duplicated, sorted), a version and two
audit stamps. Both are `USER_AUTHORED` — a tag reading "breakout" records that a
person typed the word, not that the structure engine found one.

Concurrency is optimistic: a write carries the version it read and applies only
if the row is still at it; otherwise 409 with the current version. There is **no
edit history**: a note has one current value plus `created_at` / `updated_at`.
Nothing in the journal path can reach a fill, an amount, a state or a
provenance, and the request model has no field for one.

## 9. API and bounds

`GET /paper/performance`, `GET /paper/performance/breakdowns`,
`GET /paper/journal`, `GET /paper/journal/tags`,
`GET`/`PUT /paper/positions/{id}/journal`. Request models are `extra="forbid"`;
the only writable body carries a note, tags and the expected version.

Every metric travels as a status (`AVAILABLE` / `UNAVAILABLE` /
`PARTIAL_COVERAGE` / `NOT_IMPLEMENTED`) with an optional exact decimal string and
a human-readable reason — an unknown is never `0`, `""` or `false`.

Bounds: 2 000 positions per analysis (refused with its size rather than
answered partially), 2 000 timeline points, 50 journal rows per page, 100 tag
rows, 100 breakdown rows, 4 000-character notes, 12 tags. A breakdown or tag
list that exceeds its bound reports `total`, `returned`, `omitted` and
`is_complete`; headline totals always describe the whole population.

## 10. Frontend

A Performance workspace reached from a secondary dashboard control, with
Overview / Breakdowns / Journal tabs over one filter state. A persistent banner
says the figures are simulated and not exchange-verified. Beginner shows the
headline figures and plain-Turkish sentences; Pro adds basis, coverage, sample
counts, the unavailable-metric reasons, provenance and the raw breakdowns —
the same values, never different ones.

The journal panel separates "SİMÜLASYON KAYDI" from "SİZİN NOTUNUZ". Notes
render as text; there is no `dangerouslySetInnerHTML` anywhere in the app.

## 11. Verification

| Evidence | Result |
| --- | --- |
| Golden metrics (hand-derived: trade unit, populations, classification, fee coverage A–D, averages, profit factor edges, expectancy edges, drawdown curve, streak sequence W W L L L B W, breakdowns, determinism) | 52 passed |
| Journal rules (notes, tags, normalisation, hostile text, bounds) | 27 passed |
| API boundary (forged fields, filter validation, route inventory, bounds) | 45 passed |
| Ledger authority against real PostgreSQL (fold == verified rebuild, projection corruption, populations, fee coverage, filters, ordering, query count, bar independence) | 17 passed |
| Journal persistence (schema diff empty, write/read/clear, conflict, race, tags, filtering, no financial change) | 20 passed |
| HTTP performance and journal (real routes, real ledger) | 13 passed |
| Mutation probes A–T | **20/20 detected**, files restored byte-identically (SHA-256) |
| Runtime E2E (real uvicorn + PostgreSQL + production bundle in Chrome) | 42/42 |
| Phase 9 regressions (runtime lifecycle, nginx smoke) | 53/53, 26/26 |
| Phase 8 regressions (analysis E2E, adversarial) | 23/23, 16/16 |
| Frontend | 339 passed (19 files) |

**Found during validation.**

1. *A shared transport defect.* `getJson` never checked `response.ok`, so a
   typed refusal body (413 range too large, 503 unavailable) surfaced to the
   user as "response did not match the expected schema". Fixed so a refusal is
   reported with the sentence the backend wrote — while readiness, which answers
   503 with a *valid* health body, still parses.
2. *Three weak tests of my own*, exposed by probes that were not detected on the
   first run: a fee probe neutralised by the ledger's own empty-string encoding
   (the probe was made faithful instead of the test weakened), a breakdown test
   that never asserted the short row's own win and loss counts, and a journal
   test that checked the ledger but not the projection row, and only exercised
   the insert path. All three tests were strengthened; all three probes are now
   detected.

## 12. Performance

Measured on this machine against the PostgreSQL container, with positions
created through the real Phase 9 service:

| Completed positions | Summary | Breakdowns | Journal page | Summary body |
| --- | --- | --- | --- | --- |
| 100 | 14 ms | 12 ms | 8 ms | 18.8 KiB |
| 500 | 33 ms | 26 ms | 8 ms | 73.0 KiB |
| 1 000 | 49 ms | 47 ms | 10 ms | 140.9 KiB |

Adding a position carrying 499 more bars moved the summary to 77 ms — the cost
follows positions, not bars. A summary issues **three statements** whatever the
population: a count, the matching ids, and one read of their financial events. A
test asserts that number, so an N+1 cannot appear unnoticed.

## 13. Accessibility

Measured in a real browser against the production bundle at 1280, 390 and
320 px: no horizontal overflow at any width, every filter control labelled, the
timeline carries a row-by-row table beside the sparkline (which has a text
alternative naming its last value), tabs expose `role="tab"`/`aria-selected`,
the journal form announces saves through a status region and failures through an
alert, and win/loss meaning is carried by words, never colour alone. No WCAG
certification is claimed.

## 14. Quality gates

Backend: ruff check · ruff format (346 files) · mypy linux and win32 (341
files) · lint-imports 25 kept / 0 broken · pytest 2 995 passed, 0 skipped (18
pre-existing Python 3.14 asyncio deprecation warnings).
Frontend: `npm ci --dry-run` · vitest 339 passed · typecheck · lint · prettier ·
build. Docker: `config -q`, both images rebuilt, migration applied in the
container, all three services healthy, served assets identical to the tested
build.

## 15. Human-review closeout: four semantic proofs

Approval was withheld for four narrow questions. Measuring the behaviour first
found that **all four were real**, and each is now fixed and proved.

### Realized money on positions that have not finished

*Before:* a position that took one target and still held the rest reported
`realized_gross` as UNAVAILABLE — the +80 it had actually made was invisible.

*Now:* the ledger fold carries every fill (`RealizedFill`: amount, fee, market
time), and the response carries a `realized_accounting` block beside the trade
statistics: gross, known fees, net, fill count, and how many fills came from
completed and from still-open positions. Trade-level metrics are untouched — a
partial exit still increments no trade count, win-rate denominator, expectancy
sample or streak. Proved for LONG and SHORT, with fees modelled and not, for
mixed fill coverage, and for the case where the position later closes (the
earlier fill is counted once, not twice).

### A position's outcome cannot move with the filter

The aggregate basis still switches between gross and net as fee coverage
changes, and says so. But each completed position now reports `outcome_gross`
and `outcome_net` computed **from that position alone**. The proof uses the case
that exposes a leak — gross +5 with 16 of fees, a win before costs and a loss
after — and shows the two facts unchanged whether the position is selected
alone, beside a fee-unknown position, or through a filter.

### A bounded breakdown says it is bounded

*Before:* over 100 instruments the whole request was refused, and the tag list
truncated silently.

*Now:* every breakdown returns `rows`, `total`, `returned`, `omitted` and
`is_complete`, the UI prints "this list is incomplete" with the counts, and the
headline totals keep describing the entire population rather than the displayed
rows. Tag suggestions carry the same flag. Proved with a bound of one across two
directions, and with a tag bound smaller than the tags in use.

### A date range no longer hides what is open

*Before:* applying any range dropped every non-terminal position from the
selection — `open_exposure` fell from 1 to 0 while still being labelled current
exposure, and unrealized P&L became unavailable.

*Now:* three rules, stated in the response and the UI:

| Population | Selected by |
| --- | --- |
| completed trades | market time of the closing fill |
| realized accounting | market time of **each fill** |
| open exposure, unrealized | nothing — as of each position's last observed bar |

Proved with boundary timestamps (inclusive at both ends), a position whose fills
and close fall in different ranges, and a range in a different month.

### Open-position reads: the N+1 was real

Measured before: 3 / 5 / 23 / **103** SELECTs for 0 / 1 / 10 / 50 open
positions, 214 ms at fifty — one verified read per position.

Fixed by batching: the open positions' rows and their events are read in two
statements, replayed in memory, and each replay is checked against its stored
row (state, remaining, entry, stop, mark, realized gross, fees, net, unrealized,
bars, event count) before any number is used. A row that disagrees is refused,
exactly as Phase 9's own read refuses it — so performance was bought without
weakening authority.

Measured after: **5 SELECTs at every size**, 21 ms at fifty open positions.
Above 50 the unrealized total is reported unavailable rather than replaying an
unbounded number of ledgers.

| Open positions | Before | After |
| --- | --- | --- |
| 0 | 3 SELECTs, 8 ms | 3 SELECTs, 8 ms |
| 1 | 5 SELECTs, 12 ms | 5 SELECTs, 14 ms |
| 10 | 23 SELECTs, 51 ms | 5 SELECTs, 13 ms |
| 50 | 103 SELECTs, 214 ms | 5 SELECTs, 21 ms |

### Closeout evidence

| Evidence | Result |
| --- | --- |
| `tests/unit/performance/test_realized_accounting.py` | 19 passed |
| `tests/integration/test_performance_closeout.py` (real PostgreSQL) | 19 passed |
| Closeout mutation probes U–AC | **9/9 detected**, files restored byte-identically |
| Phase 10 probes A–T re-run | 20/20 detected |
| Phase 9 probes A–O / P–U | 15/15 · 7/7 |
| Runtime E2E (real uvicorn + PostgreSQL + production bundle) | 48/48 |
| Phase 9 lifecycle · nginx smoke | 53/53 · 26/26 |
| Phase 8 analysis E2E · adversarial | 23/23 · 16/16 |

**Three weak tests of my own** were exposed by probes that escaped on their
first run: a stability test whose position was a win on both bases, a breakdown
bound test that never exceeded the bound, and a fill-time test that exercised
the domain rather than the adapter. Each test was strengthened — and in the
last case the adapter turned out to be genuinely wrong, dropping a position
whose fills were in range but whose close was not. That is fixed too.

**Performance after the changes** (host, PostgreSQL container): 100 / 500 /
1 000 completed positions summarise in 13 / 47 / 91 ms; with 50 open positions
alongside 1 000 completed, 109 ms and 142 KiB in five statements. A position
carrying 499 extra bars leaves the summary at 103 ms.

## 16. Known limitations

1. No setup, regime, signal or AI-verdict performance: positions are
   `USER_CREATED` and no analysis snapshot is persisted. Stated in the response
   itself rather than left as a missing screen.
2. No MAE/MFE, no R multiple, no Sharpe/Sortino/CAGR, no percentage drawdown —
   each unavailable with its reason, not approximated.
3. The journal keeps no edit history, and a cleared note cannot be recovered.
4. An analysis covers at most 2 000 positions; a wider range is refused.
5. The timeline holds one point per completed position; at 1 000 positions the
   response is ~141 KiB. No downsampling is done, because an invented point is
   worse than a bounded refusal.
6. Unrealized totals need a verified replay of each open position, batched into
   two statements; above 50 open positions the total is reported unavailable.
7. Tag normalisation is `casefold`-based; two tags that differ only by Turkish
   dotted/dotless I may fold together.
8. No authentication or per-user scoping (single-user local application).
9. Performance is computed on demand with no cache; that keeps one source of
   truth, at the cost of recomputing on every request.

## 17. Phase boundary

No replay feature, backtest, shadow mode, live feed, WebSocket/SSE stream,
broker, Midas, order execution, equity/crypto/FX calculation exists. The only
new domains are `app/domain/performance` and `app/domain/journal`; the only new
table is `paper_journal_annotations`, and no table caches a metric. No
dependency was added. The master spec and Phase 0–9 reports are unchanged.
Nothing was committed or pushed. Phase 11 has not begun.
