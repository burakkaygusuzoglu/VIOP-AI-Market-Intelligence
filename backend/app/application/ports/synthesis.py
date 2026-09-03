"""The market-synthesis provider port (§23).

## Why not reuse `AIProvider`

`AIProvider` is generic over a pydantic response model and takes
`system_prompt` plus free-text `user_content` and images. That shape is right
for "run this prompt and validate the answer", and wrong for synthesis in two
specific ways:

* it accepts **free text**, so any caller could assemble a prompt of its own.
  Phase 7's whole safety story is that the prompt body is built from a
  canonical context by code that delimits untrusted text (§16), and a port that
  takes a string invites bypassing it.
* it accepts **images**, which §17 says synthesis should not re-send: the
  structured vision output already travels in the context.

So this is a narrower port that takes the typed context and nothing else.
`AIProvider` stays where it is, unchanged, for uses that genuinely are
"prompt in, schema out".

## What crosses this boundary

A `SynthesisContext` in, a `SynthesisDraft` out - both plain frozen
dataclasses. No SDK types, no dictionaries, no `Any`, no HTTP objects, and no
pydantic: the strict schema lives in `synthesis/schemas.py` and an adapter
converts before returning, exactly as Phase 6's vision adapter does.

A returned draft is a **proposal**. Implementations do not decide whether it is
permitted; `validator.validate_synthesis` does, against the same context.

No adapter exists in 7A. Nothing here opens a socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from app.application.synthesis.context import SynthesisContext
from app.application.synthesis.draft import SynthesisDraft
from app.application.synthesis.errors import SynthesisProviderError
from app.application.synthesis.rendered import SynthesisPrompt


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    """One synthesis attempt.

    Carries the context and the versions that identify the attempt. It does
    **not** carry a prompt: building the prompt body from the context is the
    adapter's job in 7B, using the untrusted-text primitives, so no caller can
    hand a provider text that skipped the trust boundary.
    """

    context: SynthesisContext
    prompt: SynthesisPrompt
    """The **already rendered** prompt, built by `synthesis.prompt.build_prompt`.

    Carried rather than rebuilt inside the adapter, for one specific reason:
    the use case estimates the token cost from this text before deciding
    whether to call the provider at all. If the adapter rendered its own, the
    thing measured and the thing sent would be two different strings that
    merely usually agree - and the budget check would be measuring the wrong
    prompt the moment they diverged.

    It also keeps prompt construction in exactly one place, which is what makes
    the untrusted-text boundary in §16 a boundary rather than a convention.
    """

    locale: str = "tr"
    """Turkish first, per §4 of the master spec. The context itself is
    language-neutral; this says what the narrative should be written in."""

    max_output_tokens: int = 4096


# The failure type lives in `app.application.synthesis.errors` so there is
# exactly one, carrying both an internal detail and a fixed public phrase.
# Re-exported by name here because it is part of this port's contract:
# implementations raise it, and callers catch it.
__all__ = [
    "MarketSynthesisProvider",
    "SynthesisProviderError",
    "SynthesisRequest",
]


@runtime_checkable
class MarketSynthesisProvider(Protocol):
    """Turns a deterministic context into a proposed narrative synthesis."""

    async def synthesize(self, request: SynthesisRequest) -> SynthesisDraft:
        """Return a structurally valid proposal.

        Implementations must raise `SynthesisProviderError` rather than return
        a partially valid draft, and must never repair malformed provider
        output. Whether the returned proposal is *permitted* is decided by the
        deterministic validator, not here.
        """
        ...
