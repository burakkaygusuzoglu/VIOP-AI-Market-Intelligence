# Phase 6 completion report — Vision foundation, image safety, Claude Vision adapter

Status: **COMPLETE**, awaiting human review.
Nothing is committed. Nothing is pushed. Phase 7 has not been started.

---

## 1. Baseline / resumed state

Phase 6 was implemented across two sessions, both of which ended on usage
exhaustion. This session resumed from repository state rather than memory.

Recovered at resume:

```
HEAD                05b627c  Complete Phase 5 beginner and pro experience
branch              main (== origin/main)
working tree        Phase 6A + partial 6B, entirely uncommitted
```

`git status` showed 7 modified files and 8 untracked paths. No file was in a
syntactically broken or half-edited state; the tree imported, linted and tested.
It was, however, **not** in a working state — see §25, defect 1.

Phase 6A baseline handed forward: **1451 passed, 0 skipped**, 12 import
contracts.

## 2. What already existed before this session resumed

Complete and preserved unchanged except where a defect forced a change:

| Area | State at resume |
| --- | --- |
| `app/domain/vision/` — slots, assets, quality, extraction, precedence | Complete (6A) |
| `app/application/vision/` — images, intake, schemas | Complete (6A) |
| `ScreenshotAnalyzer` port | Complete (6A) |
| Review A — `decode.py`, Pillow bounded decode | Complete (6B) |
| Review B — multi-frame rejection | Complete (6B) |
| Review C — PNG normalisation | Complete (6B), one defect found this session |
| Review D — typed value equality | Complete (6B) |
| Adapter, transport, prompt, errors, corrections | Complete (6B) |
| API route + schemas | Written, **untested** |
| Dependencies in `pyproject.toml` | Added, container never rebuilt |

Remaining and delivered this session: API tests, the `ClockPort` seam, the §20
critical empirical review, all gates, documentation, this report.

## 3. Scope delivered

Carry-forward A–E and brief sections 0–25 in full. Vision is supplementary
context only: no Phase 7 synthesis, no persistence, no UI, no execution.

## 4. Files added / modified

**Added — domain (stdlib only, 1 196 lines)**

| File | Lines | Holds |
| --- | --- | --- |
| `app/domain/vision/slots.py` | 150 | `ScreenshotSlot`, `SlotAssignment`, `TimeframeAgreement` |
| `app/domain/vision/assets.py` | 142 | `ScreenshotAsset` (never bytes), `sanitise_filename`, `content_digest` |
| `app/domain/vision/quality.py` | 239 | 9 §39 dimensions, weights, deterministic `score_quality` |
| `app/domain/vision/extraction.py` | 177 | `ObservedField`, `ObservationKind`, `VisionConfidence`, `VisionExtraction` |
| `app/domain/vision/precedence.py` | 233 | `SourcedValue`, `ValueKind`, `resolve`, `ResolvedValue` |
| `app/domain/vision/corrections.py` | 149 | `CorrectionType`, `FieldCorrection`, `CorrectionLog` |

**Added — application (1 335 lines)**

| File | Lines | Holds |
| --- | --- | --- |
| `app/application/vision/images.py` | 178 | header-only preflight parser, decodes nothing |
| `app/application/vision/decode.py` | 258 | the **only** module that imports Pillow |
| `app/application/vision/intake.py` | 220 | the four-layer pipeline, `accept_and_verify` |
| `app/application/vision/schemas.py` | 115 | strict Pydantic, `extra="forbid"` |
| `app/application/vision/prompt.py` | 103 | versioned extraction-only prompt |
| `app/application/vision/errors.py` | 86 | `VisionFailure`, `VisionProviderError` |
| `app/application/vision/analysis.py` | 354 | review, precedence use case, staleness, corrections |
| `app/application/ports/screenshot.py` | 105 | the port, SDK-free |

**Added — adapters (385 lines)**

| File | Lines | Holds |
| --- | --- | --- |
| `app/adapters/vision/transport.py` | 189 | the **only** module that imports `anthropic` |
| `app/adapters/vision/claude_analyzer.py` | 173 | `ScreenshotAnalyzer` implementation |

**Added — API (210 lines)**: `app/api/routes/screenshots.py`,
`app/api/schemas/screenshots.py`.

**Modified**

| File | Change |
| --- | --- |
| `backend/pyproject.toml` | 3 dependencies with stated reasons; the vision contract, bringing the total to 12 |
| `app/core/config.py` | `vision_model`, timeout, retries, max tokens, `vision_is_configured` |
| `app/core/logging.py` | WARNING floor on Pillow loggers (§25 defect 3) |
| `app/main.py` | registers the screenshots router |
| `.env.example` | blank `ANTHROPIC_API_KEY`, `VISION_MODEL`, timeouts |
| `tests/unit/test_architecture.py` | clean-interpreter import test (§25 defect 1) |
| `tests/unit/test_logging.py` | Pillow-silence tests |
| `docs/architecture.md` | Phase 6 architecture, contract 11, the pipeline |

