"""Idempotent, streaming ingestion from XES into the PostgreSQL event store."""

from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb

from decisionflow.constants import BPI_2019_DATASET_NAME, BPI_2019_DOI
from decisionflow.download import md5_file
from decisionflow.xes import TraceRecord, iter_traces

PARSER_VERSION = "xes-stream-v1"
INGEST_ADVISORY_LOCK = 1_904_201_900

CASE_COPY_SQL = """
COPY p2p_cases (
    case_id, import_id, purchasing_document, item, item_type,
    gr_based_invoice_verification, goods_receipt_required, source_system,
    document_category, company, spend_classification, spend_area, sub_spend_area,
    vendor, vendor_name, document_type, item_category, first_event_at,
    last_event_at, event_count, attributes
) FROM STDIN
"""

EVENT_COPY_SQL = """
COPY p2p_events (
    import_id, case_id, event_index, activity, event_time, resource, lifecycle, attributes
) FROM STDIN
"""


class ExistingDatasetError(RuntimeError):
    """Raised when replacing a different completed dataset was not explicitly authorized."""


def _text(attributes: dict[str, Any] | Any, key: str) -> str | None:
    value = attributes.get(key)
    return value if isinstance(value, str) and value else None


def _boolean(attributes: dict[str, Any] | Any, key: str) -> bool | None:
    value = attributes.get(key)
    return value if isinstance(value, bool) else None


def _case_row(trace: TraceRecord, import_id: UUID) -> tuple[object, ...]:
    attributes = trace.attributes
    return (
        trace.case_id,
        import_id,
        _text(attributes, "Purchasing Document"),
        _text(attributes, "Item"),
        _text(attributes, "Item Type"),
        _boolean(attributes, "GR-Based Inv. Verif."),
        _boolean(attributes, "Goods Receipt"),
        _text(attributes, "Source"),
        _text(attributes, "Doc. Category name"),
        _text(attributes, "Company"),
        _text(attributes, "Spend classification text"),
        _text(attributes, "Spend area text"),
        _text(attributes, "Sub spend area text"),
        _text(attributes, "Vendor"),
        _text(attributes, "Name"),
        _text(attributes, "Document Type"),
        _text(attributes, "Item Category"),
        trace.first_event_at,
        trace.last_event_at,
        len(trace.events),
        Jsonb(dict(attributes)),
    )


def _event_rows(trace: TraceRecord, import_id: UUID) -> list[tuple[object, ...]]:
    return [
        (
            import_id,
            trace.case_id,
            event.index,
            event.activity,
            event.event_time,
            event.resource,
            event.lifecycle,
            Jsonb(dict(event.attributes)),
        )
        for event in trace.events
    ]


def _copy_batch(
    connection: Connection[Any],
    cases: Sequence[tuple[object, ...]],
    events: Sequence[tuple[object, ...]],
) -> None:
    with connection.cursor() as cursor:
        with cursor.copy(CASE_COPY_SQL) as copy:
            for row in cases:
                copy.write_row(row)
        with cursor.copy(EVENT_COPY_SQL) as copy:
            for row in events:
                copy.write_row(row)
    connection.commit()


def ingest_xes(
    connection: Connection[Any],
    path: Path,
    *,
    source_uri: str = f"https://doi.org/{BPI_2019_DOI}",
    expected_md5: str | None = None,
    replace: bool = False,
    batch_size: int = 1_000,
    on_progress: Callable[[int, int], None] | None = None,
) -> UUID:
    """Ingest an XES file, safely resuming after failed imports.

    Completed imports are immutable. Re-importing the same checksum is an idempotent no-op.
    The legacy ``replace`` switch is rejected once a completed generation exists; replacing
    provenance-bearing history requires provisioning a new event-store generation.
    """

    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    path = path.resolve()
    checksum = md5_file(path)
    if expected_md5 is not None and checksum != expected_md5:
        raise ValueError(f"XES checksum mismatch: expected {expected_md5}, got {checksum}")

    connection.execute("SELECT pg_advisory_lock(%s)", (INGEST_ADVISORY_LOCK,))
    completed = connection.execute(
        """
        SELECT id, source_checksum_md5
        FROM dataset_imports
        WHERE status = 'completed'
        ORDER BY completed_at DESC
        """
    ).fetchall()
    if completed and replace:
        connection.rollback()
        raise ExistingDatasetError(
            "Completed imports are immutable; provision a new event-store generation instead."
        )
    for existing_id, existing_checksum in completed:
        if existing_checksum == checksum:
            connection.commit()
            return UUID(str(existing_id))

    if completed:
        connection.rollback()
        raise ExistingDatasetError(
            "A different completed dataset is already loaded; provision a new event-store "
            "generation instead."
        )

    # Any non-completed row is residue from an interrupted run. Cascades remove partial batches.
    connection.execute("DELETE FROM dataset_imports WHERE status <> 'completed'")

    import_id = uuid4()
    connection.execute(
        """
        INSERT INTO dataset_imports (
            id, source_name, source_uri, source_checksum_md5, source_size_bytes,
            parser_version, status, started_at, import_metadata
        ) VALUES (%s, %s, %s, %s, %s, %s, 'running', %s, %s)
        """,
        (
            import_id,
            BPI_2019_DATASET_NAME,
            source_uri,
            checksum,
            path.stat().st_size,
            PARSER_VERSION,
            datetime.now(UTC),
            Jsonb({"filename": path.name}),
        ),
    )
    connection.commit()

    case_count = 0
    event_count = 0
    case_batch: list[tuple[object, ...]] = []
    event_batch: list[tuple[object, ...]] = []

    try:
        for trace in iter_traces(path):
            case_batch.append(_case_row(trace, import_id))
            event_batch.extend(_event_rows(trace, import_id))
            case_count += 1
            event_count += len(trace.events)
            if len(case_batch) >= batch_size:
                _copy_batch(connection, case_batch, event_batch)
                case_batch.clear()
                event_batch.clear()
                if on_progress is not None:
                    on_progress(case_count, event_count)
        if case_batch:
            _copy_batch(connection, case_batch, event_batch)
            if on_progress is not None:
                on_progress(case_count, event_count)

        connection.execute(
            """
            UPDATE dataset_imports
            SET status = 'completed', completed_at = %s,
                actual_case_count = %s, actual_event_count = %s
            WHERE id = %s
            """,
            (datetime.now(UTC), case_count, event_count, import_id),
        )
        connection.commit()
    except Exception as exc:
        connection.rollback()
        connection.execute(
            """
            UPDATE dataset_imports
            SET status = 'failed', completed_at = %s, error_message = %s,
                actual_case_count = %s, actual_event_count = %s
            WHERE id = %s
            """,
            (datetime.now(UTC), str(exc), case_count, event_count, import_id),
        )
        connection.commit()
        raise

    return import_id
