"""Typed synthesis provider failures (§25).

Deliberately the same shape as Phase 6's vision errors: an internal `detail`
for logs and a fixed Turkish `public_detail` for clients, with the HTTP mapping
reading the failure *type* rather than parsing a message. That is what keeps a
provider's own words out of a response without depending on anyone remembering
to sanitise.

The important property is the one §22 has insisted on since Phase 7A: **none of
these is a market opinion.** A timeout is not WAIT. A rate limit is not
NO_TRADE. Each maps to a `SynthesisStatus`, and the status vocabulary is
disjoint from `FinalAction` so the two cannot be confused even by accident.
"""

from __future__ import annotations

from enum import StrEnum, unique

from app.application.synthesis.draft import SynthesisStatus


@unique
class SynthesisFailure(StrEnum):
    """Why a synthesis attempt did not produce an accepted result."""

    NOT_CONFIGURED = "NOT_CONFIGURED"
    AUTHENTICATION = "AUTHENTICATION"
    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    RATE_LIMITED = "RATE_LIMITED"
    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    UNAVAILABLE = "UNAVAILABLE"

    @property
    def retryable(self) -> bool:
        """Whether retrying could plausibly help.

        `INVALID_OUTPUT` is deliberately absent: re-asking a model that
        returned malformed output is not a recovery strategy, it is the same
        request with a hope attached. `CONTEXT_TOO_LARGE` is absent because the
        context will be exactly as large next time.
        """
        return self in {
            SynthesisFailure.TIMEOUT,
            SynthesisFailure.NETWORK,
            SynthesisFailure.RATE_LIMITED,
            SynthesisFailure.UNAVAILABLE,
        }

    @property
    def status(self) -> SynthesisStatus:
        """The execution status this failure reports as - never an action."""
        if self is SynthesisFailure.NOT_CONFIGURED:
            return SynthesisStatus.NOT_CONFIGURED
        if self is SynthesisFailure.INVALID_OUTPUT:
            return SynthesisStatus.INVALID_OUTPUT
        if self is SynthesisFailure.CONTEXT_TOO_LARGE:
            return SynthesisStatus.CONTEXT_TOO_LARGE
        return SynthesisStatus.PROVIDER_FAILURE


_PUBLIC_DETAIL: dict[SynthesisFailure, str] = {
    SynthesisFailure.NOT_CONFIGURED: "Sentez servisi yapılandırılmamış.",
    SynthesisFailure.AUTHENTICATION: "Sentez servisi kimlik doğrulaması başarısız.",
    SynthesisFailure.TIMEOUT: "Sentez servisi zamanında yanıt vermedi.",
    SynthesisFailure.NETWORK: "Sentez servisine ulaşılamadı.",
    SynthesisFailure.RATE_LIMITED: "Sentez servisi istek sınırına ulaşıldı.",
    SynthesisFailure.PROVIDER_REJECTED: "Sentez servisi isteği reddetti.",
    SynthesisFailure.INVALID_OUTPUT: "Sentez geçerli bir sonuç döndürmedi; sonuç kabul edilmedi.",
    SynthesisFailure.CONTEXT_TOO_LARGE: "Analiz bağlamı sentez için fazla büyük.",
    SynthesisFailure.UNAVAILABLE: "Sentez servisi şu anda kullanılamıyor.",
}


class SynthesisProviderError(Exception):
    """A synthesis attempt failed, with a public face and a private one."""

    def __init__(self, failure: SynthesisFailure, detail: str) -> None:
        super().__init__(detail)
        self.failure = failure
        self.detail = detail
        """For logs. May name the provider, a status code or a timeout."""

    @property
    def public_detail(self) -> str:
        """A fixed Turkish phrase per failure kind.

        Never derived from the provider's message, so nothing internal can
        reach a client through the error path.
        """
        return _PUBLIC_DETAIL[self.failure]

    @property
    def status(self) -> SynthesisStatus:
        return self.failure.status

    @property
    def retryable(self) -> bool:
        return self.failure.retryable
