from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field

from kronika.types import DataAsset, LineageEdge, PolicyRule, ValidationError


@dataclass
class CycleDetected:
    cycle_nodes: frozenset[str]


@dataclass
class DataContext:
    _assets: dict[str, DataAsset]
    _edges: frozenset[LineageEdge]
    _rules: dict[str, PolicyRule]
    _successors: dict[str, frozenset[str]] = field(init=False, repr=False)
    _predecessors: dict[str, frozenset[str]] = field(init=False, repr=False)
    _edges_to: dict[str, tuple[LineageEdge, ...]] = field(init=False, repr=False)
    cycle_nodes: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        for urn, asset in self._assets.items():
            if asset.urn != urn:
                raise ValidationError("context.assets", f"key/urn mismatch for '{urn}'")

        for edge in self._edges:
            if edge.src not in self._assets:
                raise ValidationError("context.edges", f"dangling src '{edge.src}'")
            if edge.dst not in self._assets:
                raise ValidationError("context.edges", f"dangling dst '{edge.dst}'")

        for rule in self._rules.values():
            if rule.scope_urn not in self._assets:
                raise ValidationError("context.rules", f"scope '{rule.scope_urn}' not in assets")

        succ: dict[str, set[str]] = defaultdict(set)
        pred: dict[str, set[str]] = defaultdict(set)
        for edge in self._edges:
            succ[edge.src].add(edge.dst)
            pred[edge.dst].add(edge.src)

        self._successors = {k: frozenset(v) for k, v in succ.items()}
        self._predecessors = {k: frozenset(v) for k, v in pred.items()}

        edges_to_map: dict[str, list[LineageEdge]] = defaultdict(list)
        for edge in self._edges:
            edges_to_map[edge.dst].append(edge)
        self._edges_to = {k: tuple(sorted(v, key=lambda e: e.src)) for k, v in edges_to_map.items()}

        self.cycle_nodes = self._detect_cycles()

    def _detect_cycles(self) -> frozenset[str]:
        in_degree: dict[str, int] = {urn: 0 for urn in self._assets}
        for edge in self._edges:
            in_degree[edge.dst] = in_degree.get(edge.dst, 0) + 1

        queue: deque[str] = deque(urn for urn, deg in in_degree.items() if deg == 0)
        visited_count = 0
        while queue:
            node = queue.popleft()
            visited_count += 1
            for succ in self._successors.get(node, frozenset()):
                in_degree[succ] -= 1
                if in_degree[succ] == 0:
                    queue.append(succ)

        if visited_count == len(self._assets):
            return frozenset()
        return frozenset(urn for urn, deg in in_degree.items() if deg > 0)

    def asset(self, urn: str) -> DataAsset:
        try:
            return self._assets[urn]
        except KeyError:
            raise KeyError(f"asset not found: '{urn}'") from None

    def all_urns(self) -> list[str]:
        return sorted(self._assets)

    def successors(self, urn: str) -> frozenset[str]:
        return self._successors.get(urn, frozenset())

    def predecessors(self, urn: str) -> frozenset[str]:
        return self._predecessors.get(urn, frozenset())

    def rules_for(self, urn: str) -> list[PolicyRule]:
        return sorted(
            (r for r in self._rules.values() if r.scope_urn == urn),
            key=lambda r: r.rule_id,
        )

    def edges_from(self, src: str) -> list[LineageEdge]:
        return sorted(
            (e for e in self._edges if e.src == src),
            key=lambda e: e.dst,
        )

    def edges_to(self, dst: str) -> tuple[LineageEdge, ...]:
        return self._edges_to.get(dst, ())

    def replace_asset(self, asset: DataAsset) -> DataContext:
        if asset.urn not in self._assets:
            raise KeyError(f"cannot replace unknown asset '{asset.urn}'")
        updated = dict(self._assets)
        updated[asset.urn] = asset
        return DataContext(updated, self._edges, dict(self._rules))

    def __len__(self) -> int:
        return len(self._assets)

    @staticmethod
    def empty() -> DataContext:
        return DataContext({}, frozenset(), {})

    @staticmethod
    def build(
        assets: list[DataAsset],
        edges: list[LineageEdge],
        rules: list[PolicyRule],
    ) -> DataContext:
        asset_map = {a.urn: a for a in assets}
        if len(asset_map) != len(assets):
            raise ValidationError("context.assets", "duplicate urns")
        rule_map = {r.rule_id: r for r in rules}
        if len(rule_map) != len(rules):
            raise ValidationError("context.rules", "duplicate rule_ids")
        ctx =  DataContext(asset_map, frozenset(edges), rule_map)
        return ctx
