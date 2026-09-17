"""Construction of the optional AI providers, at the composition root (§13, §20).

Phase 8A's forensic audit found a real integration gap: the Phase 6 screenshot
route was complete and reachable, and `ClaudeScreenshotAnalyzer` was never
constructed anywhere in `app/`. Its dependency was overridden only in tests, so
every production call returned 503 while the OpenAPI document said otherwise.
This module closes that gap and is the only place either provider is built.

## Configured or absent — never half-built

Each builder returns `None` when its configuration is incomplete, and the
caller turns that into a typed NOT_CONFIGURED state. It never constructs a
vendor client with an empty key to "let it fail later": the SDK would raise a
vendor exception from inside a request handler, which is exactly the untyped
failure Phase 6 and 7 spent their error models avoiding.

`Settings.vision_is_configured` and `Settings.synthesis_is_configured` are the
single source of truth for what "complete" means, so the check cannot drift
from the one the rest of the system uses.

## No credential ever leaves here

The key is read from settings, handed to the transport, and never stored on an
object anything else can reach, never logged, and never included in an error.
"""

from __future__ import annotations

from app.adapters.synthesis.claude_synthesizer import (
    ClaudeMarketSynthesizer,
    SynthesizerConfig,
)
from app.adapters.synthesis.transport import AnthropicSynthesisTransport
from app.adapters.vision.claude_analyzer import AnalyzerConfig, ClaudeScreenshotAnalyzer
from app.adapters.vision.transport import AnthropicVisionTransport
from app.application.ports.screenshot import ScreenshotAnalyzer
from app.application.ports.synthesis import MarketSynthesisProvider
from app.application.synthesis.tokens import TokenBudget
from app.application.synthesis.use_case import SynthesisSettings
from app.core.config import Settings


def build_screenshot_analyzer(settings: Settings) -> ScreenshotAnalyzer | None:
    """The production vision analyzer, or ``None`` when Vision is unconfigured.

    ``None`` is a complete answer, not a failure: the route maps it to
    ``503 VISION_NOT_CONFIGURED``, which is a system state and never a market
    view.
    """
    if not settings.vision_is_configured:
        return None

    api_key = (
        settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_api_key is not None
        else ""
    )
    transport = AnthropicVisionTransport(
        api_key=api_key,
        timeout_seconds=settings.vision_timeout_seconds,
        max_retries=settings.vision_max_retries,
    )
    return ClaudeScreenshotAnalyzer(
        transport,
        AnalyzerConfig(
            model=settings.vision_model,
            max_tokens=settings.vision_max_tokens,
        ),
    )


def build_synthesizer(settings: Settings) -> MarketSynthesisProvider | None:
    """The production synthesis provider, or ``None`` when unconfigured."""
    if not settings.synthesis_is_configured:
        return None

    api_key = (
        settings.anthropic_api_key.get_secret_value()
        if settings.anthropic_api_key is not None
        else ""
    )
    transport = AnthropicSynthesisTransport(
        api_key,
        timeout_seconds=settings.synthesis_timeout_seconds,
        max_retries=settings.synthesis_max_retries,
    )
    return ClaudeMarketSynthesizer(
        transport,
        SynthesizerConfig(
            model=settings.synthesis_model,
            max_tokens=settings.synthesis_max_output_tokens,
        ),
    )


def build_synthesis_settings(settings: Settings) -> SynthesisSettings:
    """Application policy for a synthesis run.

    ``tokens`` stays ``None`` unless a context window was explicitly
    configured. Phase 7 is deliberate about this: a default window would be an
    invented provider fact, and an unstated one is treated exactly like an
    unstated model — NOT_CONFIGURED rather than a guess.
    """
    budget: TokenBudget | None = None
    if settings.synthesis_context_window > 0:
        budget = TokenBudget(
            context_window=settings.synthesis_context_window,
            max_output_tokens=settings.synthesis_max_output_tokens,
            safety_reserve=settings.synthesis_safety_reserve_tokens,
            max_prompt_tokens=settings.synthesis_max_prompt_tokens or None,
        )
    return SynthesisSettings(
        model=settings.synthesis_model,
        max_output_tokens=settings.synthesis_max_output_tokens,
        tokens=budget,
    )
