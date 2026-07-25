from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel, worse_of
from kronika.types import DataAsset, PolicyRule

_ALLOWED_BUILTINS: dict[str, Any] = {
    "abs": abs,
    "all": all,
    "any": any,
    "bool": bool,
    "float": float,
    "int": int,
    "len": len,
    "max": max,
    "min": min,
    "round": round,
    "str": str,
    "sum": sum,
}


@unique
class RuleOutcome(Enum):
    SATISFIED = "SAT"
    VIOLATED = "VIOL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RuleResult:
    rule_id: str
    outcome: RuleOutcome
    witness: str | None


class RuleEngine:
    def evaluate(self, rule: PolicyRule, asset: DataAsset, context: DataContext) -> RuleResult:
        namespace: dict[str, Any] = {
            "__builtins__": _ALLOWED_BUILTINS,
            "asset": _AssetView(asset, context),
        }
        try:
            result = eval(rule.predicate, namespace)  # noqa: S307
        except Exception as exc:
            return RuleResult(rule_id=rule.rule_id, outcome=RuleOutcome.UNKNOWN, witness=str(exc))

        if not isinstance(result, bool):
            return RuleResult(
                rule_id=rule.rule_id,
                outcome=RuleOutcome.UNKNOWN,
                witness=f"predicate returned {type(result).__name__}, expected bool",
            )

        outcome = RuleOutcome.SATISFIED if result else RuleOutcome.VIOLATED
        witness = None if result else f"predicate '{rule.predicate}' evaluated False"
        return RuleResult(rule_id=rule.rule_id, outcome=outcome, witness=witness)

    def evaluate_all(self, context: DataContext) -> dict[str, list[RuleResult]]:
        results: dict[str, list[RuleResult]] = {}
        for urn in context.all_urns():
            rules = context.rules_for(urn)
            if not rules:
                continue
            asset = context.asset(urn)
            results[urn] = [self.evaluate(rule, asset, context) for rule in rules]
        return results

    def apply_violations(self, context: DataContext, rule_results: dict[str, list[RuleResult]]) -> DataContext:
        updated = context
        for urn, results in sorted(rule_results.items()):
            asset = updated.asset(urn)
            modified = asset
            for result in results:
                if result.outcome == RuleOutcome.VIOLATED:
                    rule = next(r for r in context.rules_for(urn) if r.rule_id == result.rule_id)
                    current = modified.get_status(rule.dimension)
                    modified = modified.with_status(rule.dimension, worse_of(current, StatusLevel.CRITICAL))
            if modified != asset:
                updated = updated.replace_asset(modified)
        return updated


class _AssetView:
    def __init__(self, asset: DataAsset, context: DataContext) -> None:
        self._asset = asset
        self._context = context

    @property
    def urn(self) -> str:
        return self._asset.urn

    @property
    def tags(self) -> frozenset[str]:
        return self._asset.tags

    @property
    def has_owner(self) -> bool:
        return self._asset.owner_urn is not None

    @property
    def owner_urn(self) -> str | None:
        return self._asset.owner_urn

    @property
    def domain_urn(self) -> str | None:
        return self._asset.domain_urn

    def has_tag(self, tag: str) -> bool:
        return tag in self._asset.tags

    def has_upstream(self) -> bool:
        return bool(self._context.predecessors(self._asset.urn))
