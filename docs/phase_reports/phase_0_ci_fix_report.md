# PHASE 0 CI FIX REPORT

Date: 2026-08-20 · Follows `phase_0_hardening_report.md` · Phase 0 only, no Phase 1 work

The first real GitHub Actions run failed in the backend job:

```
app/core/runtime.py:25: error: Statement is unreachable  [unreachable]
Found 1 error in 1 file (checked 53 source files)
```

Linux runner, Python 3.12.14. The identical command passed on Windows locally.
Nothing was wrong with the runtime behaviour; the *shape* of the platform test
made mypy reach a different verdict on each operating system.

---

## Root cause

mypy resolves `sys.platform` to the platform it is checking for — the host by
default, or whatever `--platform` names — and statically eliminates the branch
that cannot be taken there. It then treats two superficially similar situations
differently, and that difference is the whole bug:

- a block **skipped by** a `sys.platform` test is intentionally conditional, so
  mypy stays silent about it;
- a statement **made unreachable because** such a test always returns first is
  reported under `warn_unreachable = true`.

The pre-fix code was a guard clause, which lands on a different side of that
line on each platform:

```python
if sys.platform != "win32":
    return False
asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())  # line 25
return True
```

| Target | Condition | What becomes unreachable | Reported? |
| --- | --- | --- | --- |
| `win32` | always False | `return False`, *inside* the platform-guarded block | **No** — suppressed as intentionally conditional |
| `linux` | always True | lines 25–26, *after* the guard | **Yes** — `[unreachable]` |

So Windows genuinely could not see this error, and a green local mypy run was
never evidence that CI would agree. `warn_unreachable = true` in
`backend/pyproject.toml` is correct and stays on; it did its job.

Reproduced locally on Windows before changing anything:

```
mypy                    -> Success: no issues found in 53 source files
mypy --platform linux   -> app\core\runtime.py:25: error: Statement is unreachable
```

### The trap inside the fix

Line 25 is also the only reference to `asyncio.WindowsSelectorEventLoopPolicy`,
which typeshed declares **only** under `win32`. Under `--platform linux` it was
never type-checked purely because it was unreachable. Any "fix" that merely
makes that line reachable on Linux converts the `[unreachable]` error into an
`[attr-defined]` one. The Windows-only API must therefore stay *inside* a
`sys.platform == "win32"` block, not merely be reordered.

---

## Exact change

`backend/app/core/runtime.py` — the guard clause becomes a symmetric branch, so
every platform-dependent statement sits inside the platform test and control
flow rejoins afterwards:

```python
def configure_event_loop_policy() -> bool:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        installed = True
    else:
        installed = False
    return installed
```

Behaviour is byte-for-byte identical: install a selector policy on Windows and
return True, do nothing elsewhere and return False. The Windows compatibility
behaviour is genuinely necessary (psycopg async cannot use `ProactorEventLoop`)
and is fully retained.

The module docstring now records the two shape rules and why they exist, so the
next reader does not "simplify" it back.

## Why the change is correct

Four candidate shapes were checked against both platform targets and against
the project's ruff rule set. This was measured, not reasoned about:

| Shape | mypy `win32` | mypy `linux` | ruff |
| --- | --- | --- | --- |
| Guard clause (pre-fix) | pass | **`[unreachable]`** | pass |
| Early `return True` inside the `if` | **`[unreachable]`** | pass | pass |
| `if/else` with a `return` in each branch | pass | pass | **RET505** |
| **`if/else` assigning, single `return` (chosen)** | **pass** | **pass** | **pass** |
| Literal hoisted to a `WINDOWS` constant | pass | **`[attr-defined]`** | pass |

Two of those rows are the reason the chosen shape is the only one available,
and both are recorded in the docstring:

- The `return`-in-each-branch form is mypy-clean but trips ruff's RET505, and
  RET505's autofix rewrites it into row 2 — the mirror-image CI failure. A
  future `ruff check --fix` would have silently reintroduced the bug.
- Hoisting `"win32"` into a named constant defeats mypy's narrowing entirely:
  it only special-cases `sys.platform` against a string literal, and without the
  narrowing the Windows-only API fails to resolve on Linux.

No `type: ignore` was added, no mypy setting was relaxed, `warn_unreachable`
remains enabled globally, and strict mode is untouched.

## Tests added

`backend/tests/unit/test_runtime.py` — new, 7 tests, previously no test covered
this module at all.

| Test | Guards |
| --- | --- |
| `test_platform_sensitive_modules_type_check_on[win32/linux/darwin]` | The regression itself: type-checks every platform-sensitive module as each supported platform and requires all three to agree |
| `test_platform_sensitive_modules_are_discovered` | The check above cannot pass vacuously if the module is renamed or the scan drifts |
| `test_returns_true_only_on_windows` | Return value reports whether a policy was installed, on both platforms |
| `test_is_idempotent` | Called from both `python -m app` and `tests/conftest.py`; repeats must be safe |
| `test_windows_gets_a_selector_policy` | The actual Windows requirement — a real `WindowsSelectorEventLoopPolicy` is installed |

The platform-sensitive module set is **discovered by scanning `app/` for
`sys.platform`**, not hard-coded, so a future module that branches on the
platform is covered without anyone remembering to add it.

`test_windows_gets_a_selector_policy` writes its Windows-only assertion to the
same shape rule as the production code: `asyncio.WindowsSelectorEventLoopPolicy`
sits inside a `sys.platform == "win32"` branch rather than behind a `skipif`
marker, because a marker would leave the reference exposed to `--platform linux`
and fail the very check this file adds. It skips visibly on Linux.

