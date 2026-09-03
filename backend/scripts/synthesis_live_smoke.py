"""Opt-in live synthesis smoke test (§6). NOT part of the test suite.

Makes **one real Anthropic call** through the production adapter and the
production validator. It exists so that "live provider unverified" can one day
become "verified on <date>" without anybody being tempted to fake it.

    Nothing here runs during pytest. It lives in `scripts/`, not `tests/`, so
    no collector can pick it up, and it refuses to run without an explicit
    opt-in even when a key is present.

## Running it

    SYNTHESIS_LIVE_SMOKE=1 \
    ANTHROPIC_API_KEY=...    \
    SYNTHESIS_MODEL=...      \
    SYNTHESIS_CONTEXT_WINDOW=200000 \
    ./.venv/Scripts/python.exe scripts/synthesis_live_smoke.py

Both the opt-in flag and the credential must come from the environment. There
is no key in this file, no default model, and no fallback: missing anything
prints what is missing and exits without calling anyone.

## What it cannot do

It cannot place an order - nothing in this repository can. It cannot bypass the
`ActionEnvelope`: the context below is built with a **forced NO_TRADE**
envelope, so if the model proposes anything else the validator must reject it.
That makes the harness a test of the safety boundary as much as of
connectivity: a "successful" run that accepted a LONG would be a failure.

It costs money. It is opt-in for that reason too.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.adapters.synthesis.claude_synthesizer import (  # noqa: E402
    ClaudeMarketSynthesizer,
    SynthesizerConfig,
)
from app.adapters.synthesis.transport import AnthropicSynthesisTransport  # noqa: E402
from app.application.synthesis.context import SynthesisContext  # noqa: E402
from app.application.synthesis.draft import SynthesisStatus  # noqa: E402
from app.application.synthesis.tokens import TokenBudget  # noqa: E402
from app.application.synthesis.use_case import SynthesisSettings, run_synthesis  # noqa: E402
from app.domain.analysis.evidence import EvidenceDirection  # noqa: E402
from app.domain.synthesis.actions import FinalAction  # noqa: E402

OPT_IN = "SYNTHESIS_LIVE_SMOKE"


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


def _missing() -> list[str]:
    required = ("ANTHROPIC_API_KEY", "SYNTHESIS_MODEL", "SYNTHESIS_CONTEXT_WINDOW")
    return [name for name in required if not os.environ.get(name, "").strip()]


def build_context() -> SynthesisContext:
    """A deterministic fixture context with a **forced NO_TRADE** envelope.

    Imported from the test factories on purpose: the harness must exercise the
    same assembly the suite does, not a second one written for the occasion.
    """
    from tests.factories_synthesis import minimal_context, sizing  # noqa: PLC0415

    from app.domain.risk.sizing import SizingOutcome  # noqa: PLC0415

    return minimal_context(
        direction=EvidenceDirection.BULLISH,
        position_sizing=sizing(SizingOutcome.NOT_PERMITTED),
    )


async def main() -> int:
    if os.environ.get(OPT_IN, "").strip() not in {"1", "true", "TRUE", "yes"}:
        print(f"NOT RUN - {OPT_IN} is not set. This script makes a real, paid API call.")
        return 0

    missing = _missing()
    if missing:
        print(f"NOT RUN - NO CREDENTIAL / INCOMPLETE CONFIGURATION: missing {', '.join(missing)}")
        return 0

    model = os.environ["SYNTHESIS_MODEL"].strip()
    window = int(os.environ["SYNTHESIS_CONTEXT_WINDOW"])
    max_output = int(os.environ.get("SYNTHESIS_MAX_OUTPUT_TOKENS", "4096"))

    context = build_context()
    print(f"model            : {model}")
    print(f"context window   : {window}")
    print(f"allowed actions  : {[a.value for a in context.envelope.allowed]}")
    print("expectation      : only NO_TRADE may be accepted\n")

    provider = ClaudeMarketSynthesizer(
        AnthropicSynthesisTransport(
            api_key=os.environ["ANTHROPIC_API_KEY"],
            timeout_seconds=float(os.environ.get("SYNTHESIS_TIMEOUT_SECONDS", "90")),
            max_retries=int(os.environ.get("SYNTHESIS_MAX_RETRIES", "2")),
        ),
        SynthesizerConfig(model=model, max_tokens=max_output),
    )

    result = await run_synthesis(
        context,
        provider,
        SystemClock(),
        SynthesisSettings(
            model=model,
            max_output_tokens=max_output,
            tokens=TokenBudget(context_window=window, max_output_tokens=max_output),
        ),
    )

    print(f"status           : {result.status.value}")
    print(f"final action     : {result.final_action}")
    print(f"context digest   : {result.audit.context_digest[:16]}...")
    print(f"prompt version   : {result.audit.prompt_version}")
    print(f"rejection codes  : {result.audit.rejection_codes}")
    print(
        f"provider usage   : in={provider.last_usage.input_tokens} "
        f"out={provider.last_usage.output_tokens}"
    )
    if result.outcome.draft is not None:
        print(f"summary          : {result.outcome.draft.summary[:200]}")

    # The safety assertion. A run that accepted anything other than NO_TRADE
    # would mean the envelope was bypassed, which is a worse outcome than a
    # provider error.
    if result.status is SynthesisStatus.SUCCESS and result.final_action is not FinalAction.NO_TRADE:
        print("\nFAIL - the envelope forced NO_TRADE and something else was accepted")
        return 1

    reached = result.status is not SynthesisStatus.NOT_CONFIGURED
    print(
        f"\n{'PASS' if reached else 'FAIL'} - provider reached: {reached}; envelope honoured: yes"
    )
    return 0 if reached else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
