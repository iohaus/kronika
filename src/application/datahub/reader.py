from __future__ import annotations

import datetime
import json
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any, TypeVar

import httpx
from datahub.ingestion.graph.client import DatahubClientConfig
from datahub.sdk import DataHubClient
from datahub_agent_context import set_client
from datahub_agent_context.mcp_tools import get_dataset_assertions, get_entities, search

from kronika.ports import DataHubReader
from kronika.types import ValidationError

log = logging.getLogger("kronika.datahub.reader")

_T = TypeVar("_T")


_GOVERNANCE_RULES_PROPERTY_KEY = "kronikaGovernanceRules"


def _column_lineage_for_edge(
    src_urn: str, fine_grained_lineages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
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
        self._sdk_client: DataHubClient | None = None

        if self._mock_data is None:
            try:
                self._sdk_client = DataHubClient(
                    config=DatahubClientConfig(
                        server=self.server_url, token=self.token, timeout_sec=self.timeout
                    )
                )
                set_client(self._sdk_client)
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

    def _call_with_timeout(self, func: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)
        sdk_client = self._sdk_client

        def _run() -> None:
            try:
                if sdk_client is not None:
                    set_client(sdk_client)
                result_queue.put(("ok", func(*args, **kwargs)))
            except Exception as exc:
                result_queue.put(("error", exc))

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        try:
            status, value = result_queue.get(timeout=self.timeout)
        except queue.Empty as exc:
            func_name = getattr(func, "__name__", repr(func))
            log.error(
                "_call_with_timeout: hard timeout exceeded | func=%s timeout=%.1fs",
                func_name,
                self.timeout,
            )
            raise DataHubAPIError(
                f"Agent Context Kit call '{func_name}' exceeded {self.timeout}s timeout"
            ) from exc

        if status == "error":
            raise value
        return value  # type: ignore[no-any-return]

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

    def _get_dataset_entities(self) -> list[dict[str, Any]]:
        found = self._call_with_timeout(
            search, query="*", filter="entity_type = dataset", num_results=1000
        )
        urns = [
            item["entity"]["urn"]
            for item in found.get("searchResults", [])
            if isinstance(item.get("entity"), dict) and item["entity"].get("urn")
        ]
        for urn in urns:
            if not isinstance(urn, str) or not urn.startswith("urn:li:"):
                raise ValidationError("reader.datasets.urn", "invalid_urn")

        if not urns:
            return []
        entities = self._call_with_timeout(get_entities, urns)
        return [e for e in entities if not _is_view(e)]

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

        entities = self._get_dataset_entities()
        datasets: list[dict[str, Any]] = []

        for entity in entities:
            urn = entity.get("urn")
            if not urn or not isinstance(urn, str) or not urn.startswith("urn:li:"):
                raise ValidationError("reader.datasets.urn", "invalid_urn")

            tags: list[str] = []
            tags_obj = entity.get("tags")
            if isinstance(tags_obj, dict):
                for t_item in tags_obj.get("tags", []):
                    tag = t_item.get("tag") if isinstance(t_item, dict) else None
                    if not isinstance(tag, dict):
                        continue
                    # get_entities() nests the name under properties; the raw GraphQL
                    # query this replaced hoisted a top-level `name` alias instead —
                    # accept either shape.
                    t_name = tag.get("name")
                    if not t_name and isinstance(tag.get("properties"), dict):
                        t_name = tag["properties"].get("name")
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
        found = self._call_with_timeout(
            search, query="*", filter="entity_type = glossary_term", num_results=1000
        )
        terms: list[dict[str, Any]] = []

        for item in found.get("searchResults", []):
            entity = item.get("entity")
            if not isinstance(entity, dict):
                continue
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
                name = entity.get("name")

            terms.append({"urn": urn, "name": name, "description": description})

        log.info("list_glossary_terms: returned %d terms (live)", len(terms))
        return terms

    @staticmethod
    def _custom_properties_of(entity: dict[str, Any]) -> dict[str, str]:
        """`get_entities()` returns `properties.customProperties` as a
        `[{"key": ..., "value": ...}, ...]` list, not a flat map."""
        properties = entity.get("properties")
        raw = properties.get("customProperties") if isinstance(properties, dict) else None
        if not isinstance(raw, list):
            return {}
        return {
            item["key"]: item["value"]
            for item in raw
            if isinstance(item, dict) and isinstance(item.get("key"), str)
        }

    def list_policy_rules(self) -> list[dict[str, Any]]:
        if self._mock_data is not None:
            rules = self._mock_data.get("policy_rules", [])
            log.debug("list_policy_rules: returned %d rules (mock)", len(rules))
            return rules

        log.debug("list_policy_rules: live mode, reading governance rules from datasets")
        rules: list[dict[str, Any]] = []
        for entity in self._get_dataset_entities():
            urn = entity.get("urn")
            if not isinstance(urn, str):
                continue
            custom_properties = self._custom_properties_of(entity)
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
        if self._mock_data is not None:
            assertions = self._mock_data.get("assertions", [])
            log.debug("list_assertions: returned %d assertions (mock)", len(assertions))
            return assertions

        results: list[dict[str, Any]] = []
        for dataset in self.list_datasets():
            dataset_urn = dataset.get("urn")
            if not isinstance(dataset_urn, str):
                continue

            response = self._call_with_timeout(
                get_dataset_assertions, dataset_urn, run_events_count=1
            )
            if not response.get("success"):
                continue
            for assertion in response.get("data", {}).get("assertions", []):
                if not isinstance(assertion, dict):
                    continue

                result_type = assertion.get("latestResultType")
                status = "FAILED" if result_type == "FAILURE" else "PASSED"

                run_history = assertion.get("runHistory") or []
                ts_ms = run_history[0].get("timestampMillis") if run_history else None
                occurred_at = (
                    datetime.datetime.fromtimestamp(ts_ms / 1000, tz=datetime.UTC).isoformat()
                    if isinstance(ts_ms, int)
                    else None
                )

                column = assertion.get("column")
                definition = assertion.get("definition") or {}
                results.append(
                    {
                        "dataset_urn": dataset_urn,
                        "status": status,
                        "occurred_at": occurred_at,
                        "columns": [column] if column else None,
                        "severity": "critical" if status == "FAILED" else "warning",
                        "description": assertion.get("description"),
                        "predicate": definition.get("logic"),
                    }
                )

        log.info("list_assertions: returned %d assertion run results (live)", len(results))
        return results
