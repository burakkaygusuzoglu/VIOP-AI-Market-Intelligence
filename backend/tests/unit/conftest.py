"""Isolation for unit tests.

Unit tests must not read the developer's or CI runner's environment. Without
this, a test asserting "no secrets are configured" passes on a clean machine
and fails the moment POSTGRES_PASSWORD is exported - and, worse, a test could
pass for the wrong reason because ambient configuration happened to match.

Integration tests deliberately do the opposite and read the environment, so
this fixture is scoped to tests/unit only.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.core.config import Settings

# Every environment variable Settings can read, derived from the model itself
# so a new setting cannot silently escape the isolation.
_SETTINGS_ENV_VARS = tuple(name.upper() for name in Settings.model_fields)


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove ambient application settings for the duration of a unit test."""
    for name in _SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield
