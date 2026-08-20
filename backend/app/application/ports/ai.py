"""AI provider port (master spec sections 68 and 69).

Anthropic SDK calls must never be scattered through business logic, and free
text must never drive application state: an AI response is accepted only after
it validates against an explicit schema.

Claude is an interpretation and explanation layer. It is not the source of
truth for numeric financial calculations.

Phase 0 defines the boundary only. No adapter, prompt or API call exists yet;
those arrive with Phase 6 (vision) and Phase 7 (synthesis).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from pydantic import BaseModel


@dataclass(frozen=True, slots=True)
class ImageInput:
    """An image supplied to a multimodal model."""

    media_type: str
    data: bytes


@dataclass(frozen=True, slots=True)
class AIRequest:
    """A single structured-output request."""

    system_prompt: str
    system_prompt_version: str
    user_content: str
    images: Sequence[ImageInput] = field(default_factory=tuple)
    max_tokens: int = 4096


@dataclass(frozen=True, slots=True)
class AIUsage:
    """Token accounting, recorded for cost tracking and auditability."""

    input_tokens: int
    output_tokens: int
    model: str


@dataclass(frozen=True, slots=True)
class AIResult[TModel: BaseModel]:
    """A validated model response together with its usage record."""

    output: TModel
    usage: AIUsage


class AIProvider[TModel: BaseModel](Protocol):
    """Returns schema-validated structured output, never raw free text."""

    async def complete_structured(
        self,
        request: AIRequest,
        response_model: type[TModel],
    ) -> AIResult[TModel]:
        """Run the request and validate the response against ``response_model``.

        Implementations must raise rather than return partially valid data.
        Invalid AI output must never reach application state.
        """
        ...
