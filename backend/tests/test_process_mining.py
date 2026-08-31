import json
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from decisionflow.process_mining import (
    ACTIVITY_KEY,
    CASE_KEY,
    TIMESTAMP_KEY,
    discover_from_frame,
)


def _event_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            CASE_KEY: ["case-1", "case-1", "case-1", "case-2", "case-2"],
            ACTIVITY_KEY: ["Create", "Receive", "Clear", "Create", "Clear"],
            TIMESTAMP_KEY: pd.to_datetime(
                [
                    "2018-01-01T00:00:00Z",
                    "2018-01-02T00:00:00Z",
                    "2018-01-03T00:00:00Z",
                    "2018-02-01T00:00:00Z",
                    "2018-02-04T00:00:00Z",
                ],
                utc=True,
            ),
            "event_index": [0, 1, 2, 0, 1],
        }
    )


def test_discovers_and_persists_dfg(tmp_path: Path) -> None:
    report = discover_from_frame(
        _event_frame(),
        tmp_path,
        import_id=uuid4(),
        source_checksum="abc",
        max_cases=2,
    )

    assert report["cases"] == 2
    assert report["events"] == 5
    assert report["activities"] == 3
    assert report["start_activities"] == {"Create": 2}
    assert report["end_activities"] == {"Clear": 2}
    assert report["variants"] == 2
    assert (tmp_path / "dfg.svg").is_file()
    edges = json.loads((tmp_path / "dfg.json").read_text())["edges"]
    assert any(edge["source"] == "Create" and edge["target"] == "Clear" for edge in edges)


def test_rejects_missing_process_columns(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing columns"):
        discover_from_frame(pd.DataFrame({CASE_KEY: ["case-1"]}), tmp_path)