**Dependencies added** — each with a written reason in `pyproject.toml`:

| Package | Installed | Reason |
| --- | --- | --- |
| `pillow>=11.0` | 12.3.0 | real bounded decode; the 6A review ruled out growing the hand-written parser into a decoder |
| `anthropic>=0.40` | 1.0.0 | the Vision provider, confined to one adapter |
| `python-multipart>=0.0.9` | 0.0.32 | FastAPI cannot parse a file upload without it |

## 5. Secure image validation

Four layers, in order, all before the provider exists:

1. **Byte-size bound** — `ImagePolicy.max_bytes`, checked before anything
   parses the buffer. The route reads `max_bytes + 1` so an oversized body is
   detected without holding an unbounded upload in memory.
2. **Signature preflight** — the Phase 6A parser reads PNG `IHDR`, JPEG `SOF`
   and WEBP `VP8`/`VP8L`/`VP8X` headers. Extension and `Content-Type` are
   recorded and **never trusted**; a GIF named `chart.png` and declared
   `image/png` is refused.
3. **Dimension / pixel policy** — declared dimensions are checked before decode,
   so a header-declared bomb dies without allocation.
4. **Real bounded decode** — see §6.

A disagreement between the header parser and the decoder about the format is
itself a rejection (`FORMAT_DISAGREEMENT`), rather than a preference for one.

## 6. Real decode / decompression-bomb protection

`verify_and_normalise` in `app/application/vision/decode.py`:

- **No request mutates any global state** (revised in the final review — see
  §25b, defects 1 and 2). `DecodePolicy` limits are enforced by this module's
  own arithmetic against the dimensions the decoder reports, *before*
  `verify()` or `load()` runs. `Image.open` reads a header and allocates no
  pixel buffer, so a bomb is refused before it can expand.
- `configure_image_safety()` sets a fixed `PILLOW_PIXEL_CEILING` (80 M) **once
  at application startup**, idempotently. `DecodePolicy` refuses a `max_pixels`
  above it at construction, so policy and backstop cannot contradict each other.
  Pillow's guard is configured, never suppressed and never disabled.
- The two layers are independent: a pixel bomb is refused whether or not
  startup configuration has run.
- `verify()` then `load()`: a structural check followed by a real, full decode.
  A file with a valid header and a corrupt or truncated body fails here.
- Typed failures: `UNIDENTIFIED`, `UNSUPPORTED_FORMAT`, `MULTI_FRAME`,
  `CORRUPT`, `TRUNCATED`, `DECOMPRESSION_BOMB`, `EXCESSIVE_PIXELS`,
  `NORMALISATION_TOO_LARGE`.
- No exception message contains image content; asserted by test.

Verified inside the Linux container as well as on the Windows host: PNG, JPEG
and WEBP decode identically, animated WebP is `MULTI_FRAME`, a truncated PNG is
`CORRUPT`.

## 7. Static-image policy

Multi-frame content is **rejected, never sampled**. `DecodePolicy.allow_multi_frame`
defaults to `False` and `n_frames > 1` raises `MULTI_FRAME`.

Probed and confirmed: animated WebP → `MULTI_FRAME`; APNG (a real PNG carrying
an `acTL` chunk) → `MULTI_FRAME`. Nothing analyses "whichever frame Pillow
happened to open".

## 8. Screenshot slots / identity

`ScreenshotSlot` is `1D | 1H | 15M | 5M`. Every observation carries its
`screenshot_id` and its slot. `SlotAssignment` keeps three distinct facts —
**expected** (the slot the user uploaded into), **detected** (what the model
read off the chart) and **confirmed** (what a user later vouched for) — and
`effective_timeframe` is `None` while they disagree.

`sanitise_filename` discards the directory part entirely, so
`../../etc/passwd.png` cannot survive as a path; the public identity is a
content digest, not a name.

## 9. Screenshot Quality

Nine §39 dimensions, three states each (`PRESENT` / `ABSENT` / `NOT_EVALUATED`),
weighted, summed in deterministic Python and versioned as
`screenshot-quality/1`.

- The model never supplies a score. A response carrying a `quality_score` field
  is refused outright by `extra="forbid"` — probed.
- Vision fills the dimensions 6A could not evaluate, by *what was actually
  read*: a dimension is `PRESENT` only when its field appears in the extraction.
- Below `QualityPolicy.minimum_coverage` (0.25) the score is `None`, not a
  number computed from almost nothing.
- `NOT_EVALUATED` never silently becomes `ABSENT`.

## 10. Vision schemas

