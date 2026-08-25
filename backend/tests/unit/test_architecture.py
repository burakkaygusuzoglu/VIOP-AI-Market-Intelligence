"""Dependency direction is enforced, not merely documented.

Runs the import-linter contracts declared in pyproject.toml (master spec
sections 94 and 95) as part of the normal test run, so a violation fails the
build rather than surviving as a code-review remark.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from importlinter.cli import EXIT_STATUS_SUCCESS, lint_imports

BACKEND_ROOT = Path(__file__).resolve().parents[2]
CONFIG = BACKEND_ROOT / "pyproject.toml"


@pytest.mark.unit
def test_import_contracts_hold() -> None:
    exit_status = lint_imports(config_filename=str(CONFIG), no_cache=True)
    assert exit_status == EXIT_STATUS_SUCCESS, "import-linter contracts are broken"


@pytest.mark.unit
def test_domain_source_never_mentions_infrastructure() -> None:
    """A second, independent check that does not rely on the linter."""
    forbidden = ("import fastapi", "import sqlalchemy", "import anthropic", "import httpx")
    domain_files = list((BACKEND_ROOT / "app" / "domain").rglob("*.py"))
    assert domain_files, "domain package is missing"
    for path in domain_files:
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path} must not depend on infrastructure"


@pytest.mark.unit
def test_application_source_never_mentions_infrastructure() -> None:
    forbidden = ("import fastapi", "import sqlalchemy", "from app.adapters", "from app.api")
    for path in (BACKEND_ROOT / "app" / "application").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path} must not depend on infrastructure"


@pytest.mark.unit
def test_adapters_never_import_the_technical_engine() -> None:
    """Indicator mathematics stays in the domain (master spec section 1).

    An independent check of the "Adapters never compute indicators" contract,
    so the rule survives a change to the linter configuration.
    """
    adapter_files = list((BACKEND_ROOT / "app" / "adapters").rglob("*.py"))
    assert adapter_files, "adapters package is missing"
    for path in adapter_files:
        source = path.read_text(encoding="utf-8")
        assert "app.domain.technical" not in source, (
            f"{path} reaches into the technical engine; calculation belongs to the domain"
        )


@pytest.mark.unit
def test_the_technical_engine_depends_on_nothing_outside_the_domain() -> None:
    """The numerical authority must stay free of frameworks and infrastructure."""
    forbidden = (
        "import fastapi",
        "import sqlalchemy",
        "import anthropic",
        "import httpx",
        "import pydantic",
        "from app.adapters",
        "from app.api",
        "from app.core",
        "from app.application",
    )
    technical_files = list((BACKEND_ROOT / "app" / "domain" / "technical").rglob("*.py"))
    assert technical_files, "technical package is missing"
    for path in technical_files:
        source = path.read_text(encoding="utf-8")
        for needle in forbidden:
            assert needle not in source, f"{path} must not depend on {needle}"


# ----------------------------------------------------------------------
# The application must actually be importable, not merely well layered
# ----------------------------------------------------------------------

ENTRY_POINTS = (
    "app.main",
    "app.api.routes.screenshots",
    "app.application.ports.screenshot",
    "app.application.vision.analysis",
    "app.application.vision.intake",
    "app.adapters.vision.claude_analyzer",
)


@pytest.mark.unit
@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_each_entry_point_imports_first_in_a_clean_interpreter(module: str) -> None:
    """A circular import is invisible once something else has warmed the package.

    import-linter reports the *direction* of a dependency and is satisfied by a
    cycle that lies inside one layer; a normal test run hides it whenever some
    earlier test imports the modules in a lucky order. Phase 6 shipped exactly
    that: `uvicorn app.main:app` raised ImportError on a partially initialised
    port while all six gates were green. Each module is therefore imported as
    the very first thing a fresh interpreter does.
    """
    result = subprocess.run(  # noqa: S603 - fixed argv, no shell, no user input
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        cwd=BACKEND_ROOT,
        check=False,
    )
    assert result.returncode == 0, (
        f"{module} cannot be imported on its own:\n{result.stderr.strip()}"
    )
