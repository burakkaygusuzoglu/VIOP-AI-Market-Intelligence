"""The two-layer output (master spec §8).

One analysis, two descriptions of it. §8 requires both, and the guarantee that
makes them safe is that **neither is computed independently**: the simple layer
translates fused evidence groups, the technical layer reports the same
analysis at full precision, and both are built from the identical
`MultiTimeframeAnalysis` in a single call. There is no path by which Level 1
can say something Level 2 disagrees with, because Level 1 holds the very
evidence items Level 2 lists - a test asserts the citation set is a subset.

The mode does not change what is built. Both layers exist on every
presentation; `ModePolicy` only decides which one a surface leads with. That is
what keeps §109's rule true - critical risk information cannot end up hidden in
Pro Mode, because Pro Mode is not where anything is hidden.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.application.presentation.beginner import build_simple_explanation
from app.application.presentation.modes import DEFAULT_MODE, ExperienceMode, ModePolicy, policy_for
from app.application.presentation.pro import TechnicalDetail, build_technical_detail
from app.application.presentation.statements import SimpleExplanation
from app.application.presentation.terms import DEFAULT_LOCALE, Locale
from app.domain.analysis.engine import MultiTimeframeAnalysis
from app.domain.futures.basis import BasisResult
from app.domain.futures.open_interest import OpenInterestReading


@dataclass(frozen=True, slots=True)
class AnalysisPresentation:
    """Both §8 layers, plus how a surface should lead with them."""

    symbol: str
    mode: ExperienceMode
    locale: Locale
    simple: SimpleExplanation
    """Level 1 - plain Turkish, every sentence traceable to evidence."""

    technical: TechnicalDetail
    """Level 2 - the same analysis at full precision."""

    @property
    def policy(self) -> ModePolicy:
        return policy_for(self.mode)

    @property
    def leads_with_simple(self) -> bool:
        return self.policy.leads_with_simple

    def in_mode(self, mode: ExperienceMode) -> AnalysisPresentation:
        """The same content, presented for a different reader.

        Switching modes cannot change a fact, so both layers are carried
        across unchanged and only the emphasis differs. A test asserts the
        layers are identical objects across modes.
        """
        return AnalysisPresentation(
            symbol=self.symbol,
            mode=mode,
            locale=self.locale,
            simple=self.simple,
            technical=self.technical,
        )


def present_analysis(
    analysis: MultiTimeframeAnalysis,
    *,
    mode: ExperienceMode = DEFAULT_MODE,
    locale: Locale = DEFAULT_LOCALE,
    basis: BasisResult | None = None,
    open_interest: OpenInterestReading | None = None,
) -> AnalysisPresentation:
    """Build both layers for one analysis.

    ``mode`` defaults to BEGINNER (§3) and ``locale`` to Turkish (§4).
    Deterministic: the same analysis always produces the same presentation.
    """
    return AnalysisPresentation(
        symbol=analysis.symbol,
        mode=mode,
        locale=locale,
        simple=build_simple_explanation(analysis),
        technical=build_technical_detail(analysis, basis=basis, open_interest=open_interest),
    )
