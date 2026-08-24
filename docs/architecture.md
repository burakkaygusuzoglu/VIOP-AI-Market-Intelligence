# Architecture

Companion to `viop_master_spec.md` sections 94–95. Where they disagree, the
master specification wins.

## Layers

```
                 ┌──────────────────────────────────────────┐
   HTTP / WS ──▶ │ app/api          routes, schemas, deps    │
                 └───────────────────┬──────────────────────┘
                                     │ calls
                 ┌───────────────────▼──────────────────────┐
                 │ app/application  use cases, DTOs, PORTS   │
                 └───────────────────┬──────────────────────┘
                                     │ depends on
                 ┌───────────────────▼──────────────────────┐
                 │ app/domain       pure financial logic     │
                 └──────────────────────────────────────────┘
                                     ▲
                 ┌───────────────────┴──────────────────────┐
                 │ app/adapters     implement the ports      │
                 │ postgres · market data · AI · news        │
                 └──────────────────────────────────────────┘
```

Inside `app/domain` the dependency order is equally strict. Analysis sits above
the engines it reads, risk stays account-side and analysis-blind, and exactly
one package is allowed to join them:

```
  market ─▶ technical ─▶ structure ─┐
                                    ├─▶ analysis ─┐
                        futures ────┘             ├─▶ suitability
                        futures ─▶ risk ──────────┘      (NO TRADE veto)
```

`analysis` may never import `risk`: §43 separates a technical score, which must
read identically for every user, from trade suitability, which cannot be
answered without knowing the account. `suitability` consumes both as finished
typed results and reimplements neither.

`app/main.py` and `app/api/dependencies.py` form the **composition root** —
the only place a concrete adapter is attached to a port. Route modules never
see an adapter, a session or an engine.

### Enforced rules

Declared as import-linter contracts in `backend/pyproject.toml` and executed by
`backend/tests/unit/test_architecture.py`:

1. **Domain is pure** — may not import fastapi, starlette, sqlalchemy, alembic,
   anthropic, httpx, requests, pydantic, pydantic-settings, or any outer layer.
2. **Application depends only on domain** — no infrastructure, no API layer.
3. **Adapters never import the API layer.**
4. **Adapters never compute indicators, structure, risk, contract maths or
   analysis** — `app.adapters` may not import `app.domain.technical`,
   `app.domain.structure`, `app.domain.risk` or `app.domain.analysis`. Every
   one of those is a numerical or analytical authority and stays where it is
   tested and type-checked as such. *(Phase 1, widened in Phases 2, 3 and 4)*
5. **The technical engine does not depend on market structure** —
   `app.domain.technical` may not import `app.domain.structure`. The dependency
   runs one way, so Phase 1 indicators stay usable on their own and an
   indicator cannot reach for structure and create a circular definition.
   *(Phase 2)*
6. **The risk engine depends only on contract facts, never on analysis** —
   `app.domain.risk` may not import `app.domain.structure` or
   `app.domain.technical`. A P&L or position size must not be coupled to an
   analysis opinion, and it keeps the §45 volatility-warning deferral from
   being undone by accident. *(Phase 3)*
7. **The futures domain does not depend on analysis engines** — contract facts
   are inputs to analysis, never outputs of it. *(Phase 3)*
8. **Nothing beneath the analysis layer depends on it** — `app.domain.analysis`
   sits at the top of the domain: it consumes the technical, structural and
   futures engines and none of them may import it back. The reverse direction
   would let an indicator depend on a multi-timeframe opinion, and would let
   evidence quietly become an input to itself. *(Phase 4)*
9. **The analysis layer never depends on the risk engine** — `app.domain.
   analysis` may not import `app.domain.risk`. §43 separates *setup quality*
   from *trade suitability*: a technical score must read identically for every
   user, and it cannot if an account balance can reach it. *(Phase 4)*
10. **Nothing beneath the suitability layer depends on it** —
    `app.domain.suitability` is the single place allowed to depend on both
    analysis and risk, and it sits above both so neither has to know about the
    other. A veto is the end of the chain, never an input to what it vetoes.
    *(Phase 4)*
