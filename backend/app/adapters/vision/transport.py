"""The only file in this repository that imports the Anthropic SDK.

Everything above it - the analyser, the use case, the port, the domain - speaks
in `VisionTransport` terms and never sees an SDK type. That is what makes the
whole vision path testable without a key: a fake transport is four lines.

## Why `messages.create` rather than `messages.parse`

The installed SDK (1.0.0, verified) offers both, and `parse(output_format=...)`
would hand back an already-validated model. It was not used, for one reason:
§4 requires that invalid model output *must not be accepted* and must produce a
typed failure, and that guarantee has to live in code this project owns and
tests. If the SDK parsed the response, the malformed-output path would be the
SDK's behaviour rather than ours, and the tests proving we reject bad JSON
would be testing a vendor.

So the transport returns **text**, and `ClaudeScreenshotAnalyzer` validates it
against `VisionExtractionSchema`. The cost is one `json.loads`; the benefit is
that every rejection rule is ours.

## Retries

`AsyncAnthropic` has bounded retries built in (`max_retries`), so this module
configures that rather than writing a second retry loop on top - two layers
would multiply into a much longer worst case than either intends. There is no
unbounded loop and no sleep of our own anywhere in the vision path.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic
from anthropic.types import (
    Base64ImageSourceParam,
    ImageBlockParam,
    MessageParam,
    TextBlockParam,
)

from app.application.vision.errors import VisionFailure, VisionProviderError

_SUPPORTED_MEDIA_TYPES: dict[str, str] = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/webp": "image/webp",
}
"""The media types both §38 and the SDK's image block accept.

Checked here rather than assumed: a payload arriving with anything else is a
configuration fault on our side, not something to forward and let the provider
refuse.
"""


def _media_type_param(media_type: str) -> Any:
    """Validate the media type before it reaches the SDK's literal type."""
    normalised = media_type.strip().lower()
    if normalised not in _SUPPORTED_MEDIA_TYPES:
        raise VisionProviderError(
            VisionFailure.CONFIGURATION,
            f"media type {normalised or 'unset'} is not one the vision API accepts",
        )
    return normalised


@dataclass(frozen=True, slots=True)
class VisionRequest:
    """One vision call, in vendor-free terms."""

    system: str
    user_message: str
    image_bytes: bytes
    image_media_type: str
    max_tokens: int
    model: str


@dataclass(frozen=True, slots=True)
class VisionUsage:
    """Token accounting, when the provider reports it.

    Optional on purpose: §15 forbids fabricating usage the provider did not
    return, and forbids business logic depending on token counts.
    """

    input_tokens: int | None = None
    output_tokens: int | None = None
    model: str = ""


@dataclass(frozen=True, slots=True)
class VisionResponse:
    """Raw text from the provider, plus whatever usage it reported."""

    text: str
    usage: VisionUsage


class VisionTransport(Protocol):
    """Sends one vision request and returns raw text.

    Deliberately tiny. Everything that could be done here and done wrong -
    parsing, validating, retrying, interpreting - is done above instead.
    """

    async def send(self, request: VisionRequest) -> VisionResponse:
        """Raises `VisionProviderError` for every failure mode."""
        ...


class AnthropicVisionTransport:
    """`VisionTransport` backed by the Anthropic SDK.

    The client is injectable so tests can supply a stub without a key. When
    none is given, one is built from the supplied credentials - and a missing
    key raises a typed `CONFIGURATION` failure rather than letting the SDK
    fail later with a vendor exception.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        timeout_seconds: float,
        max_retries: int,
        client: anthropic.AsyncAnthropic | None = None,
    ) -> None:
        if client is not None:
            self._client = client
            return
        if not (api_key or "").strip():
            raise VisionProviderError(
                VisionFailure.CONFIGURATION,
                "no Anthropic API key is configured; the vision provider cannot be built",
            )
        self._client = anthropic.AsyncAnthropic(
            api_key=api_key,
            timeout=timeout_seconds,
            max_retries=max_retries,
        )

    async def send(self, request: VisionRequest) -> VisionResponse:
        """Call the model and return its text, mapping every vendor error."""
        # The SDK's own typed params rather than bare dicts, so a shape the
        # installed version does not accept is a type error here instead of a
        # runtime rejection from the provider.
        media_type = _media_type_param(request.image_media_type)
        image_block = ImageBlockParam(
            type="image",
            source=Base64ImageSourceParam(
                type="base64",
                media_type=media_type,
                data=base64.standard_b64encode(request.image_bytes).decode("ascii"),
            ),
        )
        text_block = TextBlockParam(type="text", text=request.user_message)

        try:
            message = await self._client.messages.create(
                model=request.model,
                max_tokens=request.max_tokens,
                system=request.system,
                messages=[MessageParam(role="user", content=[image_block, text_block])],
            )
        except anthropic.APITimeoutError as error:
            raise VisionProviderError(
                VisionFailure.TIMEOUT, "the vision request timed out"
            ) from error
        except anthropic.AuthenticationError as error:
            raise VisionProviderError(
                VisionFailure.AUTHENTICATION, "the vision provider rejected the credentials"
            ) from error
        except anthropic.RateLimitError as error:
            raise VisionProviderError(
                VisionFailure.RATE_LIMITED, "the vision provider rate-limited the request"
            ) from error
        except anthropic.BadRequestError as error:
            raise VisionProviderError(
                VisionFailure.PROVIDER_REJECTED, "the vision provider rejected the request"
            ) from error
        except anthropic.APIConnectionError as error:
            raise VisionProviderError(
                VisionFailure.NETWORK, "the vision provider could not be reached"
            ) from error
        except anthropic.APIStatusError as error:
            # Covers overloaded, internal errors and any other status the SDK
            # surfaces without a more specific class.
            raise VisionProviderError(
                VisionFailure.UNAVAILABLE,
                f"the vision provider returned status {error.status_code}",
            ) from error
        except anthropic.AnthropicError as error:
            raise VisionProviderError(
                VisionFailure.UNAVAILABLE, "the vision provider failed"
            ) from error

        return VisionResponse(text=_text_of(message), usage=_usage_of(message, request.model))


def _text_of(message: object) -> str:
    """Join the text blocks of a response, ignoring any other block type.

    Written defensively against the SDK's shape rather than indexing blindly:
    a response with no text block produces an empty string, which the analyser
    rejects as INVALID_OUTPUT with its own message.
    """
    blocks = getattr(message, "content", None) or []
    parts: list[str] = []
    for block in blocks:
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "")))
    return "".join(parts)


def _usage_of(message: object, model: str) -> VisionUsage:
    """Read usage if the provider reported it; never invent it (§15)."""
    usage = getattr(message, "usage", None)
    if usage is None:
        return VisionUsage(model=model)
    return VisionUsage(
        input_tokens=_optional_int(getattr(usage, "input_tokens", None)),
        output_tokens=_optional_int(getattr(usage, "output_tokens", None)),
        model=str(getattr(message, "model", model)),
    )


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None
