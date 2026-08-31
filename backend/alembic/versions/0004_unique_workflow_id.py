"""Ensure one immutable decision record per Temporal workflow.

Revision ID: 0004_unique_workflow_id
Revises: 0003_decision_explanations
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004_unique_workflow_id"
down_revision: str | None = "0003_decision_explanations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_decision_records_workflow_id", "decision_records", ["workflow_id"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_decision_records_workflow_id", "decision_records", type_="unique")
