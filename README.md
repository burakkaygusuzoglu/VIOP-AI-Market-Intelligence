# VİOP AI Market Intelligence

An AI-assisted futures market **analysis, education, risk-management and
decision-support** platform for Borsa İstanbul VİOP.

> **This is an analytical, educational and decision-support tool. It does not
> guarantee financial outcomes.** It never places real orders. The user makes
> every real trading decision and enters every real order manually at their own
> broker.

The authoritative specification for this project is
[docs/viop_master_spec.md](docs/viop_master_spec.md). Where this README and the
specification disagree, the specification wins.

---

## Current status

**Phase 0 — Foundation.** The repository contains the architecture, the
configuration, the health slice, the test and validation harness, and the
Docker stack. **No analytical engine exists yet:** there are no indicators, no
market structure detection, no regime classification, no risk calculations, no
AI calls and no market data providers. Those arrive in Phases 1 onward, each
behind an explicit approval gate.

See [docs/phase_reports/phase_0_report.md](docs/phase_reports/phase_0_report.md)
for exactly what is implemented and what is not.

---

## Purpose

The system is not built to answer *"will the market go up or down"*. It is built
to answer:

- What is happening now?
- What evidence supports the bullish case, and the bearish case?
- What market regime are we in?
- What would make a LONG or a SHORT setup valid, and what invalidates it?
- Where does a stop logically belong, and what is the realistic risk/reward?
- Does this trade fit this account and this risk limit?
- Should the user go LONG, SHORT, **WAIT**, or take **NO TRADE**?

`WAIT` and `NO TRADE` are correct, valuable answers. Preventing a poor trade is
worth as much as finding an attractive one.

---

## Architecture

Clean / hexagonal, with four responsibilities kept apart:

| Layer | Responsibility |
| --- | --- |
| **Deterministic analytical engine** | All numbers: indicators, structure, statistics, P&L, sizing. Plain Python. Authoritative. |
| **AI interpretation layer** | Claude explains, synthesises and challenges. Behind an `AIProvider` port. **Never the source of truth for a number.** |
| **Risk engine** | Deterministic and independent of the AI. May veto a technically attractive setup. |
| **Market data engine** | Vendor-neutral ports. Structured data always outranks a screenshot estimate. |

Dependency direction is `api → application → domain`, with adapters
implementing ports. The domain depends on nothing. This is **enforced by
import-linter contracts that run as part of the test suite**, not by
convention — see `backend/tests/unit/test_architecture.py`.

Full detail: [docs/architecture.md](docs/architecture.md).

---

## Development setup

Requires Python 3.12+, Node.js 20+, and Docker (optional, for the full stack).
Verified on Python 3.14.3, Node 24.13.1, npm 11.8.0.

```bash
cp .env.example .env        # then set POSTGRES_PASSWORD
```

### Backend

```bash
cd backend
py -3.14 -m venv .venv            # Windows;  python3 -m venv .venv elsewhere
./.venv/Scripts/python.exe -m pip install -e ".[dev]"

./.venv/Scripts/python.exe -m pytest              # tests
./.venv/Scripts/python.exe -m ruff check .        # lint
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy                # strict type check
./.venv/Scripts/lint-imports                      # architecture contracts
./.venv/Scripts/python.exe -m app                 # run the API locally
```

Use `python -m app` rather than the `uvicorn` CLI on Windows: psycopg's
async mode cannot run on the default `ProactorEventLoop`, and the loop
policy has to be set before uvicorn creates the loop. On Linux and macOS
the two are equivalent, which is why the containers call uvicorn directly.

```bash
# Integration tests against a real database
docker compose up -d postgres
docker compose exec -T postgres psql -U viop -d viop -c 'CREATE DATABASE viop_test OWNER viop;'
POSTGRES_DB=viop_test ./.venv/Scripts/python.exe -m pytest -m integration
```

Integration tests that need PostgreSQL skip themselves when no database is
reachable. A skip is reported as a skip, never as a pass.

