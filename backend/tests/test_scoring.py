from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

import decisionflow.scoring as scoring
from decisionflow.features import CATEGORICAL_FEATURES, FEATURE_COLUMNS, NUMERIC_FEATURES
from decisionflow.scoring import (
    MlflowRiskScorer,
    ModelGovernanceError,
    feature_hash,
    normalize_features,
)


def complete_features() -> dict[str, Any]:
    values: dict[str, Any] = {name: 0 for name in NUMERIC_FEATURES}
    values.update({name: "value" for name in CATEGORICAL_FEATURES})
    return values


def test_feature_normalization_and_hash_are_order_independent() -> None:
    features = complete_features()
    reversed_features = {name: features[name] for name in reversed(FEATURE_COLUMNS)}

    assert normalize_features(features)[NUMERIC_FEATURES[0]] == 0.0
    assert feature_hash(features) == feature_hash(reversed_features)


def test_feature_contract_fails_closed() -> None:
    features = complete_features()
    features.pop(FEATURE_COLUMNS[0])
    with pytest.raises(ValueError, match="Missing model features"):
        normalize_features(features)

    features = complete_features()
    features["free_sql"] = "DROP TABLE"
    with pytest.raises(ValueError, match="Unknown model features"):
        normalize_features(features)


class _FakeModel:
    def __init__(self, probability: float = 0.73) -> None:
        self.probability = probability

    def predict_proba(self, _frame: Any) -> np.ndarray:
        return np.asarray([[1 - self.probability, self.probability]])


def test_mlflow_scorer_enforces_registry_governance_and_caches_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    version = SimpleNamespace(
        version="8", status="READY", tags={"governance_status": "candidate"}, run_id="run-8"
    )
    run = SimpleNamespace(
        data=SimpleNamespace(
            tags={
                "label_policy_version": "sla-proxy-v1",
                "source_checksum_md5": "4eb909242351193a61e1c15b9c3cc814",
            }
        )
    )
    client = SimpleNamespace(
        get_model_version_by_alias=lambda _name, _alias: version,
        get_run=lambda _run_id: run,
    )
    loads: list[str] = []
    monkeypatch.setattr(scoring, "MlflowClient", lambda tracking_uri: client)
    monkeypatch.setattr(
        scoring.mlflow.sklearn,
        "load_model",
        lambda uri: loads.append(uri) or _FakeModel(),
    )
    scorer = MlflowRiskScorer("file:///trusted", "decisionflow-model")

    first = scorer.score(complete_features())
    second = scorer.score(complete_features())

    assert first.score == pytest.approx(0.73)
    assert first.model_version == "8"
    assert second == first
    assert loads == ["models:/decisionflow-model/8"]


@pytest.mark.parametrize(
    ("status", "tags", "error"),
    [
        ("PENDING_REGISTRATION", {"governance_status": "candidate"}, "not READY"),
        ("READY", {}, "governance tag"),
    ],
)
def test_mlflow_scorer_rejects_unapproved_registry_versions(
    monkeypatch: pytest.MonkeyPatch,
    status: str,
    tags: dict[str, str],
    error: str,
) -> None:
    version = SimpleNamespace(version="9", status=status, tags=tags, run_id="run-9")
    client = SimpleNamespace(get_model_version_by_alias=lambda _name, _alias: version)
    monkeypatch.setattr(scoring, "MlflowClient", lambda tracking_uri: client)
    scorer = MlflowRiskScorer("file:///trusted", "decisionflow-model")

    with pytest.raises(ModelGovernanceError, match=error):
        scorer.score(complete_features())


def test_mlflow_scorer_rejects_invalid_probability(monkeypatch: pytest.MonkeyPatch) -> None:
    version = SimpleNamespace(
        version="10", status="READY", tags={"governance_status": "candidate"}, run_id="run-10"
    )
    run = SimpleNamespace(
        data=SimpleNamespace(
            tags={
                "label_policy_version": "sla-proxy-v1",
                "source_checksum_md5": "4eb909242351193a61e1c15b9c3cc814",
            }
        )
    )
    client = SimpleNamespace(
        get_model_version_by_alias=lambda _name, _alias: version,
        get_run=lambda _run_id: run,
    )
    monkeypatch.setattr(scoring, "MlflowClient", lambda tracking_uri: client)
    monkeypatch.setattr(scoring.mlflow.sklearn, "load_model", lambda _uri: _FakeModel(1.2))
    scorer = MlflowRiskScorer("file:///trusted", "decisionflow-model")

    with pytest.raises(ModelGovernanceError, match="invalid probability"):
        scorer.score(complete_features())


@pytest.mark.parametrize(
    ("tags", "error"),
    [
        (
            {
                "label_policy_version": "different-policy",
                "source_checksum_md5": "4eb909242351193a61e1c15b9c3cc814",
            },
            "label policy",
        ),
        ({"label_policy_version": "sla-proxy-v1"}, "source-data provenance"),
    ],
)
def test_mlflow_scorer_rejects_mismatched_run_provenance(
    monkeypatch: pytest.MonkeyPatch, tags: dict[str, str], error: str
) -> None:
    version = SimpleNamespace(
        version="11", status="READY", tags={"governance_status": "candidate"}, run_id="run-11"
    )
    run = SimpleNamespace(data=SimpleNamespace(tags=tags))
    client = SimpleNamespace(
        get_model_version_by_alias=lambda _name, _alias: version,
        get_run=lambda _run_id: run,
    )
    monkeypatch.setattr(scoring, "MlflowClient", lambda tracking_uri: client)
    scorer = MlflowRiskScorer("file:///trusted", "decisionflow-model")

    with pytest.raises(ModelGovernanceError, match=error):
        scorer.score(complete_features())
