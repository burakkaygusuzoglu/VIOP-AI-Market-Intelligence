"""Event loop policy: runtime behaviour, and static-analysis parity.

Two distinct failures are guarded here.

The runtime one: on Windows, psycopg's async mode cannot use the default
``ProactorEventLoop``, and installing a selector policy is the entire reason
``app/core/runtime.py`` exists.

The static one: the first real CI run failed with ``Statement is unreachable``
on ``app/core/runtime.py`` under Linux while the identical mypy invocation
passed on Windows. mypy resolves ``sys.platform`` for the platform it is
checking and eliminates the branch that cannot be taken, so a module that
tests ``sys.platform`` can type-check cleanly on one platform and fail on
another. A green local run is therefore not evidence that CI will agree.

``test_platform_sensitive_modules_type_check_on`` closes that gap: it finds
every module under ``app/`` that inspects the platform and type-checks it as
each supported platform in turn, so the discrepancy fails here rather than in
CI - and covers modules that do not exist yet.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from mypy import api as mypy_api

from app.core.runtime import configure_event_loop_policy

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFIG = BACKEND_ROOT / "pyproject.toml"
APP_ROOT = BACKEND_ROOT / "app"

# Every platform the project is developed or run on: Windows for local
# development, Linux for containers and CI, macOS for completeness.
SUPPORTED_PLATFORMS = ("win32", "linux", "darwin")


def _platform_sensitive_modules() -> list[Path]:
    """Modules whose type-checking result can differ per platform.

    Discovered by inspection rather than hard-coded, so a future module that
    branches on the platform is covered without anyone remembering to add it.
    """
    return sorted(
        path
        for path in APP_ROOT.rglob("*.py")
        if "sys.platform" in path.read_text(encoding="utf-8")
    )


@pytest.fixture
def restored_event_loop_policy() -> Iterator[None]:
    """Undo any policy change a test makes, so global state does not leak."""
    original = asyncio.get_event_loop_policy()
    yield
    asyncio.set_event_loop_policy(original)


@pytest.mark.unit
def test_platform_sensitive_modules_are_discovered() -> None:
    """Without this, the parametrised check below could pass vacuously."""
    modules = _platform_sensitive_modules()
    assert modules, "no platform-sensitive module found; the discovery rule has drifted"
    assert APP_ROOT / "core" / "runtime.py" in modules


@pytest.mark.unit
@pytest.mark.parametrize("platform", SUPPORTED_PLATFORMS)
def test_platform_sensitive_modules_type_check_on(platform: str, tmp_path: Path) -> None:
    """mypy must reach the same verdict for every supported platform."""
    targets = [str(path) for path in _platform_sensitive_modules()]
    stdout, stderr, status = mypy_api.run(
        [
            "--config-file",
            str(CONFIG),
            "--platform",
            platform,
            "--no-incremental",
            # A private cache keeps this off the developer's shared one, which
            # is keyed for the host platform.
            "--cache-dir",
            str(tmp_path / "mypy-cache"),
            *targets,
        ]
    )
    assert status == 0, f"mypy --platform {platform} failed:\n{stdout}{stderr}"


@pytest.mark.unit
def test_returns_true_only_on_windows(restored_event_loop_policy: None) -> None:
    """The return value reports whether a policy was actually installed."""
    assert configure_event_loop_policy() is (sys.platform == "win32")


@pytest.mark.unit
def test_is_idempotent(restored_event_loop_policy: None) -> None:
    """Called from both ``python -m app`` and conftest; must tolerate repeats."""
    first = configure_event_loop_policy()
    second = configure_event_loop_policy()
    assert first is second


@pytest.mark.unit
def test_windows_gets_a_selector_policy(restored_event_loop_policy: None) -> None:
    """The Windows-only assertion, written to the same shape rule as the code.

    ``asyncio.WindowsSelectorEventLoopPolicy`` is not declared by typeshed off
    Windows, so the reference has to sit inside the ``sys.platform`` branch -
    a ``skipif`` marker would leave it exposed and fail ``--platform linux``.
    """
    if sys.platform == "win32":
        assert configure_event_loop_policy() is True
        assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)
    else:
        pytest.skip("selector policy is a Windows-only requirement")