### Frontend

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173, proxies /api to the backend
npm test             # vitest
npm run typecheck
npm run lint
npm run build
```

### Health endpoints

| Endpoint | Question | Codes |
| --- | --- | --- |
| `GET /api/health/live` | Is the process alive? Touches no dependency. | always 200 |
| `GET /api/health/ready` | Can it serve work right now? | 200 / 503 |
| `GET /api/health` | Aggregate view, equivalent to readiness. | 200 / 503 |

Liveness and readiness are separate on purpose. A database outage makes the
service *degraded*, not dead; if liveness reported 503 an orchestrator would
restart a healthy container on every database blip and turn a recoverable
failure into an outage. The container `HEALTHCHECK` probes liveness.

### Docker

```bash
docker compose config    # validate without starting anything
docker compose up --build
```

- frontend → http://localhost:5173
- backend → http://localhost:8000/api/health
- postgres → localhost:5432

### Database migrations

```bash
cd backend
./.venv/Scripts/python.exe -m alembic upgrade head
./.venv/Scripts/python.exe -m alembic upgrade head --sql   # dry run, no database needed
```

The baseline revision intentionally creates no domain tables. Tables are added
by the phase that owns the concept they represent.

---

## Claude API

No Anthropic API call is made anywhere in Phase 0, and no key is required to
run the project. `ANTHROPIC_API_KEY` is read as a `SecretStr`, is redacted from
all log output, and stays unused until Phase 6.

When the AI layer arrives it will sit behind `app.application.ports.ai.AIProvider`
and will only ever return schema-validated structured output. Free text will
never drive application state.

---

## Testing

```bash
cd backend && ./.venv/Scripts/python.exe -m pytest
cd frontend && npm test
```

Financial calculations will require especially strong tests. Prices are
`Decimal` throughout — binary floating point is not acceptable for money, tick
rounding or P&L.

---

## Continuous integration

`.github/workflows/ci.yml` runs the same gates that run locally — backend
lint, format, strict types, architecture contracts and tests (against a real
PostgreSQL service, plus a live Alembic migration and downgrade round trip);
frontend typecheck, lint, format, tests and production build; and both Docker
image builds. It is validation only: no deployment, release or publishing.

## Data and verification policy

Mutable exchange facts — contract multiplier, tick size, tick value, margin,
session hours, expiry and settlement rules — are **never** assumed from model
memory and never hard-coded into domain logic. Every such value carries a
`VerificationStatus`:

`VERIFIED_CURRENT_FACT` · `DEVELOPMENT_DEFAULT` · `TEST_FIXTURE` · `MOCK_DATA` · `UNVERIFIED`

Only a verified current fact may reach a real calculation. An unknown value is
marked `UNVERIFIED` rather than guessed.

---

## Security

- Secrets come from the environment; `.env` is never committed.
- Every known secret is redacted at the log formatter, the last point a record
  passes through.
- **No broker credentials are requested or stored, ever.** There is no Midas
  scraping, no browser automation and no broker configuration.
- A `PreToolUse` hook blocks assistant-issued `git commit` and `git push`
  while leaving read-only git commands available. See
  [.claude/hooks/README.md](.claude/hooks/README.md).

---

## Roadmap

| Phase | Contents | State |
| --- | --- | --- |
| 0 | Foundation: architecture, config, health slice, Docker, tests | **Implemented** |
| 1 | Market data + deterministic technical engine | Not started |
| 2 | Market structure + regime | Not started |
| 3 | Futures contracts + risk engine | Not started |
| 4 | Multi-timeframe + evidence fusion + setup quality | Not started |
| 5 | Beginner / Pro experience | Not started |
| 6 | Claude Vision | Not started |
| 7 | Claude synthesis | Not started |
| 8 | Professional analysis UI | Not started |
| 9 | Paper trading | Not started |
| 10 | Journal + performance analytics | Not started |
| 11 | Market replay + Learn mode | Not started |
| 12 | Backtesting | Not started |
| 13 | Live architecture | Not started |
| 14 | Shadow mode | Not started |
| 15 | External data providers | Not started |
| 16 | Advanced research | Not started |

Each phase ends with a report and a stop. The next phase begins only after
explicit human approval.

---

## Limitations

- No market data, indicators, structure detection, regime classification, risk
  calculations or AI analysis are implemented yet.
- No live data feed. No broker integration. No real-money execution — by design.
- Nothing here is investment advice.
