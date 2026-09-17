# PHASE 8 COMPLETION REPORT — PROFESSIONAL ANALYSIS UI

Phase 8 = 8A (approved) + 8B, delivered as one uncommitted change set.

Nothing committed. Nothing pushed. No Phase 9 work started.

**Direct answers to the six questions the brief asks:**

| Question | Answer |
|---|---|
| Any real Anthropic call made? | **No.** No credential is configured; every AI path used a fake transport or returned a typed NOT_CONFIGURED. |
| Any real external market-data provider used? | **No.** All OHLCV was user-supplied CSV. No network egress of any kind. |
| Is analysis persistent or ephemeral? | **Ephemeral.** Request-lifetime only. No table, no store, no fetch-back endpoint. |
| Any live/streaming functionality? | **No.** No provider, no WebSocket, no SSE, no polling. |
| Any Phase 9 code? | **No.** Verified mechanically (§54). |
| Any execution capability? | **No.** No order type, no broker, no Midas path. |

---

## 1. Baseline and recovery

Captured before editing:

| Item | Value |
|---|---|
| HEAD | `f0b8e24 Complete Phase 7 safe Claude synthesis` — unchanged throughout |
| Branch | `main`, tracking `origin/main`, no divergence |
| Staged | nothing |
| Frontend baseline | 155 passed / 0 skipped — **confirmed** |
| Backend baseline | 2087 passed / 0 skipped — **confirmed** |
| Import contracts | 15 kept — **confirmed** |
| Phase 8B present already? | No |
| Phase 9 present? | No |
| Phase 8A forensic fixes present? | Yes — capability matrix, `UTC` timestamps, evidence panel, ladder ARIA roles, `min-width: 0` hardening all intact |

## 2. Scope delivered

Real ephemeral analysis (`POST /api/analysis`), OHLCV import through a shared
parser port, input bounds, snapshot identity, orchestration over the existing
engines, contract-verification gating, account/risk/currency inputs, Vision
composition-root wiring, screenshot slots and correction UI, vision-confidence
semantics, synthesis reachability, the typed API/mapper boundary, Dashboard,
Analyze Market, Analysis Workspace, an SVG candlestick chart, Beginner/Pro,
evidence and scenarios, the risk panel, loading/stale-state handling,
navigation, security, accessibility, responsive verification, mutation probes
and runtime validation.

## 3. Files added / modified

**Backend added:** `app/api/providers.py`, `app/api/routes/analysis.py`,
`app/api/schemas/analysis.py`, `app/api/schemas/analysis_projection.py`,
`app/application/analysis/{__init__,limits,request,snapshot,orchestrator}.py`,
`tests/unit/test_composition_root.py`,
`tests/unit/analysis_api/{test_analysis_api,test_analysis_synthesis,test_contract_verification}.py`.

**Backend modified:** `csv_provider.py` (parser extracted + `CsvCandleTextParser`),
`ports/market_data.py` (`CandleTextParser`, `CandleParseError`,
`CandleRowLimitError`), `main.py` (composition), `pyproject.toml` (16th contract).

**Frontend added:** `api/{analysis,mapAnalysis,screenshots}.ts`,
`components/{CandlestickChart,ScenarioPanel,ScreenshotSlots}.tsx` + CSS,
`screens/{Dashboard,AnalyzeMarket,AnalysisWorkspace}.tsx` + CSS, `test/dto.ts`,
and five test files.

**Frontend modified:** `App.tsx`, `api/client.ts` (`postJson`), `domain/models.ts`,
`domain/capabilities.ts`, `format/display.ts`, `components/RiskSummaryCard.tsx`,
`test/setup.ts`, `styles/tokens.css`.

Tracked diff: 951 insertions, 87 deletions across 11 files, plus the new files.

## 4. Final architecture — **VERIFIED**

```
POST /api/analysis
  body (extra="forbid")   -> a forged derived field is 422, not a value
  -> AnalysisRequest      -> only user-suppliable things exist on it
  -> CandleTextParser     -> one parser, shared with the on-disk provider
  -> DataQualityEngine / compute_technicals / analyse_structure   (Ph 1-2)
  -> TimeframeView -> analyse_multi_timeframe                     (Ph 4)
  -> assess_no_trade per direction / size_position                (Ph 3-4)
  -> SynthesisContext -> run_synthesis (optional)                 (Ph 7)
  -> AnalysisResponse (projection, no calculation)
  -> zod -> mapper -> AnalysisReadModel -> components
```

