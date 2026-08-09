from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.data_context import DataContext
from kronika.evidence import EvidenceRecord
from kronika.types import MetadataEvent

KRONIKA_MAX_ASSET_COUNT = 10_000


@dataclass(frozen=True)
class DecisionRecord:
    event: MetadataEvent
    context_before: DataContext
    context_after: DataContext
    evidence: EvidenceRecord


@dataclass(frozen=True)
class RecommendedAction:
    action_id: str
    kind: str
    target_urn: str
    rationale: str
    requires_human_approval: bool


class DataHubReader(Protocol):
    def list_datasets(self) -> list[dict[str, object]]: ...
    def list_lineage_edges(self) -> list[dict[str, object]]: ...
    def list_glossary_terms(self) -> list[dict[str, object]]: ...
    def list_policy_rules(self) -> list[dict[str, object]]: ...
    def list_assertions(self) -> list[dict[str, object]]: ...


class DataHubWriter(Protocol):
    def create_incident(self, urn: str, title: str, description: str, event_id: str) -> None: ...
    def add_annotation(self, urn: str, key: str, value: str, event_id: str) -> None: ...


class LLMAdapter(Protocol):
    def explain(self, evidence: EvidenceRecord, audience: str) -> tuple[str, bool]: ...


class EvidenceStore(Protocol):
    def save(self, evidence: EvidenceRecord) -> None: ...
    def load(self, event_id: str) -> EvidenceRecord | None: ...
