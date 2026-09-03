"""Provider context-window accounting (§3, §12, §13).

Phase 7A budgeted in **entries**, which is provider-neutral and useful, and is
not the same question as "will this request fit". Phase 7B's first attempt
budgeted **only the input side** against a hard-coded 180 000 ceiling. Both of
those were wrong in the same direction: they could pass a request the provider
would refuse.

## The invariant

A request fits only when the *whole* request fits:

    estimated_input_tokens
      + max_output_tokens          what we will ask the model to be allowed to write
      + safety_reserve             the admission that the estimate can be wrong
      <= context_window            the model's hard limit

Budgeting the input alone is the classic error: a prompt that occupies 95% of
the window leaves no room for the answer, and the provider rejects the call
after it has been paid for. The four quantities are kept separate here so none
of them can absorb another silently.

## The context window is configuration, never a guess

`context_window` has **no default**. A model's context size is a fact about a
provider's catalogue that changes without notice, and §118 forbids inventing
one - the earlier `provider_maximum = 180_000` was exactly that, a number
nobody in this repository had verified.

So a deployment that has not stated its model's window is **not configured**,
and reports NOT_CONFIGURED rather than guessing. That is the same rule already
applied to the model identifier, for the same reason.

## Estimation, stated honestly

The installed Anthropic SDK does expose `messages.count_tokens`, and it is **a
network call requiring credentials**. That makes it unusable as a pre-flight
check: it would need a key before the application could decide whether to use
one, add a round trip to every request, and fail exactly when the provider is
unreachable - the moment a budget check matters most.

So this module **estimates**, pessimistically, and says so everywhere.
`TokenEstimate.is_exact` exists and is `False`, so a future exact counter has
somewhere honest to report from and no caller has to guess which it got.
"""

from __future__ import annotations

from dataclasses import dataclass

CHARACTERS_PER_TOKEN = 2.5
"""Conservative divisor for mixed Turkish prose, JSON and identifiers.

English prose runs nearer 4 characters per token. This context is not English
prose: it is canonical JSON full of `EV-BULL-8F2A1C9D0B`, decimal strings and
Turkish words with suffixes, all of which fragment. 2.5 deliberately
over-estimates - an over-estimate costs a trim, an under-estimate costs a
rejected request or a truncated prompt.
"""

ESTIMATE_MARGIN = 0.15
"""Added to the raw character estimate. The divisor above is a guess about
tokenisation; this is the admission that the guess can be wrong.

Distinct from `TokenBudget.safety_reserve`, which is a whole-request cushion.
This one scales with the input; that one does not.
"""

DEFAULT_SAFETY_RESERVE = 2_000
"""Whole-request cushion, in tokens.

Covers what neither the estimate nor the declared output allowance accounts
for: provider-side framing, system-prompt overhead, and the possibility that
the tokeniser disagrees with the estimator by more than the margin.
"""


@dataclass(frozen=True, slots=True)
class TokenEstimate:
    """An estimated token count, labelled as an estimate."""

    characters: int
    estimated_tokens: int
    is_exact: bool = False
    """Always `False` today. Present so that if an exact counter is ever wired
    in, callers can distinguish a measurement from a guess rather than having
    to know which code path produced the number."""

    method: str = "characters/2.5 + 15% margin (estimate, not a token count)"


def estimate_tokens(text: str) -> TokenEstimate:
    """Estimate the tokens ``text`` will occupy. Never exact."""
    characters = len(text)
    raw = characters / CHARACTERS_PER_TOKEN
    return TokenEstimate(
        characters=characters,
        estimated_tokens=int(raw * (1 + ESTIMATE_MARGIN)) + 1,
    )


class TokenBudgetNotConfiguredError(ValueError):
    """No context window was configured for the model in use.

    Raised at construction rather than defaulted around: a budget with an
    invented window is worse than no budget, because it looks like a check.
    """


@dataclass(frozen=True, slots=True)
class TokenBudget:
    """How much of a model's context window one request may occupy.

    The four quantities are separate fields on purpose. Collapsing any two of
    them - as an earlier version did, by conflating the output allowance with
    the safety reserve - hides which one is being spent.
    """

    context_window: int
    """The model's hard limit. **Configuration, not a default.** See the module
    docstring: this project does not invent a provider fact."""

    max_output_tokens: int
    """What the request will permit the model to write. Reserved from the
    window before any input is allowed, because the answer has to fit too."""

    safety_reserve: int = DEFAULT_SAFETY_RESERVE
    max_prompt_tokens: int | None = None
    """An optional *stricter* application cap on the input.

    May lower the allowance and may never raise it: a deployment can choose to
    send less than the window permits, and cannot choose to send more.
    """

    def __post_init__(self) -> None:
        if self.context_window < 1:
            raise TokenBudgetNotConfiguredError(
                "no synthesis context window is configured; a model's window is a "
                "provider fact this project does not invent"
            )
        if self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive")
        if self.safety_reserve < 0:
            raise ValueError("safety_reserve cannot be negative")
        if self.max_output_tokens + self.safety_reserve >= self.context_window:
            # Nothing would be left for the prompt. Caught here rather than
            # producing an allowance of zero that every request then fails.
            raise ValueError(
                f"max_output_tokens {self.max_output_tokens} plus safety reserve "
                f"{self.safety_reserve} leaves no room in a {self.context_window} "
                "token window"
            )
        if self.max_prompt_tokens is not None:
            if self.max_prompt_tokens < 1:
                raise ValueError("max_prompt_tokens must be positive when set")
            if self.max_prompt_tokens > self.derived_input_allowance:
                raise ValueError(
                    f"max_prompt_tokens {self.max_prompt_tokens} exceeds what the window "
                    f"leaves for input ({self.derived_input_allowance}); an application cap "
                    "may lower the allowance, never raise it"
                )

    @property
    def derived_input_allowance(self) -> int:
        """What the window leaves for input once output and reserve are set aside."""
        return self.context_window - self.max_output_tokens - self.safety_reserve

    @property
    def usable_prompt_tokens(self) -> int:
        """The input allowance actually in force, after any application cap."""
        if self.max_prompt_tokens is None:
            return self.derived_input_allowance
        return min(self.max_prompt_tokens, self.derived_input_allowance)

    def total_for(self, estimate: TokenEstimate) -> int:
        """The whole request: input, the output we will allow, and the reserve."""
        return estimate.estimated_tokens + self.max_output_tokens + self.safety_reserve

    def fits(self, estimate: TokenEstimate) -> bool:
        """Whether the **entire** request fits the window.

        Both conditions are checked: the input against its allowance, and the
        total against the window. They agree when no application cap is set and
        differ when one is, and asserting both is what keeps the invariant in
        §3 true rather than incidentally true.
        """
        return (
            estimate.estimated_tokens <= self.usable_prompt_tokens
            and self.total_for(estimate) <= self.context_window
        )

    def explain(self, estimate: TokenEstimate) -> str:
        """Why a request did not fit, in the four separate quantities."""
        return (
            f"estimated input {estimate.estimated_tokens} + output allowance "
            f"{self.max_output_tokens} + safety reserve {self.safety_reserve} = "
            f"{self.total_for(estimate)} against a {self.context_window} token window "
            f"(input allowance {self.usable_prompt_tokens}); {estimate.method}"
        )
