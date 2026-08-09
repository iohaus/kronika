from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel, lower_by_one, worse_of
from kronika.types import ColumnLineage, DataAsset, EdgeKind, EventKind, MetadataEvent

_CRITICAL = StatusLevel.CRITICAL
_DEGRADED = StatusLevel.DEGRADED
_HEALTHY = StatusLevel.HEALTHY

_NO_CHANGE = object()

_PROP_MODES: dict[tuple[Dimension, EdgeKind], str] = {
    (Dimension.INTEGRITY, EdgeKind.IDENTITY): "inherit",
    (Dimension.INTEGRITY, EdgeKind.PROJECTION): "col_filter",
    (Dimension.INTEGRITY, EdgeKind.AGGREGATION): "agg_cap",
    (Dimension.INTEGRITY, EdgeKind.JOIN): "inherit",
    (Dimension.TRUST, EdgeKind.IDENTITY): "inherit",
    (Dimension.TRUST, EdgeKind.PROJECTION): "col_filter",
    (Dimension.TRUST, EdgeKind.AGGREGATION): "lower1",
    (Dimension.TRUST, EdgeKind.JOIN): "inherit",
    (Dimension.AVAILABILITY, EdgeKind.IDENTITY): "inherit",
    (Dimension.AVAILABILITY, EdgeKind.PROJECTION): "inherit",
    (Dimension.AVAILABILITY, EdgeKind.AGGREGATION): "inherit",
    (Dimension.AVAILABILITY, EdgeKind.JOIN): "inherit",
    (Dimension.COMPLIANCE, EdgeKind.IDENTITY): "recheck",
    (Dimension.COMPLIANCE, EdgeKind.PROJECTION): "recheck",
    (Dimension.COMPLIANCE, EdgeKind.AGGREGATION): "recheck",
    (Dimension.COMPLIANCE, EdgeKind.JOIN): "recheck",
    (Dimension.FRESHNESS, EdgeKind.IDENTITY): "inherit",
    (Dimension.FRESHNESS, EdgeKind.PROJECTION): "inherit",
    (Dimension.FRESHNESS, EdgeKind.AGGREGATION): "inherit",
    (Dimension.FRESHNESS, EdgeKind.JOIN): "inherit",
    (Dimension.OWNERSHIP, EdgeKind.IDENTITY): "no_prop",
    (Dimension.OWNERSHIP, EdgeKind.PROJECTION): "no_prop",
    (Dimension.OWNERSHIP, EdgeKind.AGGREGATION): "no_prop",
    (Dimension.OWNERSHIP, EdgeKind.JOIN): "no_prop",
    (Dimension.DOCUMENTATION, EdgeKind.IDENTITY): "no_prop",
    (Dimension.DOCUMENTATION, EdgeKind.PROJECTION): "no_prop",
    (Dimension.DOCUMENTATION, EdgeKind.AGGREGATION): "no_prop",
    (Dimension.DOCUMENTATION, EdgeKind.JOIN): "no_prop",
    (Dimension.PRIVACY, EdgeKind.IDENTITY): "col_filter",
    (Dimension.PRIVACY, EdgeKind.PROJECTION): "col_filter",
    (Dimension.PRIVACY, EdgeKind.AGGREGATION): "inherit",
    (Dimension.PRIVACY, EdgeKind.JOIN): "inherit",
}


def _translate_columns(
    upstream_active: frozenset[str] | None,
    column_lineage: frozenset[ColumnLineage] | None,
) -> frozenset[str] | None:
    """Translate an upstream node's implicated-column set through one edge's real
    column lineage into the downstream node's column namespace.

    - `None` = unknown / all columns potentially implicated. Absorbing on the
      unknown side: once unknown, stays unknown.
    - `frozenset()` = proven zero columns implicated upstream. Never widened by a
      missing edge mapping — if nothing relevant reached the source side of this
      edge, no unmapped transform can manufacture relevance out of nothing.
    - Otherwise: real intersection-based translation via the edge's authored
      column-to-column mapping.
    - An edge with no authored `column_lineage` at all, when the upstream side has
      something relevant, conservatively widens to unknown (`None`) rather than
      silently narrowing — a missing mapping can only ever produce an over-cautious
      MONITOR/HALT, never a false CLEAR.
    """
    if upstream_active is None:
        return None
    if not upstream_active:
        return frozenset()
    if column_lineage is None:
        return None
    return frozenset(
        mapping.dst_column for mapping in column_lineage if mapping.src_columns & upstream_active
    )


def _column_relevant(translated: frozenset[str] | None) -> bool:
    return translated is None or bool(translated)


def _propagate_one_dim(
    dim: Dimension,
    edge_kind: EdgeKind,
    upstream: StatusLevel,
    current: StatusLevel,
    translated_columns: frozenset[str] | None,
) -> StatusLevel | object:
    mode = _PROP_MODES.get((dim, edge_kind))
    if mode is None or mode == "no_prop" or mode == "recheck":
        return _NO_CHANGE

    if mode == "col_filter":
        if not _column_relevant(translated_columns):
            return _NO_CHANGE
        return worse_of(current, upstream)

    if mode == "inherit":
        return worse_of(current, upstream)

    if mode == "agg_cap":
        capped = _DEGRADED if upstream == _CRITICAL else upstream
        return worse_of(current, capped)

    if mode == "lower1":
        return worse_of(current, lower_by_one(upstream))

    return _NO_CHANGE


