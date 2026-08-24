"""Experience modes (master spec §3).

Two modes, and the default is not a toss-up: §3 says Beginner Mode is the
default, and the system must be usable by someone new to VİOP, futures,
leverage and position sizing.

**A mode selects emphasis, never content.** Both layers of §8's two-layer
output are built on every analysis regardless of mode; the mode says which one
a surface leads with and whether the technical detail starts expanded. It never
changes a number, and it never hides a risk warning - §109 is explicit that
critical risk information must not be buried in Pro Mode.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class ExperienceMode(StrEnum):
    """Who the presentation is being shaped for."""

    BEGINNER = "BEGINNER"
    """The §3 default. Leads with plain Turkish, keeps raw numbers behind a
    "show technical details" affordance."""

    PRO = "PRO"
    """Leads with the full deterministic detail. Still receives the simple
    layer, because a concise summary is useful at any level of experience."""


DEFAULT_MODE = ExperienceMode.BEGINNER
"""Master spec §3: Beginner Mode is the default."""


@dataclass(frozen=True, slots=True)
class ModePolicy:
    """What a mode changes about presentation - and what it cannot.

    Deliberately a small set of display hints. Anything that altered *which
    facts exist* would break the §8 guarantee that both layers describe the
    same analysis.
    """

    mode: ExperienceMode

    @property
    def leads_with_simple(self) -> bool:
        return self.mode is ExperienceMode.BEGINNER

    @property
    def technical_detail_expanded(self) -> bool:
        """Whether Level 2 starts open. Beginner keeps it one tap away - §5's
        "SHOW TECHNICAL DETAILS" - rather than absent."""
        return self.mode is ExperienceMode.PRO

    @property
    def shows_educational_hints(self) -> bool:
        """Beginners get tooltips offered; pros can still request them."""
        return self.mode is ExperienceMode.BEGINNER

    @property
    def shows_risk_warnings(self) -> bool:
        """Always true, in both modes.

        §109: *"Never hide critical risk information inside Pro Mode."* The
        property exists so the rule is stated in code rather than merely
        remembered, and a test pins it for every mode.
        """
        return True


def policy_for(mode: ExperienceMode = DEFAULT_MODE) -> ModePolicy:
    return ModePolicy(mode=mode)
