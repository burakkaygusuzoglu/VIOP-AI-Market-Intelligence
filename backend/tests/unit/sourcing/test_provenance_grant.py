"""Who may put a real-exchange label on a stream (Phase 15 Part 1).

A provider *declares* a provenance; a session *carries* one only if the grant
gate allows it. These tests build real ``LiveSession`` objects, so the check is
the one production runs, not a copy of it.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from app.adapters.live.mock_stream import ManualClock
from app.adapters.market_data.csv_provider import CsvCandleTextParser
from app.application.live.session import LiveSession
from app.domain.common.verification import VerificationStatus
from app.domain.live.events import MarketCurrency, StreamProvenance, market_currency_of
from app.domain.sourcing.capability import (
    DataCategory,
    Delivery,
    ProvenanceRefusedError,
    ProviderDeclaration,
    authorize_provenance,
    provenance_for,
)
from tests.unit.live.support import FRESHNESS, M5, SYMBOL
from tests.unit.sourcing.support import NOW, GrantedTestProvider, fixture_grant

pytestmark = pytest.mark.unit

APP = Path(__file__).resolve().parents[3] / "app"


def session_with(provenance: StreamProvenance, grant: object = None) -> LiveSession:
    provider = GrantedTestProvider(provenance)
    return LiveSession(
        provider=provider,
        symbol=SYMBOL,
        timeframes=(M5,),
        clock=ManualClock(NOW),
        parser=CsvCandleTextParser(),
        freshness=FRESHNESS,
        provider_id=GrantedTestProvider.provider_id,
        grant=grant,  # type: ignore[arg-type]
    )


class TestTheGateAtTheSession:
    @pytest.mark.parametrize(
        "provenance",
        [
            StreamProvenance.REAL_EXCHANGE_LIVE,
            StreamProvenance.REAL_EXCHANGE_DELAYED,
            StreamProvenance.PROVIDER_HISTORICAL,
        ],
    )
    def test_a_real_label_without_a_grant_is_refused(self, provenance: StreamProvenance) -> None:
        with pytest.raises(ProvenanceRefusedError) as caught:
            session_with(provenance)

        assert caught.value.code == "PROVENANCE_NOT_GRANTED"

    @pytest.mark.parametrize(
        "provenance",
        [
            StreamProvenance.SIMULATED_HISTORICAL_STREAM,
            StreamProvenance.UNVERIFIED_OR_USER_SUPPLIED,
        ],
    )
    def test_a_label_that_claims_nothing_needs_no_grant(self, provenance: StreamProvenance) -> None:
        session = session_with(provenance)

        assert session.provenance is provenance
        assert session.snapshot().market_currency is MarketCurrency.HISTORICAL

    def test_a_granted_delayed_feed_is_delayed_never_current(self) -> None:
        session = session_with(
            StreamProvenance.REAL_EXCHANGE_DELAYED, fixture_grant(Delivery.DELAYED)
        )

        assert session.snapshot().market_currency is MarketCurrency.DELAYED

    def test_only_a_granted_live_feed_is_current(self) -> None:
        session = session_with(
            StreamProvenance.REAL_EXCHANGE_LIVE, fixture_grant(Delivery.REAL_TIME)
        )

        assert session.snapshot().market_currency is MarketCurrency.CURRENT


class TestWhatAGrantMustBe:
    @pytest.mark.parametrize(
        ("grant", "code"),
        [
            (fixture_grant(provider_id="SOMEONE_ELSE"), "GRANT_FOR_ANOTHER_PROVIDER"),
            (
                fixture_grant(categories=frozenset({DataCategory.NEWS})),
                "GRANT_DOES_NOT_COVER_MARKET_DATA",
            ),
            (fixture_grant(Delivery.HISTORICAL), "GRANT_DELIVERY_MISMATCH"),
            (fixture_grant(status=VerificationStatus.TEST_FIXTURE), "GRANT_NOT_AUTHORITATIVE"),
            (fixture_grant(status=VerificationStatus.UNVERIFIED), "GRANT_NOT_AUTHORITATIVE"),
            (
                fixture_grant(status=VerificationStatus.DEVELOPMENT_DEFAULT),
                "GRANT_NOT_AUTHORITATIVE",
            ),
            (fixture_grant(source="  "), "GRANT_NOT_AUTHORITATIVE"),
            (fixture_grant(as_of=None), "GRANT_NOT_AUTHORITATIVE"),
            (fixture_grant(valid_until=NOW - timedelta(seconds=1)), "GRANT_LAPSED"),
        ],
    )
    def test_a_defective_grant_is_refused_with_its_reason(self, grant: object, code: str) -> None:
        with pytest.raises(ProvenanceRefusedError) as caught:
            authorize_provenance(
                StreamProvenance.REAL_EXCHANGE_DELAYED,
                provider_id="TEST_DOUBLE_PROVIDER",
                grant=grant,  # type: ignore[arg-type]
                at=NOW,
            )

        assert caught.value.code == code

    def test_a_refusal_never_falls_back_to_a_weaker_label(self) -> None:
        """No silent downgrade to simulated or unverified: the session is refused."""
        with pytest.raises(ProvenanceRefusedError):
            session_with(StreamProvenance.REAL_EXCHANGE_LIVE, fixture_grant(Delivery.DELAYED))


class TestDeclarations:
    def test_a_delayed_provider_must_state_its_delay(self) -> None:
        with pytest.raises(ValueError, match="states its delay"):
            ProviderDeclaration("P", frozenset({DataCategory.MARKET_DATA}), Delivery.DELAYED)

    def test_only_a_delayed_provider_states_a_delay(self) -> None:
        with pytest.raises(ValueError, match="only a delayed"):
            ProviderDeclaration(
                "P", frozenset({DataCategory.MARKET_DATA}), Delivery.REAL_TIME, delay_seconds=900
            )

    def test_declaring_asks_for_a_provenance_it_does_not_receive(self) -> None:
        declaration = ProviderDeclaration(
            "P", frozenset({DataCategory.MARKET_DATA}), Delivery.DELAYED, delay_seconds=900
        )

        asked = provenance_for(declaration)

        assert asked is StreamProvenance.REAL_EXCHANGE_DELAYED
        with pytest.raises(ProvenanceRefusedError):
            authorize_provenance(asked, provider_id="P", grant=None, at=NOW)


class TestEveryLabelHasACurrency:
    def test_currency_follows_provenance_and_unknown_origin_is_never_current(self) -> None:
        currencies = {member: market_currency_of(member) for member in StreamProvenance}

        assert currencies[StreamProvenance.UNVERIFIED_OR_USER_SUPPLIED] is MarketCurrency.HISTORICAL
        assert currencies[StreamProvenance.PROVIDER_HISTORICAL] is MarketCurrency.HISTORICAL
        assert [p for p, c in currencies.items() if c is MarketCurrency.CURRENT] == [
            StreamProvenance.REAL_EXCHANGE_LIVE
        ]


class TestNothingInTheApplicationHoldsAGrant:
    def test_no_module_constructs_a_licence_grant_or_passes_one(self) -> None:
        """The build composes no grant: no licence exists to back one."""
        offenders = [
            path.relative_to(APP).as_posix()
            for path in APP.rglob("*.py")
            if (
                "LicenceGrant(" in path.read_text(encoding="utf-8")
                or "grant=" in path.read_text(encoding="utf-8")
            )
            and path.name
            not in {"capability.py", "capabilities.py", "source_status.py", "session.py"}
        ]
        assert offenders == []

    def test_the_live_api_refuses_to_serialize_anything_but_simulated_history(self) -> None:
        from app.api.schemas.live_projection import historical_currency, simulated_provenance

        assert simulated_provenance(StreamProvenance.SIMULATED_HISTORICAL_STREAM) == (
            "SIMULATED_HISTORICAL_STREAM"
        )
        for other in StreamProvenance:
            if other is not StreamProvenance.SIMULATED_HISTORICAL_STREAM:
                with pytest.raises(ValueError, match="does not serve"):
                    simulated_provenance(other)
        with pytest.raises(ValueError, match="does not serve"):
            historical_currency(MarketCurrency.CURRENT)
