import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from alembic.config import Config
from psycopg.errors import RaiseException

from alembic import command
from decisionflow.db import connect
from decisionflow.ingest import ExistingDatasetError, ingest_xes, md5_file
from decisionflow.process_mining import discover_process
from decisionflow.quality import NoCompletedImportError, verify_import

FIXTURE = Path(__file__).parent / "fixtures" / "mini_bpi_2019.xes"


def _test_database_url() -> str:
    database_url = os.getenv("TEST_DATABASE_URL", "")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = urlparse(database_url).path.removeprefix("/")
    if not database_name.endswith("_test"):
        pytest.skip("Refusing to run destructive integration setup outside a *_test database")
    return database_url


@pytest.mark.integration
def test_ingest_is_complete_idempotent_and_immutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url = _test_database_url()
    backend_root = Path(__file__).parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "head")

    with connect(database_url) as connection:
        connection.execute("TRUNCATE p2p_events, p2p_cases, dataset_imports CASCADE")
        connection.commit()
        with pytest.raises(NoCompletedImportError):
            verify_import(connection)
        anomalous_fixture = tmp_path / "mini-bpi-2019-anomalous.xes"
        anomalous_fixture.write_text(
            FIXTURE.read_text(encoding="utf-8")
            .replace("2018-01-02T08:00:00.000+00:00", "2017-01-02T08:00:00.000+00:00")
            .replace("2018-02-20T12:15:00Z", "2020-02-20T12:15:00Z"),
            encoding="utf-8",
        )
        progress: list[tuple[int, int]] = []
        first_import_id = ingest_xes(
            connection,
            anomalous_fixture,
            source_uri="fixture://mini-bpi-2019",
            batch_size=1,
            on_progress=lambda cases, events: progress.append((cases, events)),
        )
        second_import_id = ingest_xes(
            connection,
            anomalous_fixture,
            source_uri="fixture://mini-bpi-2019",
            batch_size=1,
        )
        report = verify_import(connection, first_import_id)
        latest_report = verify_import(connection)

        different_fixture = backend_root / "tests" / "fixtures" / "different-mini.xes"
        different_fixture.write_text(
            FIXTURE.read_text().replace("4500000010_00010", "4500000099_00010"),
            encoding="utf-8",
        )
        try:
            with pytest.raises(ExistingDatasetError):
                ingest_xes(connection, different_fixture, source_uri="fixture://different")
            with pytest.raises(ValueError, match="batch_size"):
                ingest_xes(connection, FIXTURE, batch_size=0)
            with pytest.raises(ValueError, match="checksum mismatch"):
                ingest_xes(connection, FIXTURE, expected_md5="0" * 32)
            with pytest.raises(ExistingDatasetError, match="immutable"):
                ingest_xes(connection, anomalous_fixture, replace=True)
        finally:
            different_fixture.unlink(missing_ok=True)

        with pytest.raises(RaiseException, match="immutable"):
            connection.execute(
                "UPDATE dataset_imports SET source_name = 'tampered' WHERE id = %s",
                (first_import_id,),
            )
        connection.rollback()
        with pytest.raises(RaiseException, match="immutable"):
            connection.execute(
                """
                UPDATE p2p_events SET event_time = '2019-01-01T00:00:00Z'
                WHERE case_id = %s AND event_index = 0
                """,
                ("4500000010_00010",),
            )
        connection.rollback()

        monkeypatch.setattr("decisionflow.quality.BPI_2019_MD5", md5_file(anomalous_fixture))
        official_mismatch_report = verify_import(connection, first_import_id)

    process_report = discover_process(database_url, tmp_path / "process", max_cases=2)

    assert first_import_id == second_import_id
    assert progress == [(1, 3), (2, 5)]
    assert report["passed"] is True
    assert latest_report["passed"] is True
    assert report["official_source"] is False
    assert report["profile"]["cases"] == 2
    assert report["profile"]["events"] == 5
    assert report["profile"]["activities"] == 4
    assert official_mismatch_report["passed"] is False
    assert official_mismatch_report["official_source"] is True
    assert len(official_mismatch_report["warnings"]) == 2
    assert process_report["cases"] == 2
    assert (tmp_path / "process" / "dfg.svg").is_file()
