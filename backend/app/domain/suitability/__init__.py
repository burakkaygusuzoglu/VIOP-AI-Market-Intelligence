"""Trade suitability: where analysis meets account risk (master spec §43).

The one place in the domain that depends on **both** `app.domain.analysis` and
`app.domain.risk`, and it exists precisely so that nothing below it has to.

§43 draws the line this package sits on. *Setup quality* is a technical
judgement about a chart and must read identically for every user; *trade
suitability* asks whether this particular account should act, and cannot be
answered without knowing the account. Fusing them would make the same market
grade differently for two people, which is why the Phase 4A contract keeps
`app.domain.analysis` clear of `app.domain.risk` and why that contract is
preserved rather than relaxed.

Both inputs arrive as finished typed results. No risk formula and no evidence
rule is reimplemented here.

Phase 4 stops at the veto. There is no LONG, SHORT or WAIT in this package.
"""

from app.domain.suitability.no_trade import (
    DeferredNoTradeReason,
    FindingSeverity,
    NoTradeAssessment,
    NoTradeConfig,
    NoTradeFinding,
    NoTradeReason,
    assess_no_trade,
)

__all__ = [
    "DeferredNoTradeReason",
    "FindingSeverity",
    "NoTradeAssessment",
    "NoTradeConfig",
    "NoTradeFinding",
    "NoTradeReason",
    "assess_no_trade",
]