11. **API routes and schemas never reach into adapters or SQLAlchemy.**

These are verified to actually fail when violated; the check is not decorative.

## Target backend tree

Packages marked *(phase N)* do not exist yet. They are created by the phase
that owns them, so the tree never contains empty placeholders.

```
backend/app/
├── domain/
│   ├── common/        enums, VerifiedValue                  [Phase 0]
│   ├── market/        Candle, series, Data Quality Engine   [Phase 0/1]
│   ├── technical/     EMA, RSI, ATR, VWAP, MACD, ADX, BB    [Phase 1]
│   ├── structure/     swings, BOS/CHOCH, S/R, regime        [Phase 2]
│   ├── futures/       FuturesContract, basis, OI            [Phase 3]
│   ├── risk/          sizing, limits, margin, P&L           [Phase 3]
│   ├── analysis/      MTF roles, evidence, fusion, quality,
│   │                  entry quality, scenarios              [Phase 4]
│   ├── suitability/   NO TRADE veto (analysis + risk)       [Phase 4]
│   ├── strategies/    strategy configs, router              (phase 5+)
│   ├── trading/       paper positions, lifecycle            (phase 9)
│   └── backtest/      historical evaluation                 (phase 12)
├── application/
│   ├── ports/         market_data, ai, system               [Phase 0/1]
│   ├── dto/           system                                [Phase 0]
│   ├── use_cases/     get_system_health, load_market_data   [Phase 0/1]
│   └── services/                                            (phase 4+)
├── adapters/
│   ├── persistence/   Base, Database, health probe          [Phase 0]
│   ├── system/        SystemClock                           [Phase 0]
│   ├── market_data/   CSV + deterministic synthetic         [Phase 1]
│   ├── contract_metadata/  ManualContractMetadataProvider  [Phase 3]
│   ├── ai/            Claude adapter                        (phase 6)
│   └── news/                                                (phase 15)
├── api/
│   ├── routes/        health                                [Phase 0]
│   ├── schemas/       health                                [Phase 0]
│   ├── dependencies.py, middleware.py                       [Phase 0]
│   └── websocket/                                           (phase 13)
└── core/              config, logging, context              [Phase 0]
```

## Ports

| Port | Status | Owner phase |
| --- | --- | --- |
| `HistoricalMarketDataProvider` | Defined + CSV and synthetic adapters | 0 / 1 |
| `DiagnosticHistoricalMarketDataProvider` | Optional capability + CSV adapter | 1 |
| `AIProvider` | Defined, no implementation | 6 / 7 |
| `ClockPort` | Defined + `SystemClock` | 0 |
| `DatabaseHealthPort` | Defined + SQLAlchemy adapter | 0 |
| `LiveMarketDataProvider` | Deferred | 13 |
| `ContractMetadataProvider` | Defined + manual adapter | 3 |
| `ScreenshotAnalyzer` | Deferred | 6 |
| `NewsProvider` | Deferred | 15 |
| `OrderExecutionPort` | **Not scheduled** — execution disabled | — |

A port is defined only when its types can be expressed honestly. Typing a port
with `Any` to create it early is worse than not having it.

## Cross-cutting decisions

**Decimal at the data boundary, float inside the indicators.** Candle OHLCV
and every money amount is `Decimal`. Indicator mathematics is `float` — EMA,
Wilder smoothing and standard deviation are irrational-valued recursions, and
`Decimal` would carry precision the mathematics does not have. The single
sanctioned crossing is the `float_*()` accessors on `ValidatedCandleSeries`;
nothing converts back. Quantizing a level to the tick size waits for Phase 3,
when the tick size is a verified fact rather than a guess. Full policy in
`technical_conventions.md`.

**Time is injected.** Nothing reads the wall clock directly; it comes from
`ClockPort`. Replay and backtest can then supply historical time, and no engine
can observe a timestamp from the future.

