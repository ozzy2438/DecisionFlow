"""PM4Py process discovery over the immutable PostgreSQL event store."""

import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pandas as pd
import pm4py
from sqlalchemy import Engine, create_engine, text

from decisionflow.db import connect

CASE_KEY = "case:concept:name"
ACTIVITY_KEY = "concept:name"
TIMESTAMP_KEY = "time:timestamp"


def latest_completed_import(database_url: str) -> tuple[UUID, str]:
    with connect(database_url) as connection:
        row = connection.execute(
            """
            SELECT id, source_checksum_md5
            FROM dataset_imports
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        raise RuntimeError("No completed dataset import is available")
    return UUID(str(row[0])), str(row[1])


def _load_events(engine: Engine, import_id: UUID, max_cases: int | None) -> pd.DataFrame:
    if max_cases is None:
        query = text(
            """
            SELECT case_id AS "case:concept:name", activity AS "concept:name",
                   event_time AS "time:timestamp", event_index
            FROM p2p_events
            WHERE import_id = :import_id
            ORDER BY case_id, event_time, event_index
            """
        )
        params: dict[str, Any] = {"import_id": import_id}
    else:
        if max_cases < 1:
            raise ValueError("max_cases must be positive")
        query = text(
            """
            WITH selected_cases AS (
                SELECT case_id
                FROM p2p_cases
                WHERE import_id = :import_id
                ORDER BY case_id
                LIMIT :max_cases
            )
            SELECT e.case_id AS "case:concept:name", e.activity AS "concept:name",
                   e.event_time AS "time:timestamp", e.event_index
            FROM p2p_events e
            JOIN selected_cases c ON c.case_id = e.case_id
            WHERE e.import_id = :import_id
            ORDER BY e.case_id, e.event_time, e.event_index
            """
        )
        params = {"import_id": import_id, "max_cases": max_cases}

    frame = pd.read_sql_query(query, engine, params=params)
    if frame.empty:
        raise RuntimeError("The selected import contains no events")
    frame[TIMESTAMP_KEY] = pd.to_datetime(frame[TIMESTAMP_KEY], utc=True)
    return frame


def discover_from_frame(
    frame: pd.DataFrame,
    output_dir: Path,
    *,
    import_id: UUID | None = None,
    source_checksum: str | None = None,
    max_cases: int | None = None,
) -> dict[str, Any]:
    """Discover frequency and performance DFGs and persist inspectable artifacts."""

    required = {CASE_KEY, ACTIVITY_KEY, TIMESTAMP_KEY}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"Process-mining frame is missing columns: {sorted(missing)}")

    output_dir.mkdir(parents=True, exist_ok=True)
    formatted = pm4py.format_dataframe(
        frame.copy(),
        case_id=CASE_KEY,
        activity_key=ACTIVITY_KEY,
        timestamp_key=TIMESTAMP_KEY,
    )
    dfg, start_activities, end_activities = pm4py.discover_dfg(
        formatted,
        activity_key=ACTIVITY_KEY,
        timestamp_key=TIMESTAMP_KEY,
        case_id_key=CASE_KEY,
    )
    performance_dfg, _, _ = pm4py.discover_performance_dfg(
        formatted,
        activity_key=ACTIVITY_KEY,
        timestamp_key=TIMESTAMP_KEY,
        case_id_key=CASE_KEY,
    )

    edges = []
    for (source, target), frequency in sorted(
        dfg.items(), key=lambda item: (-int(item[1]), item[0][0], item[0][1])
    ):
        performance = performance_dfg.get((source, target), {"mean": 0.0})
        mean_seconds = (
            performance.get("mean", 0.0) if isinstance(performance, dict) else performance
        )
        edges.append(
            {
                "source": source,
                "target": target,
                "frequency": int(frequency),
                "mean_seconds": float(mean_seconds),
            }
        )
    variants = (
        formatted.groupby(CASE_KEY, sort=False)[ACTIVITY_KEY].agg(tuple).value_counts(sort=True)
    )
    top_variants = [
        {"activities": list(activities), "cases": int(count)}
        for activities, count in variants.head(20).items()
    ]
    case_count = int(formatted[CASE_KEY].nunique())
    event_count = len(formatted)
    summary: dict[str, Any] = {
        "status": "MEASURED_FROM_BPI_2019",
        "import_id": str(import_id) if import_id is not None else None,
        "source_checksum_md5": source_checksum,
        "selection": "all imported cases" if max_cases is None else f"first {max_cases} case IDs",
        "cases": case_count,
        "events": event_count,
        "activities": int(formatted[ACTIVITY_KEY].nunique()),
        "directly_follows_edges": len(edges),
        "variants": len(variants),
        "start_activities": dict(sorted(start_activities.items(), key=lambda item: -item[1])),
        "end_activities": dict(sorted(end_activities.items(), key=lambda item: -item[1])),
        "top_edges": edges[:20],
        "top_variants": top_variants,
        "min_event_time": formatted[TIMESTAMP_KEY].min().isoformat(),
        "max_event_time": formatted[TIMESTAMP_KEY].max().isoformat(),
        "method": "PM4Py frequency DFG plus mean-performance DFG",
    }

    (output_dir / "dfg.json").write_text(
        json.dumps({"edges": edges}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (output_dir / "process_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    pm4py.save_vis_dfg(
        dfg,
        start_activities,
        end_activities,
        str(output_dir / "dfg.svg"),
        graph_title="DecisionFlow — BPI 2019 Purchase-to-Pay DFG",
    )
    return summary


def discover_process(
    database_url: str, output_dir: Path, *, max_cases: int | None = None
) -> dict[str, Any]:
    import_id, checksum = latest_completed_import(database_url)
    engine = create_engine(database_url)
    try:
        frame = _load_events(engine, import_id, max_cases)
        return discover_from_frame(
            frame,
            output_dir,
            import_id=import_id,
            source_checksum=checksum,
            max_cases=max_cases,
        )
    finally:
        engine.dispose()
