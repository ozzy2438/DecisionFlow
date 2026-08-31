"""Evidence-producing data quality gate for ingested BPI event data."""

from typing import Any
from uuid import UUID

from psycopg import Connection

from decisionflow.constants import (
    BPI_2019_EXPECTED_ACTIVITIES,
    BPI_2019_EXPECTED_CASES,
    BPI_2019_EXPECTED_EVENTS,
    BPI_2019_MD5,
)


class NoCompletedImportError(RuntimeError):
    pass


def verify_import(connection: Connection[Any], import_id: UUID | None = None) -> dict[str, Any]:
    if import_id is None:
        row = connection.execute(
            """
            SELECT id, source_checksum_md5, actual_case_count, actual_event_count
            FROM dataset_imports
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """
        ).fetchone()
    else:
        row = connection.execute(
            """
            SELECT id, source_checksum_md5, actual_case_count, actual_event_count
            FROM dataset_imports
            WHERE id = %s AND status = 'completed'
            """,
            (import_id,),
        ).fetchone()
    if row is None:
        raise NoCompletedImportError("No completed dataset import was found")

    selected_id, checksum, recorded_cases, recorded_events = row
    counts = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM p2p_cases WHERE import_id = %s),
            (SELECT COUNT(*) FROM p2p_events WHERE import_id = %s),
            (SELECT COUNT(DISTINCT activity) FROM p2p_events WHERE import_id = %s),
            (SELECT MIN(event_time) FROM p2p_events WHERE import_id = %s),
            (SELECT MAX(event_time) FROM p2p_events WHERE import_id = %s)
        """,
        (selected_id, selected_id, selected_id, selected_id, selected_id),
    ).fetchone()
    assert counts is not None
    case_count, event_count, activity_count, min_event_time, max_event_time = counts

    integrity = connection.execute(
        """
        SELECT
            (SELECT COUNT(*) FROM p2p_events
             WHERE import_id = %s AND (activity IS NULL OR activity = '' OR event_time IS NULL)),
            (SELECT COUNT(*) FROM p2p_events e
             LEFT JOIN p2p_cases c ON c.case_id = e.case_id
             WHERE e.import_id = %s AND c.case_id IS NULL),
            (SELECT COUNT(*) FROM p2p_cases c
             LEFT JOIN (
                 SELECT case_id, COUNT(*) AS actual_events
                 FROM p2p_events WHERE import_id = %s GROUP BY case_id
             ) e ON e.case_id = c.case_id
             WHERE c.import_id = %s AND c.event_count <> COALESCE(e.actual_events, 0))
        """,
        (selected_id, selected_id, selected_id, selected_id),
    ).fetchone()
    assert integrity is not None
    invalid_required_events, orphan_events, case_event_count_mismatches = integrity

    checks = {
        "recorded_counts_match_tables": (
            recorded_cases == case_count and recorded_events == event_count
        ),
        "required_event_fields_present": invalid_required_events == 0,
        "referential_integrity": orphan_events == 0,
        "case_event_counts_match": case_event_count_mismatches == 0,
    }
    official_source = checksum == BPI_2019_MD5
    if official_source:
        checks.update(
            {
                "official_case_count": case_count == BPI_2019_EXPECTED_CASES,
                "official_event_count": event_count == BPI_2019_EXPECTED_EVENTS,
                "official_activity_count": activity_count == BPI_2019_EXPECTED_ACTIVITIES,
            }
        )

    warnings: list[str] = []
    if min_event_time is not None and min_event_time.year < 2018:
        warnings.append(
            "The source contains pre-2018 timestamps; preserve them in raw data and apply an "
            "explicit modeling cohort policy downstream."
        )
    if max_event_time is not None and max_event_time.year > 2019:
        warnings.append(
            "The source contains post-2019 timestamps; preserve them in raw data and apply an "
            "explicit modeling cohort policy downstream."
        )

    return {
        "passed": all(checks.values()),
        "import_id": str(selected_id),
        "official_source": official_source,
        "checksum_md5": checksum,
        "grain": "one row per PO line item in p2p_cases; one row per XES event in p2p_events",
        "profile": {
            "cases": case_count,
            "events": event_count,
            "activities": activity_count,
            "min_event_time": min_event_time,
            "max_event_time": max_event_time,
        },
        "integrity": {
            "invalid_required_events": invalid_required_events,
            "orphan_events": orphan_events,
            "case_event_count_mismatches": case_event_count_mismatches,
        },
        "checks": checks,
        "warnings": warnings,
    }
