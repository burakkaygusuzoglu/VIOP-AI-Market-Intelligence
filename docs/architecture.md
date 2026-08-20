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
4. **Adapters never compute indicators** — `app.adapters` may not import
   `app.domain.technical`. Indicator mathematics is the numerical authority and
   stays where it is tested and type-checked as such. *(Phase 1)*
5. **API routes and schemas never reach into adapters or SQLAlchemy.**

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
│   ├── structure/     swings, BOS/CHOCH, S/R, regime        (phase 2)
│   ├── futures/       FuturesContract, basis, OI            (phase 3)
│   ├── risk/          sizing, limits, margin, P&L           (phase 3)
│   ├── setups/        evidence fusion, quality, NO TRADE    (phase 4)
│   ├── strategies/    strategy configs, router              (phase 4+)
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
| `ContractMetadataProvider` | Deferred | 3 |
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
