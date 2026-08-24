"""Plain-language risk warnings for beginners (master spec §109).

§109's requirement is short and strict: when a beginner is heading into very
high leverage, high margin utilisation, no stop, poor risk/reward or outsized
account risk, say so in plain language - and *"Never hide critical risk
information inside Pro Mode."*

**Every threshold belongs to Phase 3.** This module owns none. It translates
`RiskWarning` objects the risk engine already produced, and each carries its
own observed value and configured threshold, so the numbers a beginner reads
are the numbers the engine used. Inventing a second "high leverage" threshold
here would create two authorities for one rule and let them drift apart.

**Both modes receive the same warnings.** `beginner` and `pro` are two
renderings of one `RiskWarning`, produced together. A surface may lay them out
differently; it cannot show one audience a warning and not the other, and a
test asserts the two lists always have identical codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum, unique

from app.domain.risk.margin import MarginAssessment, RiskWarning
from app.domain.risk.reward import RiskReward
from app.domain.risk.sizing import PositionSizing, SizingOutcome


@unique
class WarningAudience(StrEnum):
    """Who a rendering is phrased for - never who is allowed to see it."""

    BEGINNER = "BEGINNER"
    PRO = "PRO"


@dataclass(frozen=True, slots=True)
class BeginnerRiskWarning:
    """One §109 warning, phrased for both audiences from one source object."""

    code: str
    """The Phase 3 warning code, or a code for a condition Phase 3 reports
    through a different result type. The join back to the engine."""

    beginner: str
    pro: str
    observed: Decimal | None = None
    threshold: Decimal | None = None
    """Carried straight from the Phase 3 warning. Present so a reader can see
    the limit is a configured policy rather than an exchange rule."""

    def text_for(self, audience: WarningAudience) -> str:
        return self.beginner if audience is WarningAudience.BEGINNER else self.pro


_BEGINNER_TEXT: dict[str, str] = {
    "HIGH_MARGIN_UTILIZATION": (
        "Hesabınızın teminatının büyük bölümü bu pozisyonda kullanılacak. "
        "Piyasa aleyhinize hareket ederse ek pozisyon alacak alanınız kalmaz."
    ),
    "EXCESSIVE_EFFECTIVE_LEVERAGE": (
        "Bu pozisyonun kontrol ettiği büyüklük hesabınıza göre yüksek. "
        "Küçük bir fiyat hareketi hesabınızda büyük bir değişim yaratır."
    ),
    "RISK_LIMIT_EXCEEDED": ("Bu işlemin riski, kendi belirlediğiniz risk sınırının üzerinde."),
    "MARGIN_UNKNOWN": (
        "Teminat durumu doğrulanamadı. Bu, teminatın yeterli olduğu anlamına gelmez; "
        "yalnızca kontrol edilemediği anlamına gelir."
    ),
    "MARGIN_DEFICIT": (
        "Kullanılmış teminat hesap büyüklüğünüzü aşmış durumda. Hesap teminat açığında."
    ),
    "NO_STOP_DEFINED": (
        "Zarar kes seviyesi tanımlanmamış. Zarar kes olmadan bu işlemin azami kaybı belirsizdir."
    ),
    "POOR_RISK_REWARD": (
        "Hedefe olan mesafe, zarar kes mesafesine göre düşük. Aynı riske karşılık "
        "beklenen getiri sınırlı."
    ),
    "RISK_NOT_PERMITTED": (
        "Bu hesap ve bu zarar kes seviyesiyle tek sözleşme bile risk sınırınızı aşıyor. "
        "Uygun büyüklük sıfır sözleşmedir."
    ),
    "SIZING_UNDETERMINED": (
        "Pozisyon büyüklüğü belirlenemedi. Doğrulanamayan bir kısıt var; bu kısıtın "
        "karşılandığı varsayılamaz."
    ),
}


def _beginner_text(code: str) -> str:
    return _BEGINNER_TEXT.get(code, f"Risk uyarısı: {code}")


def _translate(warning: RiskWarning) -> BeginnerRiskWarning:
    """One Phase 3 warning, rendered twice.

    The pro text is the engine's own message plus the observed and threshold
    values verbatim; the beginner text explains the consequence. Neither is
    derived from the other.
    """
    return BeginnerRiskWarning(
        code=warning.code.value,
        beginner=_beginner_text(warning.code.value),
        pro=(
            f"{warning.code.value}: {warning.message} "
            f"(observed={warning.observed}, threshold={warning.threshold})"
        ),
        observed=warning.observed,
        threshold=warning.threshold,
    )


def beginner_risk_warnings(
    *,
    margin: MarginAssessment | None = None,
    sizing: PositionSizing | None = None,
    risk_reward: RiskReward | None = None,
    minimum_risk_reward: Decimal | None = None,
    stop_defined: bool | None = None,
) -> tuple[BeginnerRiskWarning, ...]:
    """Every §109 warning the supplied Phase 3 results support.

    Nothing is inferred from an absent input: omitting ``margin`` produces no
    margin warnings rather than a reassuring silence, and the checklist is
    where an unsupplied input is reported as unevaluated.

    ``minimum_risk_reward`` is the caller's configured floor - the same value
    the checklist and the NO TRADE engine use. It is a parameter rather than a
    constant here precisely so this module does not become a second place that
    decides it.
    """
    warnings: list[BeginnerRiskWarning] = []

    if margin is not None:
        warnings.extend(_translate(item) for item in margin.warnings)

    if stop_defined is False:
        warnings.append(
            BeginnerRiskWarning(
                code="NO_STOP_DEFINED",
                beginner=_beginner_text("NO_STOP_DEFINED"),
                pro="NO_STOP_DEFINED: caller reported no stop level for this trade",
            )
        )

    if sizing is not None:
        if sizing.outcome is SizingOutcome.NOT_PERMITTED:
            warnings.append(
                BeginnerRiskWarning(
                    code="RISK_NOT_PERMITTED",
                    beginner=_beginner_text("RISK_NOT_PERMITTED"),
                    pro=f"RISK_NOT_PERMITTED: {sizing.reason}",
                )
            )
        elif sizing.outcome is SizingOutcome.UNDETERMINED:
            warnings.append(
                BeginnerRiskWarning(
                    code="SIZING_UNDETERMINED",
                    beginner=_beginner_text("SIZING_UNDETERMINED"),
                    pro=f"SIZING_UNDETERMINED: {sizing.reason}",
                )
            )

    if (
        risk_reward is not None
        and minimum_risk_reward is not None
        and risk_reward.ratio is not None
        and risk_reward.ratio < minimum_risk_reward
    ):
        warnings.append(
            BeginnerRiskWarning(
                code="POOR_RISK_REWARD",
                beginner=_beginner_text("POOR_RISK_REWARD"),
                pro=(f"POOR_RISK_REWARD: ratio={risk_reward.ratio}, minimum={minimum_risk_reward}"),
                observed=risk_reward.ratio,
                threshold=minimum_risk_reward,
            )
        )

    return tuple(warnings)


def warning_texts(
    warnings: tuple[BeginnerRiskWarning, ...], audience: WarningAudience
) -> tuple[str, ...]:
    """The same warnings, phrased for one audience.

    Both audiences always receive the same set - only the wording differs.
    §109 forbids hiding a critical warning in Pro Mode, and the symmetry here
    means neither mode can lose one.
    """
    return tuple(item.text_for(audience) for item in warnings)