Import contracts: **16 kept, 0 broken.** The 16th is new: *"The analysis
orchestrator computes nothing itself."*

**An architecture violation was made and corrected.** The first orchestrator
imported the CSV adapter directly, breaking *"Application depends only on
domain."* Per CLAUDE.md the contract was right and the design was wrong: the
file format is infrastructure, so it now arrives through a `CandleTextParser`
port. The contract was not weakened.

## 5. Runtime analysis lifecycle — **VERIFIED**

Ephemeral. `analysis_id` = SHA-256 over symbol + per-dataset digests + risk
settings + `analysis_as_of`. `generated_at` is deliberately **outside** the
identity — the Phase 7 digest correction, reapplied. `ephemeral: true` ships in
the payload. There is no `GET /analysis/{id}`; adding one would promise
persistence that does not exist.

## 6. OHLCV import — **VERIFIED**

One parser. `parse_candle_csv` was extracted from the Phase 1 file provider and
is used by both paths, so a file on disk and an uploaded body cannot drift. No
sorting, deduplication, gap filling, clamping or resampling; a malformed row is
a `MALFORMED_ROW` finding travelling with the data, and a structurally broken
file raises. Missing timeframes are **not** invented.

## 7. Input / security limits — **VERIFIED**

8 MB per timeframe, 60 000 rows per timeframe, 120 000 total, 64-char symbol —
each checked before the work it guards. Over the row limit the file is
**refused, not truncated**. Errors name the limit and never echo content:
uploading `<script>alert(1)</script>` produces an error containing no `<script>`.

## 8. Trust model — **VERIFIED**

Nine forged-field payloads were tried against the live container; all returned
422. There is no request field for an indicator, structure, quality, entry
score, suitability, risk result, `ActionEnvelope`, final action or synthesis
result. Mutation probe A (`extra="allow"`) made 11 tests fail.

## 9. Analysis snapshot identity — **VERIFIED**

Carries analysis id, symbol, `analysis_as_of`, `generated_at`, per-dataset
digest / source name / row count / usability, contract-verification flag, a
one-way risk-settings digest (the equity participates but does not travel), and
`ephemeral`. Identical inputs → identical id, confirmed against the container.

## 10. Orchestrator — **VERIFIED**

Invokes existing engines only. No formula. Enforced by the new import contract
and by probe K (an indicator import breaks it) and probe L (an adapter import
breaks three contracts).

## 11. Futures / contract metadata — **VERIFIED**

No provider is composed by default, so no contract exists and futures-dependent
calculation is unavailable with a named reason. No dropdown of VİOP contracts
was fabricated. A typed symbol is stated in the UI — *before* submission — not
to be verified metadata. Sizing requires both multiplier and tick size to be
`VERIFIED_CURRENT_FACT`; `UNVERIFIED`, `DEVELOPMENT_DEFAULT`, `TEST_FIXTURE` and
`MOCK_DATA` all refuse.

## 12. Account / risk inputs — **VERIFIED**

Accepted as strings, parsed to `Decimal`. A JSON number is rejected (422) — it
has already been through a double. The frontend sends input; the backend Risk
Engine calculates. Probe B proves a `.toFixed()` financial calculation in React
fails the architecture test.

## 13. Currency semantics — **VERIFIED**

Carried only when the user supplies an ISO-style code, as `USER_CONFIRMED`. Not
inferred from locale, not defaulted to TRY. With no code, no symbol is shown.

**The Phase 8A carry-forward is closed with the distinction the brief asked
for:** the two-decimal rule for money is documented as a *generic display
policy*, explicitly **not** a currency minor-unit rule. A real minor-unit rule
would need a verified currency registry (JPY 0, KWD 3); this project has none
and does not pretend otherwise.

## 14. Vision composition-root wiring — **VERIFIED**

