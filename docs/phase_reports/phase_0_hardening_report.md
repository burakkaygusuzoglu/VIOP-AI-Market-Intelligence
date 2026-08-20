# PHASE 0 FINAL HARDENING REPORT

Date: 2026-08-20 · Follows `phase_0_report.md` · Phase 0 only, no Phase 1 work

Docker Desktop became available, which exposed four real defects that were
invisible in the previous pass. All four are fixed and covered by tests.

---

## Changes made

### 1. Docker + real database validation
Both images build, the three-service stack runs healthy, and the application
holds a real PostgreSQL connection. Four defects surfaced and were fixed:

| # | Defect | Where it hid | Fix |
| --- | --- | --- | --- |
| 1 | Compose derived the invalid image tag `viop_ai_-backend` from the directory name `VIOP_AI_`, so **no image could build at all** | Only reachable with a running daemon | Explicit `name: viop-ai` in `docker-compose.yml` |
| 2 | `CORS_ORIGINS` crashed startup: pydantic-settings JSON-decodes complex types from the environment *before* field validators run | Container only — every local test used `model_validate`, which bypasses the env source | `Annotated[tuple[str, ...], NoDecode]` + two tests that go through `monkeypatch.setenv` |
| 3 | psycopg async cannot run on Windows' default `ProactorEventLoop`; every host-side DB connection failed with `InterfaceError` | Host only — containers are Linux. **My previous report read this as "no PostgreSQL reachable"** | `app/core/runtime.py` installs a selector policy; `python -m app` entry point; `conftest.py` calls it at import time |
| 4 | A probe against an unroutable host blocked **130 seconds**, which would stall the health endpoint and any readiness probe | Only visible once integration tests actually ran | `connect_args={"connect_timeout": 5}`, configurable; test asserts the probe returns in under 30 s |

A fifth issue appeared during regression: unit tests silently read the ambient
environment, so `test_missing_anthropic_key_is_allowed` passed on a clean
machine and failed once `POSTGRES_PASSWORD` was exported. Fixed with
`tests/unit/conftest.py`, which strips every `Settings`-derived variable —
the list is derived from `Settings.model_fields`, so a future setting cannot
escape isolation.

### 2. Minimal CI
`.github/workflows/ci.yml` — three jobs, validation only. No deployment,
release, cloud or publishing steps.

### 3. Git safety hook
`.claude/settings.json` + `.claude/hooks/block_git_write_commands.py`.

### 4. Health semantics
Split into liveness and readiness (details below).

---

## Files modified

**Created** — `.github/workflows/ci.yml`, `.claude/settings.json`,
`.claude/hooks/block_git_write_commands.py`, `.claude/hooks/README.md`,
`backend/app/core/runtime.py`, `backend/app/__main__.py`,
`backend/app/application/use_cases/get_liveness.py`,
`backend/tests/unit/conftest.py`, `.env` (git-ignored, local only).

**Modified** — `docker-compose.yml`, `backend/Dockerfile`,
`backend/app/core/config.py`, `backend/app/main.py`,
`backend/app/adapters/persistence/{database,health}.py`,
`backend/app/api/{dependencies}.py`, `backend/app/api/routes/health.py`,
`backend/app/api/schemas/health.py`,
`backend/app/application/dto/{system,__init__}.py`,
`backend/app/application/use_cases/__init__.py`,
`backend/tests/conftest.py`, `backend/tests/unit/{test_config,test_health_api}.py`,
`backend/tests/integration/test_database.py`, `README.md`, `CLAUDE.md`,
`docs/architecture.md`, `.gitignore`.

`docs/viop_master_spec.md` was **not** modified.

---

## Docker build result — PASS

```
docker compose build backend   -> viop-ai-backend:latest    Built (exit 0)
docker compose build frontend  -> viop-ai-frontend:latest   Built (exit 0)
docker compose config          -> exit 0
docker compose config (no POSTGRES_PASSWORD) -> exit 1, correct refusal
```

Rebuilt after every code change. All three services report `(healthy)`.

## PostgreSQL result — PASS

`postgres:17-alpine` reaches healthy via `pg_isready`. The backend holds a real
connection: `/api/health` returns `{"status":"ok", ...,"detail":"postgres:5432/viop
reachable","latency_ms":2.09}`. Verified through the backend directly and
through the frontend's nginx proxy.

## Alembic live migration result — PASS

Executed inside the running container against real PostgreSQL:

```
alembic upgrade head     -> Running upgrade -> 0001_baseline
alembic current          -> 0001_baseline (head)
alembic downgrade base   -> Running downgrade 0001_baseline ->
alembic upgrade head     -> Running upgrade -> 0001_baseline
```

`\dt` shows exactly one table, `alembic_version`, confirming the baseline
creates no domain tables as designed.

## Integration-test result — PASS (previously skipped)

Five tests, all executing against real PostgreSQL in 5.5 s: health probe
reachability, a real `SELECT 1`, server-is-PostgreSQL confirmation, session
rollback on failure, and fast failure against an unroutable host.

