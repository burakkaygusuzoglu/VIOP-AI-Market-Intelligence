# VİOP AI Market Intelligence — Implementation Plan

Produced at the start of Phase 0, from a complete read of
`docs/viop_master_spec.md` (5,662 lines) and an inspection of the actual
repository and development environment. Preserved on disk so the project stays
recoverable if a session is interrupted.

## Environment as inspected (2026-08-20)

| Item | Finding |
| --- | --- |
| Repository | `c:\Users\asus\Desktop\VIOP_AI_` — **not a git repository**, no remote, no history |
| Prior contents | Only `docs/viop_master_spec.md`. Clean-start repository confirmed empty. |
| Python | 3.14.3 (3.10 also present), pip 26.0.1, no `uv` |
| Node / npm | v24.13.1 / 11.8.0 |
| Docker | CLI 29.6.2, Compose v5.3.1 — **daemon not running** |
| PostgreSQL | none reachable; ports 5432 / 8000 / 5173 free |
| OS | Windows 11, PowerShell + Git Bash |

The full stack was installed and exercised on Python 3.14.3 before being
committed to in this plan, rather than assumed compatible.

## 1. Product summary

A Turkish-first, beginner-and-pro decision intelligence system for Borsa
İstanbul VİOP futures. It answers what is happening, what supports each side,
what confirms or invalidates a setup, and whether a trade fits the account.
`WAIT` and `NO TRADE` are first-class outputs. Real-money execution is disabled;
the user places every real order manually.

## 2. Architecture

Clean / hexagonal. Four responsibilities kept apart: deterministic analytical
engine (authoritative for all numbers), AI interpretation layer (behind a port,
never a source of numeric truth), risk engine (deterministic, may veto), market
data engine (vendor-neutral). Live, replay, backtest and shadow mode share one
engine; only the data provider changes.

## 3. Repository structure

`docs/`, `backend/`, `frontend/`, `docker-compose.yml`, `.env.example`,
`README.md`, `CLAUDE.md`. Backend follows master spec section 94. **Only
packages with real content are created**; the target tree is documented in
`docs/architecture.md` instead of scaffolded as empty modules.

## 4. Domain boundaries

`domain/` is stdlib-only. Sub-domains arrive with their owning phase:
`technical` (1), `structure` (2), `futures` + `risk` (3), `setups` +
`strategies` (4), `trading` (9), `backtest` (12).

## 5. Dependency direction

`api → application → domain`; `adapters → application/domain`; domain depends
on nothing. Enforced by import-linter contracts executed as a test. `main.py`
and `api/dependencies.py` are the declared composition root.

## 6. Backend design

FastAPI, Pydantic v2 at the API edge only, SQLAlchemy 2 + Alembic inside the
persistence adapter only, `Decimal` for all money, structured JSON logging with
request correlation and secret redaction.

## 7. Frontend design

Vite + React + TypeScript strict + TanStack Query + Zod. Dark-mode-first
workstation tokens. Every API response validated by Zod at the boundary. i18n
prepared with a typed Turkish dictionary and no i18n dependency. Charting
library deferred to Phase 8.

## 8. Database design

PostgreSQL only. Phase 0 delivers infrastructure — declarative base with
explicit constraint naming, engine/session factory, Alembic wired to app
settings — plus a baseline migration that creates **no domain tables**. The
section 93 models are created by the phases that own them.

## 9. Market data architecture

`HistoricalMarketDataProvider` typed over `Candle`. Data priority encoded as
`DataSourcePriority`. No provider implementation in Phase 0.

## 10. Contract metadata architecture

`ContractMetadataProvider` and `FuturesContract` are Phase 3. Phase 0 delivers
the enabling primitive: `VerificationStatus` and `VerifiedValue[T]`, so no
mutable exchange fact can enter the codebase unlabelled. No multiplier, tick
size, margin or session hour is hard-coded anywhere.

## 11. Live data architecture

Phase 13. Phase 0 preserves the enabling property only: `Candle.is_closed`.

## 12–18. Deterministic engines

Technical (1), market structure (2), regime (2), evidence fusion (4),
contradiction (4), risk (3), futures (3). **None implemented in Phase 0.**
Phase 0's obligation to them is the enforced boundary keeping them in the
domain and out of reach of the LLM layer.

## 19–20. Claude vision and synthesis boundary

`AIProvider` port defined; structured output only; no adapter, no prompt, no
API call in Phase 0. Vision is Phase 6, synthesis is Phase 7.

## 21. Beginner / Pro experience

Phase 5. Phase 0 sets the Turkish default and the shared token system.

## 22–25. Paper trading, replay, backtest, live monitoring

Phases 9, 11, 12, 13. Not implemented, not scaffolded.

## 26. Auditability

Request-id correlation through every log line, and `VerifiedValue` provenance.
Evidence ledger and analysis snapshots arrive with their phases.

## 27. Data quality

The section 40 engine is Phase 1. Phase 0 keeps `Candle` free of any silent
coercion or repair.

## 28. Security

`.env` git-ignored, secret-free `.env.example`, `SecretStr` for keys, redaction
verified by test, no broker credentials anywhere, CORS restricted by config.

## 29. Testing strategy

pytest + pytest-asyncio with `unit` / `integration` markers; integration tests
skip when PostgreSQL is unreachable. Vitest + Testing Library on the frontend.
Architecture conformance is a test, not a document.

## 30. Observability

JSON logs, request-id middleware, `GET /api/health` with per-check detail and
503 on degraded. OpenTelemetry prepared for structurally, not installed.

## 31. Key technical risks

Docker daemon down (image builds unverifiable); no PostgreSQL (connectivity
unverifiable); Python 3.14 ahead of typical guidance (mitigated by actually
running the stack); spec/code drift across sessions (mitigated by `CLAUDE.md`
and phase reports on disk).

## 32. Deferred components

All indicators, structure, regime, futures, risk math, vision, synthesis,
paper trading, replay, backtest, live feeds, ML, alerts, `OrderExecutionPort`,
MCP, and every section 93 domain table.

## 33. Phase plan

Master spec section 103, Phases 0–16, one at a time, each ending in a report
and a hard stop.

## 34. Exact Phase 0 scope

Repository structure · mechanically enforced architecture boundaries · backend
skeleton with one genuine end-to-end hexagonal slice · frontend skeleton
consuming it with schema validation · database infrastructure and Alembic
baseline · Docker for backend, frontend and postgres · configuration and
`.env.example` · health endpoint · testing infrastructure with real passing
tests · structured logging with secret redaction · README · core ports ·
`CLAUDE.md`.

**Not in Phase 0:** every indicator, every algorithm, every financial
calculation, any AI call, any market data implementation, any domain table, and
the four deferred ports.

## Self-review corrections applied before implementation

1. Eight ports reduced to four. The other four cannot be typed honestly before
   their domain types exist, and typing them with `Any` would violate section 100.
2. The section 93 table set reduced to a baseline migration. Tables for
   unimplemented features are fake implementation (section 105).
3. A naive layered import contract would have flagged the composition root,
   creating pressure to weaken the rule later. Replaced with explicit forbidden
   contracts plus a declared composition root.
4. `float` for prices replaced with `Decimal` before any calculation exists,
   because it becomes an irreversible correctness bug once P&L and tick
   rounding arrive.