### The test was proven to fail against the old code

`app/core/runtime.py` was temporarily reverted to the pre-fix shape and the new
suite run on Windows:

```
FAILED tests/unit/test_runtime.py::test_platform_sensitive_modules_type_check_on[linux]
  AssertionError: mypy --platform linux failed:
    app\core\runtime.py:13: error: Statement is unreachable  [unreachable]
FAILED tests/unit/test_runtime.py::test_platform_sensitive_modules_type_check_on[darwin]
2 failed, 5 passed
```

The exact CI error now reproduces on a Windows developer machine, in under
three seconds, before a push. The file was restored immediately and re-verified.

## Preventing recurrence beyond this one module

The test covers the platform-sensitive surface quickly on every developer
machine. The full codebase is covered in CI, where the cost is irrelevant:

- `.github/workflows/ci.yml` — the `mypy` step now runs `mypy --platform linux`
  **and** `mypy --platform win32` over all 54 files. The runner's own OS no
  longer determines what gets checked.
- `CLAUDE.md` and `README.md` — the documented mypy command is now both
  invocations, so "run the gates locally" means the same thing as CI.
- `CLAUDE.md` gains a fourth entry in *Platform traps already paid for*,
  recording both shape rules.

## Local validation results

Every command below actually ran and actually passed.

| Gate | Command | Result |
| --- | --- | --- |
| Lint | `ruff check .` | **PASS** — All checks passed |
| Format | `ruff format --check .` | **PASS** — 56 files already formatted |
| Types (Linux target) | `mypy --platform linux` | **PASS** — 54 source files |
| Types (Windows target) | `mypy --platform win32` | **PASS** — 54 source files |
| Types (macOS target) | `mypy --platform darwin` | **PASS** — 54 source files |
| Architecture | `lint-imports` | **PASS** — 4 contracts kept, 0 broken |
| Backend tests (with PostgreSQL) | `pytest` | **PASS** — **78 passed, 0 skipped**, 8.84 s |
| Backend tests (without) | `pytest` | **PASS** — 74 passed, 4 skipped (named) |
| Frontend tests | `npm test` | **PASS** — 12 passed |
| Frontend typecheck / lint / build | `npm run …` | **PASS** — built in 648 ms |
| Compose | `docker compose config -q` | **PASS** |

Backend test count went from 71 to 78 (+7). Frontend was not modified; its
gates were run as a regression check only.

The new mypy-based tests add roughly 2.5 s to the suite — three mypy
invocations over a small module graph, each with a private cache directory so
the developer's shared `.mypy_cache` is neither polluted nor invalidated.

**On the Linux CI runner the suite will report 77 passed, 1 skipped** — the
Windows-only policy assertion. That skip is named and visible, never silent.

## Architecture boundaries

**Unchanged.** `lint-imports` reports 4 contracts kept, 0 broken. No layer moved,
no dependency direction changed, no contract was weakened. `app/core/runtime.py`
stays where it was. The new test imports `mypy.api`, which is an existing dev
dependency used from `tests/` only — the same pattern `test_architecture.py`
already uses with `importlinter.cli`. No production module gained an import,
and no new dependency was added to the project.

## Files modified

| File | Change |
| --- | --- |
| `backend/app/core/runtime.py` | Guard clause replaced with the symmetric branch; docstring records the two shape rules |
| `backend/tests/unit/test_runtime.py` | **New** — 7 regression tests, including the cross-platform mypy parity check |
| `.github/workflows/ci.yml` | mypy step now runs both platform targets |
| `CLAUDE.md` | Documented mypy command covers both targets; new platform trap 4 |
| `README.md` | Same command update, plus a note on why mypy runs twice |

`docs/viop_master_spec.md` was **not** modified. No Phase 1 file was created, no
Phase 1 dependency installed. Nothing was committed or pushed.

## Remaining cross-platform risk

- **Discovery is textual.** The parity test finds modules that mention
  `sys.platform`. Code that varies by platform *without* naming it — `os.name`,
  `platform.system()`, `pathlib` separator assumptions — is not picked up by the
  scan. The CI full-codebase double run does cover those; the test is the fast
  local net, not the whole net.
- **`--platform` models declarations, not behaviour.** It reflects typeshed's
  platform-conditional stubs. It cannot catch runtime differences: path
  separators, file locking, signal availability, event-loop internals. Only
  execution catches those, and **CI executes on Linux only** — Windows runtime
  behaviour is still exercised solely on the developer machine. A Windows job in
  the CI matrix would close this; it is a CI scope decision for the user, not a
  Phase 0 defect, and was not added unilaterally.
- **`sys.version_info` has the identical static-narrowing property.**
  `python_version = "3.12"` is pinned in `pyproject.toml`, so mypy agrees
  everywhere today, but the local interpreter is 3.14 while CI runs 3.12 — a
  runtime-only divergence remains possible.
- **Pre-existing, unchanged:** `asyncio.set_event_loop_policy` and
  `WindowsSelectorEventLoopPolicy` are deprecated in 3.14 and slated for removal
  in 3.16. The new test now references them too, so the removal will surface in
  one more place. Still tracked as technical debt, deliberately not silenced.
- **The corrected workflow has not itself run on GitHub.** It is validated by
  both mypy targets passing locally over the full codebase and by the step
  mirroring commands verified here. Reported as FIXED LOCALLY, not as a green CI
  run.

---

# STOP

The CI failure is fixed inside Phase 0. Phase 1 has not been started,
scaffolded, or prepared for. Nothing was committed or pushed.

Awaiting explicit approval.
