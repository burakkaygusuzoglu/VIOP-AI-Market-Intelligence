"""The rendered prompt, as plain text (§8).

A deliberately tiny module holding one dataclass of strings, separate from
`prompt.py` which builds it.

The separation is not stylistic. `prompt.py` must import `SynthesisContext` to
render one, and `SynthesisContext` reaches down into the analysis domain. An
adapter needs to *carry* a rendered prompt without acquiring that dependency,
and this is the type that lets it: four strings, no imports, nothing to reach
through.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SynthesisPrompt:
    """One rendered prompt: what will actually be sent, and what identifies it.

    Produced only by `synthesis.prompt.build_prompt`, which is where the
    untrusted-text boundary is applied. Nothing else should construct one -
    a hand-built instance would be text that skipped that boundary.
    """

    system: str
    user: str
    version: str
    context_digest: str

    @property
    def total_characters(self) -> int:
        return len(self.system) + len(self.user)