The Phase 8A gap is closed. `build_screenshot_analyzer` constructs the real
`ClaudeScreenshotAnalyzer` when Vision is configured, and `create_app` installs
it. 13 tests in `test_composition_root.py`, **none of which install a dependency
override** — the flaw that let the original gap survive was a test that
installed the thing it was checking. Unconfigured: no client is constructed, and
the route's 503 default stands. A key without a model, a model without a key,
and synthesis without a context window all correctly refuse. The credential is
not reachable from the built adapter or from `repr(settings)`.

## 15. Screenshot UI — **IMPLEMENTED**, crop/zoom **IMPLEMENTED** (closeout)

Four slots (1D/1H/15M/5M) with file selection, preview, replace, delete, upload
state, quality score, inferred timeframe, expected-vs-detected mismatch (with
`role="alert"`), unreadable fields, and the correction path.

**Crop and zoom were closed in the final closeout, not dropped.** The original
objection was sound: if cropping edits the upload in place, the image the backend
analysed is not the image the user chose and "what was actually read?" has two
answers. So the crop does not edit anything. `cropToFile` draws the selected
region onto a canvas and produces a **new `File`**, which is hashed and uploaded
like any other file; the original is untouched and simply not sent. There is one
artifact and one identity, as before.

Zoom never reaches the bytes at all: it is a CSS transform on the preview, so
what is uploaded is independent of how closely the user looked at it. Probe 15
asserts the string `zoom` does not appear anywhere in the crop path.

Bounds are clamped numerically (`normaliseRegion`, minimum 5%), and probe Y —
removing the clamp — fails the crop tests.

Object URLs are revoked on replace and unmount. Filenames render as text — a
`<img src=x onerror=...>` filename produces no element.

## 16. Correction UI — **IMPLEMENTED**

CONFIRM / CORRECT / REJECT against the real Phase 6 endpoint. Shows the original
observation (never overwritten), its origin
(`CLIENT_REPLAYED_UNVERIFIED`), the user's value, the resulting authoritative
value and source, and whether a more authoritative source overrode the input.

`USER_CONFIRMED` is explicitly labelled *"above a screen reading, not above
validated market data"*, and a test asserts `STRUCTURED_MARKET_DATA` never
appears. The request type has no field for a source, priority or status.

## 17. Vision confidence — **IMPLEMENTED** (Phase 8A deferral closed)

Rendered as `okunabilirlik 0.74 (model beyanı)` — model-reported *extraction*
confidence. Tests assert it never appears as `74%`, never near
olasılık/ihtimal/şans, and that a missing confidence renders "güven
bildirilmedi" rather than zero.

## 18. Analysis API — **VERIFIED**

`POST /api/analysis` only. No GET-latest, no history, no persistent ids, no
database records. Complete registered surface: three health operations, the two
screenshot operations, and this one.

## 19. Synthesis integration — **VERIFIED**

Phase 7's dead endpoint was **not** resurrected. Synthesis is an optional step
inside the analysis request, from a server-built context. 14 tests prove the
join, all with a fake provider.

Observed and kept as a test: a model proposing `WAIT` against an envelope of
`{NO_TRADE}` is rejected as `INVALID_OUTPUT` with the reason attached — it is
not quietly downgraded.

## 20. Provider / system-failure semantics — **VERIFIED**

Provider failure leaves the deterministic analysis intact, `final_action` null,
and fabricates no WAIT or NO_TRADE. Probe D (dropping the SUCCESS guard in the
mapper) fails a test.

## 21. Dashboard — **IMPLEMENTED**

Real analysis entry point, live health, the capability matrix. Future modules
(watchlist, live setup cards, paper positions, journal) are listed **by name
with their phase and reason** rather than rendered as empty widgets — a card
showing zero paper positions is indistinguishable from a working feature with
nothing in it.

## 22. Analyze Market — **IMPLEMENTED**

Instrument → per-timeframe CSV → account/risk → optional screenshots →
pre-submission status → ANALYSE. The status block states which timeframes are
present and missing, that a typed symbol is not verified metadata, whether risk
can be calculated, and whether Vision and synthesis are configured. No hidden
assumptions.

`FileReader` rather than `file.text()`: `text()` is unimplemented in jsdom, so
the read path would have been untestable.