`VisionExtractionSchema` with `extra="forbid"` at every level. Rejected and
tested: malformed JSON, non-JSON prose, `null` values, unknown top-level field,
unknown `ObservedField`, unknown `ObservationKind`, confidence outside `[0, 1]`.

Nothing is repaired. `_validate` raises `INVALID_OUTPUT` and its message never
echoes model content.

## 11. `ScreenshotAnalyzer` boundary

The port takes an `AcceptedScreenshot` — a type that can only be produced by
`accept_and_verify`. The decode boundary is therefore structural: an analyser
*cannot be handed* bytes that skipped verification.

The port and the whole application layer are free of SDK types; asserted by
`test_vision_architecture.py`.

## 12. Claude Vision adapter

`ClaudeScreenshotAnalyzer` depends on a `VisionTransport` protocol, so every
path is exercised with a fake. **No test needs an API key, a network connection
or a paid call.**

`AnthropicVisionTransport` is the only file importing `anthropic`. The SDK
surface was verified empirically against the installed 1.0.0 before being used —
nothing was assumed. `messages.create` with typed `ImageBlockParam` /
`Base64ImageSourceParam` / `MessageParam` was chosen over
`messages.parse(output_format=)` deliberately, so that "invalid output must not
be accepted" is enforced and tested in code this project owns.

## 13. Prompt / schema versioning

`PROMPT_VERSION = "vision-extraction/1"`, `SCHEMA_VERSION = "vision-schema/1"`,
`method_version = "screenshot-quality/1"`. The prompt forbids, in its own text
and asserted by test: calculating anything, recommending a direction, LONG /
SHORT / BUY / SELL / WAIT, proposing entry / stop / target, and guessing a value
it cannot read. `UNKNOWN` / unreadable is instructed as the correct answer.

## 14. Structured extraction

Screenshot-observable fields only: symbol, timeframe, trend context, market
structure, support/resistance, candlestick context, indicator readings, volume
context, user-drawn levels, plus an explicit `unreadable` list. Each observation
keeps screenshot id, slot, source priority, confidence, `DIRECTLY_VISIBLE` vs
`VISUALLY_INFERRED`, and the model identifier.

## 15. Confidence semantics

`VisionConfidence` is a `Decimal` in `[0, 1]` and is documented as a **legibility
signal, not a probability about the market**. Probed:

- missing confidence stays `None` — never 0, never 1;
- low confidence (0.05) survives to the response unchanged;
- confidence **cannot** influence precedence — a 1.0-confidence AI claim loses
  to a 0.01-confidence structured value.

## 16. Source precedence

Phase 0's `DataSourcePriority` is used unchanged; no new hierarchy was invented.
`resolve` ranks candidates and ties break on input order, never on confidence.

The §13 rule, probed end to end: a user confirms "the screenshot visibly says
RSI 63.2"; the resolved calculation authority is still the structured
`RSI 61.27`, the screen reading remains answerable through
`observed_value(...)`, and the displaced claim stays in `conflicts`.

## 17. Typed value equality

`SourcedValue` keeps `value` (verbatim, for audit) and `comparable` (canonical).
For `ValueKind.NUMERIC` the canonical form is an exact `Decimal` normalised for
trailing zeros — **never a float**, with **no tolerance**. Categorical values
keep exact string equality, so instrument codes are not case-folded into each
other.

61.27 / 61.270 / "61.27" resolve as one observation with zero conflicts and the
original representation preserved; 61.27 vs 61.28 remains a genuine conflict;
`Decimal("0.1")` does not equal the binary float expansion of 0.1. Precedence
ordering was not modified by this work.

The guarantee starts at the **JSON parser**, not at the comparison (§25b,
defect 4). A model may answer `{"value": 61.27}` rather than `{"value":
"61.27"}`. Parsed the default way that becomes a binary float, and
`Decimal(61.27)` is `61.27000000000000312638803734444081783294677734375` — a
conflict manufactured out of nothing but representation. The response is
therefore parsed with `parse_float=Decimal`, which hands the JSON module's own
**lexical token** to `Decimal`; the schema renders it with `format(d, "f")`
(preserving trailing zeros for audit, avoiding exponent forms), and refuses a
bare `float` for both `value` and `confidence` so the exact path cannot be
bypassed. Very small and very large decimals were tested explicitly.

## 18. Expected-vs-detected validation

A 1H slot holding a 15M chart stays `is_mismatched`, keeps both values, sets
`effective_timeframe = None` and produces a plain-language entry in
`mismatches`. Expected context is never rewritten. A symbol mismatch is reported
the same way.

**Corrected in the final review (§25b, defect 3):** an earlier version of this
report stated that "a case-only difference is not a mismatch". That was Phase 6
inventing a second, looser identity policy. The project's rule is Phase 3's, in
`app/domain/common/identity.py` — exact match after stripping whitespace, no
case folding, because casing significance is an exchange convention nobody here
has verified. A case difference is now surfaced as a mismatch for the user to
settle, and 25 cross-phase invariant tests prove the futures engine and the
screenshot check cannot drift apart.

