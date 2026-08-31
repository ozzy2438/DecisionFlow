from datetime import UTC, datetime
from pathlib import Path

import pytest
from lxml import etree

from decisionflow.xes import XESValidationError, iter_traces, parse_trace

FIXTURE = Path(__file__).parent / "fixtures" / "mini_bpi_2019.xes"


def test_streams_traces_and_preserves_source_order() -> None:
    traces = list(iter_traces(FIXTURE))

    assert [trace.case_id for trace in traces] == ["4500000010_00010", "4500000020_00020"]
    assert [event.index for event in traces[0].events] == [0, 1, 2]
    assert traces[0].events[1].activity == "Record Goods Receipt"
    assert traces[1].events[1].resource is None


def test_parses_case_types_and_event_time() -> None:
    trace = next(iter_traces(FIXTURE))

    assert trace.attributes["GR-Based Inv. Verif."] is True
    assert trace.attributes["Goods Receipt"] is True
    assert trace.first_event_at == datetime(2018, 1, 2, 8, 0, tzinfo=UTC)
    assert trace.last_event_at == datetime(2018, 1, 10, 10, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        (
            "<trace><event><string key='concept:name' value='A'/>"
            "<date key='time:timestamp' value='2018-01-01T00:00:00Z'/></event></trace>",
            "no concept:name",
        ),
        ("<trace><string key='concept:name' value='case-1'/></trace>", "contains no events"),
        (
            "<trace><string key='concept:name' value='case-1'/><event>"
            "<date key='time:timestamp' value='2018-01-01T00:00:00Z'/></event></trace>",
            "event 0 has no concept:name",
        ),
        (
            "<trace><string key='concept:name' value='case-1'/><event>"
            "<string key='concept:name' value='A'/>"
            "<date key='time:timestamp' value='not-a-date'/></event></trace>",
            "Invalid time:timestamp",
        ),
        (
            "<trace><string key='concept:name' value='case-1'/>"
            "<boolean key='Goods Receipt' value='maybe'/><event>"
            "<string key='concept:name' value='A'/>"
            "<date key='time:timestamp' value='2018-01-01T00:00:00Z'/></event></trace>",
            "Invalid XES boolean",
        ),
    ],
)
def test_rejects_invalid_trace_contract(xml: str, message: str) -> None:
    trace = etree.fromstring(xml.encode())

    with pytest.raises(XESValidationError, match=message):
        parse_trace(trace, trace_number=1)


def test_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        next(iter_traces(tmp_path / "missing.xes"))