def _apply_source_changes(asset: DataAsset, event: MetadataEvent) -> DataAsset:
    k = event.kind

    if k == EventKind.QUALITY_OBSERVATION:
        sev = event.payload_value("severity") or "critical"
        level = _CRITICAL if sev != "warning" else _DEGRADED
        return asset.with_status(
            Dimension.INTEGRITY, worse_of(asset.get_status(Dimension.INTEGRITY), level)
        )

    if k == EventKind.SCHEMA_CHANGE:
        a = asset.with_status(
            Dimension.INTEGRITY, worse_of(asset.get_status(Dimension.INTEGRITY), _DEGRADED)
        )
        return a.with_status(
            Dimension.AVAILABILITY, worse_of(a.get_status(Dimension.AVAILABILITY), _DEGRADED)
        )

    if k == EventKind.OWNERSHIP_CHANGE:
        raw = event.payload_value("new_ownership") or ""
        level = _CRITICAL if raw == "orphan" else (_DEGRADED if raw == "disputed" else _HEALTHY)
        return asset.with_status(Dimension.OWNERSHIP, level)

    if k == EventKind.PRIVACY_RECLASSIFICATION:
        raw = event.payload_value("classification") or ""
        level = _CRITICAL if raw == "exposed" else (_DEGRADED if raw == "unverified" else _HEALTHY)
        a = asset.with_status(
            Dimension.PRIVACY, worse_of(asset.get_status(Dimension.PRIVACY), level)
        )
        return a.with_status(
            Dimension.COMPLIANCE, worse_of(a.get_status(Dimension.COMPLIANCE), _DEGRADED)
        )

    if k == EventKind.FRESHNESS_VIOLATION:
        raw = event.payload_value("severity") or "stale"
        level = _CRITICAL if raw == "expired" else _DEGRADED
        a = asset.with_status(
            Dimension.FRESHNESS, worse_of(asset.get_status(Dimension.FRESHNESS), level)
        )
        if level == _CRITICAL:
            a = a.with_status(
                Dimension.AVAILABILITY, worse_of(a.get_status(Dimension.AVAILABILITY), _DEGRADED)
            )
        return a

    return asset


def _mark_all_critical(asset: DataAsset) -> DataAsset:
    result = asset
    for dim in Dimension:
        result = result.with_status(dim, _CRITICAL)
    return result


@dataclass(frozen=True)
class AssetDelta:
    urn: str
    before: DataAsset
    after: DataAsset


@dataclass(frozen=True)
class ImpactResult:
    event_id: str
    source_urn: str
    changed: dict[str, AssetDelta]
    evidence_paths: dict[str, tuple[str, ...]]


class ImpactEngine:
    def analyze(
        self, context: DataContext, event: MetadataEvent
    ) -> tuple[DataContext, ImpactResult]:
        source_urn = event.source_urn

        if source_urn in context.cycle_nodes:
            source_after = _mark_all_critical(context.asset(source_urn))
        else:
            source_after = _apply_source_changes(context.asset(source_urn), event)

        reachable = self._reachable(context, source_urn)

        in_degree: dict[str, int] = {urn: 0 for urn in reachable}
        for urn in reachable:
            for edge in context.edges_from(urn):
                if edge.dst in reachable:
                    in_degree[edge.dst] += 1

        working: dict[str, DataAsset] = {source_urn: source_after}
        evidence_paths: dict[str, tuple[str, ...]] = {source_urn: (source_urn,)}
        active_columns: dict[str, frozenset[str] | None] = {source_urn: event.columns}

        ready: list[str] = sorted(urn for urn, deg in in_degree.items() if deg == 0)

        while ready:
            urn = ready.pop(0)

            if urn != source_urn:
                updated = context.asset(urn)
                best_path: tuple[str, ...] = (urn,)
                node_columns: frozenset[str] | None = frozenset()

                for edge in context.edges_to(urn):
                    if edge.src not in reachable:
                        continue
                    upstream = working.get(edge.src, context.asset(edge.src))
                    translated = _translate_columns(
                        active_columns.get(edge.src), edge.column_lineage
                    )

                    for dim in Dimension:
                        new_level = _propagate_one_dim(
                            dim,
                            edge.kind,
                            upstream.get_status(dim),
                            updated.get_status(dim),
                            translated,
                        )
                        if isinstance(new_level, StatusLevel):
                            updated = updated.with_status(dim, new_level)

                    node_columns = (
                        None
                        if (node_columns is None or translated is None)
                        else node_columns | translated
                    )

                    src_path = evidence_paths.get(edge.src)
                    if src_path and len(src_path) + 1 > len(best_path):
                        best_path = (*src_path, urn)

                working[urn] = updated
                evidence_paths[urn] = best_path
                active_columns[urn] = node_columns

            for edge in context.edges_from(urn):
                if edge.dst in reachable:
                    in_degree[edge.dst] -= 1
                    if in_degree[edge.dst] == 0:
                        ready.append(edge.dst)
                        ready.sort()

        changed: dict[str, AssetDelta] = {}
        for urn in sorted(working):
            before = context.asset(urn)
            after = working[urn]
            if before != after:
                changed[urn] = AssetDelta(urn=urn, before=before, after=after)

        new_context = context
        for urn in sorted(changed):
            new_context = new_context.replace_asset(changed[urn].after)

        changed_paths = {urn: evidence_paths[urn] for urn in changed if urn in evidence_paths}

        return new_context, ImpactResult(
            event_id=event.event_id,
            source_urn=source_urn,
            changed=changed,
            evidence_paths=changed_paths,
        )

    @staticmethod
    def _reachable(context: DataContext, source: str) -> set[str]:
        seen: set[str] = set()
        queue: deque[str] = deque([source])
        while queue:
            urn = queue.popleft()
            if urn in seen:
                continue
            seen.add(urn)
            for succ in context.successors(urn):
                if succ not in seen:
                    queue.append(succ)
        return seen