## 19. Correction workflow

`CONFIRMED` / `CORRECTED` / `REJECTED`, for any field including symbol and
timeframe. The original `ExtractedValue` is held *inside* the correction, so it
can never be erased. `CorrectionLog` is append-only — `with_correction` returns
a new log — and later corrections supersede earlier ones for authority while the
earlier ones stay in the audit trail.

`record_correction` takes a `ClockPort` (§25, defect 5): the timestamp comes
from the port, so a replay stamps the instants replay actually had. Correcting a
field the screenshot never mentioned is refused rather than invented.

**Reachability (§25b, defect 6).** The workflow was reachable only from a test —
nothing in `app/` called it. It now has a use case,
`application/vision/correction_workflow.py`, and an edge,
`POST /api/screenshots/corrections`, exercised end to end over HTTP. No
persistence was added: the endpoint is stateless and the caller submits the
observation it is correcting. The honest cost is recorded rather than hidden —
the original reading is taken on trust, and a persistence phase should load it
by `screenshot_id` and drop `original_value` from the request.

The response answers both questions separately: `observed_screen_value` (what
the picture shows) and `authoritative_value` (what an engine may use), plus
`user_input_was_overridden` so a user whose correction loses to structured
market data is told, not left to infer it.

## 20. Multi-screenshot workflow

`ScreenshotSetReview` reports `missing_slots`, `duplicate_slots`,
`symbol_disagreements` and `timeframe_mismatches`. **Nothing is averaged and
nothing is merged**: four screenshots are four observations. A cross-screenshot
symbol disagreement is reported, never resolved — only the user can say which
chart is of the wrong instrument. A missing screenshot is allowed and explicit.

## 21. Provider errors / retries

`VisionFailure` — `TIMEOUT`, `NETWORK`, `RATE_LIMITED`, `AUTHENTICATION`,
`CONFIGURATION`, `PROVIDER_REJECTED`, `INVALID_OUTPUT`, `UNAVAILABLE` — each
with a `.retryable` property. `INVALID_OUTPUT` is deliberately **not**
retryable: re-asking a model that returned malformed output is not a recovery
strategy.

Retries are the SDK's bounded `max_retries` (default 2, configurable), applied
to transient failures only. There is no retry loop in this codebase — verified
by inspection: no `while True` in the transport.

Every error carries an internal `detail` for logs and a fixed Turkish
`public_detail` for clients. The HTTP mapping reads the failure **type**, never
a message.

## 22. API / use case

`POST /api/screenshots/analyse`: upload → validation/decode → analyzer → strict
schema → deterministic quality → mismatch evaluation → typed response.

`POST /api/screenshots/corrections`: CONFIRM / CORRECT / REJECT → precedence →
typed response carrying observed value and calculation authority separately.

No database table, no persistence, no UI, no paper trading, no broker
functionality. The response model has **no field for a trade action**, which is
how LONG/SHORT/WAIT are kept in Phase 7 — there is nowhere to put one.

`get_analyzer` is a dependency seam. Unconfigured, it returns a typed 503
(`VISION_NOT_CONFIGURED`) instead of constructing a client — confirmed against
the running container.

## 23. Security review

| Concern | Outcome |
| --- | --- |
| Client self-assigning a source / priority / verification status | Impossible — the public schema has no such field, and `ServerAnalysisContext` is unrepresentable in a request |
| Client obtaining `STRUCTURED_MARKET_DATA` authority | Refused — see §25c |
| Client-replayed context described as server-verified | Never — reported as `CLIENT_REPLAYED_UNVERIFIED` |
| Extension spoof | Refused — `UNREADABLE_IMAGE` |
| `Content-Type` spoof | Refused; declared type is recorded, never trusted |
| Valid header + corrupt body | Refused — `CORRUPT` |
| Valid header + truncated body | Refused — `CORRUPT` |
| Pixel bomb past signature check | Refused — `DECOMPRESSION_BOMB` / `EXCESSIVE_PIXELS` |
| Animated WebP / APNG | Refused — `MULTI_FRAME` |
| Path traversal filename | Directory part discarded; identity is a digest |
| Image bytes in logs | None — including Pillow's own DEBUG output (§25, defect 3) |
| Image metadata to the provider | Stripped by normalisation; asserted |
| API key in logs | Scrubbed via `secret_values()`; probed |
| API key in responses | Never; public detail is a fixed phrase |
| Stack trace in responses | Never; probed |
| Provider called after failed validation | Impossible; asserted through the real route |
| Credentials in the repo | None. `.env.example` placeholders are blank |

## 24. Financial authority boundary

