from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import TYPE_CHECKING

from kronika.dimensions import DIMENSION_COUNT, Dimension, StatusLevel, top

if TYPE_CHECKING:
    pass


class ValidationError(Exception):
    def __init__(self, field: str, code: str) -> None:
        self.field = field
        self.code = code
        super().__init__(f"{field}: {code}")


_URN_PREFIX = "urn:li:"


def _require_urn(value: str, field: str) -> None:
    if not value.startswith(_URN_PREFIX):
        raise ValidationError(field, "invalid_urn")


@unique
class EdgeKind(Enum):
    IDENTITY = "IDENTITY"
    PROJECTION = "PROJECTION"
    AGGREGATION = "AGGREGATION"
    JOIN = "JOIN"


@unique
class EventKind(Enum):
    QUALITY_OBSERVATION = "QUALITY_OBSERVATION"
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    OWNERSHIP_CHANGE = "OWNERSHIP_CHANGE"
    POLICY_UPDATE = "POLICY_UPDATE"
    PRIVACY_RECLASSIFICATION = "PRIVACY_RECLASSIFICATION"
    FRESHNESS_VIOLATION = "FRESHNESS_VIOLATION"
    LINEAGE_MODIFICATION = "LINEAGE_MODIFICATION"


@dataclass(frozen=True)
class DataAsset:
    urn: str
    status: tuple[StatusLevel, ...]
    tags: frozenset[str]
    owner_urn: str | None
    domain_urn: str | None

    def __post_init__(self) -> None:
        _require_urn(self.urn, "asset.urn")
        if len(self.status) != DIMENSION_COUNT:
            raise ValidationError(
                "asset.status",
                f"expected {DIMENSION_COUNT} dimensions, got {len(self.status)}",
            )
        for i, level in enumerate(self.status):
            if not isinstance(level, StatusLevel):
                raise ValidationError(f"asset.status[{i}]", "invalid_status_level")
        if self.owner_urn is not None:
            _require_urn(self.owner_urn, "asset.owner_urn")
        if self.domain_urn is not None:
            _require_urn(self.domain_urn, "asset.domain_urn")

    def get_status(self, dim: Dimension) -> StatusLevel:
        return self.status[dim]

    def with_status(self, dim: Dimension, level: StatusLevel) -> DataAsset:
        updated = list(self.status)
        updated[dim] = level
        return DataAsset(
            urn=self.urn,
            status=tuple(updated),
            tags=self.tags,
            owner_urn=self.owner_urn,
            domain_urn=self.domain_urn,
        )

    @staticmethod
    def healthy(
        urn: str,
        *,
        tags: frozenset[str] | None = None,
        owner_urn: str | None = None,
        domain_urn: str | None = None,
    ) -> DataAsset:
        return DataAsset(
            urn=urn,
            status=tuple(top() for _ in range(DIMENSION_COUNT)),
            tags=tags or frozenset(),
            owner_urn=owner_urn,
            domain_urn=domain_urn,
        )


@dataclass(frozen=True)
class ColumnLineage:
    dst_column: str
    src_columns: frozenset[str]

    def __post_init__(self) -> None:
        if not self.dst_column:
            raise ValidationError("column_lineage.dst_column", "empty_name")
        if not self.src_columns:
            raise ValidationError("column_lineage.src_columns", "empty_set")


@dataclass(frozen=True)
class LineageEdge:
    src: str
    dst: str
    kind: EdgeKind
    column_lineage: frozenset[ColumnLineage] | None

    def __post_init__(self) -> None:
        _require_urn(self.src, "edge.src")
        _require_urn(self.dst, "edge.dst")
        if self.src == self.dst:
            raise ValidationError("edge", "self_loop")


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    dimension: Dimension
    predicate: str
    scope_urn: str
    glossary_urn: str | None

    def __post_init__(self) -> None:
        if not self.rule_id:
            raise ValidationError("rule.rule_id", "empty_id")
        _require_urn(self.scope_urn, "rule.scope_urn")
        if self.glossary_urn is not None:
            _require_urn(self.glossary_urn, "rule.glossary_urn")
        if not self.predicate.strip():
            raise ValidationError("rule.predicate", "empty_predicate")


@dataclass(frozen=True)
class MetadataEvent:
    event_id: str
    kind: EventKind
    source_urn: str
    columns: frozenset[str] | None
    payload: tuple[tuple[str, str], ...]
    occurred_at: str

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValidationError("event.event_id", "empty_id")
        _require_urn(self.source_urn, "event.source_urn")
        if not self.occurred_at:
            raise ValidationError("event.occurred_at", "empty_timestamp")

    def payload_value(self, key: str) -> str | None:
        for k, v in self.payload:
            if k == key:
                return v
        return None
