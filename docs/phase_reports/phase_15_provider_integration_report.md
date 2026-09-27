# Phase 15 — External data providers

**STATUS: PHASE 15 — PROVIDER-INDEPENDENT FOUNDATION VALIDATED; EXTERNAL INTEGRATIONS PENDING**

Part 1 is the vendor-neutral provider foundation and the verified-data model.
Part 2A (sections 11-20) audits three Part 1 trust foundations and defines the
contracts for verified contract facts, session calendars, open interest, news
and market breadth. Part 2B (sections 21-28) makes contract-fact verification
durable and auditable, and exposes source status through a read-only API and
workspace. Part 2C (sections 29-37) validated all of it through real
PostgreSQL, Docker, nginx and real Chromium, and fixed what that found. All of
it is **uncommitted and unpushed** and has not been through CI. **No real
external provider is connected** (section 3), so Phase 15 as specified is not
complete; closing it is a human decision. Phase 16 is not started.

Baseline: HEAD `3a2e05d` "Complete Phase 14 shadow mode", `main` tracking
`origin/main`, tree clean. Backend 4,373 passing, 0 skipped, 40 import
contracts, migration head `0008_shadow_outcomes`. Frontend 484 passing.

---

## 1. The authoritative scope

Master spec, "PHASE 15 — EXTERNAL DATA PROVIDERS":

> Only after architecture is stable: evaluate legitimate providers for: live
> market data, contract metadata, open interest, news, market breadth.
> Integrate through adapters. Do not couple domain logic to one vendor.

Supporting requirements elsewhere in the spec: a vendor-independent market-data
layer (section D); `MarketDataProvider`, `LiveMarketDataProvider`,
`HistoricalMarketDataProvider`, `ContractMetadataProvider` and `NewsProvider`
interfaces (section 73); licensed WebSocket or streaming providers only, never
scraped broker UIs or unofficial endpoints; and section 118 - every mutable
exchange fact (multiplier, tick size, tick value, session hours, margins,
expiries, live-data availability) from an authoritative source, labelled
`VERIFIED_CURRENT_FACT` / `DEVELOPMENT_DEFAULT` / `TEST_FIXTURE` / `MOCK_DATA` /
`UNVERIFIED`, never guessed, and conflicts between sources recorded with the
more recent applicable official source preferred.

## 2. Scope reconciliation

The earlier planning note, "External Market and Metadata Providers", is
**narrower** than the spec, which also names **open interest, news and market
breadth**. That is not a conflict with Part 1 - the provider architecture and
verified-data model requested here are inside the spec's scope - but it means
Phase 15 is not finished when market data and metadata are. The remaining
categories are in section 16. No part of the spec was implemented in a way that
contradicts it, and the spec was not changed.

## 3. Provider feasibility

Researched from official and vendor sources (September 2026):

