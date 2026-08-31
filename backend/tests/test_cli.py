from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

import decisionflow.cli as cli


@contextmanager
def _fake_connect(_database_url: str) -> Iterator[object]:
    yield object()


def test_download_command_prints_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        cli,
        "download_bpi_2019",
        lambda path, force: {"path": str(path), "downloaded": not force},
    )

    result = cli.main(["download", "--destination", "/tmp/example.xes"])

    assert result == 0
    assert '"path": "/tmp/example.xes"' in capsys.readouterr().out


def test_ingest_command_returns_quality_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import_id = uuid4()
    observed: dict[str, Any] = {}

    def fake_ingest(_connection: object, path: Path, **kwargs: object) -> UUID:
        observed.update({"path": path, **kwargs})
        progress = kwargs["on_progress"]
        assert callable(progress)
        progress(2, 5)
        return import_id

    monkeypatch.setattr(cli, "connect", _fake_connect)
    monkeypatch.setattr(cli, "ingest_xes", fake_ingest)
    monkeypatch.setattr(
        cli,
        "verify_import",
        lambda _connection, selected_id: {"passed": True, "import_id": str(selected_id)},
    )

    result = cli.main(
        [
            "ingest",
            "--xes",
            "/tmp/example.xes",
            "--database-url",
            "postgresql://example/decisionflow_test",
            "--expected-md5",
            "",
            "--replace",
        ]
    )
    captured = capsys.readouterr()

    assert result == 0
    assert observed["replace"] is True
    assert observed["expected_md5"] is None
    assert "cases=2 events=5" in captured.err
    assert str(import_id) in captured.out


def test_verify_command_returns_nonzero_when_gate_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import_id = uuid4()
    monkeypatch.setattr(cli, "connect", _fake_connect)
    monkeypatch.setattr(
        cli,
        "verify_import",
        lambda _connection, selected_id: {
            "passed": False,
            "import_id": str(selected_id),
            "at": datetime(2018, 1, 1, tzinfo=UTC),
        },
    )

    result = cli.main(
        [
            "verify",
            "--database-url",
            "postgresql://example/decisionflow_test",
            "--import-id",
            str(import_id),
        ]
    )

    assert result == 1
    assert "2018-01-01T00:00:00+00:00" in capsys.readouterr().out


def test_process_discovery_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import decisionflow.process_mining as process_mining

    monkeypatch.setattr(
        process_mining,
        "discover_process",
        lambda database_url, output_dir, max_cases: {
            "database_url": database_url,
            "output_dir": str(output_dir),
            "max_cases": max_cases,
        },
    )

    result = cli.main(
        [
            "process-discover",
            "--database-url",
            "postgresql://example/decisionflow_test",
            "--output-dir",
            "/tmp/process",
            "--max-cases",
            "10",
        ]
    )

    assert result == 0
    assert '"max_cases": 10' in capsys.readouterr().out


def test_train_model_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import decisionflow.training as training

    monkeypatch.setattr(
        training,
        "train_from_database",
        lambda *_args, **_kwargs: {"acceptance_gate": {"passed": True}},
    )

    result = cli.main(
        [
            "train-model",
            "--database-url",
            "postgresql://example/decisionflow_test",
            "--tracking-uri",
            "sqlite:////tmp/mlflow.db",
            "--no-register",
        ]
    )

    assert result == 0
    assert '"passed": true' in capsys.readouterr().out
