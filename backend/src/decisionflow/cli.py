"""Operational command-line interface for DecisionFlow."""

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from decisionflow.config import get_settings
from decisionflow.constants import (
    BPI_2019_DOWNLOAD_URL,
    BPI_2019_MD5,
    DEFAULT_XES_PATH,
)
from decisionflow.db import connect
from decisionflow.download import download_bpi_2019
from decisionflow.ingest import ingest_xes
from decisionflow.quality import verify_import


def _json_default(value: object) -> str:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=_json_default))


def _progress(cases: int, events: int) -> None:
    print(f"ingest progress: cases={cases:,} events={events:,}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="decisionflow")
    subparsers = parser.add_subparsers(dest="command", required=True)

    download = subparsers.add_parser("download", help="download and verify BPI 2019")
    download.add_argument("--destination", type=Path, default=DEFAULT_XES_PATH)
    download.add_argument("--force", action="store_true")

    ingest = subparsers.add_parser("ingest", help="ingest an XES file into PostgreSQL")
    ingest.add_argument("--xes", type=Path, default=DEFAULT_XES_PATH)
    ingest.add_argument("--database-url", default=None)
    ingest.add_argument("--expected-md5", default=BPI_2019_MD5)
    ingest.add_argument("--source-uri", default=BPI_2019_DOWNLOAD_URL)
    ingest.add_argument("--batch-size", type=int, default=1_000)
    ingest.add_argument(
        "--replace",
        action="store_true",
        help="legacy flag; completed event-store generations cannot be replaced in place",
    )

    verify = subparsers.add_parser("verify", help="run event-store data quality gates")
    verify.add_argument("--database-url", default=None)
    verify.add_argument("--import-id", type=UUID, default=None)

    process = subparsers.add_parser(
        "process-discover", help="discover the real P2P process with PM4Py"
    )
    process.add_argument("--database-url", default=None)
    process.add_argument("--output-dir", type=Path, default=Path("/app/data/artifacts/process"))
    process.add_argument("--max-cases", type=int, default=None)

    train = subparsers.add_parser(
        "train-model", help="train and track the governed LightGBM SLA risk candidate"
    )
    train.add_argument("--database-url", default=None)
    train.add_argument("--output-dir", type=Path, default=Path("/app/data/artifacts/model"))
    train.add_argument("--tracking-uri", default=None)
    train.add_argument("--experiment-name", default=None)
    train.add_argument("--model-name", default=None)
    train.add_argument("--no-register", action="store_true")

    bootstrap = subparsers.add_parser(
        "bootstrap", help="idempotently prepare data, process artifacts, and the model candidate"
    )
    bootstrap.add_argument("--database-url", default=None)
    bootstrap.add_argument("--xes", type=Path, default=DEFAULT_XES_PATH)
    bootstrap.add_argument(
        "--process-output-dir", type=Path, default=Path("/app/data/artifacts/process")
    )
    bootstrap.add_argument(
        "--model-output-dir", type=Path, default=Path("/app/data/artifacts/model")
    )
    bootstrap.add_argument("--tracking-uri", default=None)
    bootstrap.add_argument("--experiment-name", default=None)
    bootstrap.add_argument("--model-name", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "download":
        _print_json(download_bpi_2019(args.destination, force=args.force))
        return 0

    database_url = args.database_url or get_settings().database_url
    if args.command == "bootstrap":
        from decisionflow.bootstrap import bootstrap_stack

        settings = get_settings()
        report = bootstrap_stack(
            database_url=database_url,
            xes_path=args.xes,
            process_output_dir=args.process_output_dir,
            model_output_dir=args.model_output_dir,
            tracking_uri=args.tracking_uri or settings.mlflow_tracking_uri,
            experiment_name=args.experiment_name or settings.mlflow_experiment,
            model_name=args.model_name or settings.mlflow_model_name,
        )
        _print_json(report)
        return 0

    if args.command == "ingest":
        with connect(database_url) as connection:
            import_id = ingest_xes(
                connection,
                args.xes,
                source_uri=args.source_uri,
                expected_md5=args.expected_md5 or None,
                replace=args.replace,
                batch_size=args.batch_size,
                on_progress=_progress,
            )
            report = verify_import(connection, import_id)
        _print_json(report)
        return 0 if report["passed"] else 1

    if args.command == "verify":
        with connect(database_url) as connection:
            report = verify_import(connection, args.import_id)
        _print_json(report)
        return 0 if report["passed"] else 1

    if args.command == "process-discover":
        from decisionflow.process_mining import discover_process

        report = discover_process(database_url, args.output_dir, max_cases=args.max_cases)
        _print_json(report)
        return 0

    if args.command == "train-model":
        from decisionflow.training import train_from_database

        settings = get_settings()
        report = train_from_database(
            database_url,
            args.output_dir,
            tracking_uri=args.tracking_uri or settings.mlflow_tracking_uri,
            experiment_name=args.experiment_name or settings.mlflow_experiment,
            model_name=args.model_name or settings.mlflow_model_name,
            register_model=not args.no_register,
        )
        _print_json(report)
        return 0 if report["acceptance_gate"]["passed"] else 1

    raise AssertionError(f"Unhandled command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
