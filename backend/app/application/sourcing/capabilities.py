"""The per-category capability matrix (Phase 15 Part 2A).

One status per data category, computed by the domain's staged
:func:`~app.domain.sourcing.capability.category_status`. The production build
configures no provider, so its matrix is every category ``NOT_CONFIGURED`` -
not ``UNAVAILABLE``, which would imply something was set up and failed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.sourcing.capability import (
    CapabilityStatus,
    DataCategory,
    LicenceGrant,
    ProviderDeclaration,
    category_status,
)

__all__ = ["CategoryCapability", "capability_matrix"]


@dataclass(frozen=True, slots=True)
class CategoryCapability:
    category: DataCategory
    status: CapabilityStatus
    reason: str


def capability_matrix(
    *,
    declaration: ProviderDeclaration | None,
    grant: LicenceGrant | None,
    connected: bool,
    last_delivery: Mapping[DataCategory, datetime],
    max_age: timedelta,
    at: datetime,
) -> tuple[CategoryCapability, ...]:
    """Every category's status, in category order. Never omits one."""
    matrix = []
    for category in DataCategory:
        status, reason = category_status(
            category,
            declaration=declaration,
            grant=grant,
            connected=connected,
            last_delivery_at=last_delivery.get(category),
            max_age=max_age,
            at=at,
        )
        matrix.append(CategoryCapability(category=category, status=status, reason=reason))
    return tuple(matrix)
