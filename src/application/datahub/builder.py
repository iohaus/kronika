from __future__ import annotations

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel, top
from kronika.ports import KRONIKA_MAX_ASSET_COUNT, DataHubReader
from kronika.types import DataAsset, EdgeKind, LineageEdge, PolicyRule, ValidationError


class ContextLimitExceededError(Exception):
    def __init__(self, count: int, limit: int) -> None:
        super().__init__(f"Asset count ({count}) exceeds maximum allowed limit ({limit}).")


def build_context(reader: DataHubReader, max_size: int = KRONIKA_MAX_ASSET_COUNT) -> DataContext:
    raw_datasets = reader.list_datasets()
    if len(raw_datasets) > max_size:
        raise ContextLimitExceededError(len(raw_datasets), max_size)

    assets: list[DataAsset] = []
    for d in raw_datasets:
        urn = d.get("urn")
        if not isinstance(urn, str):
            raise ValidationError("dataset.urn", "missing_or_invalid")

        raw_status = d.get("status")
        if isinstance(raw_status, tuple | list) and len(raw_status) == 8:
            status = tuple(StatusLevel(s) for s in raw_status)
        else:
            status = tuple(top() for _ in range(8))

        raw_tags = d.get("tags", [])
        tags = frozenset(str(t) for t in raw_tags)
        owner_urn = d.get("owner_urn")
        domain_urn = d.get("domain_urn")

        asset = DataAsset(
            urn=urn,
            status=status,
            tags=tags,
            owner_urn=str(owner_urn) if owner_urn else None,
            domain_urn=str(domain_urn) if domain_urn else None,
        )
        assets.append(asset)

    raw_edges = reader.list_lineage_edges()
    edges: list[LineageEdge] = []
    for e in raw_edges:
        src = e.get("src")
        dst = e.get("dst")
        if not isinstance(src, str) or not isinstance(dst, str):
            raise ValidationError("edge.src_dst", "missing_or_invalid")
        if src == dst:
            continue

        raw_kind = e.get("kind", "IDENTITY")
        try:
            kind = EdgeKind(raw_kind)
        except ValueError:
            kind = EdgeKind.IDENTITY

        raw_cols = e.get("columns")
        cols = frozenset(str(c) for c in raw_cols) if raw_cols is not None else None

        edges.append(LineageEdge(src=src, dst=dst, kind=kind, columns=cols))

    raw_rules = reader.list_policy_rules()
    rules: list[PolicyRule] = []
    for r in raw_rules:
        rule_id = r.get("rule_id")
        scope_urn = r.get("scope_urn")
        predicate = r.get("predicate")
        if not rule_id or not scope_urn or not predicate:
            continue

        raw_dim = r.get("dimension", 3)
        try:
            dim = Dimension(raw_dim) if isinstance(raw_dim, int) else Dimension[raw_dim]
        except (ValueError, KeyError):
            dim = Dimension.COMPLIANCE

        glossary_urn = r.get("glossary_urn")

        rules.append(
            PolicyRule(
                rule_id=str(rule_id),
                dimension=dim,
                predicate=str(predicate),
                scope_urn=str(scope_urn),
                glossary_urn=str(glossary_urn) if glossary_urn else None,
            )
        )

    return DataContext.build(assets=assets, edges=edges, rules=rules)
