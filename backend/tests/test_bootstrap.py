from contextlib import nullcontext
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from decisionflow import bootstrap

IMPORT_ID = UUID("00000000-0000-0000-0000-000000000019")


def _common_mocks(monkeypatch: pytest.MonkeyPatch, *, passed: bool = True) -> None:
    monkeypatch.setattr(
        bootstrap,
        "download_bpi_2019",
        lambda path: {"path": str(path), "downloaded": False},
    )
    monkeypatch.setattr(bootstrap, "connect", lambda _url: nullcontext(object()))
    monkeypatch.setattr(bootstrap, "ingest_xes", lambda *_args, **_kwargs: IMPORT_ID)
    monkeypatch.setattr(
        bootstrap,
        "verify_import",
        lambda *_args: {
            "passed": passed,
            "checksum_md5": "4eb909242351193a61e1c15b9c3cc814",
        },
    )


def test_bootstrap_reuses_matching_process_and_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _common_mocks(monkeypatch)
    process_dir = tmp_path / "process"
    process_dir.mkdir()
    (process_dir / "process_summary.json").write_text(
        """{
          "status": "MEASURED_FROM_BPI_2019",
          "import_id": "00000000-0000-0000-0000-000000000019",
          "source_checksum_md5": "4eb909242351193a61e1c15b9c3cc814",
          "selection": "all imported cases",
          "cases": 251734,
          "events": 1595923,
          "activities": 42,
          "variants": 11973
        }""",
        encoding="utf-8",
    )
    candidate = {
        "reused": True,
        "model_name": "risk-model",
        "model_version": "2",
        "alias": "candidate",
        "run_id": "run-2",
        "governance_status": "CANDIDATE_NOT_PRODUCTION_APPROVED",
    }
    monkeypatch.setattr(bootstrap, "_current_candidate", lambda *_args, **_kwargs: candidate)
    monkeypatch.setattr(
        bootstrap,
        "discover_process",
        lambda *_args, **_kwargs: pytest.fail("matching process artifact must be reused"),
    )
    monkeypatch.setattr(
        bootstrap,
        "train_from_database",
        lambda *_args, **_kwargs: pytest.fail("matching candidate must be reused"),
    )

    report = bootstrap.bootstrap_stack(
        database_url="postgresql://test",
        xes_path=tmp_path / "source.xes",
        process_output_dir=process_dir,
        model_output_dir=tmp_path / "model",
        tracking_uri="http://mlflow",
        experiment_name="experiment",
        model_name="risk-model",
    )

    assert report["status"] == "READY"
    assert report["process"]["reused"] is True
    assert report["model"] == candidate


def test_bootstrap_builds_missing_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _common_mocks(monkeypatch)
    monkeypatch.setattr(
        bootstrap,
        "discover_process",
        lambda *_args, **_kwargs: {
            "cases": 251734,
            "events": 1595923,
            "activities": 42,
            "variants": 11973,
        },
    )
    monkeypatch.setattr(bootstrap, "_current_candidate", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bootstrap,
        "train_from_database",
        lambda *_args, **_kwargs: {
            "acceptance_gate": {"passed": True},
            "governance_status": "CANDIDATE_NOT_PRODUCTION_APPROVED",
            "mlflow": {
                "model_version": 3,
                "alias": "candidate",
                "run_id": "run-3",
            },
        },
    )

    report = bootstrap.bootstrap_stack(
        database_url="postgresql://test",
        xes_path=tmp_path / "source.xes",
        process_output_dir=tmp_path / "process",
        model_output_dir=tmp_path / "model",
        tracking_uri="http://mlflow",
        experiment_name="experiment",
        model_name="risk-model",
    )

    assert report["process"]["reused"] is False
    assert report["model"]["reused"] is False
    assert report["model"]["model_version"] == "3"


def test_bootstrap_stops_on_failed_data_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _common_mocks(monkeypatch, passed=False)

    with pytest.raises(RuntimeError, match="data quality"):
        bootstrap.bootstrap_stack(
            database_url="postgresql://test",
            xes_path=tmp_path / "source.xes",
            process_output_dir=tmp_path / "process",
            model_output_dir=tmp_path / "model",
            tracking_uri="http://mlflow",
            experiment_name="experiment",
            model_name="risk-model",
        )


def test_current_candidate_requires_matching_governance_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Client:
        def __init__(self, *, tracking_uri: str) -> None:
            assert tracking_uri == "http://mlflow"

        def get_model_version_by_alias(self, name: str, alias: str) -> Any:
            assert (name, alias) == ("risk-model", "candidate")
            return type(
                "Version",
                (),
                {
                    "run_id": "run-2",
                    "version": "2",
                    "tags": {"governance_status": "candidate"},
                },
            )()

        def get_run(self, run_id: str) -> Any:
            assert run_id == "run-2"
            tags = {
                "source_checksum_md5": "checksum",
                "label_policy_version": bootstrap.DEFAULT_MODEL_POLICY.version,
            }
            return type("Run", (), {"data": type("Data", (), {"tags": tags})()})()

    monkeypatch.setattr(bootstrap, "MlflowClient", Client)

    candidate = bootstrap._current_candidate(
        "http://mlflow", model_name="risk-model", source_checksum="checksum"
    )
    assert candidate is not None
    assert candidate["model_version"] == "2"
