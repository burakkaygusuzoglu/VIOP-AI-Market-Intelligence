"""Choosing contract metadata among sources, or refusing (Phase 15 Part 1).

Every value below is a TEST_FIXTURE-sourced fact built in ``support``; none is
a VIOP specification.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pytest

from app.adapters.contract_metadata.manual_provider import ManualContractMetadataProvider
from app.application.sourcing.metadata_sources import VerifiedMetadataSources
from app.domain.common.verification import VerificationStatus
from app.domain.sourcing.metadata import (
    MetadataCandidate,
    MetadataVerdictCode,
    assess_contract_metadata,
)
from tests.unit.sourcing.support import NOW, contract, fact

pytestmark = pytest.mark.unit

MAX_AGE = timedelta(days=30)


def assess(*candidates: MetadataCandidate, requested: str = "TEST_FIXTURE_FUT"):  # type: ignore[no-untyped-def]
    return assess_contract_metadata(requested, candidates, at=NOW, max_age=MAX_AGE)


def one(source: str = "OFFICIAL_A", **kwargs: object) -> MetadataCandidate:
    return MetadataCandidate(source_id=source, contract=contract(**kwargs))  # type: ignore[arg-type]


class TestRefusals:
    def test_no_source_means_missing_never_a_default(self) -> None:
        verdict = assess()

        assert verdict.code is MetadataVerdictCode.MISSING
        assert verdict.contract is None

    def test_a_record_for_another_contract_is_refused(self) -> None:
        verdict = assess(one(symbol="TEST_FIXTURE_OTHER"))

        assert verdict.code is MetadataVerdictCode.WRONG_CONTRACT

    @pytest.mark.parametrize(
        "status",
        [
            VerificationStatus.UNVERIFIED,
            VerificationStatus.TEST_FIXTURE,
            VerificationStatus.DEVELOPMENT_DEFAULT,
            VerificationStatus.MOCK_DATA,
        ],
    )
    def test_a_non_authoritative_multiplier_is_refused(self, status: VerificationStatus) -> None:
        verdict = assess(one(multiplier=fact("10", status=status)))

        assert verdict.code is MetadataVerdictCode.NOT_AUTHORITATIVE

    def test_a_verified_fact_without_a_source_is_refused(self) -> None:
        verdict = assess(one(tick_size=fact("0.25", source="")))

        assert verdict.code is MetadataVerdictCode.NOT_AUTHORITATIVE

    def test_a_verified_fact_without_a_date_cannot_be_judged_current(self) -> None:
        verdict = assess(one(multiplier=fact("10", as_of=None)))

        assert verdict.code is MetadataVerdictCode.NOT_AUTHORITATIVE

    def test_an_old_verification_is_stale(self) -> None:
        old = NOW - MAX_AGE - timedelta(days=1)
        verdict = assess(one(multiplier=fact("10", as_of=old), tick_size=fact("0.25", as_of=old)))

        assert verdict.code is MetadataVerdictCode.STALE

    def test_an_expired_contract_is_refused(self) -> None:
        verdict = assess(one(expiry=(NOW - timedelta(days=3)).date()))

        assert verdict.code is MetadataVerdictCode.EXPIRED

    def test_disagreement_verified_at_the_same_moment_chooses_nothing(self) -> None:
        verdict = assess(one("OFFICIAL_A"), one("OFFICIAL_B", multiplier=fact("100")))

        assert verdict.code is MetadataVerdictCode.CONFLICTING
        assert verdict.contract is None
        assert "multiplier" in verdict.reason


class TestTheSection118ConflictRule:
    def test_a_more_recently_checked_source_no_longer_wins_a_disagreement(self) -> None:
        """Part 2A correction: verification time is not applicability.

        Part 1 chose the more recently verified source here. Without an
        effective period neither record says which value *applies*, so the
        disagreement is refused - with both values kept for the audit.
        """
        newer = NOW - timedelta(days=1)
        verdict = assess(
            one("OFFICIAL_OLD"),
            one(
                "OFFICIAL_NEW",
                multiplier=fact("100", as_of=newer),
                tick_size=fact("0.25", as_of=newer),
            ),
        )

        assert verdict.code is MetadataVerdictCode.CONFLICTING
        assert verdict.contract is None
        (conflict,) = verdict.conflicts
        assert {conflict.chosen_value, conflict.other_value} == {"100", "10"}
        assert {conflict.chosen_source, conflict.other_source} == {"OFFICIAL_OLD", "OFFICIAL_NEW"}
        assert "period" in verdict.reason

    def test_agreeing_sources_record_no_conflict(self) -> None:
        verdict = assess(one("OFFICIAL_A"), one("OFFICIAL_B"))

        assert verdict.usable
        assert verdict.conflicts == ()

    def test_an_unverified_source_cannot_outvote_a_verified_one(self) -> None:
        verdict = assess(
            one("OFFICIAL"),
            one("GUESS", multiplier=fact("999", status=VerificationStatus.UNVERIFIED)),
        )

        assert verdict.usable
        assert verdict.source_id == "OFFICIAL"
        assert verdict.conflicts == ()

    def test_a_verdict_without_a_contract_cannot_claim_to_be_usable(self) -> None:
        from app.domain.sourcing.metadata import MetadataVerdict

        with pytest.raises(ValueError, match="usable verdict"):
            MetadataVerdict(code=MetadataVerdictCode.USABLE, reason="forged")


class TestTheGateBehindTheExistingPort:
    async def test_an_unusable_record_reaches_consumers_as_none(self) -> None:
        guess = ManualContractMetadataProvider(
            [contract(multiplier=fact("10", status=VerificationStatus.UNVERIFIED))]
        )
        gate = VerifiedMetadataSources([("MANUAL", guess)], now=lambda: NOW, max_age=MAX_AGE)

        assert await gate.get_contract("TEST_FIXTURE_FUT") is None
        assert (await gate.assess("TEST_FIXTURE_FUT")).code is MetadataVerdictCode.NOT_AUTHORITATIVE

    async def test_a_symbol_nobody_describes_is_not_inferred(self) -> None:
        gate = VerifiedMetadataSources(
            [("MANUAL", ManualContractMetadataProvider([contract()]))],
            now=lambda: NOW,
            max_age=MAX_AGE,
        )

        assert (
            await gate.get_contract("F_XU0301226") is None
        )  # a plausible symbol, and nothing more

    async def test_a_failing_source_is_skipped_and_logged_by_type_only(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        class Broken:
            async def get_contract(self, symbol: str) -> None:
                raise ConnectionError("https://vendor.example/api?token=sk-meta-do-not-leak")

            async def list_symbols(self) -> list[str]:
                raise ConnectionError("https://vendor.example/api?token=sk-meta-do-not-leak")

        caplog.set_level(logging.DEBUG)
        gate = VerifiedMetadataSources(
            [("BROKEN", Broken()), ("MANUAL", ManualContractMetadataProvider([contract()]))],
            now=lambda: NOW,
            max_age=MAX_AGE,
        )

        record = await gate.get_contract("TEST_FIXTURE_FUT")
        symbols = await gate.list_symbols()

        assert record is not None
        assert symbols == ("TEST_FIXTURE_FUT",)
        assert "sk-meta-do-not-leak" not in caplog.text
        assert "vendor.example" not in caplog.text
        assert all(record.exc_info is None for record in caplog.records)

    def test_source_names_are_distinct(self) -> None:
        manual = ManualContractMetadataProvider()
        with pytest.raises(ValueError, match="distinct"):
            VerifiedMetadataSources(
                [("A", manual), ("A", manual)], now=lambda: NOW, max_age=MAX_AGE
            )


def test_the_fixture_expiry_is_in_the_future_by_default() -> None:
    assert contract(expiry=date(2099, 1, 1)).expiry is not None