## 23. Analysis Workspace — **IMPLEMENTED**

Header → final state + risk (always first) → optional synthesis summary →
chart + intelligence panel → scenarios + Devil's Advocate + contradictions →
evidence → Pro audit block. When it stacks, safety leads: on a phone the thing
a user must not scroll past is the thing that could cost them money.

## 24. Chart — **IMPLEMENTED** (SVG, no dependency)

Real OHLC geometry from DTO candles: wick high→low, body open→close, S/R bands,
timeframe switching, hollow bodies for unclosed bars.

**No charting library was added.** Phase 8 needs candles, bands and a switch —
not pan, zoom, crosshairs or streaming, which is most of what such a library
is. Every bar is a DOM node, so the textual alternative is the same data.
Overlays were added in the closeout (§11) and needed no library: each is a
polyline of backend-computed values, sliced to exactly the displayed window and
carrying `null` through the warm-up rather than a value nobody computed. Pan,
zoom and crosshairs remain absent; if they become requirements, revisit rather
than grow this.

Prices become JavaScript numbers exactly once, for pixel coordinates discarded
next render. Displayed extremes are the **exact backend strings** from the bars
holding them. Probe I (scaling the drawn close by 2%) fails two tests.

## 25. Beginner Mode — **IMPLEMENTED**

Leads with final state, risk, why, what is waiting/missing. Critical warnings
and missing information appear in both modes. Raw audit ids are Pro-only.

## 26. Pro Mode — **IMPLEMENTED**

Adds raw values, quality components, evidence reference ids, feasibility
detail, and the audit block (analysis id, context digest, generation time,
contract verification). No raw JSON dump.

## 27. Why Engine — **IMPLEMENTED** (the closeout closed this PARTIAL)

This section previously read PARTIAL, and the reason was accurate: reasons reached
the UI only as whatever prose the underlying engines happened to attach to a
scenario or a finding, and the Phase 5 Why Engine's own structures were not
projected at all.

`app/api/schemas/analysis_why.py` now closes that by **calling the existing
engine**. There is no second explanation engine in the API and none in the
frontend: every string in the Why block was produced by
`app.application.presentation.why`, which walks the breakdown the scoring engine
produced. Directional evidence both ways, scenario state, pending confirmation,
setup and entry quality, contradictions, the strongest zones, sizing, and the
no-trade block are each explained; where a conclusion does not exist the topic is
reported **unavailable with a reason** rather than omitted or filled in.

Deliberately still absent: `WHY_LONG` / `WHY_SHORT` / `WHY_WAIT`. The engine has
no such topic, Phase 7 owns the synthesis that would produce one, and writing it
here would mean inventing an explanation of a final action. Probe 12 asserts no
topic names or sets an action.

**Safety (§7).** The module is read-only over a finished `AnalysisOutcome`; it
creates no value, changes no action and touches no provenance, because it has no
way to write any of them. The API assembles the Why block last and never feeds it
back in, and a test diffs the whole response with the block present and absent to
prove every other field is byte-identical. Probe T — letting the Why result set
`final_action` — fails the suite. It formats no number of its own: a structural
test forbids `round(`, `format(`, `Decimal(` and f-string interpolation in the
module, and an end-to-end test harvests every string from the finished domain
objects and asserts each decimal the Why block shows was produced by an engine.

## 28-30. Evidence, scenarios, Devil's Advocate — **IMPLEMENTED**

Bull/bear/neutral side by side, never netted, never normalised; the panel says
so. The neutral case carries no directional score. Contradictions are listed,
not averaged away. When synthesis did not run, the Devil's Advocate section says
so rather than inventing an objection. Stable content-derived ids in Pro.

## 31. Risk panel — **IMPLEMENTED**, with a defect found and fixed

Prominent, `role="alert"` when blocking, and it now renders **why** a number is
absent. Two real defects were found here:

1. The card displayed "Risk belirlenemedi" and stopped — the reasons existed in
   the payload and were never rendered.
2. The projection reported `allowed_contracts is None` as `NOT_PERMITTED`,
   telling a user their trade was refused when a missing initial margin had
   stopped the calculation. Now `UNDETERMINED`, with a test pinning it.

