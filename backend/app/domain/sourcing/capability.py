"""What an external provider may claim, and who says so (Phase 15 Part 1).

Master spec Phase 15: evaluate legitimate providers for live market data,
contract metadata, open interest, news and market breadth, integrate them
through adapters, and couple no domain logic to one vendor. Nothing in this
module names a vendor.

## A capability is declared; a claim is granted

A provider adapter *declares* what it offers: which data categories, delivered
how, and with what delay. A declaration is not trust. Before a stream may carry
a real-exchange provenance, a **licence grant** must say the provider is
licensed for that category and delivery, and the grant's evidence must be a
``VERIFIED_CURRENT_FACT`` with a source and a date - a signed data agreement, a
vendor licence reference. A request body, an environment flag, a symbol or a
test fixture cannot produce one.

This build contains no grant. Borsa İstanbul disseminates real-time and delayed
data only through licensed data vendors under a data distribution agreement,
and no such licence is available to this project yet; every stream it composes
is therefore simulated history, and the functions below refuse anything else.

## Categories are separate capabilities

A price feed does not establish contract metadata, and neither establishes a
session calendar. Each is its own category with its own grant, so a provider
licensed for prices cannot, by being connected, make a multiplier authoritative.

## Six things that are not the same thing (Part 2A audit)

1. A **provider declaration** - the adapter's self-description.
2. **Verified entitlement evidence** - the grant's ``evidence``: a sourced,
   dated ``VERIFIED_CURRENT_FACT`` naming a licence or agreement.
3. A **configured adapter** - an operator chose a provider in settings.
4. An **authenticated connection** - the provider accepted the credentials
   and is delivering.
5. **Licensed market-data access** - 2 and 4 together, for ``MARKET_DATA``,
   at the declared delivery.
6. **Financial metadata authority** - a multiplier, tick size or session is
   trusted only through a reviewed, sourced fact record
   (:mod:`app.domain.sourcing.facts`). No grant confers it, including a grant
   for ``CONTRACT_METADATA``: a licence to *receive* a vendor's metadata says
   nothing about whether a given value is right or applicable.

A ``LicenceGrant`` is a claim record, not a capability token: any code can
construct one, and a test does. What stops a constructed grant opening
production is composition - no module in the application constructs or passes
one, settings admit no provider but ``none``, the live API's request models
forbid unknown fields, and the API serializes only simulated history. The
per-category :func:`category_status` below keeps stages 1-4 apart, so
"configured" is never reported as "available".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum, unique

from app.domain.common.verification import VerifiedValue
from app.domain.live.events import StreamProvenance

__all__ = [
    "CapabilityStatus",
    "DataCategory",
    "Delivery",
    "LicenceGrant",
    "ProvenanceRefusedError",
    "ProviderDeclaration",
    "authorize_provenance",
    "category_status",
    "grant_refusal",
    "provenance_for",
]


@unique
class DataCategory(StrEnum):
    """What a provider may supply. Each is granted separately."""

    MARKET_DATA = "MARKET_DATA"
    """Candles or trades for instruments."""

    CONTRACT_METADATA = "CONTRACT_METADATA"
    """Multipliers, tick sizes, expiries, margins - section 118 facts."""

    OPEN_INTEREST = "OPEN_INTEREST"
    NEWS = "NEWS"
    MARKET_BREADTH = "MARKET_BREADTH"
    SESSION_CALENDAR = "SESSION_CALENDAR"
    """Trading sessions, holidays, special sessions. Never guessed."""


@unique
class Delivery(StrEnum):
    REAL_TIME = "REAL_TIME"
    DELAYED = "DELAYED"
    HISTORICAL = "HISTORICAL"
    SIMULATED = "SIMULATED"


_PROVENANCE: dict[Delivery, StreamProvenance] = {
    Delivery.REAL_TIME: StreamProvenance.REAL_EXCHANGE_LIVE,
    Delivery.DELAYED: StreamProvenance.REAL_EXCHANGE_DELAYED,
    Delivery.HISTORICAL: StreamProvenance.PROVIDER_HISTORICAL,
    Delivery.SIMULATED: StreamProvenance.SIMULATED_HISTORICAL_STREAM,
}


class ProvenanceRefusedError(RuntimeError):
    """A provenance was claimed that nothing authorizes. Carries a code only."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class ProviderDeclaration:
    """What an adapter says about itself. Self-description, not authority."""

    provider_id: str
    categories: frozenset[DataCategory]
    delivery: Delivery
    delay_seconds: int | None = None
    """For ``DELAYED`` delivery, the stated publication delay. Required there,
    so a delayed feed can never be mistaken for a current one."""

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("a provider declaration names its provider")
        if not self.categories:
            raise ValueError("a provider declares at least one category")
        if self.delivery is Delivery.DELAYED and (
            self.delay_seconds is None or self.delay_seconds <= 0
        ):
            raise ValueError("a delayed provider states its delay")
        if self.delivery is not Delivery.DELAYED and self.delay_seconds is not None:
            raise ValueError("only a delayed provider states a delay")


@dataclass(frozen=True, slots=True)
class LicenceGrant:
    """Evidence that a provider is licensed for categories and a delivery.

    ``evidence`` is the licence or agreement reference as a ``VerifiedValue``:
    it counts only when it is a ``VERIFIED_CURRENT_FACT`` with a source and a
    date. ``valid_until`` bounds it - a lapsed licence grants nothing.
    """

    provider_id: str
    categories: frozenset[DataCategory]
    delivery: Delivery
    evidence: VerifiedValue[str]
    valid_until: datetime | None = None