Contract 11 makes it mechanical: `technical`, `structure`, `futures`, `risk`,
`analysis`, `suitability` and `market` **cannot import** `app.domain.vision`.
An indicator, a P&L or a position size cannot be derived from a screenshot
reading because the module that would do it cannot see the module that holds it.

Probed for `position_size`, `stop_loss`, `take_profit`, `pnl`,
`margin_required`, `DevilsAdvocate` and direction recommendation across the
entire vision slice — zero hits. Claude reads pictures; Python owns every
number.

## 25. Critical empirical review

Adversarial probing, not test re-running. **Five real defects found; all fixed.**

**Defect 1 — the application could not start at all.** *(critical)*
`app.application.ports.screenshot` imported from `app.application.vision.intake`,
whose package `__init__` re-exported `analysis`, which imported the port back.
`uvicorn app.main:app` raised `ImportError` on both host and container. All six
gates were green: import-linter checks direction, not cycles, and the test suite
imported the adapter first, warming the package in a lucky order. Confirmed by
recreating the container, which crashed on exactly this. Fixed by removing the
re-export facade (nothing depended on it). A clean-interpreter import test was
added and **proven to fail against the bug** before being kept. All 123 modules
now import successfully as the first statement of a fresh interpreter, and the
rebuilt container serves `/api/screenshots/analyse`.

**Defect 2 — normalisation destroyed transparent charts.** `convert("RGB")`
drops the alpha channel rather than compositing it, turning every transparent
pixel black. A chart exported with a transparent background and dark axis
labels would have reached the provider as dark ink on black, unreadable, with
nothing raised — the exact undocumented lossy transformation review C forbids.
Fixed with `_target_mode`: the output is PNG, which carries alpha, so alpha is
kept; only modes PNG cannot represent (CMYK, high-bit, float) are collapsed.
Regression tests cover RGBA, palette-with-transparency, and pixel-exact
preservation of opaque images.

**Defect 3 — Pillow narrated uploaded images into the log.** At DEBUG the
`PIL.PngImagePlugin` logger emitted chunk structure (`STREAM b'IDAT' 86 1006`)
and metadata. Raising the root logger to chase an unrelated bug would silently
have started writing user charts to logs. Fixed with a WARNING floor on the PIL
loggers in `configure_logging`, tested against a real decode at DEBUG.

**Defect 4 — untrusted model output could raise out of a resolution.** Asked for
a price, a model may answer "roughly 61 or so"; `SourcedValue` correctly refuses
a non-numeric numeric claim, but the `ValueError` escaped `effective_value`.
Fixed by catching it *only* for untrusted sources and recording it on
`ResolvedValue.unusable` — nothing is dropped silently, and `SourcedValue` stays
strict so the same text arriving as structured market data is still loud.
"Nothing observed" (`None`) and "nothing usable" (`UnusableClaimsError`) are now
distinguishable.

**Defect 5 — staleness had no clock and the wrong semantics.** The docstring
promised `ClockPort` but nothing supplied one. Worse, any positive age returned
`STALE` — a one-second-old screenshot was stale — while a capture time *ahead*
of now returned `FRESH`, which is the dangerous direction: an unread timezone or
a skewed clock lands on the reassuring answer. Fixed with a `Staleness` enum, a
**required explicit `max_age`** (no invented constant), `UNDETERMINED` for a
future capture instant, and `staleness_of` / `record_correction` taking a
`ClockPort`.

**Probes that found nothing wrong** (11/11 clean): APNG and animated WebP
rejection, MIME and extension spoofing, path traversal, structured data beating
a user-confirmed screen reading, the screen reading surviving a correction,
conflicts remaining auditable, missing confidence staying missing, low
confidence surviving, a model being unable to supply its own quality score, and
no Phase 7 leakage in the vision slice.

Two probes initially looked like defects and were **not**: an unparseable
numeric refused at construction is correct (my probe mutated a categorical
field), and `usage=None` crashing the analyser was my probe violating a
non-optional type — `_usage_of` returns `None` token counts rather than
fabricating zeros, which is what §15 requires.

## 25b. Final human-review closeout

A second review round audited seven specific issues. **Four were real defects**,
two were not, and one was a documentation inconsistency.

**Issue 1 — Pillow `MAX_IMAGE_PIXELS` concurrency. REAL DEFECT.**
`verify_and_normalise` set the process-global `Image.MAX_IMAGE_PIXELS` from the
policy per call. Measured with threads, a concurrent observer saw **two distinct
ceilings** (`7000000` and `89478485`) while decodes were in flight — so the
limit in force during any decode was whatever another request had last written.
A strict request could decode beneath a permissive request's raised ceiling.
Fixed: no request mutates any global. `DecodePolicy` limits are enforced by the
decode module's own arithmetic against decoder-reported dimensions *before*
`verify()`/`load()`, and `configure_image_safety()` sets a fixed
`PILLOW_PIXEL_CEILING` once at startup, which `DecodePolicy` may not exceed.
Both new concurrency tests were **confirmed to fail against the old design**.

