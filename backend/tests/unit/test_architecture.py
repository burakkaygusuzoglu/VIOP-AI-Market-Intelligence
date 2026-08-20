"""Dependency direction is enforced, not merely documented.

Runs the import-linter contracts declared in pyproject.toml (master spec
sections 94 and 95) as part of the normal test run, so a violation fails the
build rather than surviving as a code-review remark.
"""

from __future__ import annotations

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
