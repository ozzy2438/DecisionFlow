"""Leakage-safe prefix feature extraction for SLA risk modeling."""

from typing import Any
from uuid import UUID

import pandas as pd
from sqlalchemy import Engine, text

from decisionflow.model_policy import ModelPolicy

NUMERIC_FEATURES = [
    "gr_based_invoice_verification",
    "goods_receipt_required",
    "prefix_event_count",
    "unique_activity_count",
    "goods_receipt_count",
    "invoice_receipt_count",
    "vendor_invoice_count",
    "change_count",
    "cancellation_count",
    "payment_block_count",
    "days_since_last_event",
    "days_to_first_goods_receipt",
    "days_to_first_invoice_receipt",
]

CATEGORICAL_FEATURES = [
    "item_type",
    "source_system",
    "company",
    "spend_classification",
    "spend_area",
    "sub_spend_area",
    "vendor",
    "document_type",
    "item_category",
    "last_activity",
]

FEATURE_COLUMNS = NUMERIC_FEATURES + CATEGORICAL_FEATURES
TARGET_COLUMN = "sla_breach"

FEATURE_SQL = text(
    """
    WITH case_bounds AS (
        SELECT
            case_id,
            MIN(event_time) FILTER (WHERE activity = :start_activity) AS start_time,
            MAX(event_time) FILTER (WHERE activity = :completion_activity) AS clear_time
        FROM p2p_events
        WHERE import_id = :import_id
        GROUP BY case_id
    ),
    eligible AS (
        SELECT
            case_id,
            start_time,
            clear_time,
            start_time + make_interval(days => :observation_days) AS snapshot_at
        FROM case_bounds
        WHERE start_time >= :cohort_start
          AND start_time < :cohort_end
          AND clear_time > start_time + make_interval(days => :observation_days)
    ),
    prefix AS (
        SELECT
            b.case_id,
            COUNT(*) AS prefix_event_count,
            COUNT(DISTINCT e.activity) AS unique_activity_count,
            COUNT(*) FILTER (WHERE e.activity = 'Record Goods Receipt') AS goods_receipt_count,
            COUNT(*) FILTER (WHERE e.activity = 'Record Invoice Receipt') AS invoice_receipt_count,
            COUNT(*) FILTER (WHERE e.activity = 'Vendor creates invoice') AS vendor_invoice_count,
            COUNT(*) FILTER (
                WHERE e.activity LIKE 'Change %' OR e.activity LIKE 'Update %'
            ) AS change_count,
            COUNT(*) FILTER (
                WHERE e.activity LIKE 'Cancel %' OR e.activity LIKE 'Delete %'
            ) AS cancellation_count,
            COUNT(*) FILTER (
                WHERE e.activity IN ('Set Payment Block', 'Remove Payment Block')
            ) AS payment_block_count,
            MAX(e.event_time) AS last_event_time,
            (ARRAY_AGG(e.activity ORDER BY e.event_time DESC, e.event_index DESC))[1]
                AS last_activity,
            MIN(e.event_time) FILTER (WHERE e.activity = 'Record Goods Receipt')
                AS first_goods_receipt_time,
            MIN(e.event_time) FILTER (WHERE e.activity = 'Record Invoice Receipt')
                AS first_invoice_receipt_time
        FROM eligible b
        JOIN p2p_events e ON e.case_id = b.case_id
        WHERE e.event_time >= b.start_time AND e.event_time <= b.snapshot_at
        GROUP BY b.case_id
    )
    SELECT
        b.case_id,
        b.start_time,
        b.clear_time,
        b.snapshot_at,
        EXTRACT(EPOCH FROM (b.clear_time - b.start_time)) / 86400.0 AS total_duration_days,
        (b.clear_time > b.start_time + make_interval(days => :sla_days))::integer AS sla_breach,
        COALESCE(c.gr_based_invoice_verification::integer, 0)
            AS gr_based_invoice_verification,
        COALESCE(c.goods_receipt_required::integer, 0) AS goods_receipt_required,
        p.prefix_event_count,
        p.unique_activity_count,
        p.goods_receipt_count,
        p.invoice_receipt_count,
        p.vendor_invoice_count,
        p.change_count,
        p.cancellation_count,
        p.payment_block_count,
        EXTRACT(EPOCH FROM (b.snapshot_at - p.last_event_time)) / 86400.0
            AS days_since_last_event,
        EXTRACT(EPOCH FROM (p.first_goods_receipt_time - b.start_time)) / 86400.0
            AS days_to_first_goods_receipt,
        EXTRACT(EPOCH FROM (p.first_invoice_receipt_time - b.start_time)) / 86400.0
            AS days_to_first_invoice_receipt,
        c.item_type,
        c.source_system,
        c.company,
        c.spend_classification,
        c.spend_area,
        c.sub_spend_area,
        c.vendor,
        c.document_type,
        c.item_category,
        p.last_activity
    FROM eligible b
    JOIN p2p_cases c ON c.case_id = b.case_id
    JOIN prefix p ON p.case_id = b.case_id
    ORDER BY b.start_time, b.case_id
    """
)


def load_training_frame(engine: Engine, import_id: UUID, policy: ModelPolicy) -> pd.DataFrame:
    parameters: dict[str, Any] = {
        "import_id": import_id,
        "start_activity": policy.start_activity,
        "completion_activity": policy.completion_activity,
        "observation_days": policy.observation_horizon_days,
        "sla_days": policy.sla_days,
        "cohort_start": policy.cohort_start,
        "cohort_end": policy.cohort_end_exclusive,
    }
    frame = pd.read_sql_query(FEATURE_SQL, engine, params=parameters)
    validate_training_frame(frame)
    return frame


def validate_training_frame(frame: pd.DataFrame) -> None:
    required = set(FEATURE_COLUMNS) | {
        "case_id",
        "start_time",
        "clear_time",
        "snapshot_at",
        "total_duration_days",
        TARGET_COLUMN,
    }
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Training frame is missing columns: {sorted(missing)}")
    if frame.empty:
        raise ValueError("Training frame is empty")
    if frame["case_id"].duplicated().any():
        raise ValueError("Training frame has duplicate case IDs")
    if not (pd.to_datetime(frame["snapshot_at"]) < pd.to_datetime(frame["clear_time"])).all():
        raise ValueError("Training frame contains cases already closed at the observation point")
    target_values = set(frame[TARGET_COLUMN].dropna().astype(int).unique())
    if target_values != {0, 1}:
        raise ValueError(f"Training target must contain both classes, got {sorted(target_values)}")
    forbidden = {"clear_time", "total_duration_days", TARGET_COLUMN}.intersection(FEATURE_COLUMNS)
    if forbidden:
        raise ValueError(f"Outcome leakage columns entered the feature list: {sorted(forbidden)}")


def model_matrix(frame: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    features = frame[FEATURE_COLUMNS].copy()
    for column in NUMERIC_FEATURES:
        # A floating schema safely represents absent values at inference time. Logging integer
        # columns would make MLflow reject legitimate missing numeric values.
        features[column] = pd.to_numeric(features[column], errors="coerce").astype("float64")
    for column in CATEGORICAL_FEATURES:
        features[column] = features[column].astype("string").fillna("__missing__").astype("object")
    target = frame[TARGET_COLUMN].astype(int)
    return features, target