**Issue 2 — warning-filter concurrency. REAL DEFECT.** The per-call
`warnings.catch_warnings()` was measured leaking: an observer thread saw the
promoted `DecompressionBombWarning` filter while doing no decoding itself.
Removed entirely; the explicit pixel check is the boundary, and Pillow's own
`DecompressionBombError` still maps to a typed failure. Protection is not
weakened — a pixel bomb is refused whether or not startup configuration has run,
because the primary layer does not depend on the backstop.

**Issue 3 — symbol identity. REAL DEFECT.** Phase 3's `require_matching_quote`
fixes the project rule and says so explicitly: exact match after stripping,
*"no case folding … because every one of those would be an exchange convention
this project has not verified."* Phase 6's `SymbolAgreement` case-folded,
inventing a second and looser policy — and putting the looser one exactly where
a user is told whether the chart shows the instrument they meant. The previous
report documented this as a feature. Fixed: the rule now lives once in
`app/domain/common/identity.py`; Phase 3's two validators and Phase 6's checks
both call it, with Phase 3 behaviour unchanged. A case difference is now a
**mismatch surfaced to the user**, not a silent merge. Two tests that encoded the
old behaviour were corrected, and 25 cross-phase invariant tests were added.

While fixing this, a pre-existing §118 test caught a real instrument code in a
docstring I had written; it was replaced with neutral wording.

**Issue 4 — JSON number → Decimal. LATENT GAP, now closed.** No float noise
could occur, because a JSON number was **rejected outright** by the strict
schema. That is safe but brittle: a model writing `61.27` instead of `"61.27"`
lost an entire screenshot analysis. Fixed exactly, without tolerance: the
response is parsed with `parse_float=Decimal`, which hands the JSON module's own
lexical token to `Decimal` (`Decimal("61.27")`, not
`Decimal(61.27)` = `61.27000000000000312638803734444081783294677734375`). The
schema renders it with `format(d, "f")`, preserving trailing zeros for audit,
and refuses a bare `float` so the exact path cannot be bypassed — for
`confidence` as well as `value`. Source precedence is untouched.

**Issue 5 — quality polarity. NOT A DEFECT.** §39's negative conditions are
already named positively (`NOT_CROPPED`, `NOT_STALE`) so `PRESENT` is the good
outcome everywhere. Proven exhaustively: across all nine dimensions and both
unevaluated policies, **zero** combinations existed where `ABSENT` scored higher
than `PRESENT`. Cropped costs 5 points, stale costs 5. The adapter never sets
either, so staleness stays `NOT_EVALUATED` rather than being invented, and an
unknown dimension is visible as reduced coverage. Locked with new tests.

**Issue 6 — correction workflow reachability. REAL DEFECT.** Phase 6 requires
CONFIRM / CORRECT / REJECT, and `record_correction` was called from **nothing**
in `app/` — no route, no use case. The workflow was reachable only from a test.
Fixed with the smallest boundary: `application/vision/correction_workflow.py`
and `POST /api/screenshots/corrections`. **No persistence was added.** It is
stateless, and the cost is stated rather than hidden: the original observation
is taken on trust from the caller, and a persistence phase should load it by
`screenshot_id` instead. The response returns observed screen value and
calculation authority as separate answers, plus `user_input_was_overridden`.

**Issue 7 — report consistency. REAL (documentation).** Fixed in §28 above.

**Regression probes: 26/26 clean** — concurrent validation, ceiling stability
(verified stable both before and after startup configuration), corrupt body,
truncated body, pixel bomb, animated WebP, APNG, provider never invoked after a
failed decode, symbol identity under the existing rule, JSON numeric versus
structured Decimal, both quality polarities, all three correction actions
preserving the original, structured data still beating a user-confirmed screen
reading, and no Phase 7 leakage.

## 25c. Provenance trust-boundary audit

A third review round audited one question: can an API client choose the trust
rank of a value it supplies? **It could. REAL DEFECT — introduced by the
correction endpoint added in round two (§25b, defect 6).**

**Evidence, against the running container.** The literal label was refused:
`{"source": "STRUCTURED_MARKET_DATA"}` → `422 extra_forbidden`. But the *value
that receives the label* was not:

```
POST /api/screenshots/corrections   {"structured_value": "999", ...}
→ 200 {"authoritative_value": "999",
       "authoritative_source": "STRUCTURED_MARKET_DATA"}
```

A number a hostile caller typed came back as validated market data. `extra="forbid"`
had blocked the label while the workflow stamped `STRUCTURED_MARKET_DATA` onto
whatever arrived in `structured_value`. Self-assigning the label and
self-assigning the value that gets the label are the same escalation; only the
second one looks harmless.

