"""Contract-fact verification journal tables (Phase 15 Part 2B).

The shape and every constraint are explained in migration
``0009_fact_verification``; this module mirrors it for SQLAlchemy, and an
integration test compares the two so they cannot drift. The append-only
triggers live only in the migration, as for the Phase 10 and Phase 14 journals.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Identity,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.persistence.base import Base


class ContractFactRecordRow(Base):
    __tablename__ = "contract_fact_records"

    record_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    underlying_symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_name: Mapped[str] = mapped_column(String(200), nullable=False)
    authority: Mapped[str] = mapped_column(String(24), nullable=False)
    reference: Mapped[str] = mapped_column(String(500), nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    corrects: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("contract_fact_records.record_id", ondelete="RESTRICT"),
        nullable=True,
    )
    reviewed_by: Mapped[str] = mapped_column(String(200), nullable=False)
    multiplier_submission: Mapped[str] = mapped_column(String(64), nullable=False)
    multiplier_result: Mapped[str] = mapped_column(String(16), nullable=False)
    tick_size_submission: Mapped[str] = mapped_column(String(64), nullable=False)
    tick_size_result: Mapped[str] = mapped_column(String(16), nullable=False)
    expiry_submission: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expiry_result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("sequence", name="uq_contract_fact_records_sequence"),
        CheckConstraint("symbol = btrim(symbol) AND symbol <> ''", name="record_symbol_canonical"),
        CheckConstraint(
            "authority IN ('EXCHANGE_OFFICIAL', 'LICENSED_PROVIDER')",
            name="record_authority_is_authoritative",
        ),
        CheckConstraint("btrim(reference) <> ''", name="record_cites_a_document"),
        CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="record_period_not_empty",
        ),
        CheckConstraint(
            "corrects IS NULL OR corrects <> record_id", name="record_not_self_correcting"
        ),
        CheckConstraint("multiplier_result = 'APPROVED'", name="record_multiplier_approved"),
        CheckConstraint("tick_size_result = 'APPROVED'", name="record_tick_size_approved"),
        CheckConstraint(
            "(expiry_submission IS NULL AND expiry_result IS NULL) OR "
            "(expiry_submission IS NOT NULL AND expiry_result = 'APPROVED')",
            name="record_expiry_approved_if_present",
        ),
        CheckConstraint("recorded_at >= verified_at", name="record_known_after_verified"),
        *(
            ForeignKeyConstraint(
                [f"{name}_submission", f"{name}_result"],
                ["fact_review_decisions.submission_id", "fact_review_decisions.result"],
                name=f"fk_record_{name}_approved",
                ondelete="RESTRICT",
                use_alter=True,
            )
            for name in ("multiplier", "tick_size", "expiry")
        ),
        Index("ix_contract_fact_records_symbol", "symbol", "sequence"),
    )


class FactSubmissionRow(Base):
    __tablename__ = "fact_submissions"

    submission_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    symbol: Mapped[str] = mapped_column(String(64), nullable=False)
    fact: Mapped[str] = mapped_column(String(16), nullable=False)
    value_decimal: Mapped[Decimal | None] = mapped_column(Numeric(), nullable=True)
    value_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    reference: Mapped[str] = mapped_column(String(500), nullable=False)
    authority: Mapped[str] = mapped_column(String(24), nullable=False)
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    effective_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitted_by: Mapped[str] = mapped_column(String(80), nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    origin: Mapped[str] = mapped_column(String(16), nullable=False)
    corrects: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("contract_fact_records.record_id", ondelete="RESTRICT"),
        nullable=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("sequence", name="uq_fact_submissions_sequence"),
        CheckConstraint("btrim(submission_id) <> ''", name="submission_has_identity"),
        CheckConstraint(
            "symbol = btrim(symbol) AND symbol <> ''", name="submission_symbol_canonical"
        ),
        CheckConstraint(
            "fact IN ('MULTIPLIER', 'TICK_SIZE', 'EXPIRY_DATE')", name="submission_fact_known"
        ),
        CheckConstraint(
            "NOT (value_decimal IS NOT NULL AND value_date IS NOT NULL)",
            name="submission_one_value",
        ),
        CheckConstraint(
            "authority IN ('EXCHANGE_OFFICIAL', 'LICENSED_PROVIDER', 'SECONDARY', 'UNKNOWN')",
            name="submission_authority_known",
        ),
        CheckConstraint(
            "origin IN ('MANUAL_ENTRY', 'FILE_IMPORT')", name="submission_origin_known"
        ),
        CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="submission_period_not_empty",
        ),
        Index("ix_fact_submissions_symbol", "symbol"),
    )


class FactReviewDecisionRow(Base):
    __tablename__ = "fact_review_decisions"

    submission_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("fact_submissions.submission_id", ondelete="RESTRICT"),
        primary_key=True,
    )
    reviewer: Mapped[str] = mapped_column(String(80), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    document_checked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, server_default="")
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    refusal_code: Mapped[str | None] = mapped_column(String(48), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("submission_id", "result", name="uq_fact_review_decision_result"),
        CheckConstraint("outcome IN ('APPROVED', 'REJECTED')", name="decision_outcome_known"),
        CheckConstraint(
            "result IN ('APPROVED', 'REJECTED', 'REFUSED')", name="decision_result_known"
        ),
        CheckConstraint(
            "(result = 'REFUSED') = (refusal_code IS NOT NULL)", name="decision_refusal_has_code"
        ),
        CheckConstraint(
            "result <> 'APPROVED' OR (outcome = 'APPROVED' AND document_checked)",
            name="decision_approval_attested",
        ),
        CheckConstraint(
            "result <> 'REJECTED' OR outcome = 'REJECTED'", name="decision_rejection_is_rejection"
        ),
    )
