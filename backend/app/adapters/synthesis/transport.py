"""The Anthropic transport for synthesis (§10, §11).

The **only** module in the synthesis slice that imports the SDK, mirroring what
Phase 6 did for vision. Everything above it speaks `SynthesisRequest` and
`SynthesisDraft`; nothing above it knows what an `anthropic` type looks like.

Deliberately tiny. Everything that could be done here and done wrong - parsing,
validating, retrying, interpreting, deciding what is permitted - is done above
instead, where it is testable without a network.

## Why not reuse the vision transport

The vision transport sends an image block and takes a media type. Synthesis
sends text only: §17 says the structured vision output already travels in the
context, so a screenshot is never re-sent to the synthesis provider. Forcing
one transport to serve both would mean an image parameter that synthesis must
always pass empty, which is the kind of unused path that eventually gets used.
The exception mapping is genuinely the same, and is the part that was reused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import anthropic
from anthropic.types import MessageParam

from app.application.synthesis.errors import SynthesisFailure, SynthesisProviderError


@dataclass(frozen=True, slots=True)
class SynthesisTransportRequest:
    """One provider call, in vendor-neutral terms."""

    system: str
    user_message: str
    model: str
    max_tokens: int


@dataclass(frozen=True, slots=True)
class SynthesisUsage:
    """Token accounting, exactly as the provider reported it.

    `None` means the provider did not say. §26: never fabricated as zero, which
    would be indistinguishable from a genuinely free call.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str = ""
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class SynthesisTransportResponse:
    """Raw text from the provider, plus whatever usage it reported."""

    text: str
    usage: SynthesisUsage


class SynthesisTransport(Protocol):
    """Sends one synthesis request and returns raw text."""

    async def send(self, request: SynthesisTransportRequest) -> SynthesisTransportResponse:
        """Raises `SynthesisProviderError` for every failure mode."""
        ...


class AnthropicSynthesisTransport:
    """Calls the Anthropic Messages API.

    Retries are the SDK's own bounded `max_retries`; there is no loop in this
    file. §25 forbids an unbounded retry, and the way to guarantee that is to
    have nowhere for one to live.
    """

    def __init__(
        self,
        api_key: str,
        *,
        timeout_seconds: float = 90.0,
        max_retries: int = 2,
    ) -> None:
        if not api_key.strip():
            raise SynthesisProviderError(
                SynthesisFailure.NOT_CONFIGURED, "no synthesis API key is configured"
            )
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def send(self, request: SynthesisTransportRequest) -> SynthesisTransportResponse:
        messages: list[MessageParam] = [MessageParam(role="user", content=request.user_message)]
        try:
            message = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=messages,
            )
        except anthropic.APITimeoutError as error:
            raise SynthesisProviderError(
                SynthesisFailure.TIMEOUT, "the synthesis provider timed out"
            ) from error
        except anthropic.AuthenticationError as error:
            raise SynthesisProviderError(
                SynthesisFailure.AUTHENTICATION, "the synthesis provider rejected the credential"
            ) from error
        except anthropic.RateLimitError as error:
            raise SynthesisProviderError(
                SynthesisFailure.RATE_LIMITED, "the synthesis provider rate limit was reached"
            ) from error
        except anthropic.BadRequestError as error:
            raise SynthesisProviderError(
                SynthesisFailure.PROVIDER_REJECTED,
                f"the synthesis provider rejected the request: {error.status_code}",
            ) from error
        except anthropic.APIConnectionError as error:
            raise SynthesisProviderError(
                SynthesisFailure.NETWORK, "the synthesis provider could not be reached"
            ) from error
        except anthropic.APIStatusError as error:
            raise SynthesisProviderError(
                SynthesisFailure.UNAVAILABLE,
                f"the synthesis provider returned status {error.status_code}",
            ) from error
        except anthropic.AnthropicError as error:
            raise SynthesisProviderError(
                SynthesisFailure.UNAVAILABLE, "the synthesis provider failed"
            ) from error

        return SynthesisTransportResponse(
            text=_text_of(message),
            usage=_usage_of(message, request.model),
        )


def _text_of(message: object) -> str:
    """Join the text blocks, ignoring any other block type.

    Written defensively against the SDK's shape rather than indexing blindly: a
    response with no text block yields an empty string, which the analyser
    above rejects as INVALID_OUTPUT with its own message.
    """
    blocks = getattr(message, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts)


def _usage_of(message: object, model: str) -> SynthesisUsage:
    """Read usage if reported; never invent it (§26)."""
    usage = getattr(message, "usage", None)
    request_id = str(getattr(message, "id", "") or "")
    if usage is None:
        return SynthesisUsage(model=model, request_id=request_id)
    return SynthesisUsage(
        input_tokens=_optional_int(getattr(usage, "input_tokens", None)),
        output_tokens=_optional_int(getattr(usage, "output_tokens", None)),
        model=str(getattr(message, "model", model)),
        request_id=request_id,
    )


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