A second, milder vector: `original_kind` was client-chosen, and it selects
`SCREENSHOT_EXTRACTED` (rank 4) versus `AI_VISUAL_INFERENCE` (rank 5). Below
`USER_CONFIRMED` either way, so no escalation past the user — but still the
client picking its own precedence, and the response then described the value as
`SCREENSHOT_EXTRACTED` as though the server had extracted it.

**Changes.** The reviewer's first suggested design, taken literally: the
workflow accepts server-owned context internally while the public API accepts
only user-correction fields.

* `ServerAnalysisContext` — a frozen dataclass holding the structured figure.
  **Only server code constructs it.** No field of `CorrectionRequestBody`
  produces one, so the escalation is not validated away, it is unrepresentable.
  It is the sole path to `STRUCTURED_MARKET_DATA` in this module.
* `CorrectionRequestBody` lost `structured_value` and `original_kind`. Its eight
  remaining fields are all user-correction data.
* `original_value` → `replayed_observation`, named for what it is, and it enters
  precedence at the weakest rank (`VISUALLY_INFERRED` → `AI_VISUAL_INFERENCE`)
  regardless of what the caller says.
* `ObservationOrigin` — a typed distinction between
  `CLIENT_REPLAYED_UNVERIFIED` (Phase 6's only reachable value) and
  `SERVER_VISION_RESULT` (no Phase 6 path produces it; it exists so a
  persistence phase has somewhere honest to put it). Returned on the response as
  `observed_value_origin`, so nothing downstream has to assume.
* The route leaves `server_context` and `origin` at their untrusted defaults,
  with a comment saying a future store is the only thing that may set them.

**No persistence, no signing, no session infrastructure was added.**

**Verified against the rebuilt container.** The original hostile request now
returns `422 extra_forbidden` on `structured_value`. A legitimate correction
returns `authoritative_source: USER_CONFIRMED`,
`observed_value_origin: CLIENT_REPLAYED_UNVERIFIED`, and the displaced claim
recorded as `AI_VISUAL_INFERENCE reported '63.2'`.

**Tests.** 32 adversarial tests in `tests/unit/vision/test_provenance_trust.py`:
15 forged-field variants (`source`, `source_priority`, `priority`, `authority`,
`verification_status`, `verified`, `trusted`, `origin`, `server_context`,
`structured_value`, `authoritative_value`, …) each asserted to return
`extra_forbidden`; a structural test pinning the schema's exact field set; proof
that no `ServerAnalysisContext` is reachable from the request type; the hostile
request itself; the trusted server path still beating `USER_CONFIRMED`;
`USER_CONFIRMED` still beating the replayed observation; a user correction never
becoming market data under either value kind; statelessness; and no Phase 7
leakage. Two of them were **confirmed to fail** against the reinstated hole
before being kept.

## 26. Tests and exact counts

| File | Tests |
| --- | --- |
| `tests/unit/vision/test_adapter_and_workflow.py` | 78 |
| `tests/unit/vision/test_slots_and_quality.py` | 65 |
| `tests/unit/vision/test_intake.py` | 46 |
| `tests/unit/vision/test_extraction_and_precedence.py` | 39 |
| `tests/unit/vision/test_decode_security.py` | 38 |
| `tests/unit/vision/test_json_decimal.py` | 32 |
| `tests/unit/vision/test_provenance_trust.py` | 32 |
| `tests/unit/vision/test_symbol_identity.py` | 26 |
| `tests/unit/vision/test_screenshot_api.py` | 24 |
| `tests/unit/vision/test_vision_architecture.py` | 19 |
| `tests/unit/vision/test_correction_workflow.py` | 17 |
| **Phase 6 total** | **416** |

Suite total: **1740 passed, 0 skipped** (Phase 6A baseline: 1451).
Net new: **289** beyond the 6A baseline, plus the tests added to
`test_architecture.py` and `test_logging.py`.

Integration tests are included in that 0-skipped figure: PostgreSQL was started
and a `viop_test` database created, so the database tests actually ran.

Integration tests are included in that 0-skipped figure: PostgreSQL was started
and a `viop_test` database created, so the database tests actually ran rather
than being reported as skipped.

## 27. Quality gates

Fresh run, all green:

| Gate | Result |
| --- | --- |
| `ruff check .` | All checks passed |
| `ruff format --check .` | 210 files already formatted |
| `mypy --platform linux` | no issues in 208 source files |
| `mypy --platform win32` | no issues in 208 source files |
| `lint-imports` | **12 kept, 0 broken** |
| `pytest` | **1740 passed, 0 skipped** |
| Frontend tests | 12 passed (2 files) |
| Frontend typecheck / ESLint / Prettier | clean |
| Frontend build | built in 703 ms |
| `docker compose config -q` | OK |
| `docker compose build backend` | built (dependencies changed) |
| Stack | backend, frontend, postgres all **healthy** |
| Live route check | `/api/screenshots/analyse` served; typed 503 when unconfigured |

The Docker build and live route check were run **because** dependencies changed,
and they are what caught defect 1.

## 28. Architecture review

Dependency direction holds: `api → application → domain`, adapters implement
ports, the domain imports nothing outward. The domain vision package is stdlib
only — no pydantic, no SDK, no Pillow.

Third-party containment, asserted by test:

- Pillow appears in exactly one module, `app/application/vision/decode.py`.
- The `anthropic` SDK appears in exactly one module,
  `app/adapters/vision/transport.py`.
- The header parser still decodes nothing.
- No port, use case or domain module holds an SDK type.

One contract was added this phase — *"No calculation engine depends on
screenshot vision"*, which appears **11th** in `pyproject.toml` and brings the
total to **12**. (An earlier draft of this report called it both "the 12th
contract" and "contract 11", which was confusing; both described the same rule.)
`docs/architecture.md` numbers it 11 to match the file order.

`docs/architecture.md` now documents the pipeline, the observed-value /
calculation-authority split, the per-call image limits, the single instrument
identity rule, and the lesson that import *direction* is necessary but not
sufficient.

## 29. Technical debt / limitations

Stated plainly rather than hidden:

1. **No real provider call has ever been made.** Every adapter test uses a fake
   transport. The SDK surface was verified empirically, but the live
   request/response shape is unproven until someone runs it with a key. The
   model identifier deliberately has **no default** — an unconfigured system
   refuses rather than silently choosing an unknown model.
2. **No screenshot persistence.** Analysis is transient. Two consequences worth
   naming: Phase 8 cannot re-display a screenshot, and the corrections endpoint
   must take the original observation on trust because there is nothing to
   verify it against. When a store arrives, `correction_workflow` should load
   the observation by `screenshot_id` and the request schema should lose
   `original_value` entirely; nothing else in that module needs to change.
3. **Staleness is effectively always `UNDETERMINED`** in practice: chart
   timestamps are rendered in an unknown timezone, so `observed_at` is not
   populated. The machinery is correct and tested; it awaits a trustworthy
   capture instant.
4. **Quality dimension coverage is partial.** Several §39 dimensions can only be
   filled by fields the model may not read, so `NOT_EVALUATED` is common and the
   score is often `None`. That is honest, not broken.
5. **`ObservedField` is a fixed vocabulary.** A chart element outside it is
   unreportable; widening it is a schema-version change.
6. **Normalisation always re-encodes to PNG**, costing CPU and some bytes on a
   large JPEG. Accepted deliberately: metadata stripping and static-frame
   guarantees are worth more than the bytes.
7. **`domain/vision/__init__.py` still re-exports.** It is cycle-free today and
   covered by the clean-interpreter test, but it is the same shape that caused
   defect 1 and would be better inert.

## 30. Phase-boundary verification

Not implemented, and mechanically absent:

- final LONG / SHORT / global WAIT / global NO TRADE synthesis
- Devil's Advocate
- Bull / Bear / Neutral AI synthesis
- stop, target, sizing or contract-count generation
- Phase 7 `AnalysisResult`
- Phase 8 UI, persistence, paper trading, broker or execution functionality

Probed across the whole vision slice for sizing, stop, target, P&L, margin and
direction-recommendation identifiers: zero hits. The API response type has no
field a trade action could occupy.

Master spec §120 holds: execution remains disabled. Midas is never scraped,
automated or asked for credentials.

## 31. Final Git status

Nothing committed, nothing pushed. `HEAD` remains `05b627c`.

```
 M .env.example
 M backend/app/api/dependencies.py
 M backend/app/core/config.py
 M backend/app/core/logging.py
 M backend/app/domain/futures/contract.py
 M backend/app/main.py
 M backend/pyproject.toml
 M backend/tests/unit/test_architecture.py
 M backend/tests/unit/test_logging.py
 M docs/architecture.md
?? backend/app/adapters/vision/
?? backend/app/api/routes/screenshots.py
?? backend/app/api/schemas/screenshots.py
?? backend/app/application/ports/screenshot.py
?? backend/app/application/vision/
?? backend/app/domain/common/identity.py
?? backend/app/domain/vision/
?? backend/tests/factories_vision.py
?? backend/tests/unit/vision/
?? docs/phase_reports/phase_6_completion_report.md
```

`backend/app/domain/futures/contract.py` is the only Phase 3 file touched: its
two symbol validators now call `domain.common.identity` instead of restating the
rule inline. Behaviour is unchanged and the full Phase 3 suite passes.

`docs/viop_master_spec.md` is unmodified. Phase 0–5 completion reports are
unmodified.

**Phase 6 stops here, awaiting human review.**
