from __future__ import annotations

from typing import Any

import httpx

try:
    from datahub.emitter.rest_emitter import DataHubRestEmitter

    HAS_DATAHUB_SDK = True
except ImportError:
    HAS_DATAHUB_SDK = False

from kronika.ports import DataHubWriter


class DataHubWriteError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(
            f"DataHub Write Error ({status_code}): {message}"
            if status_code
            else f"DataHub Write Error: {message}"
        )


class HttpDataHubWriter(DataHubWriter):
    def __init__(
        self,
        server_url: str = "http://localhost:8080",
        token: str | None = None,
        timeout: float = 30.0,
        mock_mode: bool = False,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.mock_mode = mock_mode
        self.incidents_written: list[dict[str, Any]] = []
        self.annotations_written: list[dict[str, Any]] = []
        self._seen_events: set[str] = set()

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def create_incident(self, urn: str, title: str, description: str, event_id: str) -> None:
        dedup_key = f"incident:{event_id}:{urn}"
        if dedup_key in self._seen_events:
            return
        self._seen_events.add(dedup_key)

        record = {
            "urn": urn,
            "title": title,
            "description": description,
            "event_id": event_id,
        }
        self.incidents_written.append(record)

        if self.mock_mode:
            return

        if HAS_DATAHUB_SDK:
            try:
                emitter = DataHubRestEmitter(gms_server=self.server_url, token=self.token)
                emitter.test_connection()
            except Exception:
                pass

        query = """
        mutation createIncident($input: CreateIncidentInput!) {
            createIncident(input: $input)
        }
        """
        variables = {
            "input": {
                "resourceUrn": urn,
                "type": "OPERATIONAL",
                "title": title,
                "description": description,
            }
        }
        url = f"{self.server_url}/api/graphql"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    url, json={"query": query, "variables": variables}, headers=self._headers()
                )
                if resp.status_code != 200:
                    raise DataHubWriteError(resp.text, resp.status_code)
        except httpx.RequestError as exc:
            raise DataHubWriteError(f"Connection failed: {exc}") from exc

    def add_annotation(self, urn: str, key: str, value: str, event_id: str) -> None:
        dedup_key = f"annotation:{event_id}:{urn}:{key}"
        if dedup_key in self._seen_events:
            return
        self._seen_events.add(dedup_key)

        record = {
            "urn": urn,
            "key": key,
            "value": value,
            "event_id": event_id,
        }
        self.annotations_written.append(record)

        if self.mock_mode:
            return

        url = f"{self.server_url}/api/v2/aspects?action=ingestProposal"
        proposal = {
            "proposal": {
                "entityUrn": urn,
                "entityType": "dataset",
                "aspectName": "kronikaAnnotation",
                "changeType": "UPSERT",
                "aspect": {
                    "contentType": "application/json",
                    "value": f'{{"key": "{key}", "value": "{value}", "eventId": "{event_id}"}}',
                },
            }
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=proposal, headers=self._headers())
                if resp.status_code not in (200, 201):
                    raise DataHubWriteError(resp.text, resp.status_code)
        except httpx.RequestError as exc:
            raise DataHubWriteError(f"Connection failed: {exc}") from exc
