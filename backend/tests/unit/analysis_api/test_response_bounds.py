"""Response and cancellation bounds (§13, §14).

Input limits do not imply output limits. Most collections here are bounded by
the *engines' own vocabulary* - three scenarios, two suitability directions,
four timeframe roles, a fixed indicator set - so their size does not follow the
input at all. One collection did follow it: data-quality findings, one per
malformed row. A file of 2 400 bad rows produced 2 400 findings and a 240 KiB
response.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api.schemas.analysis_projection import MAX_ISSUES_PER_TIMEFRAME
from app.core.config import Settings
from app.main import create_app
from tests.unit.analysis_api.test_analysis_api import body, dataset


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


def malformed(rows: int, *, with_blocker: bool = False) -> str:
    lines = ["open_time,open,high,low,close,volume"]
    if with_blocker:
        # A real OHLC violation: high below low. The Data Quality Engine blocks
        # on this, and it must never be the finding that gets truncated away.
        lines.append("2026-01-01T00:00:00+00:00,100,90,95,97,5")
    lines.extend(f"2026-01-01T00:{index % 60:02d}:00+00:00,nope,2,1,1.5,5" for index in range(rows))
    return "\n".join(lines) + "\n"


class TestFindingsAreBounded:
    def test_a_flood_of_malformed_rows_does_not_flood_the_response(
        self, client: TestClient
    ) -> None:
        response = client.post(
            "/api/analysis",
            json=body(
                datasets=[{"timeframe": "1H", "content": malformed(2_400), "source_name": "a"}]
            ),
        )
        issues = response.json()["timeframes"][0]["issues"]

        assert len(issues) <= MAX_ISSUES_PER_TIMEFRAME
        assert len(response.content) < 100_000, f"response was {len(response.content)} bytes"

    def test_the_omitted_count_is_reported(self, client: TestClient) -> None:
        """Nothing looks complete when it is not."""
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[{"timeframe": "1H", "content": malformed(2_400), "source_name": "a"}]
            ),
        ).json()
        timeframe = payload["timeframes"][0]

        assert timeframe["omitted_issue_count"] > 0
        assert len(timeframe["issues"]) + timeframe["omitted_issue_count"] > 2_000

    def test_a_blocking_finding_is_never_the_one_truncated_away(self, client: TestClient) -> None:
        """The reason a dataset was refused must survive any cap.

        A cap applied in file order would keep twenty-five malformed-row notes
        and drop the OHLC violation that actually blocked the series.
        """
        payload = client.post(
            "/api/analysis",
            json=body(
                datasets=[
                    {
                        "timeframe": "1H",
                        "content": malformed(2_400, with_blocker=True),
                        "source_name": "a",
                    }
                ]
            ),
        ).json()
        issues = payload["timeframes"][0]["issues"]

        assert any(item["severity"] == "BLOCK" for item in issues)
        assert any(item["code"] == "INVALID_OHLC_RELATIONSHIP" for item in issues)

    def test_a_small_file_omits_nothing(self, client: TestClient) -> None:
        payload = client.post(
            "/api/analysis",
            json=body(datasets=[{"timeframe": "1H", "content": malformed(3), "source_name": "a"}]),
        ).json()

        assert payload["timeframes"][0]["omitted_issue_count"] == 0


class TestTheRestIsBoundedByVocabulary:
    def test_a_full_four_timeframe_analysis_has_a_sane_response_size(
        self, client: TestClient
    ) -> None:
        """Measured at the input cap: about 390 KiB, most of it candles."""
        response = client.post(
            "/api/analysis",
            json=body(datasets=[dataset(code, count=2_500) for code in ("1D", "1H", "15M", "5M")]),
        )

        assert response.status_code == 200
        assert len(response.content) < 1_000_000, f"response was {len(response.content)} bytes"

    def test_collections_do_not_grow_with_the_input(self, client: TestClient) -> None:
        """Doubling the candles must not double the evidence.

        These are shaped by the engines' vocabulary - roles times categories,
        three scenarios, two directions - not by how much data arrived.
        """
        small = client.post(
            "/api/analysis",
            json=body(datasets=[dataset(code, count=300) for code in ("1D", "1H", "15M", "5M")]),
        ).json()
        large = client.post(
            "/api/analysis",
            json=body(datasets=[dataset(code, count=1_200) for code in ("1D", "1H", "15M", "5M")]),
        ).json()

        assert len(large["scenarios"]) == len(small["scenarios"]) == 3
        assert len(large["suitability"]) == len(small["suitability"]) == 2
        assert len(large["technical"]) == len(small["technical"]) == 4
        assert len(large["evidence"]) <= len(small["evidence"]) * 2

    def test_the_chart_is_capped_however_much_arrives(self, client: TestClient) -> None:
        from app.api.schemas.analysis_projection import MAX_CHART_CANDLES

        payload = client.post(
            "/api/analysis",
            json=body(datasets=[dataset("5M", count=2_000)]),
        ).json()

        assert len(payload["chart"][0]["candles"]) == MAX_CHART_CANDLES

    def test_safety_critical_content_is_never_truncated(self, client: TestClient) -> None:
        """`missing` and the suitability findings carry the blockers."""
        payload = client.post(
            "/api/analysis", json=body(datasets=[dataset("1D"), dataset("1H")])
        ).json()

        assert any("15M" in item for item in payload["missing"])
        assert any("5M" in item for item in payload["missing"])
        assert payload["suitability"]


# ----------------------------------------------------------------------
# §14: cancellation
# ----------------------------------------------------------------------


class TestCancellationSemantics:
    """What a cancelled analysis leaves behind: nothing.

    The frontend aborts the request and ignores any late response. These tests
    pin the property that makes that safe — that there is nothing on the server
    for a discarded completion to have changed.
    """

    def test_an_analysis_writes_nothing(self, client: TestClient) -> None:
        """Two identical requests are indistinguishable in their effects.

        If an analysis accumulated state anywhere, the second would differ.
        """
        first = client.post("/api/analysis", json=body()).json()
        second = client.post("/api/analysis", json=body()).json()

        first.pop("identity")
        second.pop("identity")
        assert first == second

    def test_nothing_can_be_fetched_back(self, client: TestClient) -> None:
        """No retrieval surface exists, so a completed-then-discarded analysis
        leaves nothing a later request could pick up."""
        identity = client.post("/api/analysis", json=body()).json()["identity"]

        assert client.get(f"/api/analysis/{identity['analysis_id']}").status_code == 404
        assert client.get("/api/analysis").status_code == 405
        assert identity["ephemeral"] is True

    def test_the_registered_surface_has_no_retrieval_route(self) -> None:
        from app.main import create_app

        settings = Settings(
            app_env="test",
            app_version="0.0.0-test",
            postgres_host="localhost",
            postgres_port=5432,
            postgres_user="viop",
            postgres_password=SecretStr("fixture-password"),  # TEST_FIXTURE value
            postgres_db="viop_test",
        )
        paths: dict[str, Any] = create_app(settings).openapi()["paths"]
        analysis_ops = {
            method
            for path, ops in paths.items()
            if path.startswith("/api/analysis")
            for method in ops
        }

        assert analysis_ops == {"post"}


class TestValidationErrorsAreBounded:
    """A rejected request must not be an amplifier.

    Found while proving §13 against the running stack: FastAPI's default 422
    body carries pydantic's `input` key, which is the rejected value itself. A
    request with one 2 MB unexpected field produced a 2 000 113 byte error
    response - the reply grew byte-for-byte with the request, on the path that
    costs the server least.
    """

    def test_a_huge_rejected_field_does_not_come_back(self, client: TestClient) -> None:
        response = client.post(
            "/api/analysis",
            json={**body(), "forged": "A" * 2_000_000},
        )

        assert response.status_code == 422
        assert len(response.content) < 2_000, f"error body was {len(response.content)} bytes"
        assert b"AAAAAAAAAA" not in response.content

    def test_the_error_body_does_not_grow_with_the_input(self, client: TestClient) -> None:
        sizes = [
            len(client.post("/api/analysis", json={**body(), "forged": "A" * size}).content)
            for size in (1_000, 500_000)
        ]

        assert sizes[0] == sizes[1], f"body size tracked the input: {sizes}"

    def test_the_error_still_says_where_the_problem_is(self, client: TestClient) -> None:
        """Bounding must not cost the client its diagnosis."""
        payload = client.post("/api/analysis", json={**body(), "forged": "x"}).json()
        first = payload["detail"][0]

        assert "forged" in first["loc"]
        assert first["type"] == "extra_forbidden"
        assert first["msg"].strip()
        assert "input" not in first, "the rejected value was echoed back"

    def test_a_request_wrong_in_many_ways_lists_a_bounded_number(self, client: TestClient) -> None:
        from app.api.limits import MAX_VALIDATION_ERRORS

        payload = client.post(
            "/api/analysis",
            json={**body(), **{f"forged_{index}": "x" for index in range(200)}},
        ).json()

        assert len(payload["detail"]) == MAX_VALIDATION_ERRORS
        assert payload["omitted_error_count"] == 200 - MAX_VALIDATION_ERRORS

    def test_a_normal_validation_failure_is_unaffected(self, client: TestClient) -> None:
        """The common case - a missing required field - still reads clearly."""
        payload = client.post("/api/analysis", json={"datasets": []}).json()

        assert payload["omitted_error_count"] == 0
        assert any("symbol" in item["loc"] for item in payload["detail"])
