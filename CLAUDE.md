# CLAUDE.md — working rules for this repository

## The specification is on disk, not in memory

`docs/viop_master_spec.md` is the **single source of truth** (5,662 lines).
Read it from disk before creating, modifying, deleting, moving, installing or
generating anything. Never rely on a remembered version of it, and never infer
missing requirements from an earlier conversation.

## Resuming after an interruption

1. Read `docs/viop_master_spec.md`.
2. Read this file.
3. Run `git status` (note: the working tree may not be a git repository yet).
4. Inspect the repository state.
5. Read `docs/implementation_plan.md`.
6. Read the latest report in `docs/phase_reports/`.
7. Determine what was actually completed.
8. Continue **only** the currently approved phase. Never redo completed work.

## Phase gate — the hard rule

Work proceeds one phase at a time (master spec sections 103, 119).

At the end of a phase: run all validation, write the phase report, then
**STOP**. Do not start, scaffold, or install dependencies for the next phase.

Silence is not approval. Passing tests are not approval. Finishing the phase is
not approval. Only an explicit instruction such as *"Proceed to Phase 1"* is.

If corrections are requested after a report, stay inside the current phase,
fix, re-validate, re-report, and stop again.

**Current state: Phase 0 complete and hardened, awaiting approval for Phase 1.**

## Never

- Never commit or push unless explicitly instructed. A `PreToolUse` hook in
  `.claude/settings.json` enforces this mechanically; read-only git commands
  stay available. Do not disable it to work around a block.
- Never let Claude own a financial calculation. Indicators, structure, risk,
  sizing, P&L, margin and basis are deterministic Python. The LLM interprets,
  explains and challenges — it is never the source of truth for a number.
- Never fabricate a price, indicator, volume, open interest, multiplier,
  margin, expiry or tick size. Mark it `UNVERIFIED` instead.
- Never hard-code a mutable exchange fact into domain logic. It goes through a
  provider or a `VerifiedValue` with a `VerificationStatus`.
- Never claim guaranteed profit, risk-free, or certain prediction. Use
  bullish/bearish bias, conditional, waiting for confirmation, uncertain.
- Never scrape Midas, browser-automate Midas, request broker credentials, or
  send a real order. Execution is disabled (master spec section 120).
- Never let unvalidated AI output drive application state.
- Never mark a placeholder as complete. Use IMPLEMENTED / PARTIAL / NOT STARTED
  and report failures honestly.
- Never add a library, database, queue, cloud service, MCP server or ML model
  to look sophisticated. Every dependency needs a stated reason.

## Architecture rules

Dependency direction: `api → application → domain`; adapters implement ports;
the domain depends on nothing.

- `app/domain/` — stdlib only. No pydantic, FastAPI, SQLAlchemy, SDKs, HTTP.
- `app/application/` — domain + its own ports. No infrastructure, no API.
- `app/adapters/` — the only place infrastructure libraries appear.
- `app/main.py` and `app/api/dependencies.py` — the composition root, the only
  place adapters are wired into the API.

Enforced by import-linter contracts in `backend/pyproject.toml`, run by
`backend/tests/unit/test_architecture.py`. If a contract is in the way, the
design is wrong — do not weaken the contract.

Prices and money are `Decimal`. Never float.

Live, replay, backtest and shadow mode must share the same deterministic
engines; only the data provider changes.

## Commands

```bash
# Backend (from backend/)
./.venv/Scripts/python.exe -m pytest
./.venv/Scripts/python.exe -m ruff check .
./.venv/Scripts/python.exe -m ruff format --check .
./.venv/Scripts/python.exe -m mypy
./.venv/Scripts/lint-imports

# Frontend (from frontend/)
npm test && npm run typecheck && npm run lint && npm run build

# Docker (from repository root)
docker compose config
docker compose up -d
docker compose exec -T backend alembic upgrade head
```

CI (`.github/workflows/ci.yml`) runs exactly these gates. A green local run
and a green CI run must mean the same thing.

Run all of these before writing a phase report. Report a check as passed only
if it actually ran and actually passed.

## Environment notes (verified 2026-08-20)

- Windows 11. Python 3.14.3. Node 24.13.1. npm 11.8.0.
- Docker daemon available (server 29.7.2, Linux containers). Both images build;
  the three-service stack runs healthy.
- PostgreSQL runs via `docker compose up -d postgres`. Integration tests need
  a `viop_test` database and `POSTGRES_DB=viop_test`.

## Platform traps already paid for

Three bugs were invisible on the host and only appeared in the container, or
vice versa. Do not re-introduce them:

1. **Windows event loop.** psycopg's async mode cannot run on the default
   `ProactorEventLoop`. `app/core/runtime.py` installs a selector policy, and it
   must run *before* the loop is created — hence `python -m app` and the
   `configure_event_loop_policy()` call at the top of `tests/conftest.py`.
2. **Environment-variable parsing.** pydantic-settings JSON-decodes complex
   types from the environment before field validators run. `cors_origins` needs
   `NoDecode`. Tests that use `Settings(...)` or `model_validate` bypass this
   path entirely and will not catch it — test through `monkeypatch.setenv`.
3. **Connect timeouts.** Without `connect_args={"connect_timeout": ...}` a probe
   against an unroutable host blocks for over two minutes and stalls the health
   endpoint.
