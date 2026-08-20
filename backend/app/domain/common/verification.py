"""Provenance labelling for financial facts (master spec section 118).

Mutable exchange, broker and contract facts - multipliers, tick sizes,
margins, session hours, expiry rules - must never be assumed from model
memory or silently hard-coded. Every such value carries the status of how it
was obtained, so that an unverified number can never be mistaken for an
authoritative one anywhere downstream.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum, unique


@unique
class VerificationStatus(StrEnum):
    """How a financial fact was obtained."""

    VERIFIED_CURRENT_FACT = "VERIFIED_CURRENT_FACT"
    """Taken from a current authoritative primary source, with a reference."""

    DEVELOPMENT_DEFAULT = "DEVELOPMENT_DEFAULT"
    """A placeholder chosen to let development proceed. Not authoritative."""

    TEST_FIXTURE = "TEST_FIXTURE"
    """Test data. Must never be presented as a current exchange specification."""

    MOCK_DATA = "MOCK_DATA"
    """Generated or simulated data."""

    UNVERIFIED = "UNVERIFIED"
    """Value is unknown or unconfirmed. Preferred over guessing."""

    @property
    def is_authoritative(self) -> bool:
        """True only for values safe to use as a current market fact."""
        return self is VerificationStatus.VERIFIED_CURRENT_FACT


@dataclass(frozen=True, slots=True)
class VerifiedValue[T]:
    """A financial value bound to its provenance.

    ``source`` names where the value came from (an official document, a
    provider, a fixture file). ``as_of`` records when it was valid.
    """

    value: T
    status: VerificationStatus
    source: str
    as_of: datetime | None = None
    note: str | None = None

    @property
    def is_authoritative(self) -> bool:
        return self.status.is_authoritative

    def require_authoritative(self, context: str) -> T:
        """Return the value, refusing to release non-authoritative data.

        Call sites performing real financial arithmetic use this so that a
        development default or an unverified guess cannot silently reach a
        calculation presented to the user.
        """
        if not self.is_authoritative:
            raise UnverifiedFinancialFactError(context=context, fact=self)
        return self.value

    @classmethod
    def unverified(cls, source: str, note: str | None = None) -> VerifiedValue[None]:
        """Explicitly mark a fact as unknown rather than guessing it."""
        return VerifiedValue[None](
            value=None,
            status=VerificationStatus.UNVERIFIED,
            source=source,
            note=note,
        )


class UnverifiedFinancialFactError(RuntimeError):
    """Raised when non-authoritative data is used where a real fact is required."""

    def __init__(self, context: str, fact: VerifiedValue[object]) -> None:
        self.context = context
        self.fact = fact
        super().__init__(
            f"{context}: value from '{fact.source}' is {fact.status.value}, "
            "not a verified current fact"
        )