Rewritten to read connection settings from the environment, so the same tests
run locally, in Docker and in CI. They still skip — visibly — when no database
is reachable.

## Backend test count

| Environment | Result |
| --- | --- |
| With PostgreSQL | **71 passed, 0 skipped** |
| Without PostgreSQL | **67 passed, 4 skipped** (skips named and visible) |

Up from 61 tests. New: 2 CORS-from-environment, 5 liveness/readiness,
4 integration, plus the connect-timeout assertion.

## Frontend test count

**12 passed, 0 failed.** Unchanged — no frontend behaviour changed.

## CI status / configuration

`.github/workflows/ci.yml`, triggered on push, pull request and manual dispatch.

- **backend** — ruff check · ruff format check · mypy strict · lint-imports ·
  pytest (against a `postgres:17-alpine` service) · live Alembic upgrade,
  current, downgrade, upgrade
- **frontend** — tsc · eslint · prettier check · vitest · production build
- **docker** — compose config validation · backend image build · frontend image build

Permissions are `contents: read`; concurrent runs on a ref cancel.

**Status: configured and structurally validated, never executed.** The
repository is not a git repository and has no remote, so no run has occurred.
Reported as CONFIGURED, not PASSING.

## Architecture-boundary result — PASS

`lint-imports` — 4 contracts kept, 0 broken. The new modules (`runtime`,
`__main__`, `get_liveness`) respect the dependency rule; `GetLiveness` depends
only on `ClockPort`.

## Hook behavior

**Logic: verified, 25/25 cases.**

| Blocked | Allowed |
| --- | --- |
| `git commit -m 'x'` · `git push` · `git push origin main --force` | `git status` · `git diff` · `git log` · `git branch -a` · `git show` · `git remote -v` |
| `cd /tmp && git commit -m 'x'` · `git status; git push` | `git log --grep="commit"` · `git log --format="%s push"` |
| `git -C /path commit` · `git --no-pager push` · `git -c user.name=x commit` | `git init` · `git add -A` |
| `git.exe push` · `/usr/bin/git push` · `GIT_DIR=/x git push` | `npm run push-docs` · `echo 'git push'` |

The subcommand is found by parsing tokens, not substring matching, so
`git log --grep="commit"` is correctly allowed. Fails open on malformed input:
it is a guard rail against forgetfulness, not a security boundary.

**Activation: NOT ACTIVE in this session.** I tested it end to end and the hook
did not fire — the command ran and git's own error appeared instead of the
hook's denial. Claude Code loads project settings that existed when the session
started, and `.claude/` was created mid-session. **Open `/hooks` once, or
restart the session, to activate it.** I cannot do that myself.

The end-to-end probe was `git commit --allow-empty` in a directory that is not
a git repository, so it could not have created a commit under any circumstance.
It returned `fatal: not a git repository`. Nothing was committed.

## Health / liveness / readiness behavior

The previous design had a real flaw: a single 503-producing `/api/health`, used
as the container `HEALTHCHECK`, would mark the backend unhealthy whenever
PostgreSQL blipped — inviting an orchestrator to restart a perfectly healthy
process and turn a recoverable dependency failure into an outage.

| Endpoint | Question | DB up | DB down |
| --- | --- | --- | --- |
| `/api/health/live` | Is the process alive? Touches no dependency. | 200 `alive` | **200 `alive`** |
| `/api/health/ready` | Can it serve work now? | 200 `ok` | 503 `degraded` |
| `/api/health` | Aggregate (spec §96), equals readiness | 200 `ok` | 503 `degraded` |

The container `HEALTHCHECK` now probes liveness.

**Verified against a real outage, not just unit tests.** With `docker compose
stop postgres`: liveness stayed 200, readiness returned 503 `degraded`, and the
backend container **remained `(healthy)`** rather than flipping to unhealthy.
On `docker compose start postgres` the service returned to `ok` on its own with
no restart.

## Implemented ports

| Port | Why it belongs in Phase 0 |
| --- | --- |
| `HistoricalMarketDataProvider` (`ports/market_data.py`) | Live/replay/backtest parity (§74) depends on the data provider being the *only* thing that varies. Fixing that seam now costs nothing; retrofitting it after engines exist means rewriting them. Fully typed over `Candle`. **No implementation** — CSV and mock providers are Phase 1. |
| `AIProvider` (`ports/ai.py`) | §69 requires Anthropic calls never be scattered through business logic, and §68 requires structured, schema-validated output. The boundary must exist *before* the first call site, or the first call site defines it. **No adapter, no prompt, no API call.** |
| `ClockPort` (`ports/system.py`) | Replay and backtest must supply historical time (§79, §81). Anything reading the wall clock directly becomes a lookahead-bias bug later. Injecting time from day one makes that impossible by construction. Implemented by `SystemClock`. |
| `DatabaseHealthPort` (`ports/system.py`) | Carries the Phase 0 vertical slice that proves the hexagon actually works end to end, and gives `/api/health` a real dependency check. Implemented by `SqlAlchemyDatabaseHealth`. |

## Deferred ports

