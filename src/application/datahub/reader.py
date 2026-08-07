from __future__ import annotations

import logging
from typing import Any

import httpx

from kronika.ports import DataHubReader
from kronika.types import ValidationError

log = logging.getLogger("kronika.datahub.reader")


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
            searchAcrossEntities(input: {types: [DATASET], start: 0, count: 1000}) {
                searchResults {
                    entity {
                        urn
                        type
                        ... on Dataset {
                            name
                            platform { name }
                            tags { tags { tag { urn name } } }
                            ownership { owners { owner { urn } } }
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
            for t_item in entity.get("tags", {}).get("tags", []):
                t_name = t_item.get("tag", {}).get("name")
                if t_name:
                    tags.append(t_name)

            owners = entity.get("ownership", {}).get("owners", [])
            owner_urn = owners[0].get("owner", {}).get("urn") if owners else None

            domain_obj = entity.get("domain", {}).get("domain")
            domain_urn = domain_obj.get("urn") if isinstance(domain_obj, dict) else None

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
            searchAcrossLineage(input: {direction: DOWNSTREAM, start: 0, count: 1000}) {
                searchResults {
                    entity { urn }
                    lineageRelationshipTypes
                }
            }
        }
        """
        data = self._post_graphql(query)
        results = data.get("searchAcrossLineage", {}).get("searchResults", [])
        edges: list[dict[str, Any]] = []

        for res in results:
            src = res.get("entity", {}).get("urn")
            dst = res.get("destination", {}).get("urn") if "destination" in res else None
            if src and dst:
                if not src.startswith("urn:li:") or not dst.startswith("urn:li:"):
                    raise ValidationError("reader.edge", "invalid_urn")
                edges.append(
                    {
                        "src": src,
                        "dst": dst,
                        "kind": "IDENTITY",
                        "columns": None,
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

    def list_policy_rules(self) -> list[dict[str, Any]]:
        if self._mock_data is not None:
            rules = self._mock_data.get("policy_rules", [])
            log.debug("list_policy_rules: returned %d rules (mock)", len(rules))
            return rules
        log.debug("list_policy_rules: live mode, returning empty")
        return []