def provenance_for(declaration: ProviderDeclaration) -> StreamProvenance:
    """The provenance a declaration *asks for*. Asking is not being granted."""
    return _PROVENANCE[declaration.delivery]


def authorize_provenance(
    declared: StreamProvenance,
    *,
    provider_id: str,
    grant: LicenceGrant | None,
    at: datetime,
) -> StreamProvenance:
    """Return ``declared`` if it may be carried, or refuse.

    Simulated and unverified provenances claim nothing and need no grant. A
    real-exchange provenance needs a grant that names this provider, covers
    market data, matches the delivery the provenance implies, rests on
    authoritative, sourced and dated evidence, and has not lapsed. Every
    failure is a typed refusal; none falls back to a weaker label silently.
    """
    if not declared.requires_grant:
        return declared
    if grant is None:
        raise ProvenanceRefusedError(
            "PROVENANCE_NOT_GRANTED",
            f"{declared.value} requires a licence grant and none was presented",
        )
    if grant.provider_id != provider_id:
        raise ProvenanceRefusedError(
            "GRANT_FOR_ANOTHER_PROVIDER", "the licence grant names a different provider"
        )
    if DataCategory.MARKET_DATA not in grant.categories:
        raise ProvenanceRefusedError(
            "GRANT_DOES_NOT_COVER_MARKET_DATA", "the licence grant does not cover market data"
        )
    if _PROVENANCE[grant.delivery] is not declared:
        raise ProvenanceRefusedError(
            "GRANT_DELIVERY_MISMATCH",
            f"the licence grant covers {grant.delivery.value} delivery, not {declared.value}",
        )
    evidence = grant.evidence
    if not evidence.is_authoritative or not evidence.source.strip() or evidence.as_of is None:
        raise ProvenanceRefusedError(
            "GRANT_NOT_AUTHORITATIVE",
            "the licence evidence is not a sourced, dated, verified current fact",
        )
    if grant.valid_until is not None and at >= grant.valid_until:
        raise ProvenanceRefusedError("GRANT_LAPSED", "the licence grant has lapsed")
    return declared


@unique
class CapabilityStatus(StrEnum):
    """What may be said about one data category of one provider, right now."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    """No provider is configured to offer this category."""

    NOT_LICENSED = "NOT_LICENSED"
    """A provider offers it, but no valid grant covers it."""

    UNAVAILABLE = "UNAVAILABLE"
    """Licensed, but not connected or nothing has been delivered."""

    AVAILABLE = "AVAILABLE"
    """Licensed, connected, and delivered within the freshness bound."""

    STALE = "STALE"
    """Licensed and connected, but the last delivery is older than the bound."""


def grant_refusal(
    grant: LicenceGrant | None,
    *,
    provider_id: str,
    category: DataCategory,
    delivery: Delivery,
    at: datetime,
) -> tuple[str, str] | None:
    """Why ``grant`` does not cover this provider, category and delivery at
    ``at`` - a ``(code, detail)`` pair - or ``None`` when it does."""
    if grant is None:
        return ("NOT_GRANTED", "no licence grant was presented")
    if grant.provider_id != provider_id:
        return ("GRANT_FOR_ANOTHER_PROVIDER", "the licence grant names a different provider")
    if category not in grant.categories:
        return ("GRANT_DOES_NOT_COVER_CATEGORY", f"the licence grant does not cover {category}")
    if delivery is Delivery.SIMULATED or grant.delivery is Delivery.SIMULATED:
        return ("SIMULATION_IS_NOT_LICENSED", "a simulation is licensed for nothing real")
    if grant.delivery is not delivery:
        return ("GRANT_DELIVERY_MISMATCH", "the licence grant covers another delivery")
    evidence = grant.evidence
    if not evidence.is_authoritative or not evidence.source.strip() or evidence.as_of is None:
        return (
            "GRANT_NOT_AUTHORITATIVE",
            "the licence evidence is not a sourced, dated, verified current fact",
        )
    if grant.valid_until is not None and at >= grant.valid_until:
        return ("GRANT_LAPSED", "the licence grant has lapsed")
    return None


def category_status(
    category: DataCategory,
    *,
    declaration: ProviderDeclaration | None,
    grant: LicenceGrant | None,
    connected: bool,
    last_delivery_at: datetime | None,
    max_age: timedelta,
    at: datetime,
) -> tuple[CapabilityStatus, str]:
    """The status of one category, with the reason, stage by stage.

    Each stage is required before the next is even asked: a configured
    provider without a grant is ``NOT_LICENSED`` however well it is
    connected; a licensed provider that is not connected, or has delivered
    nothing, is ``UNAVAILABLE``. Only a delivery inside ``max_age`` is
    ``AVAILABLE``.
    """
    if declaration is None or category not in declaration.categories:
        return CapabilityStatus.NOT_CONFIGURED, f"no provider is configured for {category}"
    refusal = grant_refusal(
        grant,
        provider_id=declaration.provider_id,
        category=category,
        delivery=declaration.delivery,
        at=at,
    )
    if refusal is not None:
        return CapabilityStatus.NOT_LICENSED, refusal[1]
    if not connected:
        return CapabilityStatus.UNAVAILABLE, "configured and licensed, but not connected"
    if last_delivery_at is None:
        return CapabilityStatus.UNAVAILABLE, "connected, but nothing has been delivered"
    if at - last_delivery_at > max_age:
        return CapabilityStatus.STALE, "the last delivery is older than the freshness bound"
    return CapabilityStatus.AVAILABLE, "licensed, connected and fresh"