Unverified metadata visibly limits dependent outputs. No "0" for an unavailable
calculation.

## 32. Provenance / raw-display — **VERIFIED**

Unchanged from 8A and extended: observed values are not reformatted, raw values
never mutate, Pro shows the exact string, `—` for missing.

## 33. Loading / stale state — **IMPLEMENTED** (Phase 8A deferral closed)

idle → uploading → analysing → success/error, with cancel. Ticket + AbortController;
a result lands only if its ticket is newest. The submit button disables while
pending, so the reachable interleaving is submit → cancel → submit, and that is
what the test drives. Probe J (removing the guard) fails.

## 34. Navigation — **IMPLEMENTED** (no router added)

A state machine, not URLs. The analysis is ephemeral, so a workspace URL would
be a promise the system cannot keep. The workspace says the result is not saved.
Revisit when analyses persist.

## 35. Accessibility — **BROWSER VERIFIED** (the closeout re-measured it)

Labelled file inputs and form controls, `role="group"` timeframe switching with
`aria-pressed`, `role="alert"` for blocking risk and mismatches, chart
`role="img"` with title + description plus a visible caption carrying the same
facts, explicit ladder ARIA roles, colour never the only signal.

The closeout replaced "the DOM was inspected" with numbers. The real production
bundle is driven through a real analysis inside Chrome — files injected with
`DataTransfer`, the same object a drop produces — and the page measures itself
with `getBoundingClientRect` and `getComputedStyle`, reporting through
`--headless --dump-dom`. No automation dependency was added.

Measured on the rendered workspace:

| Check | Result |
| --- | --- |
| Focusable controls without an accessible name | 0 of 12 |
| Form inputs without a bound label | 0 |
| Positive `tabindex` | 0 |
| Targets below WCAG 2.2 AA 24x24 (hit area) | 0 |
| Heading structure | starts at `h1`, 40 headings, no skipped level |
| Text nodes below AA contrast | **0 of 350** |
| Horizontal scroll at 1280 / 640 / 390 / 320 px | none at any width |

Two defects were found this way and fixed: focus was left on `<body>` when the
form unmounted (it now moves to the result heading, `tabIndex={-1}`), and
progress was a visual cue only (it is now also a polite `role="status"` live
region).

Still **not** claimed: this is not a WCAG certification, and no real screen
reader was driven. Contrast, target size, reflow, labelling and focus order are
measured; comprehension by an actual assistive-technology user is not.

## 36. Responsive — **BROWSER VERIFIED**

Measured inside iframes (Chrome on Windows refuses windows under ~500px, so a
`--window-size=390` screenshot silently lays out at 512 — the Phase 8A lesson).
Under stress: long Turkish prose, a 24-digit numeric, an unbreakable token,
scenario narratives, validation errors, the chart.

| 1440 | 1024 | 768 | 390 | 320 |
|---|---|---|---|---|
| no overflow | no overflow | no overflow | no overflow | no overflow |

Chart overflow is contained deliberately (`aspect-ratio`, width 100%).

## 37. Frontend security — **VERIFIED**

No `dangerouslySetInnerHTML`, no `innerHTML`, no raw HTML/Markdown execution.
Filenames, CSV errors, Vision text and synthesis narrative all render as text.
Adversarial strings stay inert (7 tests). No fixture leakage: the module graph
from `main.tsx` reaches no test file, and the shipped bundle contains zero
fixture markers.

## 38. Performance / bundle — **MEASURED**

`index.js` 344.62 kB (101.68 kB gz), `index.css` 29.36 kB (4.90 kB gz), built in
~0.6 s. Growth from 8A's 306 kB is three screens, the chart and the screenshot
UI — no dependency was added. Chart geometry is memoised on the series; nothing
else was memoised, because nothing was measured to need it. Code splitting was
**not** added: there are no future-phase screens to defer and a 101 kB gzipped
bundle does not justify the complexity. Recorded rather than optimised by
intuition.

## 39. Runtime E2E — **VERIFIED (22/22)**

Through `localhost:5173/api` — nginx → backend, exactly as the browser goes:

