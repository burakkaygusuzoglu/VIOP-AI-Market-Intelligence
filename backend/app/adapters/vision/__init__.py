"""Claude Vision adapter (master spec sections 38, 68, 69).

The only place in this repository that imports the Anthropic SDK. Everything
above it speaks in the vendor-free `VisionTransport` and `ScreenshotAnalyzer`
vocabularies, which is what lets the whole vision path be tested with a fake
and no API key.
"""

from app.adapters.vision.claude_analyzer import AnalyzerConfig, ClaudeScreenshotAnalyzer
from app.adapters.vision.transport import (
    AnthropicVisionTransport,
    VisionRequest,
    VisionResponse,
    VisionTransport,
    VisionUsage,
)

__all__ = [
    "AnalyzerConfig",
    "AnthropicVisionTransport",
    "ClaudeScreenshotAnalyzer",
    "VisionRequest",
    "VisionResponse",
    "VisionTransport",
    "VisionUsage",
]
