"""Turkish-first terminology, in one place (master spec §4).

**The single registry.** Every Turkish word the system shows a user lives here
and nowhere else. That is the whole point: §8 of the Phase 5A brief forbids
scattering duplicated terminology through unrelated modules, and the domain
engines must stay language-free so the same calculation can be presented in
any language without touching a financial formula. A test scans
``app/domain`` for these exact strings and fails if one has leaked downward.

**Turkish leads; English follows.** §4 makes Turkish the primary language and
allows technical English secondarily, which is what ``bilingual()`` produces:
*"Destek (Support)"*. English is carried for every term rather than a chosen
few, because a trader reading Turkish documentation still meets English
terminology on every chart and broker screen.

**Prepared for i18n, not a framework.** Locales are a `dict[Locale, str]` per
term. That is genuinely enough for two languages of fixed vocabulary, and §2
of the brief explicitly says not to build a translation framework unless one is
required. Adding a locale means adding a column here; adding a *user-supplied*
locale, plural rules or message formatting would mean adopting a real i18n
library, and that decision is deferred until something needs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum, unique


@unique
class Locale(StrEnum):
    """Supported presentation languages. Turkish is the default everywhere."""

    TR = "tr"
    EN = "en"


DEFAULT_LOCALE = Locale.TR


@unique
class TermKey(StrEnum):
    """Every term the presentation layer can name.

    A key rather than a string, so a caller cannot typo a label into existence
    and no module has to repeat a translation.
    """

    # The ten §4 names verbatim
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    MARGIN = "MARGIN"
    LEVERAGE = "LEVERAGE"
    OPEN_INTEREST = "OPEN_INTEREST"
    RISK_REWARD = "RISK_REWARD"
    BREAKOUT = "BREAKOUT"
    RETEST = "RETEST"

    # Indicators
    EMA = "EMA"
    SMA = "SMA"
    RSI = "RSI"
    MACD = "MACD"
    ATR = "ATR"
    VWAP = "VWAP"
    ADX = "ADX"
    BOLLINGER_BANDS = "BOLLINGER_BANDS"

    # Market description
    TREND = "TREND"
    MOMENTUM = "MOMENTUM"
    TREND_STRENGTH = "TREND_STRENGTH"
    VOLATILITY = "VOLATILITY"
    VOLUME = "VOLUME"
    RELATIVE_VOLUME = "RELATIVE_VOLUME"
    MARKET_STRUCTURE = "MARKET_STRUCTURE"
    MARKET_REGIME = "MARKET_REGIME"
    BOS = "BOS"
    CHOCH = "CHOCH"
    DIVERGENCE = "DIVERGENCE"
    PULLBACK = "PULLBACK"
    UPTREND = "UPTREND"
    DOWNTREND = "DOWNTREND"
    RANGE = "RANGE"

    # Futures and risk context
    BASIS = "BASIS"
    LIQUIDITY = "LIQUIDITY"
    SPREAD = "SPREAD"

    # Analysis vocabulary
    TIMEFRAME = "TIMEFRAME"
    EVIDENCE = "EVIDENCE"
    CONTRADICTION = "CONTRADICTION"
    SETUP_QUALITY = "SETUP_QUALITY"
    ENTRY_QUALITY = "ENTRY_QUALITY"
    NO_TRADE = "NO_TRADE"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class Term:
    """One concept's name in each supported language."""

    key: TermKey
    turkish: str
    english: str

    def in_locale(self, locale: Locale = DEFAULT_LOCALE) -> str:
        return self.turkish if locale is Locale.TR else self.english

    def bilingual(self, locale: Locale = DEFAULT_LOCALE) -> str:
        """The §4 form: the reader's language first, the other in brackets.

        Collapses to a single word when both languages use it - writing
        "Momentum (Momentum)" would be noise rather than help.
        """
        primary = self.in_locale(locale)
        secondary = self.english if locale is Locale.TR else self.turkish
        return primary if primary == secondary else f"{primary} ({secondary})"


