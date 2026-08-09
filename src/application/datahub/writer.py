from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

try:
    from datahub.sdk import DataHubClient
    from datahub_agent_context import set_client

    HAS_AGENT_CONTEXT = True
except ImportError:
    HAS_AGENT_CONTEXT = False

from kronika.ports import DataHubWriter

log = logging.getLogger("kronika.datahub.writer")


_GOVERNANCE_RULES_PROPERTY_KEY = "kronikaGovernanceRules"


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
        self.policy_rules_written: list[dict[str, Any]] = []
        self._seen_events: set[str] = set()

        if HAS_AGENT_CONTEXT and not mock_mode:
            try:
                sdk_client = DataHubClient(server=self.server_url, token=self.token)
                set_client(sdk_client)
                log.info("HttpDataHubWriter: DataHub Agent Context Kit bound successfully.")
            except Exception as exc:
                log.warning("HttpDataHubWriter: Agent Context Kit binding warning: %s", exc)
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

    def _get_dataset_properties(self, urn: str) -> dict[str, Any]:
        encoded = quote(urn, safe="")
        url = f"{self.server_url}/openapi/v3/entity/dataset/{encoded}/datasetProperties"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers())
        except httpx.RequestError as exc:
            log.error("_get_dataset_properties: connection failed | url=%s error=%s", url, exc)
            raise DataHubWriteError(f"Connection failed: {exc}") from exc

        if resp.status_code == 404:
            return {}
        if resp.status_code != 200:
            log.error(
                "_get_dataset_properties: read failed | status=%d body=%.200s",
                resp.status_code,
                resp.text,
            )
            raise DataHubWriteError(resp.text, resp.status_code)

        value = resp.json().get("value")
        return value if isinstance(value, dict) else {}

    def _put_dataset_properties(self, urn: str, properties: dict[str, Any]) -> None:
        url = f"{self.server_url}/aspects?action=ingestProposal"
        proposal = {
            "proposal": {
                "entityUrn": urn,
                "entityType": "dataset",
                "aspectName": "datasetProperties",
                "changeType": "UPSERT",
                "aspect": {
                    "contentType": "application/json",
                    "value": json.dumps(properties),
                },
            }
        }
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(url, json=proposal, headers=self._headers())
        except httpx.RequestError as exc:
            log.error("_put_dataset_properties: connection failed | url=%s error=%s", url, exc)
            raise DataHubWriteError(f"Connection failed: {exc}") from exc

        if resp.status_code not in (200, 201):
            log.error(
                "_put_dataset_properties: write failed | status=%d body=%.200s",
                resp.status_code,
                resp.text,
            )
            raise DataHubWriteError(resp.text, resp.status_code)

    def _merge_custom_properties(self, urn: str, updates: dict[str, str]) -> None:
        """Read-modify-write `updates` into `datasetProperties.customProperties`,
        preserving every other field already on the aspect (name, description, ...)."""
        current = self._get_dataset_properties(urn)
        custom_properties = dict(current.get("customProperties") or {})
        custom_properties.update(updates)
        current["customProperties"] = custom_properties
        self._put_dataset_properties(urn, current)

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

        query = """
        mutation raiseIncident($input: RaiseIncidentInput!) {
            raiseIncident(input: $input)
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
                body = resp.json()
                if body.get("errors"):
                    log.error("create_incident: GraphQL errors | errors=%s", body["errors"])
                    raise DataHubWriteError(str(body["errors"]))
                incident_urn = body.get("data", {}).get("raiseIncident")
                log.info(
                    "create_incident: written successfully | urn=%s incident_urn=%s",
                    urn,
                    incident_urn,
                )
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

        log.info(
            "add_annotation: writing annotation to DataHub | urn=%s key=%s",
            urn,
            key,
        )
        self._merge_custom_properties(urn, {key: value})
        log.info("add_annotation: written successfully | urn=%s key=%s", urn, key)

    def add_tag(self, urn: str, tag_urn: str = "urn:li:tag:critical") -> None:
        if self.mock_mode:
            log.debug("add_tag: mock mode — skipping live write")
            return

        query = """
        mutation addTag($input: TagAssociationInput!) {
            addTag(input: $input)
        }
        """
        variables = {
            "input": {
                "tagUrn": tag_urn,
                "resourceUrn": urn,
            }
        }
        url = f"{self.server_url}/api/graphql"
        log.info("add_tag: writing tag to DataHub | url=%s urn=%s tag=%s", url, urn, tag_urn)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.post(
                    url, json={"query": query, "variables": variables}, headers=self._headers()
                )
                if resp.status_code != 200:
                    log.error(
                        "add_tag: write failed | status=%d body=%.200s",
                        resp.status_code,
                        resp.text,
                    )
                else:
                    log.info("add_tag: written successfully | urn=%s tag=%s", urn, tag_urn)
        except httpx.RequestError as exc:
            log.error("add_tag: connection failed | url=%s error=%s", url, exc)

    def write_policy_rule(
        self,
        rule_id: str,
        dimension: int,
        predicate: str,
        scope_urn: str,
        glossary_urn: str | None = None,
    ) -> None:
        rule = {
            "rule_id": rule_id,
            "dimension": dimension,
            "predicate": predicate,
            "scope_urn": scope_urn,
            "glossary_urn": glossary_urn,
        }
        self.policy_rules_written.append(rule)
        log.info(
            "write_policy_rule: rule queued | rule_id=%s scope_urn=%s predicate=%r",
            rule_id,
            scope_urn,
            predicate,
        )

        if self.mock_mode:
            log.debug("write_policy_rule: mock mode — skipping live write")
            return

        current = self._get_dataset_properties(scope_urn)
        custom_properties = dict(current.get("customProperties") or {})
        existing_raw = custom_properties.get(_GOVERNANCE_RULES_PROPERTY_KEY)
        try:
            existing_rules = json.loads(existing_raw) if existing_raw else []
        except (ValueError, TypeError):
            log.warning(
                "write_policy_rule: existing governance rules JSON unparsable, "
                "replacing | scope_urn=%s",
                scope_urn,
            )
            existing_rules = []
        if not isinstance(existing_rules, list):
            existing_rules = []

        merged_rules = [r for r in existing_rules if r.get("rule_id") != rule_id]
        merged_rules.append(rule)
        custom_properties[_GOVERNANCE_RULES_PROPERTY_KEY] = json.dumps(merged_rules)
        current["customProperties"] = custom_properties
        self._put_dataset_properties(scope_urn, current)
        log.info(
            "write_policy_rule: written successfully | rule_id=%s scope_urn=%s",
            rule_id,
            scope_urn,
        )
