"""Turkish-first terminology (§4) and educational explanations (§7).

Every market referenced here is TEST_FIXTURE data.
"""

from __future__ import annotations

import pytest

from app.application.presentation.education import (
    Concept,
    ConceptAvailability,
    all_concepts,
    explain,
    measured_concepts,
    unmeasured_concepts,
)
from app.application.presentation.safety import violations
from app.application.presentation.terms import (
    DEFAULT_LOCALE,
    Locale,
    TermKey,
    all_terms,
    label,
    term,
)

# ----------------------------------------------------------------------
# Terminology
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_turkish_is_the_default_locale() -> None:
    """§4 makes Turkish primary; nothing should have to ask for it."""
    assert DEFAULT_LOCALE is Locale.TR
    assert label(TermKey.SUPPORT) == "Destek"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("key", "turkish", "english"),
    (
        (TermKey.SUPPORT, "Destek", "Support"),
        (TermKey.RESISTANCE, "Direnç", "Resistance"),
        (TermKey.STOP_LOSS, "Zarar Kes", "Stop-Loss"),
        (TermKey.TAKE_PROFIT, "Kâr Al", "Take-Profit"),
        (TermKey.MARGIN, "Teminat", "Margin"),
        (TermKey.LEVERAGE, "Kaldıraç", "Leverage"),
        (TermKey.OPEN_INTEREST, "Açık Pozisyon", "Open Interest"),
        (TermKey.RISK_REWARD, "Risk/Ödül", "Risk/Reward"),
        (TermKey.BREAKOUT, "Kırılım", "Breakout"),
        (TermKey.RETEST, "Yeniden Test", "Retest"),
    ),
)
def test_the_ten_terms_section_4_names_are_exact(key: TermKey, turkish: str, english: str) -> None:
    """§4 lists these ten verbatim, so they are pinned verbatim."""
    found = term(key)
    assert found.turkish == turkish
    assert found.english == english


@pytest.mark.unit
def test_english_follows_turkish_when_asked_for_both() -> None:
    assert term(TermKey.SUPPORT).bilingual() == "Destek (Support)"


@pytest.mark.unit
def test_a_word_shared_by_both_languages_is_not_repeated() -> None:
    """ "Momentum (Momentum)" would be noise, not help."""
    assert term(TermKey.MOMENTUM).bilingual() == "Momentum"


@pytest.mark.unit
def test_english_can_be_requested_and_then_leads() -> None:
    """i18n readiness: the structure supports a second locale today."""
    assert label(TermKey.SUPPORT, Locale.EN) == "Support"
    assert term(TermKey.SUPPORT).bilingual(Locale.EN) == "Support (Destek)"


@pytest.mark.unit
def test_every_term_key_has_an_entry() -> None:
    assert {item.key for item in all_terms()} == set(TermKey)
    for item in all_terms():
        assert item.turkish.strip()
        assert item.english.strip()


@pytest.mark.unit
def test_terminology_lives_in_exactly_one_registry() -> None:
    """The point of the registry: no duplicated translation anywhere."""
    turkish = [item.turkish for item in all_terms()]
    assert len(turkish) == len(set(turkish))


# ----------------------------------------------------------------------
# Educational content
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_every_concept_section_7_requires_is_covered() -> None:
    required = {
        "EMA",
        "SMA",
        "RSI",
        "MACD",
        "ATR",
        "VWAP",
        "ADX",
        "BOLLINGER_BANDS",
        "VOLUME",
        "RELATIVE_VOLUME",
        "OPEN_INTEREST",
        "BASIS",
        "MARGIN",
        "LEVERAGE",
        "RISK_REWARD",
        "SUPPORT",
        "RESISTANCE",
        "BREAKOUT",
        "RETEST",
        "BOS",
        "CHOCH",
        "MARKET_REGIME",
        "LIQUIDITY",
        "SPREAD",
    }
    assert {concept.value for concept in Concept} == required
    assert len(all_concepts()) == len(required)


@pytest.mark.unit
@pytest.mark.parametrize("concept", tuple(Concept))
def test_every_concept_answers_all_four_questions(concept: Concept) -> None:
    """§7 requires four answers, and the fourth is the one a shorter
    implementation would quietly drop."""
    explanation = explain(concept)
    assert len(explanation.answers) == 4
    for answer in explanation.answers:
        assert answer.strip()
        assert len(answer) > 25, f"{concept.value} has a placeholder answer"


@pytest.mark.unit
@pytest.mark.parametrize("concept", tuple(Concept))
def test_every_concept_is_named_in_turkish(concept: Concept) -> None:
    explanation = explain(concept)
    assert explanation.turkish_name.strip()
    assert explanation.bilingual_name.strip()


@pytest.mark.unit
def test_the_rsi_explanation_refuses_the_rule_section_7_forbids() -> None:
    """§7 names *"RSI > 70 = SELL"* as the thing not to teach.

    The explanation must not merely omit it - it must say the opposite, since
    a beginner arrives already believing it.
    """
    explanation = explain(Concept.RSI)
    joined = " ".join(explanation.answers)
    assert "RSI > 70 = SAT" not in joined.upper().replace("SELL", "SAT")
    assert "anlamına gelmez" in explanation.what_not_to_assume
    assert not violations(joined)


@pytest.mark.unit
@pytest.mark.parametrize("concept", tuple(Concept))
def test_no_explanation_makes_a_forbidden_claim(concept: Concept) -> None:
    for answer in explain(concept).answers:
        assert not violations(answer), f"{concept.value}: {answer}"


@pytest.mark.unit
def test_a_concept_can_teach_a_limitation_without_tripping_the_scanner() -> None:
    """The caveats are the safety content; a guard that punished them would
    push an author to delete them."""
    support = explain(Concept.SUPPORT)
    assert "garanti etmez" in support.what_not_to_assume
    assert not violations(support.what_not_to_assume)


# ----------------------------------------------------------------------
# Unmeasured concepts stay unmeasured
# ----------------------------------------------------------------------


@pytest.mark.unit
def test_liquidity_and_spread_are_explained_but_marked_unmeasured() -> None:
    """§7 requires the definitions; this repository has no order book and no
    bid/ask quotes, so nothing may imply an analysis reports them."""
    unmeasured = {item.concept for item in unmeasured_concepts()}
    assert unmeasured == {Concept.LIQUIDITY, Concept.SPREAD}
    for concept in unmeasured:
        explanation = explain(concept)
        assert explanation.availability is ConceptAvailability.NOT_MEASURED
        assert not explanation.is_measured
        assert "ölçülmemektedir" in explanation.what_not_to_assume
        assert "eğitim amaçlıdır" in explanation.what_not_to_assume


@pytest.mark.unit
def test_every_other_concept_is_actually_measured_by_an_engine() -> None:
    measured = {item.concept for item in measured_concepts()}
    assert Concept.LIQUIDITY not in measured
    assert Concept.SPREAD not in measured
    assert len(measured) == len(Concept) - 2


@pytest.mark.unit
def test_the_vwap_explanation_discloses_its_development_default_anchor() -> None:
    """Phase 1 anchors VWAP to the UTC calendar day because verified session
    hours are a §118 fact nobody holds. A user reading the tooltip should meet
    that caveat, not discover it later."""
    explanation = explain(Concept.VWAP)
    assert "UTC" in explanation.what_not_to_assume
    assert "geliştirme varsayılanıdır" in explanation.what_not_to_assume
