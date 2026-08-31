"""Idempotent first-run bootstrap for the complete local DecisionFlow stack."""

import json
from pathlib import Path
from typing import Any

from mlflow import MlflowClient
from mlflow.exceptions import MlflowException

from decisionflow.constants import BPI_2019_DOWNLOAD_URL, BPI_2019_MD5
from decisionflow.db import connect
from decisionflow.download import download_bpi_2019
from decisionflow.ingest import ingest_xes
from decisionflow.model_policy import DEFAULT_MODEL_POLICY
from decisionflow.process_mining import discover_process
from decisionflow.quality import verify_import
from decisionflow.training import train_from_database


def _current_process_artifact(
    output_dir: Path, *, import_id: str, source_checksum: str
) -> dict[str, Any] | None:
    summary_path = output_dir / "process_summary.json"
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if (
        payload.get("status") != "MEASURED_FROM_BPI_2019"
        or payload.get("import_id") != import_id
        or payload.get("source_checksum_md5") != source_checksum
        or payload.get("selection") != "all imported cases"
    ):
        return None
    return dict(payload)


def _current_candidate(
    tracking_uri: str, *, model_name: str, source_checksum: str
) -> dict[str, Any] | None:
    client = MlflowClient(tracking_uri=tracking_uri)
    try:
        version = client.get_model_version_by_alias(model_name, "candidate")
        if not version.run_id:
            return None
        run = client.get_run(version.run_id)
    except MlflowException:
        return None
    tags = run.data.tags
    if (
        version.tags.get("governance_status") != "candidate"
        or tags.get("source_checksum_md5") != source_checksum
        or tags.get("label_policy_version") != DEFAULT_MODEL_POLICY.version
    ):
        return None
    return {
        "reused": True,
        "model_name": model_name,
        "model_version": str(version.version),
        "alias": "candidate",
        "run_id": version.run_id,
        "governance_status": "CANDIDATE_NOT_PRODUCTION_APPROVED",
    }


def bootstrap_stack(
    *,
    database_url: str,
    xes_path: Path,
    process_output_dir: Path,
    model_output_dir: Path,
    tracking_uri: str,
    experiment_name: str,
    model_name: str,
) -> dict[str, Any]:
    """Make a fresh Docker volume runnable while preserving idempotency on restarts."""

    download = download_bpi_2019(xes_path)
    with connect(database_url) as connection:
        import_id = ingest_xes(
            connection,
            xes_path,
            source_uri=BPI_2019_DOWNLOAD_URL,
            expected_md5=BPI_2019_MD5,
        )
        quality = verify_import(connection, import_id)
    if not quality["passed"]:
        raise RuntimeError("BPI 2019 data quality gate failed")

    source_checksum = str(quality["checksum_md5"])
    process = _current_process_artifact(
        process_output_dir,
        import_id=str(import_id),
        source_checksum=source_checksum,
    )
    process_reused = process is not None
    if process is None:
        process = discover_process(database_url, process_output_dir)

    model = _current_candidate(
        tracking_uri,
        model_name=model_name,
        source_checksum=source_checksum,
    )
    if model is None:
        training = train_from_database(
            database_url,
            model_output_dir,
            tracking_uri=tracking_uri,
            experiment_name=experiment_name,
            model_name=model_name,
        )
        if not training["acceptance_gate"]["passed"]:
            raise RuntimeError("LightGBM candidate acceptance gate failed")
        model = {
            "reused": False,
            "model_name": model_name,
            "model_version": str(training["mlflow"]["model_version"]),
            "alias": training["mlflow"]["alias"],
            "run_id": training["mlflow"]["run_id"],
            "governance_status": training["governance_status"],
        }

    return {
        "status": "READY",
        "download": download,
        "data_quality": quality,
        "process": {
            "reused": process_reused,
            "cases": process["cases"],
            "events": process["events"],
            "activities": process["activities"],
            "variants": process["variants"],
        },
        "model": model,
    }