* real analysis, 20 evidence items, three scenarios;
* uptrend → 8 bullish / 0 bearish; **inverted input → 0 bullish / 8 bearish**;
* identical input → identical id; changed input → different id;
* missing timeframes explicit, no neutral stand-in;
* unknown timeframe, forged `final_action`, forged `risk_permitted` → 422;
* no metadata fabricated, no risk facts invented, no final action;
* every drawn close was in the supplied CSV (220 bars);
* screenshot endpoint returns a typed 503, not an action.

No precomputed fixture is returned; the container serves the exact tested bundle
(`index-DXNucBG2.js`).

## 40. Vision integration — **VERIFIED**

Composition-root construction proven without overrides; unconfigured builds no
client; the real decode path is untouched. **No paid request was made.**

## 41. Synthesis integration — **VERIFIED**

analysis → context → envelope → fake provider → validator → accepted; provider
failure leaves the analysis intact; an out-of-envelope action is refused.

## 42. Critical empirical review

All 27 listed attacks were exercised. Defects found and fixed: the risk-reason
rendering gap, the UNDETERMINED/NOT_PERMITTED conflation, an unconditional unit
suffix, `MISSING` provenance formatted as calculated, a gauge that drew a
perfect score from `outOf: 0`, an SVG `<title>` React warning, and the
architecture rule that named two fixture files instead of the directory.

## 43. Mutation probes — **12/12 DETECTED**

| Probe | Result |
|---|---|
| A. Client-forged final action accepted | DETECTED |
| B. Frontend recomputes a financial value | DETECTED |
| C. WAIT presented as NO_TRADE | DETECTED |
| D. System failure becomes a market action | DETECTED |
| E. Production module imports a test fixture | DETECTED |
| F. Unknown enum silently cast | DETECTED |
| G. Risk blocker hidden | DETECTED |
| H. Unverified metadata shown as verified | DETECTED |
| I. Chart draws a fabricated price | DETECTED |
| J. Stale response overwrites current | DETECTED |
| K. Orchestrator imports an indicator routine | DETECTED |
| L. Orchestrator imports an adapter | DETECTED |

**Four were NOT detected on the first run**, and each gap was real: the fixture
rule enumerated two filenames and missed `test/dto.ts`; no test had ever
supplied an unverified-but-present contract; the chart tests checked text while
the fabricated price existed only as geometry; and probe K was malformed. All
four are now covered — including a doji test that makes chart geometry
falsifiable without restating the scaling formula.

## 44. Backend tests — 2166 passed / 0 skipped

Analysis API 43 · synthesis integration 14 · contract verification 9 ·
composition root 13, plus the Phase 0–7 suite.

## 45. Frontend tests — 221 passed / 0 skipped, 13 files

`capabilities` 12 · `display` 47 · `health` 8 · `architecture` 12 ·
`mapAnalysis` 21 · `EvidencePanel` 12 · `App` 11 · `SystemStatus` 4 ·
`security` 7 · `accessibility` 17 · `AnalysisDashboard` 29 ·
`AnalysisWorkspace` 24 · `ScreenshotSlots` 17.

## 46. Quality gates

| Gate | Result |
|---|---|
| `ruff check .` | All checks passed |
| `ruff format --check .` | 268 files already formatted |
| `mypy --platform linux` | no issues, 265 files |
| `mypy --platform win32` | no issues, 265 files |
| `lint-imports` | **16 kept, 0 broken** |
| `pytest` (ordinary env) | 2162 passed, 4 skipped (no DB configured) |
| `pytest` (integration configured) | **2166 passed, 0 skipped** |
| `npm ci --dry-run` | clean install works from the lockfile |
| `npx vitest run` | **221 passed, 0 skipped** |
| `npm run typecheck` | clean |
| `npm run lint` | clean |
| `npx prettier --check .` | clean |
| `npm run build` | built, 344.62 kB JS / 29.36 kB CSS |
| `docker compose config -q` | valid |

## 47. Docker / runtime

`backend`, `frontend`, `postgres` all `running (healthy)`. Both images rebuilt.
The served asset hash matches the locally tested build exactly. No error or
crit lines in frontend logs. `/api/health` 200 through nginx.

## 48. Architecture review — **VERIFIED**

