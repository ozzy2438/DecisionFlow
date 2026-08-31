"""SQLAlchemy schema declarations used by migrations and application code."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class DatasetImport(Base):
    __tablename__ = "dataset_imports"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    source_name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_checksum_md5: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    source_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    parser_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_case_count: Mapped[int | None] = mapped_column(BigInteger)
    actual_event_count: Mapped[int | None] = mapped_column(BigInteger)
    error_message: Mapped[str | None] = mapped_column(Text)
    import_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)


class P2PCase(Base):
    __tablename__ = "p2p_cases"

    case_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    import_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dataset_imports.id", ondelete="CASCADE"), nullable=False
    )
    purchasing_document: Mapped[str | None] = mapped_column(String(100))
    item: Mapped[str | None] = mapped_column(String(100))
    item_type: Mapped[str | None] = mapped_column(String(200))
    gr_based_invoice_verification: Mapped[bool | None]
    goods_receipt_required: Mapped[bool | None]
    source_system: Mapped[str | None] = mapped_column(String(200))
    document_category: Mapped[str | None] = mapped_column(String(200))
    company: Mapped[str | None] = mapped_column(String(200))
    spend_classification: Mapped[str | None] = mapped_column(Text)
    spend_area: Mapped[str | None] = mapped_column(Text)
    sub_spend_area: Mapped[str | None] = mapped_column(Text)
    vendor: Mapped[str | None] = mapped_column(String(200))
    vendor_name: Mapped[str | None] = mapped_column(Text)
    document_type: Mapped[str | None] = mapped_column(String(200))
    item_category: Mapped[str | None] = mapped_column(String(200))
    first_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_count: Mapped[int] = mapped_column(Integer, nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_p2p_cases_import_id", "import_id"),
        Index("ix_p2p_cases_vendor", "vendor"),
        Index("ix_p2p_cases_last_event_at", "last_event_at"),
    )


class P2PEvent(Base):
    __tablename__ = "p2p_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    import_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("dataset_imports.id", ondelete="CASCADE"), nullable=False
    )
    case_id: Mapped[str] = mapped_column(
        String(160), ForeignKey("p2p_cases.case_id", ondelete="CASCADE"), nullable=False
    )
    event_index: Mapped[int] = mapped_column(Integer, nullable=False)
    activity: Mapped[str] = mapped_column(String(300), nullable=False)
    event_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resource: Mapped[str | None] = mapped_column(String(300))
    lifecycle: Mapped[str | None] = mapped_column(String(100))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        UniqueConstraint("case_id", "event_index", name="uq_p2p_events_case_position"),
        Index("ix_p2p_events_import_id", "import_id"),
        Index("ix_p2p_events_case_time", "case_id", "event_time"),
        Index("ix_p2p_events_activity", "activity"),
    )


class TwinCase(Base):
    """A point-in-time case inside the explicitly simulated operational twin."""

    __tablename__ = "twin_cases"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    display_id: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    source_case_id: Mapped[str | None] = mapped_column(String(160))
    is_simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    case_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("is_simulated", name="ck_twin_cases_simulated_only"),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_twin_cases_status"),
        Index("ix_twin_cases_created_at", "created_at"),
    )


class DecisionRecord(Base):
    """Immutable model-and-rule decision; later state is reconstructed from ledger events."""

    __tablename__ = "decision_records"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    twin_case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("twin_cases.id", ondelete="RESTRICT"), nullable=False
    )
    workflow_id: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    model_alias: Mapped[str] = mapped_column(String(80), nullable=False)
    model_policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(120), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(80), nullable=False)
    selected_action: Mapped[str] = mapped_column(String(80), nullable=False)
    impact: Mapped[str] = mapped_column(String(20), nullable=False)
    approval_required: Mapped[bool] = mapped_column(Boolean, nullable=False)
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint("risk_score >= 0 AND risk_score <= 1", name="ck_decision_risk_score"),
        CheckConstraint("impact IN ('NONE', 'LOW', 'HIGH')", name="ck_decision_impact"),
        CheckConstraint(
            "selected_action IN ('NO_ACTION', 'CREATE_FOLLOW_UP_TASK', "
            "'PLACE_SIMULATED_PAYMENT_HOLD')",
            name="ck_decision_selected_action",
        ),
        Index("ix_decision_records_case_created", "twin_case_id", "created_at"),
        Index("ix_decision_records_workflow_id", "workflow_id"),
    )


class ApprovalRecord(Base):
    __tablename__ = "approval_records"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("decision_records.id", ondelete="RESTRICT"), nullable=False
    )
    outcome: Mapped[str] = mapped_column(String(20), nullable=False)
    approver_id: Mapped[str] = mapped_column(String(160), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("outcome IN ('APPROVED', 'REJECTED')", name="ck_approval_outcome"),
        UniqueConstraint("decision_id", name="uq_approval_decision"),
    )


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("decision_records.id", ondelete="RESTRICT"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("attempt > 0", name="ck_tool_execution_attempt"),
        CheckConstraint("status IN ('SUCCEEDED', 'FAILED')", name="ck_tool_execution_status"),
        UniqueConstraint("decision_id", "attempt", name="uq_tool_execution_attempt"),
    )


class ReadbackVerification(Base):
    __tablename__ = "readback_verifications"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    execution_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("tool_executions.id", ondelete="RESTRICT"), nullable=False
    )
    expected_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    observed_state: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (UniqueConstraint("execution_id", name="uq_verification_execution"),)


class TwinFollowUpTask(Base):
    __tablename__ = "twin_follow_up_tasks"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    twin_case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("twin_cases.id", ondelete="RESTRICT"), nullable=False
    )
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("decision_records.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False)
    queue: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("status = 'OPEN'", name="ck_twin_follow_up_status"),
        UniqueConstraint("decision_id", name="uq_twin_follow_up_decision"),
    )


class TwinPaymentHold(Base):
    __tablename__ = "twin_payment_holds"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    twin_case_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("twin_cases.id", ondelete="RESTRICT"), nullable=False
    )
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("decision_records.id", ondelete="RESTRICT"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(220), unique=True, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("active", name="ck_twin_payment_hold_active"),
        UniqueConstraint("decision_id", name="uq_twin_payment_hold_decision"),
    )


class DecisionLedgerEntry(Base):
    __tablename__ = "decision_ledger"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("decision_records.id", ondelete="RESTRICT"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    __table_args__ = (
        CheckConstraint("sequence > 0", name="ck_decision_ledger_sequence"),
        UniqueConstraint("decision_id", "sequence", name="uq_decision_ledger_sequence"),
        Index("ix_decision_ledger_decision", "decision_id", "sequence"),
    )


class DecisionExplanation(Base):
    __tablename__ = "decision_explanations"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    decision_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("decision_records.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status IN ('GENERATED', 'TEMPLATE_FALLBACK')",
            name="ck_decision_explanation_status",
        ),
        UniqueConstraint("decision_id", name="uq_decision_explanation_decision"),
    )