**Forming vs closed.** `Candle.is_closed` exists from the first day so a
forming bar can never be silently treated as a confirmed signal. From Phase 1
the Data Quality Engine blocks a forming candle from any historical dataset.

**Validation is a type, not a habit.** Indicators accept only
`ValidatedCandleSeries`, which the Data Quality Engine produces and whose
structural invariants are enforced in its constructor. A provider cannot
bypass validation without a type error.

**Discovery time is separate from event time.** *(Phase 2)* Structure is
recognised late: a swing pivot at candle 100 with a two-candle confirmation
window does not exist until candle 102. So every Phase 2 fact carries both
where it happened and when it could first have been known — `pivot_index` with
`confirmed_index` on a swing, `confirmed_index` on structural events,
breakouts, false breakouts, retests and divergences. Downstream code asks what
was known at a candle, never what turned out to be true later. A false breakout
is therefore a separate event stamped at the candle that revealed the failure,
and the breach it invalidates is never rewritten.

**Structure depends on indicators, never the reverse.** *(Phase 2)*
`app/domain/structure/` consumes `TechnicalSnapshot` for ATR, ADX, EMA and
volume, and computes no indicator of its own. There is exactly one
implementation of each formula in the codebase.

**Money is Decimal; analytics may be float.** *(Phase 3)* Prices, multipliers,
tick sizes, margins, P&L, account balances and risk amounts are `Decimal`
throughout `domain/futures/` and `domain/risk/`, enforced by a test that fails
on any `float(` or `: float` in either package. Division runs in a pinned
`decimal` context so a result cannot depend on ambient global state, and every
zero denominator returns `None` rather than infinity or a misleading zero.

**Mutable exchange facts arrive through a provider, wrapped in provenance.**
*(Phase 3)* No multiplier, tick size, tick value, margin, expiry, settlement
type or session is hard-coded anywhere; each is a `VerifiedValue` carrying its
`VerificationStatus`, and only `VERIFIED_CURRENT_FACT` is released into money
arithmetic. Two absences are kept distinct: `None` means never supplied, while
a present value with `UNVERIFIED` status means supplied but not to be relied
on. Tests scan the whole source tree for instrument codes and default-constant
names. A `VERIFIED_CURRENT_FACT` must also carry the evidence it implies: a
missing `source` is blocking, a missing `as_of` is a warning, and nothing
anywhere decides a fact is *stale* — that would need an exchange revision
schedule this project does not hold.

**An instrument's metadata may only be paired with its own observations.**
*(Phase 3)* `FuturesContract` and `FuturesQuote` each carry a symbol, and every
contract-aware engine calls `require_matching_quote` or
`require_same_instrument` before computing. A pairing of contract A's
multiplier with contract B's price fails loudly rather than producing a
confident wrong number. Matching is exact string equality after whitespace
trimming — no symbol convention is assumed, not even case folding.

**Timeframes have roles and are never averaged.** *(Phase 4)* 1D reads the
regime, 1H the directional bias, 15M the setup, 5M the timing, and §10 forbids
treating them equally. A view whose timeframe does not match its role is
refused rather than quietly reordered, and a role with no view stays absent all
the way through — a gap in the hierarchy is never filled with a neutral
reading. A lower timeframe moving against higher ones that agree is reported as
a *pullback*, not a conflict; the exemption is revoked only by a strong
opposing regime, or by a change of character **and** a confirmed breakout
against the consensus.

**Repetition is not confirmation.** *(Phase 4)* The Evidence Fusion Engine
collapses each category on each timeframe into one `EvidenceGroup`, so twenty
records of one divergence argue once. Every score reads groups rather than
items, and each quality component is capped at its own weight, which is also
how correlated components are kept from compounding: the regime is derived
partly from the EMA stack and the structure bias, so it is weighted *below*
both rather than being allowed to re-award what they already counted.