| Port | Becomes concrete in | Why defining it now would be premature |
| --- | --- | --- |
| `ContractMetadataProvider` | **Phase 3** | It returns a `FuturesContract` — multiplier, tick size, tick value, margin, expiry, settlement. None of those types exist, and none of those *values* may be assumed from model memory (§118). A signature written now would either use `Any` (violating §100) or hard-code a shape guessed before consulting an authoritative source. `VerificationStatus` / `VerifiedValue[T]` already exist so the values cannot enter unlabelled when it does arrive. |
| `ScreenshotAnalyzer` | **Phase 6** | Its contract is a per-field extraction with `{field, value, source, confidence}` (§38) plus a screenshot-quality score (§39). Both depend on the deterministic values the extraction must be *reconciled against*, which are Phase 1–2 output. Designing the reconciliation contract before knowing what it reconciles produces a shape that gets thrown away. |
| `LiveMarketDataProvider` | **Phase 13** | It carries ticks, candle-forming state, provider status, latency and staleness (§50, §52, §63). Only `Candle.is_closed` is knowable today. A streaming interface written before the aggregation and state machine exist would be a guess at the hardest part of the system. |
| `NewsProvider` | **Phase 15** | Vendor-neutrality (§71) is exactly the reason to wait: the abstraction should be drawn *after* seeing at least one real provider's data, not from an imagined one. |
| `OrderExecutionPort` | **Not scheduled** | §120 permits it as an abstraction, but nothing needs it and real execution is disabled. Its absence is the clearest statement that execution is out of scope. A test (`test_no_broker_execution_port_exists`) asserts no broker, execution or Midas module appears in `ports/`. |

**No empty or dishonest interface was created to round the count up.** The four
implemented ports are fully typed and exercised by tests; the five deferred
ones do not exist at all.

---

## Known limitations

- **The git hook is not active in this session** and needs `/hooks` or a
  restart. Until then the no-commit rule rests on instruction-following alone.
- **CI has never run.** The workflow is validated by YAML parsing and by the
  fact that every step mirrors a command verified locally — not by a green run.
- `asyncio.set_event_loop_policy` and `WindowsSelectorEventLoopPolicy` are
  deprecated in Python 3.14 and **slated for removal in 3.16**. They are
  currently the only hook that works for both uvicorn and pytest-asyncio. The
  deprecation warning is left visible rather than suppressed.
- The `viop_test` database is created manually; there is no bootstrap script.
- Integration tests were run on Windows against a containerised database; the
  Linux-host path is exercised only by CI, which has not run.
- The application still reports nothing but its own health. No market data, no
  indicators, no analysis — the intended state of Phase 0.
- Turkish text still unreviewed by a native speaker.

## Technical debt

| Item | Impact | When |
| --- | --- | --- |
| Deprecated event-loop policy API, removed in Python 3.16 | Medium — will break on a future interpreter | Before adopting 3.16; revisit if pytest-asyncio/uvicorn expose a loop factory |
| CI unverified by an actual run | Medium | First push to a remote |
| No test-database bootstrap | Low | Phase 1, when fixtures need schema |
| Alembic `env.py` excluded from mypy | Low | First real migration |
| `AIProvider` designed before its consumers | Low | Phase 6 |
| `/api/health` duplicates `/api/health/ready` | Low — spec §96 requires the plain path | Revisit if §96 is ever relaxed |
| Frontend has no routing or layout shell | Low, deliberate | Phase 8 |

## Git state

**Still not a git repository.** `git status` returns *fatal: not a git
repository*. No branch, no remote, no history, no commits, no pushes. I did not
run `git init`, `git commit` or `git push`.

`.gitignore` covers `.env`, `.venv/`, `node_modules/`, build output, all tool
caches and `*.tsbuildinfo`. The local `.env` created for stack validation
contains a development-only password and is git-ignored.

---

## Regression summary

| Check | Result |
| --- | --- |
| Backend tests (with PostgreSQL) | **71 passed, 0 skipped** |
| Backend tests (without) | **67 passed, 4 skipped** |
| `ruff check` | **PASS** |
| `ruff format --check` | **PASS** — 54 files |
| `mypy` strict | **PASS** — 52 source files |
| `lint-imports` | **PASS** — 4 kept, 0 broken |
| Frontend tests | **12 passed** |
| Frontend typecheck / lint / format | **PASS** |
| Frontend build | **PASS** — 636 ms |
| `docker compose config` | **PASS** |
| Backend + frontend image builds | **PASS** |
| Stack health | **PASS** — all three `(healthy)` |
| Live Alembic migration + round trip | **PASS** |
| Liveness/readiness under real outage | **PASS** |
| Git hook logic | **PASS** — 25/25 |
| Git hook activation | **NOT ACTIVE** — needs `/hooks` or restart |
| CI | **CONFIGURED** — never executed |

---

# STOP

Phase 0 hardening is complete and validated. Phase 1 has not been started,
scaffolded, or prepared for. No Phase 1 dependency was installed. Nothing was
committed or pushed.

Awaiting explicit approval before any further work.