| Finding | Source |
| --- | --- |
| Borsa İstanbul disseminates real-time, delayed and end-of-day data through licensed data vendors; redistributing real-time data needs a signed Data Distribution Agreement. | [Borsa İstanbul — Data Dissemination](https://www.borsaistanbul.com/en/data/data-dissemination), [Data Distribution Agreement](https://www.borsaistanbul.com/en/data/data-dissemination/borsa-istanbul-data-distribution-agreement), [Data Vendors Directory](https://www.borsaistanbul.com/en/data/data-dissemination/data-vendors-directory) |
| VIOP has its own retransmission server with per-market sequence numbers, and 10-level depth. | [VIOP data dissemination FAQ](https://www.borsaistanbul.com/en/faq/technical-side-futures-options-market-viop-data-dissemination) |
| At least one vendor (dxFeed) offers BIST futures real-time, delayed, replay and historical data by API, with a purchased licence; trials are added to an existing production connection. | [dxFeed — Borsa Istanbul Futures](https://dxfeed.com/market-data/futures/borsa-istanbul-futures/), [dxFeed FAQ](https://kb.dxfeed.com/en/faq.html) |
| Contract specifications are official web pages and PDFs, not an API. | [VIOP Contract Specifications](https://www.borsaistanbul.com/en/markets/viop/contract-specifications) |
| Session hours are published on official pages and change by announcement. | [VIOP](https://www.borsaistanbul.com/en/markets/viop), [Trading Session Hours change](https://www.borsaistanbul.com/en/announcement/13376/borsa-istanbul-trading-session-hours-change) |

**Conclusion.** No real provider is accessible to this project: every
legitimate route needs a licence, an account and credentials it does not have.
A vendor adapter written now would target an API that cannot be exercised, and
would be a claim of integration without evidence, so none was written.
Specific contract values and session times appear on those pages; **none was
copied into this repository**, because a search summary or an unverified read
is exactly what section 118 forbids.

**What the user must do to go further:** obtain a licensed data subscription
(for example through a vendor in Borsa İstanbul's directory), confirm its
redistribution terms for this use, and place its credential in an untracked
`.env` as `MARKET_DATA_PROVIDER_TOKEN` - never in source or a chat transcript.
For verified metadata, either a licensed metadata feed or a documented
operator verification of the official specification pages, with a source
reference and date for each fact.

## 4. What Part 1 built

| Area | Where |
| --- | --- |
| Provenance vocabulary | `domain/live/events.py`: `REAL_EXCHANGE_LIVE`, `REAL_EXCHANGE_DELAYED`, `PROVIDER_HISTORICAL`, `UNVERIFIED_OR_USER_SUPPLIED`; currencies `DELAYED`, `CURRENT` |
| Capabilities and grants | `domain/sourcing/capability.py`: six data categories, deliveries, `ProviderDeclaration`, `LicenceGrant`, `authorize_provenance` |
| The gate | `application/live/session.py`: a session refuses a real-exchange label without an authoritative grant |
| Metadata among sources | `domain/sourcing/metadata.py`, `application/sourcing/metadata_sources.py` |
| Calendar | `domain/sourcing/calendar.py`, `application/ports/session_calendar.py`, `adapters/sourcing/unavailable_calendar.py` |
| Vendor-neutral candles | `application/sourcing/candles.py` |
| Bounds and retries | `domain/sourcing/limits.py` |
| Configuration | `core/config.py`, `.env.example` |
| API edge | `api/schemas/live_projection.py`: serializes simulated history only, refuses anything else |

Design and rationale: `docs/architecture.md`, "External data providers - the
foundation (Phase 15, part 1)".

## 5. Trust rules, as implemented

* **Provenance is granted, not declared.** A provider declaring a real-exchange
  label is refused at session construction unless a `LicenceGrant` names it,
  covers market data, matches the delivery, rests on a sourced, dated
  `VERIFIED_CURRENT_FACT`, and has not lapsed. No module constructs or passes a
  grant. A request body, a flag, a symbol or a fixture cannot produce one.
* **Currency follows provenance.** Only a granted real-time feed is `CURRENT`;
  a delayed feed is `DELAYED`; unknown origin and all history are `HISTORICAL`.
* **Metadata is a separate capability.** Connecting a price feed establishes no
  contract fact. `assess_contract_metadata` returns a record only if it is for
  the requested instrument, authoritative, sourced, dated, current and
  unexpired; disagreeing sources are resolved by recency with the conflict
  recorded, or refused when recency cannot decide.
* **No calendar is guessed.** The composed calendar always answers
  `UNAVAILABLE`; an in-session answer cannot be built without a verified source.
* **External candles skip nothing.** They pass the same validation and
  `CandleBook`; a provider's publication time cannot become market or receive
  time; floats are refused.
* **Secrets.** The provider token is a `SecretStr`, redacted from logs, never
  echoed, and refused when no provider is configured. Source failures are
  logged by exception type only.

## 6. Phase 13 and 14 compatibility

Phase 13's streaming machinery is unchanged and is what external candles pass
through. Two Phase 13 guards moved with this boundary rather than being
deleted: "`StreamProvenance` has one member" became "every provider the build
ships returns the simulated label" plus "real labels need a grant"; and "every
provenance is `HISTORICAL`" became "every label attainable without a grant is
`HISTORICAL`, and only `REAL_EXCHANGE_LIVE` is `CURRENT`". Phase 14 journals are
untouched: nothing rewrites stored provenance, and the Shadow API still accepts
only simulated history.

## 7. Tests

75 added; 74 in `tests/unit/sourcing/` and one net in the moved Phase 13 guard.

| File | What it pins |
| --- | --- |
| `test_provenance_grant.py` | Real labels refused without a grant at a real `LiveSession`; nine defective grants each refused with their code (other provider, wrong category, delivery mismatch, `TEST_FIXTURE`, `UNVERIFIED`, `DEVELOPMENT_DEFAULT`, unsourced, undated, lapsed); no silent downgrade; delayed never current; no module holds a grant; the API refuses non-simulated labels |
| `test_metadata_assessment.py` | Missing, wrong contract, four non-authoritative statuses, unsourced, undated, stale, expired, same-time conflict refused; newer source chosen with the conflict recorded; an unverified source cannot outvote a verified one; unusable records reach consumers as `None`; no symbol inference; a failing source logs its type only |
| `test_calendar_limits_settings.py` | No calendar invented; a session answer needs a verified source; retries capped and terminal on refusals; bounds positive; unknown provider and a token without a provider refuse to start; the token is redacted from logs |
| `test_external_stream.py` | Through a real `LiveSession`: forming never confirmed, gaps, duplicates, quarantined corrections, sequence disagreement, disconnects; receive time is the session's; provider time never leaks; floats refused |

Every provider in these tests is a labelled test double, and every grant is
built by the test suite with a source that says so. **No real-provider end to
end was run, and none is claimed.**

## 8. Validation

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | Passed / 496 files formatted |
| `mypy --platform linux` / `win32` | No issues, 486 source files each |
| `lint-imports` | **42 kept, 0 broken** (two added: the sourcing domain is vendor-neutral; provider orchestration speaks no network protocol and cannot trade) |
| `pytest`, real PostgreSQL | **4,448 passed, 0 skipped** (4,448 collected) - *two of these were not tests; see section 11* |
| Frontend | Unchanged by Part 1; `npx vitest run` 484 passed, run fresh |
| Migrations | None added; head `0008_shadow_outcomes` |

One gate run was invalid and is not counted: a background run whose steps
were piped through `tail` (masking a mypy failure) continued into its own
pytest concurrently with a foreground one, and both reported spurious failures
from shared-database interference. The sequential run above is the result.

## 9. Known limitations

* No real provider, and therefore no real-provider data, metadata or calendar.
* The live and shadow APIs serialize only simulated history; widening them
  needs a reviewed real provider first.
* Metadata age limits and grant validity are enforced but not yet configured
  from settings, because nothing supplies them.
* Open interest, news and market breadth have categories and grant semantics
  but no ports or adapters yet.

## 10. Remaining Phase 15 work

1. With a licence: a real market-data adapter behind `LiveMarketDataProvider`,
   its grant configured outside source, its protocol mapped to `VendorCandle`,
   retries and bounds applied, and a real sandbox end-to-end.
2. Verified metadata: a licensed metadata source or a documented operator
   verification workflow feeding `VerifiedMetadataSources`.
3. A verified session calendar source behind `SessionCalendarProvider`.
4. Evaluation, ports and (where licensed) adapters for open interest, news
   (`NewsProvider`, section 71) and market breadth (section 35).
5. Widening the live and shadow APIs, and the frontend, for real provenance,
   delayed data and calendar state - after a real provider exists.

---

# Part 2A — External intelligence sources and verified fact workflows

Baseline on resuming: HEAD `3a2e05d`, `main` tracking `origin/main`, Part 1
uncommitted in the tree as reported above, migration head
`0008_shadow_outcomes`. Design: `docs/architecture.md`, "External
intelligence and verified facts (Phase 15, part 2A)".

## 11. Part 1 preserved, and one count corrected

Part 1's provenance gate, grant semantics, bounds, settings and candle path
are unchanged except for the three audit corrections in section 12. One
Part 1 defect was found that is not a trust issue: the test helper
`test_grant` was imported into two test modules, and pytest collected it as a
test in each. Part 1's 4,448 therefore included **two non-tests**; the real
count was 4,446. The helper is renamed `fixture_grant`; nothing else changed.

## 12. Audits of the Part 1 foundations

| Audit | Finding | Correction |
| --- | --- | --- |
| A. Licence grants | A `LicenceGrant` is a claim record any code can construct. Protection was compositional and correct, but "configured", "connected" and "licensed" were not separately visible. | `CapabilityStatus` and staged `category_status` per category; a production matrix of all `NOT_CONFIGURED`; a simulated source is licensed for nothing; tests that a forged grant cannot enter through settings, the API, or any application module. |
| B. Metadata conflicts | **Defect.** Recency was judged by verification time, so a recently checked obsolete value could defeat an older but still applicable official one. | `ContractSourceRecord` (authority, reference, effective period, verification, corrections) and `assess_fact_records`, which chooses by applicable period and authority; verification time decides only staleness. Plain records without a period now refuse a disagreement. |
| C. Publication time | `VendorCandle.provider_time` was dropped, losing the evidence to audit a delayed feed's stated delay. | Optional validated `published_at` on `RawCandleEvent`, `Observation` and `StreamRecord`; excluded from duplicate detection; never ordering, freshness or receive time. |

## 13. What Part 2A built

| Area | Where |
| --- | --- |
| Capability stages | `domain/sourcing/capability.py`, `application/sourcing/capabilities.py` |
| Contract facts by period and authority | `domain/sourcing/facts.py`; `VerifiedContractFacts` in `application/sourcing/intelligence.py` |
| Operator review boundary and audit trail | `domain/sourcing/review.py`, `application/sourcing/fact_review.py`, `ContractReviewLog` port |
| Verified calendar days | `domain/sourcing/calendar.py`; `RecordedSessionCalendar` |
| Open interest | `domain/sourcing/open_interest.py`; `OpenInterestReader` |
| News | `domain/sourcing/news.py`; `NewsReader` |
| Market breadth | `domain/sourcing/breadth.py`; `BreadthReader` |
| Ports | `application/ports/intelligence.py` |
| Bounds | `domain/sourcing/limits.py`: fact records, calendar days, open-interest points, articles, universe size |
| Publication time | `domain/live/events.py`, `domain/live/validation.py`, `application/live/records.py`, `application/live/session.py`, `application/sourcing/candles.py` |

**None of the new ports has an implementation, and nothing composes them.**
No licensed source exists to put behind them. An interface is not an
integration, and none is described as one.

## 14. Time semantics

| Clock | Meaning | Used for |
| --- | --- | --- |
| Market event time | When the market event happened (candle interval end, OI measurement, breadth observation) | Ordering and confirmation |
| Provider publication time | When the source says it published | Research availability for OI; audit for candles |
| System receive time | When this system took the item in (session clock) | Live availability; freshness |
| Research availability | The earliest a decision could have used the item | Lookahead prevention: OI, news, breadth |
| Audit time | When something was written down | Journals |

A decision at T sees only items available at or before T, and only the
revision available by then; a later revision or retraction never reaches back.

## 15. Financial authority

No new module computes a price, indicator, size, P&L, margin or basis.
Approving a fact produces metadata only; risk approval, sizing and every Paper
gate are unchanged. Two new import contracts (44 kept):

* **External intelligence reaches no trading, risk or strategy code** - the
  sourcing domain, sourcing application and intelligence ports cannot import
  paper, risk, shadow, backtest, synthesis, strategy or use cases.
* **No decision path reads external intelligence yet** - live, replay,
  backtest, shadow, risk, paper, structure, technical, analysis, synthesis,
  strategy and use cases cannot import the calendar, facts, review, open
  interest, news, breadth, the readers or the intelligence and calendar ports.

One earlier guard was widened, deliberately: the Phase 8 test "no production
module hands out verified status for contract facts" now allows
`domain/sourcing/review.py`, the review boundary this part was asked to
build. It is the only addition, and its refusals are tested.

## 16. Security and licensing

No token in logs, responses or journals; no credential in source or tests.
Every reader logs a failing source by name and exception type only, and its
unavailable answer carries no provider text. Bounds refuse rather than
truncate. No scraper, no broker UI automation, no unofficial endpoint, no
network library in any sourcing module. No specific VIOP value was copied: all
test values are `TEST_FIXTURE`-sourced and labelled.

## 17. Tests

153 tests added in six new files; 4,446 + 153 = 4,599. The rename removed two
non-tests, and the Part 1 metadata conflict test was rewritten, not added.

| File | Brief items |
| --- | --- |
| `test_part2a_trust.py` (22) | A no licence no trust; B forged grant cannot open production; C configured is not connected; R a mock is never exchange-live; S production unconfigured by default |
| `test_contract_facts.py` (42) | D wrong contract; E obsolete period rejected; F official verified preferred; G conflicts recorded or refused; review boundary; append-only audit trail; the port gate |
| `test_publication_time.py` (8) | H publication time preserved, validated, never market time |
| `test_calendar_records.py` (22) | I absent calendar unavailable; J no invented session break; live domain reads no calendar |
| `test_open_interest_news_breadth.py` (38) | K OI never from volume; L future OI cannot reach the past; M future news cannot; N retraction explicit; O unknown denominator unavailable; P incomplete coverage honest |
| `test_intelligence_boundaries.py` (21) | Q no secret leaks; T no automatic Paper position; U no broker order |
| Existing suites | V Phase 8-14 regression: the full suite passes |

## 18. Validation (Part 2A)

Run sequentially, backend from `backend/`:

| Gate | Result |
| --- | --- |
| `ruff check .` | Passed |
| `ruff format --check .` | 511 files already formatted |
| `mypy --platform linux` / `win32` | No issues, 501 source files each |
| `lint-imports` | **44 kept, 0 broken** |
| `pytest`, real PostgreSQL (`viop_test`) | **4,599 passed, 0 skipped, 0 failed** |
| Frontend `npm test` | 484 passed, run fresh; frontend source unchanged, so typecheck, lint and build were not re-run in Part 2A |
| Migrations | None added; head `0008_shadow_outcomes` |

## 19. Known limitations

* No real provider, so no real data, metadata, calendar, open interest, news
  or breadth. Every new port lacks an implementation.
* The review workflow has no durable log, API or screen; no verified contract
  fact exists in this build.
* Capability status is computed but not exposed by the API or frontend.
* News carries no interpretation; section 71's classification is not built.
* Breadth counts advancing, declining and unchanged; section 35's
  moving-average participation and new highs/lows are not built.

## 20. Remaining Phase 15 work (Part 2B onward)

1. Persistence for the review log (a migration, justified by durable audit),
   and an API and screen for submitting and reviewing facts.
2. Exposing the capability matrix and calendar status through the API and UI.
3. With a licence: real adapters behind the market-data, fact, calendar, OI,
   news and breadth ports, each with a sandbox end-to-end.
4. Deciding, per category, whether and how a verified source may inform
   analysis - each a reviewed change to the "no decision path" contract.

---

# Part 2B — Durable verification, capability status and source management

Baseline on resuming: HEAD `3a2e05d`, `main` tracking `origin/main`, Parts 1
and 2A uncommitted as reported above, migration head `0008_shadow_outcomes`.
Design: `docs/architecture.md`, "Durable verification and source status
(Phase 15, part 2B)".

## 21. The authorization decision

The master specification names no authentication control for this, and the
application has none. Option A of the brief was chosen: a **read-only HTTP
API and workspace**, with writes performed only by a **local operator
command** (`python -m app.operator.fact_review`) that needs shell and database
access on the host.

* No HTTP route writes a review; the source API is GET-only, pinned by a test
  over the OpenAPI document, and every write verb answers 404/405.
* A reviewer or submitter name is recorded as the operator's **assertion**
  and labelled `OPERATOR_ASSERTION_NOT_AUTHENTICATED` wherever it is shown.
* Approval requires the explicit `--document-checked` flag; a file import,
  a secondary or unknown publisher (whatever the URL looks like), a missing
  reference or effective date are refused and the refusal is journalled.
* The command never fetches a URL or opens a referenced file, and stamps its
  own clock; nothing is backdated.
* **Financial use stays disabled**: no risk, paper, backtest or shadow
  composition reads the journal, and the API's `financial_use_enabled` is a
  `Literal[False]`.

## 22. What Part 2B built

| Area | Where |
| --- | --- |
| Durable journal (3 append-only tables) | `alembic/versions/0009_fact_verification.py`, `adapters/persistence/fact_models.py`, `adapters/persistence/fact_store.py` |
| Store port | `application/ports/fact_verification.py` (replaces 2A's in-memory `ContractReviewLog`) |
| Write service | `application/sourcing/fact_review.py` (`FactVerificationService`, replaces 2A's `FactReviewWorkflow`) |
| Read service | `application/sourcing/source_status.py` |
| Knowledge time | `domain/sourcing/facts.py`: `known_at`, `known_by`, `NOT_YET_KNOWN`; refused conflicts now carry both values |
| Durable verdict | `domain/sourcing/review.py`: `ReviewResult`, `ReviewVerdict`, `judge` |
| Operator command | `app/operator/fact_review.py` |
| API | `api/routes/sources.py`, `api/schemas/sources.py`, `api/schemas/sources_projection.py` |
| Composition | `app/main.py` (read-only status, always composed); `core/config.py` `FACT_VERIFICATION_MAX_AGE_DAYS` |
| Workspace | `frontend/src/screens/Sources.tsx`, `src/api/sources.ts`, `src/components/Sources.css`; dashboard entry and capability registry |

## 23. Transactions, idempotency and migration

Each journal write is a single `INSERT ... ON CONFLICT DO NOTHING`. A decision
and its result share one row; a record can only cite decisions whose stored
result is `APPROVED` (composite foreign keys), so "APPROVED with no evidence"
cannot be written - tested with a writer that bypasses the application, an
interrupted transaction, a rejected and a refused decision, a racing pair of
conflicting reviews (exactly one row) and five identical concurrent retries
(one write).

Migration `0009_fact_verification` follows `0008_shadow_outcomes`, which was
the applied head on both databases. Model parity is checked by
`compare_metadata`. Upgrade, downgrade to 0008 and upgrade again were run on
the disposable `viop_test` database only. The development database was only
**upgraded** (forward, through the documented container command); nothing
was downgraded there.

## 24. Tests

115 added; backend **4,714 passed, 0 skipped, 0 failed** (4,599 + 115).

| File | Count | Brief items |
| --- | --- | --- |
| `tests/integration/test_fact_verification_persistence.py` | 24 | A restart, B UPDATE/DELETE refused, C no false approval (bypassing writer, rollback, rejected/refused evidence, unknown submission, unknown correction), D idempotency, E concurrency, K durable knowledge time, I exact-contract reads, no N+1, bounded pages, schema parity |
| `tests/integration/test_sources_api.py` | 57 | F no HTTP approval, G/H assertions and official-looking URLs confer nothing, I, J, K, L, M, O, S credentials hidden, T pagination, U hostile identifiers, V/W no position and no order, the operator command end to end |
| `tests/unit/sourcing/test_part2b_status.py` | 31 | K knowledge boundary, L conflicts recorded, G reviewer names, N calendar audits, O/P/Q/R status service |
| `tests/unit/sourcing/test_contract_facts.py` | +2 | the Part 2A audit-trail tests moved onto the durable service |
| `tests/unit/sourcing/test_intelligence_boundaries.py` | +1 | its per-module guard now also covers `source_status.py` |
| Existing suites | — | X Phase 8–14 regression: the full suite passes |

Frontend: 12 added in `Sources.test.tsx`; **496 passed** (484 + 12).

Four existing tests were changed, each for a stated reason:

* `test_paper_persistence.py::test_no_future_phase_table_exists` - the three
  new tables added to its expected set.
* Three parity tests import `fact_models` so their `compare_metadata` sees
  the whole schema.
* `test_part2a_trust.py` / `test_provenance_grant.py` grant guards - allow
  `source_status.py`, which only forwards the composition's grant, and assert
  the composition root passes no grant or declaration.
* `test_paper_api.py` "not-yet-closed" bar - **a pre-existing, date-dependent
  Phase 9 test defect, surfaced by the calendar date.** It placed a
  "future" bar 5,000 hours after 2026-03-02 10:00 UTC, which passed into
  the past around 2026-09-26 18:00 UTC; it failed on this run for that reason
  alone. The bar is now a day beyond the real clock.

## 25. Validation (Part 2B)

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | Passed / 525 files formatted |
| `mypy --platform linux` / `win32` | No issues, 514 source files each |
| `lint-imports` | **47 kept, 0 broken** (three added: the source API reads status and cannot write reviews, trade or value; nothing imports the operator command; the operator command reaches no trading code) |
| `pytest`, real PostgreSQL (`viop_test`) | **4,714 passed, 0 skipped, 0 failed** |
| Frontend `npm ci --dry-run`, `vitest run`, `typecheck`, `lint`, `prettier --check`, `build` | All passed; 496 tests |
| Docker | `docker compose config` valid; backend and frontend images rebuilt; all three services healthy; `alembic upgrade head` in the container reached `0009_fact_verification`; `/api/health/ready` 200; `/api/sources/capabilities` reports every category `NOT_CONFIGURED`; `POST /api/sources/reviews` 405 |

## 26. Performance (measured against `viop_test`, TEST_FIXTURE data)

| Operation | Median | p95 | SQL per request |
| --- | --- | --- | --- |
| Review append (submit + decide) | 16.0 ms | 19.0 ms | 2 writes (+ reads) |
| `GET /sources/capabilities` | 7.8 ms | 8.5 ms | 3 |
| `GET /sources/metadata/{symbol}` (60 records) | 14.2 ms | 18.5 ms | 2 |
| `GET /sources/calendar/{symbol}` | 1.0 ms | 1.2 ms | 0 |
| `GET /sources/reviews?limit=100` | 11.3 ms | 12.0 ms | 2 |

Peak traced Python memory over 40 bounded reads: about 2.9 MiB. UI: the
production bundle is 532 kB JavaScript (154 kB gzip) including the new screen;
the screen's 12 jsdom tests render in about 4 s in total. These figures
describe an empty or fixture journal and **no provider**; no real-feed
throughput is claimed.

## 27. Known limitations

* No real provider, no verified calendar source, no licensed news, open
  interest or breadth; their categories are `NOT_CONFIGURED`.
* Reviewer identity is an operator assertion, not authentication; the
  protection is host access. A multi-user authorization system does not exist.
* The review boundary covers multiplier, tick size and expiry; tick value,
  margins and session hours are not reviewable yet.
* Calendar records have no review workflow or storage; the composed calendar
  answers `UNAVAILABLE`.
* Verified facts are not consumed by any financial path, by design.

## 28. Final closeout requirements (not started)

Real-browser (Chromium) end-to-end of the workspace, the final mutation
sweep, and the phase closeout review - plus, whenever they become possible, a
licensed provider adapter behind each port with a sandbox end-to-end, and a
reviewed decision on whether and how verified facts may reach a financial
consumer.

---

# Part 2C — Final validation of the provider-independent foundation

Baseline on resuming: HEAD `3a2e05d`, `main` tracking `origin/main`, Parts
1-2B uncommitted; development database and `viop_test` both at
`0009_fact_verification`; three containers healthy. Design:
`docs/architecture.md`, "Final validation of the source foundation (Phase 15,
part 2C)".

## 29. Status, in four separate categories

**A. Implemented and validated** (real PostgreSQL, composed app, Docker,
nginx, real Chromium): the provenance/grant gate; the per-category capability
matrix built from actual composition; the vendor-neutral candle path with
`published_at`; contract-fact records chosen by applicable period and
authority, with knowledge time; the durable append-only review journal and
the local operator command; the read-only source API; the source-management
workspace; the domain contracts for calendars, open interest, news and
breadth.

**B. Implemented but not fully validated**: the calendar, open-interest, news
and breadth *readers* are exercised only with test doubles - no real source
exists to validate them against. The review workflow's human process
(someone actually opening an official document) cannot be validated by
software at all; only its recording is.

**C. Not implemented**: any licensed adapter (market data, metadata, calendar,
open interest, news, breadth); calendar review and storage; review of tick
value, margins or session hours; a separate operator database role;
multi-user authentication; live/shadow API and UI exposure of real
provenance or delayed data; section 71 news classification; section 35
moving-average participation and new highs/lows.

**D. Blocked by provider access**: every real-provider integration and its
end-to-end test. No licence, account or credential exists for this project.

## 30. Defects found and fixed in Part 2C

| # | Found by | Defect | Fix |
| --- | --- | --- | --- |
| 1 | Operator audit | An over-long value reached PostgreSQL as a `DataError` and was reported `JOURNAL_UNREACHABLE` | Service bounds (`FIELD_TOO_LONG`); store maps `DataError` to `VALUE_OUT_OF_BOUNDS` |
| 2 | Operator audit | An empty effective period surfaced as an unnamed `INTEGRITY_REFUSED` | Service refusal `EFFECTIVE_PERIOD_EMPTY`; check constraints mapped to codes |
| 3 | Operator audit | Asserted times later than the server clock were accepted | `FUTURE_DATED` for submissions and decisions |
| 4 | Operator audit | A retried CLI command restamps its time, so a genuine retry was reported as a conflict | `same_claim` / `same_decision`: identity by content, first time stands |
| 5 | §8 audit | Margins were omitted from the metadata status | `initial_margin`, `maintenance_margin` shown `NOT_REVIEWABLE` |
| 6 | Real Chromium | Pro table widened the page by 281/351 px at 390/320 | `min-width: 0` on the tab panel; tables scroll in their wrapper |
| 7 | Real Chromium | Tabs lacked the roving-tabindex keyboard pattern; no polite status region | Same pattern as Live/Shadow; status region added |
| 8 | §14 review | The Part 2B Phase 9 fix read the wall clock (would drift) | The test injects the existing `FixedClock`; the bar is a day after that clock |
| 9 | Mutation D | No test pinned the service's own "not approved" check (two further layers stood behind it) | `TestPublishRefusesEveryNonApproval` |
| 10 | Mutation Z | The smuggled-grant test failed on missing fields, not on the forged ones | Valid bodies plus forged fields; asserts `extra_forbidden` |
| 11 | Mutation K prep | No test pinned the `EXPIRED` refusal for fact records | `TestExpiry` |

No migration was needed; `0009` was not edited.

## 31. Financial authority, in the production composition

A published, `USABLE` record for a contract changes nothing financial:
`app.state.product_resolver` stays `None`, a Paper position for that contract
is refused `PRODUCT_METADATA_UNAVAILABLE`, backtest capability reports no
financial execution, Shadow reports no financial metadata (with the local
simulation composed), and the source API's `financial_use_enabled` is
`Literal[False]`. Tested against the composed application, not an assessor.

## 32. Real Chromium and nginx

Chrome 153 headless, the built frontend through nginx, the backend pointed at
`viop_test` (backend container only, `--no-deps`; the development database
untouched), holding a **disclosed fixture** written through the real operator
command: 60 pending/rejected claims, one published record, two refused
approvals (a file import; a secondary source with an official-looking URL),
two disagreeing published records.

* Journeys A-N: **16/16** - six `Yapılandırılmadı` categories matching the API;
  no provider; missing metadata and calendar shown as unknown; 68 history
  entries paged once each in journal order; refusal codes and reviewer
  assertions; the conflict with both values and none chosen; Beginner/Pro same
  verdict; unchanged after reload and after a backend restart (healthy in
  5.8 s); no approve/verify/publish control; no paper position; the journal
  unchanged by the session. Screen first render 229 ms.
* Viewports 1280/768/390/320, Beginner and Pro, all five sections: overflow 0,
  nothing clipped, long references wrap, 0 unnamed buttons, 0 unlabelled
  inputs, 0 buttons under 24 px; arrows/Home/End move focus; focus outline
  solid 2 px; errors announced with `role=alert`. **7/7** (after fix 6).
* nginx: **16/16** - 36 write attempts (4 verbs x 9 paths, forged bodies)
  all 404/405; no CORS grant to a foreign origin; 11 hostile symbols x 2 routes
  400/404/422 and never echoed; 13 malformed cursors/limits 422; the largest
  valid cursor 200; 8 naive/malformed/ambiguous times 422; extreme dates
  answered without a 500; a 20 kB query 414; journal and paper state
  byte-identical afterwards.

No WCAG certification is claimed.

## 33. Mutation sweep A-Z

Every probe edited the intended code, ran the named suite and restored the
bytes (SHA-256 verified); all production files matched the pre-sweep hashes
afterwards. E and F re-ran migration 0009 on `viop_test` only.

| Probe | Mutation | Result |
| --- | --- | --- |
| A | HTTP approval route exposed | DETECTED |
| B | forged reviewer name gains authority | DETECTED |
| C | `--document-checked` no longer required | DETECTED |
| D | unapproved decision publishes a record | **SURVIVED** first (re-judgement and FK still refused); DETECTED after fix 9 |
| E | UPDATE allowed on the journal | DETECTED (trigger) |
| F | DELETE allowed on the journal | DETECTED (trigger) |
| G | duplicate review without idempotent retry | DETECTED |
| H | conflicting review silently accepted | DETECTED |
| I | `known_at` ignored in an as-of query | DETECTED |
| J | retrospective result loses its label | DETECTED |
| K | expired fact becomes current | DETECTED (test added first, fix 11) |
| L | wrong-contract metadata selected | DETECTED |
| M | newer-checked obsolete fact wins | DETECTED |
| N | missing calendar becomes `IN_SESSION` | DETECTED |
| O | simulated data becomes exchange-current | DETECTED |
| P | unsupported provider becomes `AVAILABLE` | DETECTED |
| Q | open interest inferred from volume | DETECTED |
| R | future news becomes past evidence | DETECTED |
| S | breadth without a verified denominator | DETECTED |
| T | driver detail leaks into a response | DETECTED |
| U | source API gains a Paper-order path | DETECTED (import contract) |
| V | `TEST_FIXTURE` becomes production metadata | DETECTED |
| W | Phase 9 test reads the advancing wall clock | DETECTED |
| X | old status response overwrites new UI state | DETECTED |
| Y | pagination skips or duplicates | DETECTED |
| Z | licence grant manufactured from a request flag | **SURVIVED** first (weak test); DETECTED after fix 10 |

## 34. Performance (local, `viop_test`, fixture data, no provider)

| Operation | Median | p95 | SQL |
| --- | --- | --- | --- |
| Review append (submit + decide) | 11.9 ms | 14.5 ms | 2 writes |
| Capabilities | 5.0 ms | 5.6 ms | 3 |
| Metadata, 60 records | 11.1 ms | 14.7 ms | 2 |
| Calendar | 0.6 ms | 0.8 ms | 0 |
| Review page (100) | 8.6 ms | 9.2 ms | 2 |
| Browser: screen first render | 229 ms | | |
| Backend restart to healthy | 5.8 s | | |

Peak traced memory over 40 bounded reads about 2.9 MiB. No exchange-feed
throughput is claimed.

## 35. Validation (Part 2C, all fresh)

| Gate | Result |
| --- | --- |
| `ruff check .` / `ruff format --check .` | Passed / 526 files |
| `mypy --platform linux` / `win32` | No issues, 515 files each |
| `lint-imports` | 47 kept, 0 broken |
| `pytest`, real PostgreSQL | **4,756 passed, 0 skipped, 0 failed** (4,714 + 42) |
| Frontend `npm ci --dry-run`, `vitest`, `typecheck`, `lint`, `prettier --check`, `build` | All passed; **499** tests (496 + 3) |
| Docker | `config -q` ok; both images built; 3/3 healthy; `0009_fact_verification (head)` on the development database; `/api/health` 200 direct and through nginx |
| Served assets | JS, CSS and `index.html` byte-identical to the tested build |

## 36. Remaining real-provider dependencies

A licensed vendor subscription with redistribution terms confirmed for this
use; credentials outside source; for each category, an adapter behind the
existing port and a sandbox end-to-end; a documented operator process (and
ideally a separate database role) for official-document review; and a
reviewed decision on whether verified facts may ever reach a financial
consumer.

## 37. Phase status

**PHASE 15 — PROVIDER-INDEPENDENT FOUNDATION VALIDATED; EXTERNAL INTEGRATIONS
PENDING.** Phase 15 as the master specification defines it ("evaluate
legitimate providers ... integrate through adapters") is **not complete**:
no provider is integrated, because none is accessible. Whether to close the
phase on this foundation is a human decision.
