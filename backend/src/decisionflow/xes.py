"""Memory-bounded parser for IEEE XES event logs."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path

from lxml import etree

type AttributeValue = str | int | float | bool | None


class XESValidationError(ValueError):
    """Raised when a trace cannot satisfy the event-store contract."""


@dataclass(frozen=True, slots=True)
class EventRecord:
    index: int
    activity: str
    event_time: datetime
    resource: str | None
    lifecycle: str | None
    attributes: Mapping[str, AttributeValue]


@dataclass(frozen=True, slots=True)
class TraceRecord:
    case_id: str
    attributes: Mapping[str, AttributeValue]
    events: tuple[EventRecord, ...]

    @property
    def first_event_at(self) -> datetime:
        return min(event.event_time for event in self.events)

    @property
    def last_event_at(self) -> datetime:
        return max(event.event_time for event in self.events)


def _local_name(element: etree._Element) -> str:
    return etree.QName(element).localname


def _parse_value(kind: str, value: str | None) -> AttributeValue:
    if value is None:
        return None
    if kind == "boolean":
        normalized = value.strip().lower()
        if normalized not in {"true", "false"}:
            raise XESValidationError(f"Invalid XES boolean value: {value!r}")
        return normalized == "true"
    if kind == "int":
        return int(value)
    if kind == "float":
        parsed = float(value)
        return parsed if isfinite(parsed) else None
    # Dates remain ISO-8601 strings inside JSON. The canonical event timestamp is parsed below.
    return value


def _attributes(parent: etree._Element) -> dict[str, AttributeValue]:
    result: dict[str, AttributeValue] = {}
    for child in parent:
        kind = _local_name(child)
        if kind == "event":
            continue
        key = child.get("key")
        if key is None or kind not in {"string", "date", "int", "float", "boolean", "id"}:
            continue
        result[key] = _parse_value(kind, child.get("value"))
    return result


def _parse_datetime(value: AttributeValue, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise XESValidationError(f"Missing or invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise XESValidationError(f"Invalid {field}: {value!r}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _optional_text(value: AttributeValue) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_trace(trace: etree._Element, *, trace_number: int) -> TraceRecord:
    trace_attributes = _attributes(trace)
    case_id = _optional_text(trace_attributes.get("concept:name"))
    if case_id is None:
        raise XESValidationError(f"Trace {trace_number} has no concept:name")

    events: list[EventRecord] = []
    for event_element in trace:
        if _local_name(event_element) != "event":
            continue
        attributes = _attributes(event_element)
        activity = _optional_text(attributes.get("concept:name"))
        if activity is None:
            raise XESValidationError(f"Trace {case_id!r}, event {len(events)} has no concept:name")
        events.append(
            EventRecord(
                index=len(events),
                activity=activity,
                event_time=_parse_datetime(
                    attributes.get("time:timestamp"), field="time:timestamp"
                ),
                resource=_optional_text(attributes.get("org:resource")),
                lifecycle=_optional_text(attributes.get("lifecycle:transition")),
                attributes=attributes,
            )
        )

    if not events:
        raise XESValidationError(f"Trace {case_id!r} contains no events")
    return TraceRecord(case_id=case_id, attributes=trace_attributes, events=tuple(events))


def iter_traces(path: Path) -> Iterator[TraceRecord]:
    """Yield one trace at a time and release its XML tree immediately."""

    if not path.is_file():
        raise FileNotFoundError(path)

    context = etree.iterparse(
        str(path),
        events=("end",),
        tag="{*}trace",
        huge_tree=True,
        recover=False,
    )
    for trace_number, (_, trace) in enumerate(context, start=1):
        yield parse_trace(trace, trace_number=trace_number)
        parent = trace.getparent()
        trace.clear()
        if parent is not None:
            while trace.getprevious() is not None:
                del parent[0]
