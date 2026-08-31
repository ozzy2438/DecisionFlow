"""Create the simulated twin, governed action records, and append-only ledger.

Revision ID: 0002_governed_decision_twin
Revises: 0001_event_store
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002_governed_decision_twin"
down_revision: str | None = "0001_event_store"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid(name: str) -> sa.Column:
    return sa.Column(name, postgresql.UUID(as_uuid=True), nullable=False)


def _json(name: str) -> sa.Column:
    return sa.Column(name, postgresql.JSONB(astext_type=sa.Text()), nullable=False)


def upgrade() -> None:
    op.create_table(
        "twin_cases",
        _uuid("id"),
        sa.Column("display_id", sa.String(80), nullable=False),
        sa.Column("source_case_id", sa.String(160)),
        sa.Column("is_simulated", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("status", sa.String(20), server_default="OPEN", nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        _json("feature_snapshot"),
        sa.Column(
            "case_context",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("is_simulated", name="ck_twin_cases_simulated_only"),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_twin_cases_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("display_id"),
    )
    op.create_index("ix_twin_cases_created_at", "twin_cases", ["created_at"])

    op.create_table(
        "decision_records",
        _uuid("id"),
        _uuid("twin_case_id"),
        sa.Column("workflow_id", sa.String(200), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("model_version", sa.String(50), nullable=False),
        sa.Column("model_alias", sa.String(80), nullable=False),
        sa.Column("model_policy_version", sa.String(80), nullable=False),
        sa.Column("risk_score", sa.Float(), nullable=False),
        sa.Column("rule_id", sa.String(120), nullable=False),
        sa.Column("rule_version", sa.String(80), nullable=False),
        sa.Column("selected_action", sa.String(80), nullable=False),
        sa.Column("impact", sa.String(20), nullable=False),
        sa.Column("approval_required", sa.Boolean(), nullable=False),
        _json("feature_snapshot"),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("risk_score >= 0 AND risk_score <= 1", name="ck_decision_risk_score"),
        sa.CheckConstraint("impact IN ('NONE', 'LOW', 'HIGH')", name="ck_decision_impact"),
        sa.CheckConstraint(
            "selected_action IN ('NO_ACTION', 'CREATE_FOLLOW_UP_TASK', "
            "'PLACE_SIMULATED_PAYMENT_HOLD')",
            name="ck_decision_selected_action",
        ),
        sa.ForeignKeyConstraint(["twin_case_id"], ["twin_cases.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_decision_records_case_created", "decision_records", ["twin_case_id", "created_at"]
    )
    op.create_index("ix_decision_records_workflow_id", "decision_records", ["workflow_id"])

    op.create_table(
        "approval_records",
        _uuid("id"),
        _uuid("decision_id"),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("approver_id", sa.String(160), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("outcome IN ('APPROVED', 'REJECTED')", name="ck_approval_outcome"),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_approval_decision"),
    )

    op.create_table(
        "tool_executions",
        _uuid("id"),
        _uuid("decision_id"),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("tool_name", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(220), nullable=False),
        _json("request_payload"),
        _json("response_payload"),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("attempt > 0", name="ck_tool_execution_attempt"),
        sa.CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_tool_execution_status"),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", "attempt", name="uq_tool_execution_attempt"),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "readback_verifications",
        _uuid("id"),
        _uuid("execution_id"),
        _json("expected_state"),
        _json("observed_state"),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["tool_executions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("execution_id", name="uq_verification_execution"),
    )

    op.create_table(
        "twin_follow_up_tasks",
        _uuid("id"),
        _uuid("twin_case_id"),
        _uuid("decision_id"),
        sa.Column("idempotency_key", sa.String(220), nullable=False),
        sa.Column("queue", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status = 'OPEN'", name="ck_twin_follow_up_status"),
        sa.ForeignKeyConstraint(["twin_case_id"], ["twin_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_twin_follow_up_decision"),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "twin_payment_holds",
        _uuid("id"),
        _uuid("twin_case_id"),
        _uuid("decision_id"),
        sa.Column("idempotency_key", sa.String(220), nullable=False),
        sa.Column("reason_code", sa.String(80), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("active", name="ck_twin_payment_hold_active"),
        sa.ForeignKeyConstraint(["twin_case_id"], ["twin_cases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_twin_payment_hold_decision"),
        sa.UniqueConstraint("idempotency_key"),
    )

    op.create_table(
        "decision_ledger",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        _uuid("decision_id"),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("actor_type", sa.String(40), nullable=False),
        sa.Column("actor_id", sa.String(160), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        _json("payload"),
        sa.Column("previous_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.CheckConstraint("sequence > 0", name="ck_decision_ledger_sequence"),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", "sequence", name="uq_decision_ledger_sequence"),
        sa.UniqueConstraint("entry_hash"),
    )
    op.create_index(
        "ix_decision_ledger_decision", "decision_ledger", ["decision_id", "sequence"]
    )

    # Governance records and applied twin actions are append-only. Twin cases remain operational
    # records; there is deliberately no real ERP table in this project.
    op.execute(
        """
        CREATE FUNCTION decisionflow_reject_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'append-only table % does not allow %', TG_TABLE_NAME, TG_OP;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    for table in (
        "decision_records",
        "approval_records",
        "tool_executions",
        "readback_verifications",
        "twin_follow_up_tasks",
        "twin_payment_holds",
        "decision_ledger",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION decisionflow_reject_mutation()"
        )


def downgrade() -> None:
    for table in (
        "decision_ledger",
        "twin_payment_holds",
        "twin_follow_up_tasks",
        "readback_verifications",
        "tool_executions",
        "approval_records",
        "decision_records",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS decisionflow_reject_mutation()")
    op.drop_table("decision_ledger")
    op.drop_table("twin_payment_holds")
    op.drop_table("twin_follow_up_tasks")
    op.drop_table("readback_verifications")
    op.drop_table("tool_executions")
    op.drop_table("approval_records")
    op.drop_table("decision_records")
    op.drop_table("twin_cases")
