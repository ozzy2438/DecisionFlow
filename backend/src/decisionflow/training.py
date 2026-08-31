"""LightGBM training, evaluation, MLflow tracking, and candidate registration."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from lightgbm import LGBMClassifier
from mlflow import MlflowClient
from mlflow.models import infer_signature
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder
from sqlalchemy import create_engine

from decisionflow.features import (
    CATEGORICAL_FEATURES,
    FEATURE_COLUMNS,
    NUMERIC_FEATURES,
    TARGET_COLUMN,
    load_training_frame,
    model_matrix,
)
from decisionflow.model_policy import DEFAULT_MODEL_POLICY, ModelPolicy
from decisionflow.process_mining import latest_completed_import

RANDOM_SEED = 42


@dataclass(frozen=True, slots=True)
class TemporalSplit:
    train: pd.DataFrame
    validation: pd.DataFrame
    test: pd.DataFrame
    validation_start: pd.Timestamp
    test_start: pd.Timestamp


def temporal_split(
    frame: pd.DataFrame, *, train_fraction: float = 0.70, validation_fraction: float = 0.15
) -> TemporalSplit:
    if not 0 < train_fraction < 1 or not 0 < validation_fraction < 1:
        raise ValueError("split fractions must be between zero and one")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train and validation fractions must leave a test partition")
    ordered = frame.sort_values(["start_time", "case_id"], kind="stable").reset_index(drop=True)
    if len(ordered) < 30:
        raise ValueError("At least 30 cases are required for a temporal split")
    starts = pd.to_datetime(ordered["start_time"], utc=True)
    validation_start = starts.iloc[int(len(starts) * train_fraction)]
    test_start = starts.iloc[int(len(starts) * (train_fraction + validation_fraction))]
    train = ordered.loc[starts < validation_start].copy()
    validation = ordered.loc[(starts >= validation_start) & (starts < test_start)].copy()
    test = ordered.loc[starts >= test_start].copy()
    if train.empty or validation.empty or test.empty:
        raise ValueError("Temporal boundaries produced an empty partition")
    return TemporalSplit(
        train=train,
        validation=validation,
        test=test,
        validation_start=validation_start,
        test_start=test_start,
    )


def build_pipeline() -> Pipeline:
    numeric = Pipeline(steps=[("imputer", SimpleImputer(strategy="median", add_indicator=True))])
    categorical = Pipeline(
        steps=[
            ("imputer", SimpleImputer(strategy="constant", fill_value="__missing__")),
            (
                "one_hot",
                OneHotEncoder(handle_unknown="ignore", min_frequency=20, sparse_output=True),
            ),
        ]
    )
    preprocess = ColumnTransformer(
        transformers=[
            ("numeric", numeric, NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
        ],
        remainder="drop",
        sparse_threshold=0.2,
    )
    classifier = LGBMClassifier(
        objective="binary",
        n_estimators=300,
        learning_rate=0.05,
        num_leaves=31,
        min_child_samples=40,
        subsample=0.85,
        colsample_bytree=0.85,
        reg_lambda=1.0,
        importance_type="gain",
        random_state=RANDOM_SEED,
        n_jobs=-1,
        deterministic=True,
        force_col_wise=True,
        verbosity=-1,
    )
    return Pipeline(steps=[("preprocess", preprocess), ("classifier", classifier)])


def evaluate_classifier(
    pipeline: Pipeline, features: pd.DataFrame, target: pd.Series, *, threshold: float = 0.5
) -> dict[str, float]:
    probabilities = np.asarray(pipeline.predict_proba(features))[:, 1]
    predictions = (probabilities >= threshold).astype(int)
    return {
        "roc_auc": float(roc_auc_score(target, probabilities)),
        "average_precision": float(average_precision_score(target, probabilities)),
        "log_loss": float(log_loss(target, probabilities)),
        "brier_score": float(brier_score_loss(target, probabilities)),
        "precision_at_0_5": float(precision_score(target, predictions, zero_division=0)),
        "recall_at_0_5": float(recall_score(target, predictions, zero_division=0)),
        "f1_at_0_5": float(f1_score(target, predictions, zero_division=0)),
        "balanced_accuracy_at_0_5": float(balanced_accuracy_score(target, predictions)),
    }


def _feature_importance(pipeline: Pipeline, limit: int = 40) -> list[dict[str, Any]]:
    preprocess: ColumnTransformer = pipeline.named_steps["preprocess"]
    classifier: LGBMClassifier = pipeline.named_steps["classifier"]
    names = preprocess.get_feature_names_out()
    importance = classifier.booster_.feature_importance(importance_type="gain")
    pairs = sorted(zip(names, importance, strict=True), key=lambda pair: -float(pair[1]))
    return [{"feature": str(name), "gain": float(gain)} for name, gain in pairs[:limit]]


def _partition_profile(frame: pd.DataFrame) -> dict[str, Any]:
    return {
        "cases": len(frame),
        "breaches": int(frame[TARGET_COLUMN].sum()),
        "breach_rate": float(frame[TARGET_COLUMN].mean()),
        "min_start_time": pd.to_datetime(frame["start_time"], utc=True).min().isoformat(),
        "max_start_time": pd.to_datetime(frame["start_time"], utc=True).max().isoformat(),
    }


def _acceptance_gate(validation: dict[str, float], test: dict[str, float]) -> dict[str, Any]:
    checks = {
        "test_roc_auc_at_least_0_70": test["roc_auc"] >= 0.70,
        "test_average_precision_at_least_0_60": test["average_precision"] >= 0.60,
        "test_brier_at_most_0_25": test["brier_score"] <= 0.25,
        "test_recall_at_least_0_50": test["recall_at_0_5"] >= 0.50,
        "temporal_auc_drop_at_most_0_15": validation["roc_auc"] - test["roc_auc"] <= 0.15,
    }
    return {"passed": all(checks.values()), "checks": checks}


def train_candidate(
    frame: pd.DataFrame,
    *,
    import_id: UUID,
    source_checksum: str,
    output_dir: Path,
    tracking_uri: str,
    experiment_name: str,
    model_name: str,
    register_model: bool = True,
    policy: ModelPolicy = DEFAULT_MODEL_POLICY,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    # Fail fast on tracking/registry connectivity before spending minutes on feature training.
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)
    split = temporal_split(frame)
    train_x, train_y = model_matrix(split.train)
    validation_x, validation_y = model_matrix(split.validation)
    test_x, test_y = model_matrix(split.test)

    pipeline = build_pipeline()
    pipeline.fit(train_x, train_y)
    validation_metrics = evaluate_classifier(pipeline, validation_x, validation_y)
    test_metrics = evaluate_classifier(pipeline, test_x, test_y)
    acceptance = _acceptance_gate(validation_metrics, test_metrics)
    feature_importance = _feature_importance(pipeline)

    report: dict[str, Any] = {
        "governance_status": "CANDIDATE_NOT_PRODUCTION_APPROVED",
        "policy": policy.as_dict(),
        "training_data": {
            "source": "BPI Challenge 2019",
            "import_id": str(import_id),
            "source_checksum_md5": source_checksum,
            "eligible_cases": len(frame),
            "selection": (
                "2018 PO-line cases with an observed Clear Invoice after the seven-day "
                "observation point"
            ),
            "censoring": (
                "Cases without an observed Clear Invoice are excluded rather than silently labeled "
                "as breaches"
            ),
        },
        "features": FEATURE_COLUMNS,
        "leakage_controls": [
            "All event aggregates stop at start_time + 7 days",
            "clear_time, total_duration_days and sla_breach are excluded from model features",
            "train, validation and test are split chronologically by case start_time",
        ],
        "partitions": {
            "train": _partition_profile(split.train),
            "validation": _partition_profile(split.validation),
            "test": _partition_profile(split.test),
            "validation_start": split.validation_start.isoformat(),
            "test_start": split.test_start.isoformat(),
        },
        "metrics": {"validation": validation_metrics, "test": test_metrics},
        "acceptance_gate": acceptance,
        "feature_importance": feature_importance,
        "runtime": {
            "random_seed": RANDOM_SEED,
            "git_sha": os.getenv("GITHUB_SHA", "unavailable-in-local-container"),
        },
        "mlflow": {
            "tracking_uri": tracking_uri,
            "experiment": experiment_name,
            "registered_model": model_name if register_model else None,
            "run_id": None,
            "model_version": None,
            "alias": None,
        },
    }

    with mlflow.start_run(run_name=f"{policy.version}-{str(import_id)[:8]}") as run:
        mlflow.set_tags(
            {
                "governance_status": report["governance_status"],
                "label_policy_version": policy.version,
                "source_checksum_md5": source_checksum,
            }
        )
        mlflow.log_params(
            {
                "policy_version": policy.version,
                "observation_horizon_days": policy.observation_horizon_days,
                "sla_days": policy.sla_days,
                "train_cases": len(split.train),
                "validation_cases": len(split.validation),
                "test_cases": len(split.test),
                "random_seed": RANDOM_SEED,
            }
        )
        mlflow.log_metrics(
            {
                **{f"validation_{key}": value for key, value in validation_metrics.items()},
                **{f"test_{key}": value for key, value in test_metrics.items()},
            }
        )
        prediction_example = np.asarray(pipeline.predict_proba(train_x.head(5)))
        signature = infer_signature(train_x.head(5), prediction_example)
        model_info = mlflow.sklearn.log_model(
            sk_model=pipeline,
            name="sla_risk_model",
            registered_model_name=model_name if register_model else None,
            signature=signature,
            input_example=train_x.head(5),
            serialization_format="cloudpickle",
            pyfunc_predict_fn="predict_proba",
            metadata={
                "governance_status": report["governance_status"],
                "policy_version": policy.version,
                "source_checksum_md5": source_checksum,
            },
        )
        report["mlflow"]["run_id"] = run.info.run_id
        version = model_info.registered_model_version
        if register_model and version is not None:
            report["mlflow"]["model_version"] = int(version)
            client = MlflowClient(tracking_uri=tracking_uri)
            client.set_model_version_tag(
                model_name,
                str(version),
                "governance_status",
                "candidate" if acceptance["passed"] else "acceptance_failed",
            )
            if acceptance["passed"]:
                client.set_registered_model_alias(model_name, "candidate", str(version))
                report["mlflow"]["alias"] = "candidate"

        (output_dir / "training_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        (output_dir / "feature_importance.json").write_text(
            json.dumps(feature_importance, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        mlflow.log_artifact(str(output_dir / "training_report.json"), "governance")
        mlflow.log_artifact(str(output_dir / "feature_importance.json"), "governance")
    return report


def train_from_database(
    database_url: str,
    output_dir: Path,
    *,
    tracking_uri: str,
    experiment_name: str,
    model_name: str,
    register_model: bool = True,
    policy: ModelPolicy = DEFAULT_MODEL_POLICY,
) -> dict[str, Any]:
    import_id, checksum = latest_completed_import(database_url)
    engine = create_engine(database_url)
    try:
        frame = load_training_frame(engine, import_id, policy)
    finally:
        engine.dispose()
    return train_candidate(
        frame,
        import_id=import_id,
        source_checksum=checksum,
        output_dir=output_dir,
        tracking_uri=tracking_uri,
        experiment_name=experiment_name,
        model_name=model_name,
        register_model=register_model,
        policy=policy,
    )
