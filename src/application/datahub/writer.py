from __future__ import annotations

import logging
from typing import Any

import httpx

try:
    from datahub.emitter.rest_emitter import DataHubRestEmitter

    HAS_DATAHUB_SDK = True
except ImportError:
    HAS_DATAHUB_SDK = False

from kronika.ports import DataHubWriter

log = logging.getLogger("kronika.datahub.writer")


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
        mode = "MOCK" if mock_mode else "LIVE"
        log.info(
            "HttpDataHubWriter initialized in %s mode | server_url=%s",
            mode,
            self.server_url,
        )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def create_incident(self, urn: str, title: str, description: str, event_id: str) -> None:
        dedup_key = f"incident:{event_id}:{urn}"
        if dedup_key in self._seen_events:
            log.debug(
                "create_incident: duplicate suppressed | event_id=%s urn=%s",
                event_id,
                urn,
            )
            return
        self._seen_events.add(dedup_key)

        record = {
            "urn": urn,
            "title": title,
            "description": description,
            "event_id": event_id,
        }
        self.incidents_written.append(record)
        log.info(
            "create_incident: incident queued | event_id=%s urn=%s title=%r",
            event_id,
            urn,
            title,
        )

        if self.mock_mode:
            log.debug("create_incident: mock mode — skipping live write")
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
        log.info("create_incident: writing incident to DataHub | url=%s urn=%s", url, urn)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    url, json={"query": query, "variables": variables}, headers=self._headers()
                )
                if resp.status_code != 200:
                    log.error(
                        "create_incident: write failed | status=%d body=%.200s",
                        resp.status_code,
                        resp.text,
                    )
                    raise DataHubWriteError(resp.text, resp.status_code)
                log.info("create_incident: written successfully | urn=%s", urn)
        except httpx.RequestError as exc:
            log.error("create_incident: connection failed | url=%s error=%s", url, exc)
            raise DataHubWriteError(f"Connection failed: {exc}") from exc

    def add_annotation(self, urn: str, key: str, value: str, event_id: str) -> None:
        dedup_key = f"annotation:{event_id}:{urn}:{key}"
        if dedup_key in self._seen_events:
            log.debug(
                "add_annotation: duplicate suppressed | event_id=%s urn=%s key=%s",
                event_id,
                urn,
                key,
            )
            return
        self._seen_events.add(dedup_key)

        record = {
            "urn": urn,
            "key": key,
            "value": value,
            "event_id": event_id,
        }
        self.annotations_written.append(record)
        log.info(
            "add_annotation: annotation queued | event_id=%s urn=%s key=%s value=%s",
            event_id,
            urn,
            key,
            value,
        )

        if self.mock_mode:
            log.debug("add_annotation: mock mode — skipping live write")
            return

        url = f"{self.server_url}/api/v2/aspects?action=ingestProposal"
        log.info("add_annotation: writing annotation to DataHub | url=%s urn=%s", url, urn)
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
                    log.error(
                        "add_annotation: write failed | status=%d body=%.200s",
                        resp.status_code,
                        resp.text,
                    )
                    raise DataHubWriteError(resp.text, resp.status_code)
                log.info("add_annotation: written successfully | urn=%s key=%s", urn, key)
        except httpx.RequestError as exc:
            log.error("add_annotation: connection failed | url=%s error=%s", url, exc)
            raise DataHubWriteError(f"Connection failed: {exc}") from exc
