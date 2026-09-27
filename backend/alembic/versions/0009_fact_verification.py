"""The durable contract-fact verification journal (Phase 15 Part 2B).

A forward migration after ``0008_shadow_outcomes``, which is the applied head
on the development database; nothing earlier is edited.

Three append-only tables:

``fact_submissions`` - a claim that a document states a fact about a contract.
It carries no status column, because a claim has none: storing a submission
makes nothing verified.

``fact_review_decisions`` - one decision per submission (the primary key *is*
the submission), holding what the reviewer asserted and what the review
boundary made of it: ``APPROVED``, ``REJECTED``, or ``REFUSED`` with its code.
The result is in the same row as the decision, so an approval and its evidence
are written by one statement and cannot disagree. Checks make an approval
without an attested document, or a refusal without a code, unrepresentable.

``contract_fact_records`` - a contract's facts published from approved
decisions. Each fact column pairs a submission id with a result column fixed
to ``'APPROVED'`` by a check, and the pair is a foreign key to the decision's
``(submission_id, result)``: PostgreSQL itself refuses a record citing a
decision that was rejected, refused or never made. ``recorded_at`` is when
this system first held the record - its knowledge time, kept apart from the
period it governs.

Nothing is updated or deleted: a trigger refuses both on every table. A
correction is a new submission and a new record naming the one it corrects.

Revision ID: 0009_fact_verification
Revises: 0008_shadow_outcomes
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_fact_verification"
down_revision = "0008_shadow_outcomes"
branch_labels = None
depends_on = None

_TABLES = ("fact_submissions", "fact_review_decisions", "contract_fact_records")


def _fact_pair(name: str, *, nullable: bool) -> list[sa.Column[object]]:
    return [
        sa.Column(f"{name}_submission", sa.String(length=64), nullable=nullable),
        sa.Column(f"{name}_result", sa.String(length=16), nullable=nullable),
    ]


def upgrade() -> None:
    op.create_table(
        "contract_fact_records",
        sa.Column("record_id", sa.String(length=64), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("underlying_symbol", sa.String(length=64), nullable=False),
        sa.Column("contract_name", sa.String(length=200), nullable=False),
        sa.Column("authority", sa.String(length=24), nullable=False),
        sa.Column("reference", sa.String(length=500), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "corrects",
            sa.String(length=64),
            sa.ForeignKey("contract_fact_records.record_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("reviewed_by", sa.String(length=200), nullable=False),
        *_fact_pair("multiplier", nullable=False),
        *_fact_pair("tick_size", nullable=False),
        *_fact_pair("expiry", nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sequence", name="uq_contract_fact_records_sequence"),
        sa.CheckConstraint(
            "symbol = btrim(symbol) AND symbol <> ''", name="record_symbol_canonical"
        ),
        sa.CheckConstraint(
            "authority IN ('EXCHANGE_OFFICIAL', 'LICENSED_PROVIDER')",
            name="record_authority_is_authoritative",
        ),
        sa.CheckConstraint("btrim(reference) <> ''", name="record_cites_a_document"),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_until > effective_from",
            name="record_period_not_empty",
        ),
        sa.CheckConstraint(
            "corrects IS NULL OR corrects <> record_id", name="record_not_self_correcting"
        ),
        sa.CheckConstraint("multiplier_result = 'APPROVED'", name="record_multiplier_approved"),
        sa.CheckConstraint("tick_size_result = 'APPROVED'", name="record_tick_size_approved"),
        sa.CheckConstraint(
            "(expiry_submission IS NULL AND expiry_result IS NULL) OR "
            "(expiry_submission IS NOT NULL AND expiry_result = 'APPROVED')",
            name="record_expiry_approved_if_present",
        ),
        sa.CheckConstraint("recorded_at >= verified_at", name="record_known_after_verified"),
    )
    op.create_index(
        "ix_contract_fact_records_symbol", "contract_fact_records", ["symbol", "sequence"]
    )

    op.create_table(
        "fact_submissions",
        sa.Column("submission_id", sa.String(length=64), primary_key=True),
        sa.Column("sequence", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("symbol", sa.String(length=64), nullable=False),
        sa.Column("fact", sa.String(length=16), nullable=False),
        sa.Column("value_decimal", sa.Numeric(), nullable=True),
        sa.Column("value_date", sa.Date(), nullable=True),
        sa.Column("reference", sa.String(length=500), nullable=False),
        sa.Column("authority", sa.String(length=24), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by", sa.String(length=80), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column(
            "corrects",
            sa.String(length=64),
            sa.ForeignKey("contract_fact_records.record_id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("sequence", name="uq_fact_submissions_sequence"),
        sa.CheckConstraint("btrim(submission_id) <> ''", name="submission_has_identity"),
        sa.CheckConstraint(
            "symbol = btrim(symbol) AND symbol <> ''", name="submission_symbol_canonical"
        ),
        sa.CheckConstraint(
            "fact IN ('MULTIPLIER', 'TICK_SIZE', 'EXPIRY_DATE')", name="submission_fact_known"
        ),
        sa.CheckConstraint(
            "NOT (value_decimal IS NOT NULL AND value_date IS NOT NULL)",
            name="submission_one_value",
        ),
        sa.CheckConstraint(
            "authority IN ('EXCHANGE_OFFICIAL', 'LICENSED_PROVIDER', 'SECONDARY', 'UNKNOWN')",
            name="submission_authority_known",
        ),
        sa.CheckConstraint(
            "origin IN ('MANUAL_ENTRY', 'FILE_IMPORT')", name="submission_origin_known"
        ),
        sa.CheckConstraint(
            "effective_until IS NULL OR effective_from IS NULL OR effective_until > effective_from",
            name="submission_period_not_empty",
        ),
    )
    op.create_index("ix_fact_submissions_symbol", "fact_submissions", ["symbol"])

    op.create_table(
        "fact_review_decisions",
        sa.Column(
            "submission_id",
            sa.String(length=64),
            sa.ForeignKey("fact_submissions.submission_id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("reviewer", sa.String(length=80), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("document_checked", sa.Boolean(), nullable=False),
        sa.Column("note", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("refusal_code", sa.String(length=48), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("submission_id", "result", name="uq_fact_review_decision_result"),
        sa.CheckConstraint("outcome IN ('APPROVED', 'REJECTED')", name="decision_outcome_known"),
        sa.CheckConstraint(
            "result IN ('APPROVED', 'REJECTED', 'REFUSED')", name="decision_result_known"
        ),
        sa.CheckConstraint(
            "(result = 'REFUSED') = (refusal_code IS NOT NULL)",
            name="decision_refusal_has_code",
        ),
        sa.CheckConstraint(
            "result <> 'APPROVED' OR (outcome = 'APPROVED' AND document_checked)",
            name="decision_approval_attested",
        ),
        sa.CheckConstraint(
            "result <> 'REJECTED' OR outcome = 'REJECTED'", name="decision_rejection_is_rejection"
        ),
    )

    for name in ("multiplier", "tick_size", "expiry"):
        op.create_foreign_key(
            f"fk_record_{name}_approved",
            "contract_fact_records",
            "fact_review_decisions",
            [f"{name}_submission", f"{name}_result"],
            ["submission_id", "result"],
            ondelete="RESTRICT",
        )

    op.execute(
        """
        CREATE FUNCTION fact_verification_append_only() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only (% refused)', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in _TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {table}_no_update_or_delete
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION fact_verification_append_only();
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_no_update_or_delete ON {table}")
    op.execute("DROP FUNCTION IF EXISTS fact_verification_append_only()")
    for name in ("multiplier", "tick_size", "expiry"):
        op.drop_constraint(
            f"fk_record_{name}_approved", "contract_fact_records", type_="foreignkey"
        )
    op.drop_table("fact_review_decisions")
    op.drop_index("ix_fact_submissions_symbol", table_name="fact_submissions")
    op.drop_table("fact_submissions")
    op.drop_index("ix_contract_fact_records_symbol", table_name="contract_fact_records")
    op.drop_table("contract_fact_records")