Domain independent; application → domain only; adapters implement ports; API →
application. No FastAPI/SQLAlchemy/Anthropic in the domain. No frontend
financial formulas. No market-data vendor coupling. No fake persistence. One
contract added, for a meaningful boundary.

## 49. Dependencies — **none added in 8B**

Zod already existed and is used at the API boundary. No router (state machine
instead), no chart library (SVG instead), no test utilities beyond 8A's
`@testing-library/user-event`. `package.json` and the lockfile are consistent
and `npm ci --dry-run` succeeds.

## 50. Security / secret review — **VERIFIED**

No API key, no `.env` content, no machine-specific path, no vscode-webview URL,
no debug logging, no temp or recovery file, no screenshot or CSV committed, no
base64 image, no test credential beyond obviously-fake `sk-ant-fixture` strings
in a composition test that asserts the key never leaks. The real PostgreSQL
password appears nowhere in source or docs.

## 51. Known limitations

- **No `WHY_LONG` / `WHY_SHORT` / `WHY_WAIT` topic** (§27) — the engine has none,
  and Phase 7 owns the synthesis that would justify one.
- **No contract metadata provider is composed**, so risk sizing is unavailable
  in the default deployment. Not a defect: no verified VİOP specifications
  exist to load.
- **Vision and synthesis are unconfigured here**; both paths were exercised
  with fakes only.
- **Accessibility is not certified** (§35). Contrast, target size, reflow,
  labelling and focus order are measured; no real screen reader was driven.
- **Chart has no pan, zoom or crosshair.** Overlays were added in the closeout.
- **Only RSI is surfaced as a headline *fact*** per timeframe; the full Phase 1
  indicator set reaches the UI through the technical panel.
- **No visual-regression suite**; responsive verification is reproducible but
  manual.
- **`color-mix()` needs Chrome 111+** — border tints only, degrades safely.
- **Analysis is lost on refresh**, stated in the UI.

## 52. Technical debt

- The frontend `AnalysisReadModel` carries Phase 8 fields as optional so 8A
  fixtures stay valid; they should become required once 8A-era fixtures retire.
- `_facts` in the projection chooses RSI by hand; a fact registry would be
  better once more indicators are surfaced.
- The stale-request guard lives in `App`. If a second async surface appears it
  should move into a shared hook rather than be copied.

## 53. Explicitly deferred future-phase capabilities

Paper trading and positions, trade lifecycle, journal, replay, backtest,
`LiveMarketDataProvider`, WebSocket/SSE, streaming, shadow mode, external paid
data vendors, broker connectivity, Midas automation, order execution,
persistence and analysis history.

## 54. Phase-boundary verification — **VERIFIED**

Mechanically searched for every listed term. All occurrences are docstrings or
UI copy naming what is deliberately absent, plus the architecture test's own ban
list. No order type, no broker call, no execution path.

## 54b. Final 10/10 human-review closeout

Phase 8 was reported complete, then audited again against twenty closeout items
and re-proved against the running stack. Seven items were genuinely incomplete
and are now closed; the rest were re-proved rather than re-implemented.

**Closed in the closeout**

| # | Item | What was actually wrong |
| --- | --- | --- |
| 1 | HTTP ingest bound | The 8 MiB limit ran *after* the body was materialised. A 64 MiB body was accepted in 0.5 s and only then refused. |
| 2/3 | Temporal coherence and `analysis_as_of` | A 1D series a year ahead of the 5M data was accepted with no finding, and it changed the evidence (11/0 bullish became 9/3). |
| 4 | Analysis vs display dataset | The chart received every candle; the row cap was a guessed 60 000 against a measured quadratic cost. |
| 6/7 | Why Engine projection | Phase 5's structures never reached the API. |
| 10/11 | Technical workspace and overlays | One indicator of twenty-three was surfaced; the chart had no overlays. |
| 13 | Response bounds | Findings grew one-per-malformed-row; the 422 body echoed the rejected value. |
| 12 | Accessibility | Claims were inspection, not measurement. Focus was lost to `<body>`; progress was visual only. |

**Re-proved, not re-implemented**

Vision trust boundary (§5), Why safety (§7), crop and zoom (§8, §9),
cancellation semantics (§14), and the phase boundary.

**Defects found by the closeout's own probes**

