from pathlib import Path
from uuid import uuid4

import pytest

from decisionflow.features import model_matrix
from decisionflow.training import (
    build_pipeline,
    evaluate_classifier,
    temporal_split,
    train_candidate,
)
from tests.test_features import valid_frame


def test_temporal_split_and_lightgbm_pipeline() -> None:
    frame = valid_frame(300)
    for index in frame.index:
        label = int(frame.loc[index, "sla_breach"])
        frame.loc[index, "prefix_event_count"] = 20.0 if label else 2.0
        frame.loc[index, "days_since_last_event"] = 6.0 if label else 1.0
    split = temporal_split(frame)
    train_x, train_y = model_matrix(split.train)
    test_x, test_y = model_matrix(split.test)
    pipeline = build_pipeline()

    pipeline.fit(train_x, train_y)
    metrics = evaluate_classifier(pipeline, test_x, test_y)

    assert len(split.train) > len(split.validation)
    assert split.train["start_time"].max() < split.validation["start_time"].min()
    assert metrics["roc_auc"] > 0.95
    assert metrics["brier_score"] < 0.10


def test_train_candidate_logs_governance_artifacts(tmp_path: Path) -> None:
    frame = valid_frame(300)
    for index in frame.index:
        label = int(frame.loc[index, "sla_breach"])
        frame.loc[index, "prefix_event_count"] = 20.0 if label else 2.0
        frame.loc[index, "days_since_last_event"] = 6.0 if label else 1.0

    report = train_candidate(
        frame,
        import_id=uuid4(),
        source_checksum="fixture-checksum",
        output_dir=tmp_path / "artifacts",
        tracking_uri=f"sqlite:///{tmp_path / 'mlflow.db'}",
        experiment_name="decisionflow-test",
        model_name="decisionflow-test-model",
        register_model=False,
    )

    assert report["acceptance_gate"]["passed"] is True
    assert report["governance_status"] == "CANDIDATE_NOT_PRODUCTION_APPROVED"
    assert report["mlflow"]["run_id"]
    assert report["mlflow"]["model_version"] is None
    assert (tmp_path / "artifacts" / "training_report.json").is_file()
    assert (tmp_path / "artifacts" / "feature_importance.json").is_file()


def test_temporal_split_rejects_unsafe_partitions() -> None:
    with pytest.raises(ValueError, match="At least 30"):
        temporal_split(valid_frame(10))
    with pytest.raises(ValueError, match="between zero and one"):
        temporal_split(valid_frame(30), train_fraction=0)
    with pytest.raises(ValueError, match="leave a test"):
        temporal_split(valid_frame(30), train_fraction=0.8, validation_fraction=0.2)