_TERMS: dict[TermKey, Term] = {
    term.key: term
    for term in (
        Term(TermKey.SUPPORT, "Destek", "Support"),
        Term(TermKey.RESISTANCE, "Direnç", "Resistance"),
        Term(TermKey.STOP_LOSS, "Zarar Kes", "Stop-Loss"),
        Term(TermKey.TAKE_PROFIT, "Kâr Al", "Take-Profit"),
        Term(TermKey.MARGIN, "Teminat", "Margin"),
        Term(TermKey.LEVERAGE, "Kaldıraç", "Leverage"),
        Term(TermKey.OPEN_INTEREST, "Açık Pozisyon", "Open Interest"),
        Term(TermKey.RISK_REWARD, "Risk/Ödül", "Risk/Reward"),
        Term(TermKey.BREAKOUT, "Kırılım", "Breakout"),
        Term(TermKey.RETEST, "Yeniden Test", "Retest"),
        Term(TermKey.EMA, "Üstel Hareketli Ortalama", "EMA"),
        Term(TermKey.SMA, "Basit Hareketli Ortalama", "SMA"),
        Term(TermKey.RSI, "Göreceli Güç Endeksi", "RSI"),
        Term(TermKey.MACD, "MACD", "MACD"),
        Term(TermKey.ATR, "Ortalama Gerçek Aralık", "ATR"),
        Term(TermKey.VWAP, "Hacim Ağırlıklı Ortalama Fiyat", "VWAP"),
        Term(TermKey.ADX, "Ortalama Yönsel Endeks", "ADX"),
        Term(TermKey.BOLLINGER_BANDS, "Bollinger Bantları", "Bollinger Bands"),
        Term(TermKey.TREND, "Trend", "Trend"),
        Term(TermKey.MOMENTUM, "Momentum", "Momentum"),
        Term(TermKey.TREND_STRENGTH, "Trend Gücü", "Trend Strength"),
        Term(TermKey.VOLATILITY, "Oynaklık", "Volatility"),
        Term(TermKey.VOLUME, "Hacim", "Volume"),
        Term(TermKey.RELATIVE_VOLUME, "Göreceli Hacim", "Relative Volume"),
        Term(TermKey.MARKET_STRUCTURE, "Piyasa Yapısı", "Market Structure"),
        Term(TermKey.MARKET_REGIME, "Piyasa Rejimi", "Market Regime"),
        Term(TermKey.BOS, "Yapı Kırılımı", "Break of Structure"),
        Term(TermKey.CHOCH, "Karakter Değişimi", "Change of Character"),
        Term(TermKey.DIVERGENCE, "Uyumsuzluk", "Divergence"),
        Term(TermKey.PULLBACK, "Geri Çekilme", "Pullback"),
        Term(TermKey.UPTREND, "Yükseliş Trendi", "Uptrend"),
        Term(TermKey.DOWNTREND, "Düşüş Trendi", "Downtrend"),
        Term(TermKey.RANGE, "Yatay Bant", "Range"),
        Term(TermKey.BASIS, "Baz", "Basis"),
        Term(TermKey.LIQUIDITY, "Likidite", "Liquidity"),
        Term(TermKey.SPREAD, "Makas", "Spread"),
        Term(TermKey.TIMEFRAME, "Zaman Dilimi", "Timeframe"),
        Term(TermKey.EVIDENCE, "Kanıt", "Evidence"),
        Term(TermKey.CONTRADICTION, "Çelişki", "Contradiction"),
        Term(TermKey.SETUP_QUALITY, "Kurulum Kalitesi", "Setup Quality"),
        Term(TermKey.ENTRY_QUALITY, "Giriş Kalitesi", "Entry Quality"),
        Term(TermKey.NO_TRADE, "İşlem Yok", "No Trade"),
        Term(TermKey.UNAVAILABLE, "Veri Yok", "Unavailable"),
    )
}


def term(key: TermKey) -> Term:
    """The registry entry for ``key``.

    Every member of `TermKey` has one - a test asserts the registry is
    complete, so this cannot raise in practice.
    """
    return _TERMS[key]


def label(key: TermKey, locale: Locale = DEFAULT_LOCALE, *, bilingual: bool = False) -> str:
    """The display name for ``key``, Turkish by default."""
    found = term(key)
    return found.bilingual(locale) if bilingual else found.in_locale(locale)


def all_terms() -> tuple[Term, ...]:
    """Every term, in declaration order. Used by the leak test."""
    return tuple(_TERMS[key] for key in TermKey)
