"""Typed vision-provider failures (§14 of the Phase 6B brief).

Every way a provider call can fail maps to one of these. The point is that a
caller - and eventually an API route - can react to a *kind* of failure without
parsing a vendor exception or a message string, and without a vendor type
crossing the port boundary.

Two properties matter downstream:

`retryable` says whether trying again could plausibly work. A timeout might; a
malformed schema response will not, and retrying it would burn quota to receive
the same broken answer.

`public_detail` is what may be shown to a user. It never contains a key, a
provider payload, a stack trace or image bytes - §17 requires typed public
errors, and the way to guarantee that is to have the safe text be a separate
field from the diagnostic one rather than a filtered version of it.
"""

from __future__ import annotations

from enum import StrEnum, unique


@unique
class VisionFailure(StrEnum):
    """What went wrong, in terms the application understands."""

    TIMEOUT = "TIMEOUT"
    NETWORK = "NETWORK"
    RATE_LIMITED = "RATE_LIMITED"
    AUTHENTICATION = "AUTHENTICATION"
    CONFIGURATION = "CONFIGURATION"
    """The provider was never called: no key, no model, or a setting that
    cannot produce a valid request."""

    PROVIDER_REJECTED = "PROVIDER_REJECTED"
    """The provider refused the request - an unsupported image, a payload over
    its own limits, a content refusal."""

    INVALID_OUTPUT = "INVALID_OUTPUT"
    """The response arrived but did not satisfy the schema. Never repaired,
    never partially accepted."""

    UNAVAILABLE = "UNAVAILABLE"
    """A transient provider-side fault: overloaded, 5xx."""

    @property
    def retryable(self) -> bool:
        """Whether another attempt could plausibly succeed.

        Deliberately narrow. `INVALID_OUTPUT` is excluded because a model that
        returned the wrong shape will most likely return it again, and
        `AUTHENTICATION` because a bad key does not fix itself.
        """
        return self in _RETRYABLE


_RETRYABLE = frozenset(
    {
        VisionFailure.TIMEOUT,
        VisionFailure.NETWORK,
        VisionFailure.UNAVAILABLE,
        VisionFailure.RATE_LIMITED,
    }
)


class VisionProviderError(RuntimeError):
    """A vision call that did not produce a usable result.

    ``detail`` is for logs and may name the provider's own error class.
    ``public_detail`` is for users and is a fixed phrase per failure kind, so
    no provider text can leak through it by accident.
    """

    def __init__(
        self,
        failure: VisionFailure,
        detail: str,
        *,
        attempts: int = 1,
    ) -> None:
        super().__init__(detail)
        self.failure = failure
        self.detail = detail
        self.attempts = attempts

    @property
    def retryable(self) -> bool:
        return self.failure.retryable

    @property
    def public_detail(self) -> str:
        """Safe to show. Never derived from provider output."""
        return _PUBLIC_DETAIL[self.failure]


_PUBLIC_DETAIL: dict[VisionFailure, str] = {
    VisionFailure.TIMEOUT: "Görüntü analizi zaman aşımına uğradı. Lütfen tekrar deneyin.",
    VisionFailure.NETWORK: "Görüntü analizi servisine ulaşılamadı.",
    VisionFailure.RATE_LIMITED: "Görüntü analizi servisi şu an yoğun. Lütfen biraz sonra deneyin.",
    VisionFailure.AUTHENTICATION: "Görüntü analizi servisi yapılandırılmamış.",
    VisionFailure.CONFIGURATION: "Görüntü analizi servisi yapılandırılmamış.",
    VisionFailure.PROVIDER_REJECTED: "Görüntü analiz servisi tarafından kabul edilmedi.",
    VisionFailure.INVALID_OUTPUT: (
        "Görüntü analizi geçerli bir sonuç döndürmedi; sonuç kabul edilmedi."
    ),
    VisionFailure.UNAVAILABLE: "Görüntü analizi servisi geçici olarak kullanılamıyor.",
}
