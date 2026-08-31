from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from decisionflow.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
    model_matrix,
    validate_training_frame,
)


def valid_frame(rows: int = 4) -> pd.DataFrame:
    start = datetime(2018, 1, 1, tzinfo=UTC)
    records = []
    for index in range(rows):
        started_at = start + timedelta(days=index)
        label = index % 2
        record: dict[str, object] = {
            "case_id": f"case-{index}",
            "start_time": started_at,
            "snapshot_at": started_at + timedelta(days=7),
            "clear_time": started_at + timedelta(days=120 if label else 45),
            "total_duration_days": 120.0 if label else 45.0,
            "sla_breach": label,
        }
        record.update({feature: float(index + 1) for feature in NUMERIC_FEATURES})
        record.update({feature: f"value-{index % 2}" for feature in CATEGORICAL_FEATURES})
        records.append(record)
    return pd.DataFrame(records)


def test_validates_and_builds_leakage_safe_matrix() -> None:
    frame = valid_frame()
    validate_training_frame(frame)

    features, target = model_matrix(frame)

    assert list(features.columns) == FEATURE_COLUMNS
    assert "clear_time" not in features
    assert target.tolist() == [0, 1, 0, 1]


def test_rejects_duplicate_or_closed_cases() -> None:
    duplicate = valid_frame()
    duplicate.loc[1, "case_id"] = duplicate.loc[0, "case_id"]
    with pytest.raises(ValueError, match="duplicate"):
        validate_training_frame(duplicate)

    closed = valid_frame()
    closed.loc[0, "clear_time"] = closed.loc[0, "snapshot_at"]
    with pytest.raises(ValueError, match="already closed"):
        validate_training_frame(closed)


def test_rejects_missing_columns_and_single_class() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        validate_training_frame(pd.DataFrame({"case_id": ["case-1"]}))

    one_class = valid_frame()
    one_class["sla_breach"] = 0
    with pytest.raises(ValueError, match="both classes"):
        validate_training_frame(one_class)
