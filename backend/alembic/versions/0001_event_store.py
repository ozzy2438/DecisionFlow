"""Create the immutable BPI event store.

Revision ID: 0001_event_store
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_event_store"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dataset_imports",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_name", sa.String(length=200), nullable=False),
        sa.Column("source_uri", sa.Text(), nullable=False),
        sa.Column("source_checksum_md5", sa.String(length=32), nullable=False),
        sa.Column("source_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("parser_version", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actual_case_count", sa.BigInteger(), nullable=True),
        sa.Column("actual_event_count", sa.BigInteger(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "import_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'completed', 'failed')", name="ck_dataset_imports_status"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_dataset_imports_source_checksum_md5",
        "dataset_imports",
        ["source_checksum_md5"],
    )

    op.create_table(
        "p2p_cases",
        sa.Column("case_id", sa.String(length=160), nullable=False),
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("purchasing_document", sa.String(length=100), nullable=True),
        sa.Column("item", sa.String(length=100), nullable=True),
        sa.Column("item_type", sa.String(length=200), nullable=True),
        sa.Column("gr_based_invoice_verification", sa.Boolean(), nullable=True),
        sa.Column("goods_receipt_required", sa.Boolean(), nullable=True),
        sa.Column("source_system", sa.String(length=200), nullable=True),
        sa.Column("document_category", sa.String(length=200), nullable=True),
        sa.Column("company", sa.String(length=200), nullable=True),
        sa.Column("spend_classification", sa.Text(), nullable=True),
        sa.Column("spend_area", sa.Text(), nullable=True),
        sa.Column("sub_spend_area", sa.Text(), nullable=True),
        sa.Column("vendor", sa.String(length=200), nullable=True),
        sa.Column("vendor_name", sa.Text(), nullable=True),
        sa.Column("document_type", sa.String(length=200), nullable=True),
        sa.Column("item_category", sa.String(length=200), nullable=True),
        sa.Column("first_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count", sa.Integer(), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["import_id"], ["dataset_imports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_index("ix_p2p_cases_import_id", "p2p_cases", ["import_id"])
    op.create_index("ix_p2p_cases_vendor", "p2p_cases", ["vendor"])
    op.create_index("ix_p2p_cases_last_event_at", "p2p_cases", ["last_event_at"])

    op.create_table(
        "p2p_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("import_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", sa.String(length=160), nullable=False),
        sa.Column("event_index", sa.Integer(), nullable=False),
        sa.Column("activity", sa.String(length=300), nullable=False),
        sa.Column("event_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resource", sa.String(length=300), nullable=True),
        sa.Column("lifecycle", sa.String(length=100), nullable=True),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(["case_id"], ["p2p_cases.case_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["import_id"], ["dataset_imports.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "event_index", name="uq_p2p_events_case_position"),
    )
    op.create_index("ix_p2p_events_import_id", "p2p_events", ["import_id"])
    op.create_index("ix_p2p_events_case_time", "p2p_events", ["case_id", "event_time"])
    op.create_index("ix_p2p_events_activity", "p2p_events", ["activity"])


def downgrade() -> None:
    op.drop_table("p2p_events")
    op.drop_table("p2p_cases")
    op.drop_table("dataset_imports")