Three, all fixed: the unbounded 422 validation body; silent staleness when a
finer timeframe ran ahead of a coarser one; and the two accessibility defects
above. Two further "findings" turned out to be defects in the probes themselves
and are recorded as such — semi-transparent backgrounds were not composited
before measuring contrast (ten false failures, zero real ones), and a small radio
was measured instead of the label that is its hit area.

**Evidence**

| Suite | Result |
| --- | --- |
| Adversarial probes (§15) | 16 of 16 held |
| Mutation probes (§16) | 13 of 13 detected; all 8 touched files restored byte-identically |
| End-to-end through nginx + containers | 23 of 23 passed |
| Accessibility, measured in Chrome | 0 nameless controls, 0 unlabelled inputs, 0 positive tabindex, 0 undersized targets, 0 contrast failures of 350, no horizontal scroll at 1280/640/390/320 px |
| Backend | 2 292 passed, **0 skipped**, 16 import contracts kept (2 353 after the micro-closeout) |
| Frontend | 252 passed across 15 files |

## 54c. Human-review micro-closeout

Three items were held back from approval and are now closed.

**Content-Length fast path — the code was right, the report was not.** The
closeout said a 500 MB body was "refused after 1 MiB received". That figure was
measured from the client side: the probe wrote a megabyte into the socket before
pausing to look, by which time the 413 was already waiting. Counting awaits of
the ASGI `receive` callable shows the server consumed **zero** body events, in
isolation and through the composed app. The wording is corrected; the code is
unchanged.

**Vision non-join — structurally true, and now said out loud.** The backend has
no field to put a Vision reading in, so the claim was already structural. What
was missing was the screen saying so at the moment it matters. The pre-submit
summary now states the scope beside the capability, and a stale note claiming
"no crop or zoom is applied" — true when written, false once the crop shipped —
now states the guarantee that actually holds. Proven in a browser: an analysis
run after a screenshot upload has a byte-identical `analysis_id` to a direct API
call with the same CSVs and no screenshot.

**Output bounds — three more unbounded collections, and a 500.** Walking the
response recursively against a *range-bound* series (the earlier fixture drifted
and produced almost no structure) found evidence at 1 388, one scenario's
supporting list at 1 372, Why reasons at 231 and zones at 29 per timeframe. All
are now capped by their engine's own vocabulary, keeping one of each kind before
any repeat and reporting what was omitted. The same audit produced an
unhandled 500: two identical evidence items collided on a content-derived
reference id, and the resulting `ValueError` destroyed a complete deterministic
analysis. Identical content is now one item, and context assembly is guarded.

| Evidence | Result |
| --- | --- |
| `receive` calls on an over-declared request | **0** |
| Vision separation tests (backend / frontend) | 20 / 14 |
| Browser proof: UI vs direct-API `analysis_id` | byte-identical |
| Collections measured at 300 / 1 200 / 2 500 rows | 37, none unbounded |
| Response size at 1 200 vs 2 500 rows | 530 676 → 530 021 bytes |
| Adversarial probes | 16 of 16 held |
| Mutation probes | 13 of 13 detected, 8 files restored byte-identically |
| End-to-end through nginx | 23 of 23 |
| Backend | 2 353 passed, **0 skipped** |
| Frontend | 266 passed across 16 files |

## 55. Final git status

```
 M backend/app/adapters/market_data/csv_provider.py
 M backend/app/application/ports/market_data.py
 M backend/app/main.py
 M backend/pyproject.toml
 M docs/architecture.md
 M frontend/package-lock.json  package.json  src/App.tsx
 M frontend/src/api/client.ts  src/styles/tokens.css  src/test/setup.ts
?? backend/app/api/{providers.py,routes/analysis.py,schemas/analysis*.py}
?? backend/app/application/analysis/
?? backend/tests/unit/{analysis_api/,test_composition_root.py}
?? docs/phase_reports/phase_8a_*.md
?? frontend/src/{api,components,domain,format,screens,test}/…
```

HEAD `f0b8e24`, `main` tracking `origin/main` with no divergence. Nothing
staged. `git diff --check` clean.

**Nothing committed. Nothing pushed. Phase 9 not started.**
