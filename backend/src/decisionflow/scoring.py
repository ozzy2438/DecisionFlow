"""Governed model loading and point-in-time SLA risk scoring."""

import hashlib
import json
from dataclasses import dataclass
from math import isfinite
from threading import Lock
from typing import Any, Protocol

import mlflow
import mlflow.sklearn
import numpy as np
import pandas as pd
from mlflow import MlflowClient

from decisionflow.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS, NUMERIC_FEATURES


class ModelGovernanceError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RiskPrediction:
    score: float
    model_name: str
    model_version: str
    model_alias: str
    model_policy_version: str


class RiskScorer(Protocol):
    def score(self, features: dict[str, Any]) -> RiskPrediction: ...


def normalize_features(features: dict[str, Any]) -> dict[str, Any]:
    unknown = set(features).difference(FEATURE_COLUMNS)
    missing = set(FEATURE_COLUMNS).difference(features)
    if unknown:
        raise ValueError(f"Unknown model features: {sorted(unknown)}")
    if missing:
        raise ValueError(f"Missing model features: {sorted(missing)}")

    normalized: dict[str, Any] = {}
    for name in NUMERIC_FEATURES:
        value = features[name]
        if value is None:
            normalized[name] = None
            continue
        number = float(value)
        if not isfinite(number):
            raise ValueError(f"Feature {name!r} must be finite or null")
        normalized[name] = number
    for name in CATEGORICAL_FEATURES:
        value = features[name]
        normalized[name] = "__missing__" if value is None else str(value)[:500]
    return normalized


def feature_hash(features: dict[str, Any]) -> str:
    canonical = json.dumps(normalize_features(features), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class MlflowRiskScorer:
    """Load only the Registry candidate alias and record its immutable version per score."""

    def __init__(
        self,
        tracking_uri: str,
        model_name: str,
        *,
        alias: str = "candidate",
        expected_policy_version: str = "sla-proxy-v1",
    ) -> None:
        self.tracking_uri = tracking_uri
        self.model_name = model_name
        self.alias = alias
        self.expected_policy_version = expected_policy_version
        self._lock = Lock()
        self._loaded_version: str | None = None
        self._model: Any = None

    def _model_for_current_alias(self) -> tuple[Any, str]:
        mlflow.set_tracking_uri(self.tracking_uri)
        client = MlflowClient(tracking_uri=self.tracking_uri)
        version = client.get_model_version_by_alias(self.model_name, self.alias)
        if version.status != "READY":
            raise ModelGovernanceError(f"Registry model version is not READY: {version.status}")
        if version.tags.get("governance_status") != "candidate":
            raise ModelGovernanceError(
                "Registry version lacks the required candidate governance tag"
            )
        if not version.run_id:
            raise ModelGovernanceError("Registry version has no training-run provenance")
        run_tags = client.get_run(version.run_id).data.tags
        if run_tags.get("label_policy_version") != self.expected_policy_version:
            raise ModelGovernanceError("Registry version uses an unexpected label policy")
        source_checksum = run_tags.get("source_checksum_md5", "")
        if len(source_checksum) != 32 or any(
            character not in "0123456789abcdef" for character in source_checksum.lower()
        ):
            raise ModelGovernanceError("Registry version lacks valid source-data provenance")
        version_number = str(version.version)
        with self._lock:
            if self._loaded_version != version_number:
                # Model artifacts are loaded only from this project's authenticated Registry.
                self._model = mlflow.sklearn.load_model(
                    f"models:/{self.model_name}/{version_number}"
                )
                self._loaded_version = version_number
            return self._model, version_number

    def score(self, features: dict[str, Any]) -> RiskPrediction:
        normalized = normalize_features(features)
        row = pd.DataFrame([normalized], columns=FEATURE_COLUMNS)
        for name in NUMERIC_FEATURES:
            row[name] = pd.to_numeric(row[name], errors="coerce").astype("float64")
        for name in CATEGORICAL_FEATURES:
            row[name] = row[name].astype("string").fillna("__missing__").astype("object")
        model, version = self._model_for_current_alias()
        probability = float(np.asarray(model.predict_proba(row))[0, 1])
        if not isfinite(probability) or not 0 <= probability <= 1:
            raise ModelGovernanceError("Model returned an invalid probability")
        return RiskPrediction(
            score=probability,
            model_name=self.model_name,
            model_version=version,
            model_alias=self.alias,
            model_policy_version=self.expected_policy_version,
        )
