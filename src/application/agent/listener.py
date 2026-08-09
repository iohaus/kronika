from __future__ import annotations

from kronika.ports import DataHubReader
from kronika.types import EventKind, MetadataEvent


class EventListener:
    def __init__(self, reader: DataHubReader) -> None:
        self.reader = reader
        self._seen_events: set[tuple[str, str, str]] = set()

    def poll_events(self) -> list[MetadataEvent]:
        events: list[MetadataEvent] = []

        for assertion in self.reader.list_assertions():
            urn = assertion.get("dataset_urn")
            if not isinstance(urn, str) or assertion.get("status") != "FAILED":
                continue

            occurred_at = assertion.get("occurred_at") or "2026-07-25T12:00:00Z"
            dedup_key = (EventKind.QUALITY_OBSERVATION.value, urn, occurred_at)
            if dedup_key in self._seen_events:
                continue
            self._seen_events.add(dedup_key)

            cols = assertion.get("columns")
            evt = MetadataEvent(
                event_id=f"poll-{len(self._seen_events):04d}",
                kind=EventKind.QUALITY_OBSERVATION,
                source_urn=urn,
                columns=frozenset(cols) if cols else None,
                payload=(
                    ("severity", assertion.get("severity", "critical")),
                    ("source", "datahub_assertion"),
                ),
                occurred_at=occurred_at,
            )
            events.append(evt)

        return events