**A score is a heuristic, never a probability.** *(Phase 4)* Setup Quality and
Entry Quality are 0-100 and labelled `HEURISTIC`; §19 forbids calling an
uncalibrated analysis score a probability, a win rate or an edge, and a test
forbids the field names that would invite it. Bull and bear qualities are
computed independently and do not sum to 100 — both can be poor at once, which
is what a directionless market looks like. Components with no evidence are
excluded from numerator *and* denominator, with the denominator actually used
published on every result, and a `DATA_AVAILABILITY` component keeps missing
data from being free.

**Agreement and completeness are different facts.** *(Phase 4)* Alignment is a
*relation* between timeframe readings, so it needs at least two of them: with
fewer, `TIMEFRAME_ALIGNMENT` is `UNAVAILABLE` and says why, because scoring one
timeframe as partially aligned would measure a relation that does not exist. A
missing timeframe is neither agreement nor disagreement. How much of the
hierarchy exists is scored separately by `TIMEFRAME_COVERAGE`, weighted by role
so a missing 1D costs more than a missing 5M — which is also what stops
excluding alignment from the denominator from quietly *raising* an incomplete
analysis's score.

**Phase 4 stops before the decision.** *(Phase 4)* Scenarios reach
`WAITING_FOR_CONFIRMATION` and the NO TRADE engine returns a veto with reason
codes, but nothing produces LONG, SHORT or WAIT. That synthesis weighs analysis
against account risk and belongs to the later phase that owns it. What Phase 4
*does* preserve is the information that decision will need: every finding
carries a `FindingSeverity` of **BLOCKING** (waiting cannot fix it — zero risk
allowance, corrupt data, a timeframe conflict), **PENDING** (a future candle
genuinely could — a missing entry confirmation) or **CAUTION** (disclosed, and
neither). A boolean would have collapsed the first two together and destroyed
the WAIT/NO-TRADE distinction before the phase that owns it could make it.
§25 reasons with no authoritative data source — liquidity, event risk, news —
are enumerated as a separate `DeferredNoTradeReason` enum that shares no member
with the live one, so they are visible as known gaps and cannot fire.

**Classification may decline to classify.** *(Phase 2, extended in Phases 3 and 4)* `StructureBias` has
`AMBIGUOUS` and `INSUFFICIENT`; `StructuralEventType` has `LEVEL_BREAK` for a
break with no directional structure behind it; `MarketRegime` has `UNCERTAIN`
and `CHAOTIC`. Phase 3 adds `ContractState.UNKNOWN` for an expiry that cannot
be decided without inventing a session hour, `SizingOutcome.UNDETERMINED` for a
position whose margin or tick feasibility is unknown, `TickFeasibility` for
levels that cannot be confirmed placeable, and `CostCompleteness` so a partial
cost set yields a named upper bound instead of a `net` that silently values the
missing components at zero. Phase 4 adds `EvidenceDirection.UNAVAILABLE` kept
permanently apart from `NEUTRAL`, `ComponentAvailability.UNAVAILABLE` for a
score component with nothing to measure, `ScenarioState.UNAVAILABLE` for a case
that cannot be judged, `RequirementStatus.UNKNOWN` for a condition nobody could
evaluate, and a three-state `no_trade` of true / false / **None**, because "we
could not tell" must never collapse into "go ahead". These are first-class
outputs, not fallbacks — master spec section 2 forbids manufacturing confidence
the evidence does not support.

**Provenance.** `VerifiedValue[T]` binds a financial fact to how it was
obtained. `require_authoritative()` refuses to release a development default,
a fixture or an unverified guess into a real calculation.

**Structured logging.** One JSON object per line, correlated by request id,
scrubbed of secrets at the formatter.

**Liveness is not readiness.** `/api/health/live` never touches a dependency;
`/api/health/ready` and `/api/health` return 503 when one is unreachable. A
degraded dependency must never be reported as a dead process, or an
orchestrator will restart a healthy container on every database blip.

**One engine, four modes.** Live, replay, backtest and shadow mode must share
the same deterministic engines behind the market data ports. Separate analysis
logic per mode is the failure this architecture exists to prevent.
