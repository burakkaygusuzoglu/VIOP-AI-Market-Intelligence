"""The Claude synthesis provider (§10, §14).

Implements `MarketSynthesisProvider` by rendering the versioned prompt, sending
it through a transport, and parsing the answer against the strict schema.

    prompt (application) → transport → json.loads → SynthesisOutputSchema
                                                  → SynthesisDraft

That is the whole adapter. It deliberately does **not** decide whether the
draft is permitted: `validate_synthesis` does, in the use case, against the
same context. An adapter that also judged its own output would be marking its
own homework, and the judgement would live where the provider's shape can
influence it.

## Nothing is repaired

§14: malformed output becomes `INVALID_OUTPUT`. Not a partial draft, not a
best-effort parse, not a second request hoping for better. The rejection
message never echoes model content, so a provider cannot write a log line or an
error body by returning one.

## Structured output

`messages.create` plus our own validation, rather than the SDK's
`messages.parse(output_format=)` - the same choice Phase 6 made and for the
same reason: the guarantee that invalid output cannot be accepted is enforced
by code in this repository, where it is tested with a fake transport and cannot
change under us when the SDK does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal

from pydantic import ValidationError

from app.adapters.synthesis.transport import (
    SynthesisTransport,
    SynthesisTransportRequest,
    SynthesisUsage,
)
from app.application.ports.synthesis import SynthesisRequest
from app.application.synthesis.draft import SynthesisDraft
from app.application.synthesis.errors import SynthesisFailure, SynthesisProviderError
from app.application.synthesis.schemas import SynthesisOutputSchema, scenario_cases_are_complete


@dataclass(frozen=True, slots=True)
class SynthesizerConfig:
    """What the adapter needs to make one call."""

    model: str
    max_tokens: int = 4096

    def __post_init__(self) -> None:
        if not self.model.strip():
            # §11: no implicit fallback. An unknown model is a configuration
            # failure, not something to guess around.
            raise SynthesisProviderError(
                SynthesisFailure.NOT_CONFIGURED, "no synthesis model is configured"
            )
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")


class ClaudeMarketSynthesizer:
    """`MarketSynthesisProvider` backed by Anthropic.

    Takes a `SynthesisTransport`, so every path - success, malformed JSON,
    schema violation, timeout, rate limit - is exercised in tests with a fake
    and no key, no network and no paid request.
    """

    def __init__(self, transport: SynthesisTransport, config: SynthesizerConfig) -> None:
        self._transport = transport
        self._config = config
        self._last_usage = SynthesisUsage(model=config.model)

    @property
    def last_usage(self) -> SynthesisUsage:
        """Usage from the most recent call, as the provider reported it.

        Read by the caller for the audit record. Never fabricated: absent
        counts stay `None`.
        """
        return self._last_usage

    async def synthesize(self, request: SynthesisRequest) -> SynthesisDraft:
        # The prompt arrives rendered. This adapter deliberately does not build
        # one: the use case already measured that exact text against the token
        # budget, and rendering a second copy here would mean the thing
        # measured and the thing sent were different strings.
        response = await self._transport.send(
            SynthesisTransportRequest(
                system=request.prompt.system,
                user_message=request.prompt.user,
                model=self._config.model,
                max_tokens=min(self._config.max_tokens, request.max_output_tokens),
            )
        )
        self._last_usage = response.usage
        return _validate(response.text).to_draft()


def _validate(text: str) -> SynthesisOutputSchema:
    """Parse and validate, refusing anything that does not fit exactly."""
    stripped = text.strip()
    if not stripped:
        raise SynthesisProviderError(
            SynthesisFailure.INVALID_OUTPUT, "the synthesis provider returned an empty response"
        )

    try:
        # `parse_float=Decimal` for the same reason Phase 6 uses it: a JSON
        # number must never become a binary float on the way in. No current
        # field is numeric, and the guarantee should not depend on that
        # staying true.
        parsed = json.loads(stripped, parse_float=Decimal, parse_int=Decimal)
    except json.JSONDecodeError as error:
        raise SynthesisProviderError(
            SynthesisFailure.INVALID_OUTPUT,
            "the synthesis response was not valid JSON and was not repaired",
        ) from error

    if not isinstance(parsed, dict):
        raise SynthesisProviderError(
            SynthesisFailure.INVALID_OUTPUT,
            f"the synthesis response was a {type(parsed).__name__}, not an object",
        )

    try:
        schema = SynthesisOutputSchema.model_validate(parsed)
    except ValidationError as error:
        # The message names the failing fields, never their values: a model
        # must not be able to write a log line by putting text in a field.
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        raise SynthesisProviderError(
            SynthesisFailure.INVALID_OUTPUT,
            f"the synthesis response failed schema validation at: {', '.join(fields)}",
        ) from error

    if not scenario_cases_are_complete(schema):
        # Field names satisfied, content swapped - a BULL case in the bear
        # slot. Structurally valid and semantically wrong.
        raise SynthesisProviderError(
            SynthesisFailure.INVALID_OUTPUT,
            "the three scenario narratives are not one of each case",
        )

    return schema
