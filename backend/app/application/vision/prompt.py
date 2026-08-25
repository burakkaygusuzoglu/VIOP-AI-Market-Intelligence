"""The versioned Vision extraction prompt (§3 of the Phase 6B brief).

**This is not the Phase 7 synthesis prompt.** It asks one question - *what is
visible in this chart image* - and every instruction in it exists to stop the
model answering a larger question than that.

The prompt is versioned because it is part of the contract: an observation
recorded last month was produced by a specific set of instructions, and
auditing it later means knowing which. `PROMPT_VERSION` travels with every
extraction, alongside the schema version and the model id.

## What the instructions are actually defending against

Each of these has a matching rule in the text below, and most have a test:

* a model that reads "RSI" and helpfully computes what it *should* be;
* a model that infers a price from the axis and reports it as read;
* a model that decides the chart looks bullish and calls that a fact;
* a model that fills a field it cannot see rather than admitting it;
* a model that reports 1.0 confidence for everything;
* a model that offers a stop, a target, a size or a direction.

The last is the one that matters most: §120 and §1 both put the trade decision
outside the model's reach, and the fastest way to break that is a prompt that
invites an opinion.
"""

from __future__ import annotations

from dataclasses import dataclass

PROMPT_VERSION = "vision-extraction/1"
"""Bumped whenever the wording changes in a way that could change output.

Recorded on every extraction so a stored observation can be traced back to the
instructions that produced it.
"""

SCHEMA_VERSION = "vision-schema/1"
"""The shape `VisionExtractionSchema` currently enforces."""


VISION_SYSTEM_PROMPT = """\
You are a chart-screenshot reader for a Borsa Istanbul VIOP futures analysis
system. You report ONLY what is visible in the supplied image.

WHAT YOU DO
- Read text, numbers and labels that are actually legible in the image.
- Describe visible chart context: trend direction, market structure, support
  and resistance areas, candlestick context, volume context, user-drawn levels.
- State clearly which observations you READ and which you JUDGED.

THE TWO CATEGORIES - THIS DISTINCTION IS MANDATORY
- DIRECTLY_VISIBLE: the value is printed in the image and you read it.
  Example: the indicator panel prints "RSI 63.2".
- VISUALLY_INFERRED: you formed a judgement from the picture.
  Example: "the trend appears upward" from the shape of the price action.
Never label a judgement as directly visible.

WHAT YOU MUST NOT DO
- Do NOT calculate anything. You do not compute EMA, SMA, RSI, MACD, ATR, ADX,
  VWAP, Bollinger Bands, or any other indicator. If a value is not printed in
  the image, it is not available to you.
- Do NOT calculate or estimate profit, loss, risk, margin, leverage, position
  size, contract count, tick value or risk/reward.
- Do NOT recommend a direction. No LONG, no SHORT, no BUY, no SELL, no WAIT.
- Do NOT propose an entry price, a stop-loss or a take-profit level.
- Do NOT guess a value you cannot read. Do NOT interpolate a price from the
  axis and report it as if you read it.
- Do NOT invent a symbol or a timeframe. If the ticker or the interval is not
  legible, say so.
- Do NOT claim certainty. Do not report confidence 1.0 unless the text is
  unambiguous and fully legible.
- Do NOT comment on whether the setup is good, or on what the trader should do.

MISSING INFORMATION
Reporting that something is unreadable is a correct and useful answer. When a
field is cropped, blurred, overlapped or simply absent, list it as unreadable
with a short reason. Prefer saying you cannot read something over guessing it.

CONFIDENCE
Give a confidence between 0 and 1 for each observation, reflecting how legible
the source was. This is a legibility signal, not a probability about the
market. If you genuinely cannot judge legibility, omit the confidence rather
than inventing one.

TIMEFRAME
Report the timeframe you can see on the chart. Do not assume it matches what
you were asked about - if you were told the slot is 1H and the chart says 15M,
report 15M. A mismatch is useful information, not an error to correct.

OUTPUT
Return only data that matches the required schema. No prose outside it, no
commentary, no markdown fence.
"""


@dataclass(frozen=True, slots=True)
class VisionPrompt:
    """The instructions plus the identity to record alongside the result."""

    system: str = VISION_SYSTEM_PROMPT
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION

    def user_message(self, *, slot: str, expected_symbol: str = "") -> str:
        """The per-request instruction.

        The expected symbol is supplied as *context to check against*, phrased
        so the model is asked to confirm or contradict rather than to agree. A
        prompt that says "this is ASELS" invites a model to read ASELS off a
        chart that says something else.
        """
        lines = [
            f"This image was uploaded into the {slot} screenshot slot.",
            "Report what is visible in it.",
        ]
        if expected_symbol.strip():
            lines.append(
                f"The user believes this chart shows {expected_symbol.strip()}. "
                "Do not assume they are right - report the ticker you can "
                "actually read, even if it differs."
            )
        lines.append(
            "If the timeframe printed on the chart differs from the slot, "
            "report what the chart says."
        )
        return "\n".join(lines)
