from __future__ import annotations

from kronika.ports import DataHubReader
from kronika.types import EventKind, MetadataEvent


class EventListener:
    def __init__(self, reader: DataHubReader) -> None:
        self.reader = reader
        self._seen_events: set[tuple[str, str, str]] = set()

    def poll_events(self) -> list[MetadataEvent]:
        events: list[MetadataEvent] = []
        raw_datasets = self.reader.list_datasets()

        for d in raw_datasets:
            urn = d.get("urn")
            if not isinstance(urn, str):
                continue

            raw_assertions = d.get("assertions", [])
            for assertion in raw_assertions:
                status = assertion.get("status")
                occurred_at = assertion.get("occurred_at", "2026-07-25T12:00:00Z")
                if status == "FAILED":
                    dedup_key = (EventKind.QUALITY_OBSERVATION.value, urn, occurred_at)
                    if dedup_key in self._seen_events:
                        continue
                    self._seen_events.add(dedup_key)

                    evt = MetadataEvent(
                        event_id=f"poll-{len(self._seen_events):04d}",
                        kind=EventKind.QUALITY_OBSERVATION,
                        source_urn=urn,
                        columns=frozenset(assertion.get("columns", []))
                        if assertion.get("columns")
                        else None,
                        payload=(("severity", "critical"),),
                        occurred_at=occurred_at,
                    )
                    events.append(evt)

        return events
