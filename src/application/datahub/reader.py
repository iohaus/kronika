from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import httpx

from kronika.ports import DataHubReader
from kronika.types import ValidationError

log = logging.getLogger("kronika.datahub.reader")

# Must match application.datahub.writer._GOVERNANCE_RULES_PROPERTY_KEY — governance rules
# round-trip through datasetProperties.customProperties (DataHub's registered escape hatch
# for free-form string metadata; unregistered custom aspect names are rejected outright).
_GOVERNANCE_RULES_PROPERTY_KEY = "kronikaGovernanceRules"


class DataHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(
            f"DataHub API Error ({status_code}): {message}"
            if status_code
            else f"DataHub API Error: {message}"
        )


class ConfigurationError(Exception):
    def __init__(self, capability: str) -> None:
        super().__init__(f"DataHub permission/capability probe failed: missing '{capability}'")


def capability_probe(reader: DataHubReader) -> None:
    try:
        reader.list_datasets()
    except Exception as exc:
        raise ConfigurationError("list_entities") from exc


try:
    from datahub.sdk import DataHubClient
    from datahub_agent_context import set_client

    HAS_AGENT_CONTEXT = True
except ImportError:
    HAS_AGENT_CONTEXT = False


class HttpDataHubReader(DataHubReader):
    def __init__(
        self,
        server_url: str = "http://localhost:8080",
        token: str | None = None,
        timeout: float = 30.0,
        mock_data: dict[str, list[dict[str, Any]]] | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self._mock_data = mock_data

        if HAS_AGENT_CONTEXT and self._mock_data is None:
            try:
                sdk_client = DataHubClient(server=self.server_url, token=self.token)
                set_client(sdk_client)
                log.info("HttpDataHubReader: DataHub Agent Context Kit bound successfully.")
            except Exception as exc:
                log.warning("HttpDataHubReader: Agent Context Kit binding warning: %s", exc)

        if mock_data is not None:
            log.info(
                "HttpDataHubReader initialized in MOCK mode | datasets=%d edges=%d",
                len(mock_data.get("datasets", [])),
                len(mock_data.get("edges", [])),
            )
        else:
            log.info(
                "HttpDataHubReader initialized in LIVE mode | server_url=%s timeout=%.1fs",
                self.server_url,
                self.timeout,
            )

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post_graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._mock_data is not None:
            return {}

        url = f"{self.server_url}/api/graphql"
        log.debug("_post_graphql: POST %s", url)
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(
                    url,
                    json={"query": query, "variables": variables or {}},
                    headers=self._headers(),
                )
                if response.status_code != 200:
                    log.error(
                        "_post_graphql: non-200 response | status=%d body=%.200s",
                        response.status_code,
                        response.text,
                    )
                    raise DataHubAPIError(response.text, response.status_code)
                data = response.json()
                if "errors" in data:
                    log.error("_post_graphql: GraphQL errors | errors=%s", data["errors"])
                    raise DataHubAPIError(str(data["errors"]))
                log.debug("_post_graphql: success | keys=%s", list(data.get("data", {}).keys()))
                return data.get("data", {})
        except httpx.RequestError as exc:
            log.error("_post_graphql: connection failed | url=%s error=%s", url, exc)
            raise DataHubAPIError(f"Connection failed: {exc}") from exc

    def list_datasets(self) -> list[dict[str, Any]]:
        log.debug("list_datasets: fetching datasets")
        if self._mock_data is not None:
            datasets = self._mock_data.get("datasets", [])
            for d in datasets:
                urn = d.get("urn")
                if not urn or not isinstance(urn, str) or not urn.startswith("urn:li:"):
                    log.error("list_datasets: invalid URN in mock data | record=%s", d)
                    raise ValidationError("reader.datasets.urn", "invalid_urn")
            log.info("list_datasets: returned %d datasets (mock)", len(datasets))
            return datasets

        query = """
        query listDatasets {
            searchAcrossEntities(input: {types: [DATASET], query: "*", start: 0, count: 1000}) {
                searchResults {
                    entity {
                        urn
                        type
                        ... on Dataset {
                            name
                            platform { name }
                            tags { tags { tag { urn name } } }
                            ownership {
                                owners {
                                    owner {
                                        ... on CorpUser { urn }
                                        ... on CorpGroup { urn }
                                    }
                                }
                            }
                            domain { domain { urn } }
                        }
                    }
                }
            }
        }
        """
        data = self._post_graphql(query)
        search_res = data.get("searchAcrossEntities", {}).get("searchResults", [])
        datasets: list[dict[str, Any]] = []

        for item in search_res:
            entity = item.get("entity", {})
            urn = entity.get("urn")
            if not urn or not isinstance(urn, str) or not urn.startswith("urn:li:"):
                raise ValidationError("reader.datasets.urn", "invalid_urn")

            tags: list[str] = []
            tags_obj = entity.get("tags")
            if isinstance(tags_obj, dict):
                for t_item in tags_obj.get("tags", []):
                    if isinstance(t_item, dict):
                        t_name = (
                            t_item.get("tag", {}).get("name")
                            if isinstance(t_item.get("tag"), dict)
                            else None
                        )
                        if t_name:
                            tags.append(t_name)

            owners_obj = entity.get("ownership")
            owner_urn = None
            if isinstance(owners_obj, dict):
                owners = owners_obj.get("owners", [])
                if owners and isinstance(owners[0], dict):
                    owner_obj = owners[0].get("owner")
                    if isinstance(owner_obj, dict):
                        owner_urn = owner_obj.get("urn")

            domain_container = entity.get("domain")
            domain_urn = None
            if isinstance(domain_container, dict):
                domain_obj = domain_container.get("domain")
                if isinstance(domain_obj, dict):
                    domain_urn = domain_obj.get("urn")

            datasets.append(
                {
                    "urn": urn,
                    "tags": tags,
                    "owner_urn": owner_urn,
                    "domain_urn": domain_urn,
                }
            )

        log.info("list_datasets: returned %d datasets (live)", len(datasets))
        return datasets

    def list_lineage_edges(self) -> list[dict[str, Any]]:
        log.debug("list_lineage_edges: fetching edges")
        if self._mock_data is not None:
            edges = self._mock_data.get("edges", [])
            log.info("list_lineage_edges: returned %d edges (mock)", len(edges))
            return edges

        query = """
        query getLineage {
            searchAcrossEntities(input: {types: [DATASET], query: "*", start: 0, count: 1000}) {
                searchResults {
                    entity {
                        urn
                        ... on Dataset {
                            schemaMetadata {
                                fields {
                                    fieldPath
                                }
                            }
                            lineage(input: {direction: DOWNSTREAM, start: 0, count: 100}) {
                                relationships {
                                    entity {
                                        urn
                                    }
                                    type
                                }
                            }
                        }
                    }
                }
            }
        }
        """
        data = self._post_graphql(query)
        search_res = data.get("searchAcrossEntities", {}).get("searchResults", [])
        edges: list[dict[str, Any]] = []

        schema_fields_map: dict[str, list[str]] = {}
        for item in search_res:
            entity = item.get("entity", {})
            urn = entity.get("urn")
            if urn and isinstance(urn, str):
                fields: list[str] = []
                schema_meta = entity.get("schemaMetadata")
                if isinstance(schema_meta, dict):
                    f_list = schema_meta.get("fields", [])
                    if isinstance(f_list, list):
                        for f in f_list:
                            if isinstance(f, dict) and f.get("fieldPath"):
                                fields.append(f["fieldPath"])
                if fields:
                    schema_fields_map[urn] = fields

        for item in search_res:
            entity = item.get("entity", {})
            src = entity.get("urn")
            lineage_obj = entity.get("lineage") if isinstance(entity, dict) else None
            if isinstance(lineage_obj, dict):
                rels = lineage_obj.get("relationships", [])
                for rel in rels:
                    if isinstance(rel, dict):
                        dst_entity = rel.get("entity")
                        dst = dst_entity.get("urn") if isinstance(dst_entity, dict) else None
                        if src and dst:
                            if not src.startswith("urn:li:") or not dst.startswith("urn:li:"):
                                raise ValidationError("reader.edge", "invalid_urn")

                            dst_fields = schema_fields_map.get(dst)

                            if dst_fields:
                                kind = "PROJECTION"
                                cols_list = dst_fields
                            else:
                                kind = "IDENTITY"
                                cols_list = None

                            edges.append(
                                {
                                    "src": src,
                                    "dst": dst,
                                    "kind": kind,
                                    "columns": cols_list,
                                }
                            )

        log.info("list_lineage_edges: returned %d edges (live)", len(edges))
        return edges

    def list_glossary_terms(self) -> list[dict[str, Any]]:
        if self._mock_data is not None:
            terms = self._mock_data.get("glossary_terms", [])
            log.debug("list_glossary_terms: returned %d terms (mock)", len(terms))
            return terms
        log.debug("list_glossary_terms: live mode, returning empty")
        return []

    def _get_custom_properties(self, urn: str) -> dict[str, str]:
        """Read `datasetProperties.customProperties` for one dataset via OpenAPI v3.
        Returns `{}` if the dataset has no `datasetProperties` aspect yet."""
        encoded = quote(urn, safe="")
        url = f"{self.server_url}/openapi/v3/entity/dataset/{encoded}/datasetProperties"
        try:
            with httpx.Client(timeout=self.timeout) as client:
                resp = client.get(url, headers=self._headers())
        except httpx.RequestError as exc:
            log.error("_get_custom_properties: connection failed | url=%s error=%s", url, exc)
            raise DataHubAPIError(f"Connection failed: {exc}") from exc

        if resp.status_code == 404:
            return {}
        if resp.status_code != 200:
            log.error(
                "_get_custom_properties: read failed | status=%d body=%.200s",
                resp.status_code,
                resp.text,
            )
            raise DataHubAPIError(resp.text, resp.status_code)

        value = resp.json().get("value")
        custom_properties = value.get("customProperties") if isinstance(value, dict) else None
        return custom_properties if isinstance(custom_properties, dict) else {}

    def list_policy_rules(self) -> list[dict[str, Any]]:
        if self._mock_data is not None:
            rules = self._mock_data.get("policy_rules", [])
            log.debug("list_policy_rules: returned %d rules (mock)", len(rules))
            return rules

        log.debug("list_policy_rules: live mode, reading governance rules from datasets")
        rules: list[dict[str, Any]] = []
        for dataset in self.list_datasets():
            urn = dataset.get("urn")
            if not isinstance(urn, str):
                continue
            custom_properties = self._get_custom_properties(urn)
            raw_rules = custom_properties.get(_GOVERNANCE_RULES_PROPERTY_KEY)
            if not raw_rules:
                continue
            try:
                parsed = json.loads(raw_rules)
            except (ValueError, TypeError):
                log.warning("list_policy_rules: unparsable governance rules JSON | urn=%s", urn)
                continue
            if isinstance(parsed, list):
                rules.extend(r for r in parsed if isinstance(r, dict))

        log.info("list_policy_rules: returned %d rules (live)", len(rules))
        return rules
