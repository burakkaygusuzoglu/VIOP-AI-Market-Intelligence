"""SQLAlchemy declarative base.

Explicit constraint naming conventions are set now, before any table exists,
so that every future Alembic migration can reference indexes and constraints
by a deterministic name instead of a database-generated one.

Phase 0 defines no tables. The models listed in master spec section 93 are
created by the phases that own each concept; tables for unimplemented
features would be fake implementation (master spec section 105).
"""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base for every persistence model."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
