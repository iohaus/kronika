from __future__ import annotations

import datetime
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


def _column_lineage_for_edge(
    src_urn: str, fine_grained_lineages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Filter a destination entity's raw `fineGrainedLineages` entries down to only
    those whose upstream schema fields belong to `src_urn` — DataHub's GraphQL
    `SchemaFieldRef.urn` resolves to the owning *dataset* URN (not a combined
    schemaField URN), with the column name in `path` alongside it. Matching on that
    dataset URN correctly attributes a multi-upstream FGL entry to the right edge
    even for future joins, not just this pipeline's current linear/fork topology."""
    mappings: list[dict[str, Any]] = []
    for fgl in fine_grained_lineages:
        if not isinstance(fgl, dict):
            continue
        upstreams = [u for u in fgl.get("upstreams", []) if isinstance(u, dict)]
        downstreams = [d for d in fgl.get("downstreams", []) if isinstance(d, dict)]
        src_paths = sorted({u["path"] for u in upstreams if u.get("urn") == src_urn})
        if not src_paths:
            continue
        for d in downstreams:
            dst_path = d.get("path")
            if dst_path:
                mappings.append({"dst_column": dst_path, "src_columns": src_paths})
    return mappings


def _is_view(entity: dict[str, Any]) -> bool:
    """SQL views are ingested as `Dataset` entities alongside real tables, but they're
    duplicative of the mart they mirror — excluded from Kronika's world model entirely
    rather than given their own (redundant) lineage/reasoning treatment."""
    sub_types = entity.get("subTypes")
    type_names = sub_types.get("typeNames") if isinstance(sub_types, dict) else None
    return isinstance(type_names, list) and "View" in type_names


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
                            subTypes { typeNames }
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
        skipped_views = 0

        for item in search_res:
            entity = item.get("entity", {})
            urn = entity.get("urn")
            if not urn or not isinstance(urn, str) or not urn.startswith("urn:li:"):
                raise ValidationError("reader.datasets.urn", "invalid_urn")

            if _is_view(entity):
                skipped_views += 1
                continue

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

        log.info(
            "list_datasets: returned %d datasets (live) | skipped_views=%d",
            len(datasets),
            skipped_views,
        )
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
                            subTypes { typeNames }
                            fineGrainedLineages {
                                upstreams { urn path }
                                downstreams { urn path }
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

        view_urns: set[str] = set()
        fine_grained_by_dst: dict[str, list[dict[str, Any]]] = {}
        for item in search_res:
            entity = item.get("entity", {})
            urn = entity.get("urn")
            if not urn or not isinstance(urn, str):
                continue
            if _is_view(entity):
                view_urns.add(urn)
                continue
            fgl_list = entity.get("fineGrainedLineages")
            if isinstance(fgl_list, list) and fgl_list:
                fine_grained_by_dst[urn] = fgl_list

        skipped_view_edges = 0
        edges_with_real_lineage = 0
        for item in search_res:
            entity = item.get("entity", {})
            src = entity.get("urn")
            if src in view_urns:
                continue
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

                            if dst in view_urns:
                                skipped_view_edges += 1
                                continue

                            column_lineage = _column_lineage_for_edge(
                                src, fine_grained_by_dst.get(dst, [])
                            )
                            if column_lineage:
                                kind = "PROJECTION"
                                edges_with_real_lineage += 1
                            else:
                                kind = "IDENTITY"
                                column_lineage = None

                            edges.append(
                                {
                                    "src": src,
                                    "dst": dst,
                                    "kind": kind,
                                    "column_lineage": column_lineage,
                                }
                            )

        log.info(
            "list_lineage_edges: returned %d edges (live) | with_real_column_lineage=%d "
            "fall_back_to_unknown=%d | skipped_view_edges=%d",
            len(edges),
            edges_with_real_lineage,
            len(edges) - edges_with_real_lineage,
            skipped_view_edges,
        )
        return edges

    def list_glossary_terms(self) -> list[dict[str, Any]]:
        if self._mock_data is not None:
            terms = self._mock_data.get("glossary_terms", [])
            log.debug("list_glossary_terms: returned %d terms (mock)", len(terms))
            return terms

        query = """
        query listGlossaryTerms {
            searchAcrossEntities(
                input: {types: [GLOSSARY_TERM], query: "*", start: 0, count: 1000}
            ) {
                searchResults {
                    entity {
                        urn
                        ... on GlossaryTerm {
                            hierarchicalName
                            properties { name description }
                        }
                    }
                }
            }
        }
        """
        data = self._post_graphql(query)
        search_res = data.get("searchAcrossEntities", {}).get("searchResults", [])
        terms: list[dict[str, Any]] = []

        for item in search_res:
            entity = item.get("entity", {})
            urn = entity.get("urn")
            if not urn or not isinstance(urn, str):
                continue

            properties = entity.get("properties")
            name = None
            description = None
            if isinstance(properties, dict):
                name = properties.get("name")
                description = properties.get("description")
            if not name:
                name = entity.get("hierarchicalName")

            terms.append({"urn": urn, "name": name, "description": description})

        log.info("list_glossary_terms: returned %d terms (live)", len(terms))
        return terms

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

    def list_assertions(self) -> list[dict[str, Any]]:
        """Real DataHub assertion run results — the perception source for
        `EventListener.poll_events()`. Kept separate from `list_datasets()`: it's only
        needed by the poller, not by every `build_context()` call on the hot path."""
        if self._mock_data is not None:
            assertions = self._mock_data.get("assertions", [])
            log.debug("list_assertions: returned %d assertions (mock)", len(assertions))
            return assertions

        query = """
        query listAssertions {
            searchAcrossEntities(
                input: {types: [ASSERTION], query: "*", start: 0, count: 200}
            ) {
                searchResults {
                    entity {
                        urn
                        ... on Assertion {
                            info {
                                customAssertion { field { path } }
                            }
                            dataset { urn }
                            runEvents(status: COMPLETE, limit: 5) {
                                runEvents {
                                    timestampMillis
                                    result { type rowCount unexpectedCount severity }
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
        results: list[dict[str, Any]] = []

        for item in search_res:
            entity = item.get("entity", {})
            dataset_obj = entity.get("dataset")
            dataset_urn = dataset_obj.get("urn") if isinstance(dataset_obj, dict) else None
            if not dataset_urn:
                continue

            field_path = None
            info = entity.get("info")
            if isinstance(info, dict):
                custom_assertion = info.get("customAssertion")
                if isinstance(custom_assertion, dict) and isinstance(
                    custom_assertion.get("field"), dict
                ):
                    field_path = custom_assertion["field"].get("path")

            run_events_obj = entity.get("runEvents")
            run_events = (
                run_events_obj.get("runEvents", []) if isinstance(run_events_obj, dict) else []
            )
            for run in run_events:
                if not isinstance(run, dict):
                    continue
                result = run.get("result")
                if not isinstance(result, dict):
                    continue

                result_type = result.get("type")
                if result_type == "FAILURE":
                    status = "FAILED"
                elif result_type == "SUCCESS":
                    status = "PASSED"
                else:
                    status = result_type

                ts_ms = run.get("timestampMillis")
                occurred_at = (
                    datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.UTC).isoformat()
                    if isinstance(ts_ms, int)
                    else None
                )

                results.append(
                    {
                        "dataset_urn": dataset_urn,
                        "status": status,
                        "occurred_at": occurred_at,
                        "columns": [field_path] if field_path else None,
                        "severity": "critical" if result.get("severity") == "HIGH" else "warning",
                    }
                )

        log.info("list_assertions: returned %d assertion run results (live)", len(results))
        return results
