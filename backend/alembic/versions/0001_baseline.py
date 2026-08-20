"""Baseline revision.

Establishes the migration chain and the alembic_version table. It creates no
domain tables on purpose: the models listed in master spec section 93 are
introduced by the phases that own them, so that the schema never contains
tables for features that do not exist (master spec section 105).

Revision ID: 0001_baseline
Revises:
Create Date: Phase 0
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """No schema objects yet."""


def downgrade() -> None:
    """No schema objects to remove."""
