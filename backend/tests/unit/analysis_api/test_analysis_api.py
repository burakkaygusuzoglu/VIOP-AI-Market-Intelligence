"""The on-demand analysis endpoint (§18, §41, §46, §48).

The tests that matter most here are the ones that try to *forge* something. A
client may supply market data, an account and risk settings; everything else in
the response is constructed server-side by engines the client cannot reach.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.main import create_app

STEP = {
    "1D": timedelta(days=1),
    "1H": timedelta(hours=1),
    "15M": timedelta(minutes=15),
    "5M": timedelta(minutes=5),
}


SNAPSHOT = datetime(2026, 3, 2, tzinfo=UTC)
"""The instant every timeframe in this fixture is known through.

Series are built **backwards** from here on purpose. An earlier version started
all four timeframes at the same moment with the same bar count, which meant 1D
covered 220 days while 5M covered eighteen hours - four readings of four
different epochs presented as one picture. Real market data does not look like
that, and the cross-timeframe coherence rule (§2) correctly refuses it.
"""


def candles_csv(
    timeframe: str,
    count: int = 220,
    drift: str = "0.30",
    end: datetime = SNAPSHOT,
) -> str:
    """A deterministic, structurally valid OHLCV series ending at ``end``.

    Test *input*, not a test result: everything the endpoint reports about it
    is computed by the real engines from these bars.

    `end` is the instant the series is known *through*, so the last bar opens
    one interval before it. Moving one timeframe's `end` is how the
    cross-timeframe rules (§2, §3) are exercised.
    """
    step = STEP[timeframe]
    last_open = end - step
    rows = ["open_time,open,high,low,close,volume"]
    price = Decimal("100")
    move = Decimal(drift)
    for index in range(count):
        moment = last_open - step * (count - 1 - index)
        open_price = price
        close_price = price + move
        high = max(open_price, close_price) + Decimal("0.5")
        low = min(open_price, close_price) - Decimal("0.5")
        rows.append(f"{moment.isoformat()},{open_price},{high},{low},{close_price},{1000 + index}")
        price = close_price
    return "\n".join(rows) + "\n"


def dataset(timeframe: str, *, content: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """One dataset entry, generating candles unless content is given.

    `content` is an explicit keyword rather than a `kwargs.pop` default: the
    default expression would otherwise be evaluated eagerly, generating candles
    for a timeframe under test precisely because it is unsupported.
    """
    return {
        "timeframe": timeframe,
        "content": candles_csv(timeframe, **kwargs) if content is None else content,
        "source_name": f"{timeframe}.csv",
    }


def body(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "symbol": "TEST_FIXTURE_FUT",
        "datasets": [dataset("1D"), dataset("1H"), dataset("15M"), dataset("5M")],
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        app_env="test",
        app_version="0.0.0-test",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_user="viop",
        postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
        postgres_db="viop_test",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client


# ----------------------------------------------------------------------
# A real analysis, from real engines
# ----------------------------------------------------------------------


class TestRealAnalysis:
    def test_returns_a_computed_analysis(self, client: TestClient) -> None:
        response = client.post("/api/analysis", json=body())
        assert response.status_code == 200

        payload = response.json()
        assert payload["technical_available"] is True
        assert len(payload["timeframes"]) == 4
        assert all(item["usable"] for item in payload["timeframes"])
        assert payload["evidence"], "the engines produced no evidence"
        assert len(payload["scenarios"]) == 3

    def test_an_uptrend_produces_more_bullish_than_bearish_evidence(
        self, client: TestClient
    ) -> None:
        """The result tracks the input, which a canned fixture could not do."""
        payload = client.post("/api/analysis", json=body()).json()
        directions = [item["direction"] for item in payload["evidence"]]

        assert directions.count("BULLISH") > directions.count("BEARISH")

    def test_reversing_the_input_reverses_the_evidence(self, client: TestClient) -> None:
        down = body(
            datasets=[
                dataset(tf, content=candles_csv(tf, drift="-0.30"))
                for tf in ("1D", "1H", "15M", "5M")
            ]
        )
        payload = client.post("/api/analysis", json=down).json()
        directions = [item["direction"] for item in payload["evidence"]]

        assert directions.count("BEARISH") > directions.count("BULLISH")

    def test_the_same_input_produces_the_same_analysis_id(self, client: TestClient) -> None:
        first = client.post("/api/analysis", json=body()).json()
        second = client.post("/api/analysis", json=body()).json()

        assert first["identity"]["analysis_id"] == second["identity"]["analysis_id"]

    def test_different_input_produces_a_different_analysis_id(self, client: TestClient) -> None:
        other = body(datasets=[dataset("1D", count=221)])
        first = client.post("/api/analysis", json=body()).json()
        second = client.post("/api/analysis", json=other).json()

        assert first["identity"]["analysis_id"] != second["identity"]["analysis_id"]

    def test_identity_separates_market_time_from_run_time(self, client: TestClient) -> None:
        identity = client.post("/api/analysis", json=body()).json()["identity"]

        assert identity["analysis_as_of"] is not None
        assert identity["generated_at"] != identity["analysis_as_of"]
        assert identity["ephemeral"] is True

    def test_the_chart_carries_only_candles_that_were_supplied(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body(datasets=[dataset("1H")])).json()
        series = payload["chart"][0]
        closes = {item["close"] for item in series["candles"]}
        supplied = {line.split(",")[4] for line in candles_csv("1H").strip().splitlines()[1:]}

        assert closes <= supplied, "the chart contains a price that was never supplied"


# ----------------------------------------------------------------------
# Forgery: the trust boundary
# ----------------------------------------------------------------------


class TestForgedAuthorityIsRejected:
    @pytest.mark.parametrize(
        "forged",
        [
            {"final_action": "LONG"},
            {"allowed_actions": ["LONG"]},
            {"risk_permitted": True},
            {"setup_quality": 99},
            {"suitability": {"no_trade": False}},
            {"technical_available": True},
            {"evidence": [{"direction": "BULLISH"}]},
            {"identity": {"analysis_id": "forged"}},
            {"synthesis": {"final_action": "LONG"}},
        ],
    )
    def test_a_derived_field_in_the_request_is_rejected(
        self, client: TestClient, forged: dict[str, Any]
    ) -> None:
        """There is no field to put it in, so the whole request fails.

        Not "supplied and ignored" — ignoring would leave the door open for a
        future field name to collide with a real one.
        """
        response = client.post("/api/analysis", json=body(**forged))
        assert response.status_code == 422

    def test_a_forged_verified_multiplier_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/analysis",
            json=body(contract={"multiplier": "10", "verified": True}),
        )
        assert response.status_code == 422

    def test_a_client_cannot_supply_a_calculated_indicator(self, client: TestClient) -> None:
        response = client.post("/api/analysis", json=body(facts=[{"id": "RSI", "raw": "99"}]))
        assert response.status_code == 422

    def test_no_contract_metadata_is_invented_for_an_unknown_symbol(
        self, client: TestClient
    ) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        assert payload["identity"]["contract_metadata_verified"] is False
        assert payload["risk"]["contract"] is None
        assert payload["risk"]["available"] is False


# ----------------------------------------------------------------------
# Input validation
# ----------------------------------------------------------------------


class TestInputValidation:
    def test_an_unknown_timeframe_is_rejected(self, client: TestClient) -> None:
        header_only = "open_time,open,high,low,close,volume\n"
        response = client.post(
            "/api/analysis",
            json=body(datasets=[dataset("4H", content=header_only)]),
        )
        assert response.status_code == 422

    def test_a_duplicated_timeframe_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/analysis", json=body(datasets=[dataset("1H"), dataset("1H")]))
        assert response.status_code == 422

    def test_no_datasets_is_rejected(self, client: TestClient) -> None:
        assert client.post("/api/analysis", json=body(datasets=[])).status_code == 422

    def test_an_empty_symbol_is_rejected(self, client: TestClient) -> None:
        assert client.post("/api/analysis", json=body(symbol="")).status_code == 422

    def test_a_missing_header_is_reported_not_crashed(self, client: TestClient) -> None:
        payload = client.post(
            "/api/analysis",
            json=body(datasets=[dataset("1H", content="")]),
        ).json()

        assert payload["input_errors"], "an unreadable file produced no error"
        assert payload["technical_available"] is False

    def test_a_missing_column_is_reported(self, client: TestClient) -> None:
        content = "open_time,open,high,low,volume\n2026-01-01T00:00:00+00:00,1,2,0,5\n"
        payload = client.post(
            "/api/analysis", json=body(datasets=[dataset("1H", content=content)])
        ).json()

        assert any("close" in error for error in payload["input_errors"])

    def test_a_timestamp_without_a_timezone_is_accepted_as_utc_by_policy(
        self, client: TestClient
    ) -> None:
        """The Phase 1 provider's documented default, unchanged.

        Recorded as a test so that changing it is a deliberate act: a silent
        switch to "reject" or to a different zone shifts every candle.
        """
        content = "open_time,open,high,low,close,volume\n2026-01-01T00:00:00,1,2,0.5,1.5,5\n"
        response = client.post(
            "/api/analysis", json=body(datasets=[dataset("1H", content=content)])
        )
        assert response.status_code == 200

    def test_a_malformed_row_is_a_finding_not_a_crash(self, client: TestClient) -> None:
        good = candles_csv("1H")
        content = good + "2026-06-01T00:00:00+00:00,not-a-number,2,1,1.5,5\n"
        payload = client.post(
            "/api/analysis", json=body(datasets=[dataset("1H", content=content)])
        ).json()

        issues = payload["timeframes"][0]["issues"]
        assert any(item["code"] == "MALFORMED_ROW" for item in issues)

    def test_an_impossible_candle_is_reported_by_the_quality_engine(
        self, client: TestClient
    ) -> None:
        content = (
            "open_time,open,high,low,close,volume\n"
            "2026-01-01T00:00:00+00:00,100,90,95,97,5\n"  # high below low
        )
        payload = client.post(
            "/api/analysis", json=body(datasets=[dataset("1H", content=content)])
        ).json()

        assert payload["timeframes"][0]["usable"] is False

    def test_an_oversized_dataset_is_refused_rather_than_truncated(
        self, client: TestClient
    ) -> None:
        """A truncated series is a wrong analysis; a refused one is honest."""
        huge = candles_csv("5M", count=61_000)
        payload = client.post(
            "/api/analysis", json=body(datasets=[dataset("5M", content=huge)])
        ).json()

        assert payload["input_errors"], "an oversized dataset was silently accepted"
        assert any("rows" in error for error in payload["input_errors"])

    def test_an_error_message_does_not_echo_the_input(self, client: TestClient) -> None:
        payload = client.post(
            "/api/analysis",
            json=body(datasets=[dataset("1H", content="<script>alert(1)</script>")]),
        ).json()

        rendered = " ".join(payload["input_errors"])
        assert "<script>" not in rendered


# ----------------------------------------------------------------------
# Partial is not complete
# ----------------------------------------------------------------------


class TestPartialAnalysis:
    def test_a_missing_timeframe_is_explicit(self, client: TestClient) -> None:
        payload = client.post(
            "/api/analysis", json=body(datasets=[dataset("1D"), dataset("1H")])
        ).json()

        assert set(payload["missing_timeframes"]) == {"15M", "5M"}

    def test_a_missing_timeframe_is_never_a_neutral_reading(self, client: TestClient) -> None:
        payload = client.post(
            "/api/analysis", json=body(datasets=[dataset("1D"), dataset("1H")])
        ).json()
        present = {item["timeframe"] for item in payload["timeframes"]}

        assert present == {"1D", "1H"}
        assert not any(item["direction"] == "NEUTRAL" for item in payload["timeframes"])

    def test_risk_is_unavailable_with_named_reasons_not_a_zero(self, client: TestClient) -> None:
        risk = client.post("/api/analysis", json=body()).json()["risk"]

        assert risk["available"] is False
        assert risk["outcome"] == "UNAVAILABLE"
        assert risk["unavailable_reasons"]
        assert risk["facts"] == []

    def test_technical_and_risk_availability_are_separate_facts(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        assert payload["technical_available"] is True
        assert payload["risk"]["available"] is False

    def test_synthesis_absence_does_not_disturb_the_analysis(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body()).json()

        assert payload["synthesis"]["status"] in {"NOT_CONFIGURED", "NOT_APPLICABLE"}
        assert payload["synthesis"]["final_action"] is None
        assert payload["technical_available"] is True

    def test_an_unconfigured_synthesis_is_never_an_action(self, client: TestClient) -> None:
        synthesis = client.post("/api/analysis", json=body()).json()["synthesis"]

        assert synthesis["status"] not in {"LONG", "SHORT", "WAIT", "NO_TRADE"}
        assert synthesis["final_action"] is None


# ----------------------------------------------------------------------
# Account, risk and currency
# ----------------------------------------------------------------------


class TestAccountAndRisk:
    def test_account_and_risk_settings_are_accepted(self, client: TestClient) -> None:
        response = client.post(
            "/api/analysis",
            json=body(
                account={"equity": "100000.50", "used_margin": "0", "currency": "TRY"},
                risk={"mode": "FIXED", "fixed_risk": "1000"},
                entry_price="120.5",
                stop_price="118.0",
            ),
        )
        assert response.status_code == 200

    def test_risk_stays_unavailable_without_verified_contract_metadata(
        self, client: TestClient
    ) -> None:
        """Everything else supplied, and sizing still refuses.

        An unverified multiplier produces a wrong contract count that looks
        completely normal, which is exactly what §118 exists to prevent.
        """
        payload = client.post(
            "/api/analysis",
            json=body(
                account={"equity": "100000", "used_margin": "0"},
                risk={"mode": "FIXED", "fixed_risk": "1000"},
                entry_price="120.5",
                stop_price="118.0",
            ),
        ).json()

        assert payload["risk"]["available"] is False
        assert any("kontrat" in reason.lower() for reason in payload["risk"]["unavailable_reasons"])

    def test_a_negative_equity_is_rejected_by_the_domain(self, client: TestClient) -> None:
        response = client.post(
            "/api/analysis",
            json=body(account={"equity": "1000", "used_margin": "-5"}),
        )
        assert response.status_code == 422

    def test_a_non_numeric_equity_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/analysis", json=body(account={"equity": "lots"}))
        assert response.status_code == 422

    def test_money_crosses_the_wire_as_text(self, client: TestClient) -> None:
        """A JSON number would already have been through a double."""
        response = client.post("/api/analysis", json=body(account={"equity": 100000.5}))
        assert response.status_code == 422

    def test_an_invalid_currency_code_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/analysis",
            json=body(account={"equity": "1000", "currency": "TRYX"}),
        )
        assert response.status_code == 422

    def test_no_currency_is_invented_when_none_is_supplied(self, client: TestClient) -> None:
        payload = client.post("/api/analysis", json=body(account={"equity": "1000"})).json()

        for fact in payload["risk"]["facts"]:
            assert fact["currency"] is None
