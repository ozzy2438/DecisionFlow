"""Add append-only, non-authoritative LLM explanations.

Revision ID: 0003_decision_explanations
Revises: 0002_governed_decision_twin
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_decision_explanations"
down_revision: str | None = "0002_governed_decision_twin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_explanations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(80), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("status", sa.String(40), nullable=False),
        sa.Column("prompt_version", sa.String(80), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("output", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('GENERATED', 'TEMPLATE_FALLBACK')",
            name="ck_decision_explanation_status",
        ),
        sa.ForeignKeyConstraint(["decision_id"], ["decision_records.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id", name="uq_decision_explanation_decision"),
    )
    op.execute(
        """
        CREATE TRIGGER trg_decision_explanations_append_only
        BEFORE UPDATE OR DELETE ON decision_explanations
        FOR EACH ROW EXECUTE FUNCTION decisionflow_reject_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_decision_explanations_append_only ON decision_explanations"
    )
    op.drop_table("decision_explanations")
