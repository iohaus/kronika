from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Any

from kronika.data_context import DataContext
from kronika.rules import _ALLOWED_BUILTINS, _AssetView
from kronika.types import DataAsset, PolicyRule


@unique
class FindingKind(Enum):
    PROOF = "PROOF"
    COUNTEREXAMPLE = "COUNTEREXAMPLE"
    INCONCLUSIVE = "INCONCLUSIVE"


@dataclass(frozen=True)
class WitnessBinding:
    urn: str
    evidence: str


@dataclass(frozen=True)
class VerificationResult:
    rule_id: str
    kind: FindingKind
    witnesses: tuple[WitnessBinding, ...]
    minimal_witness: tuple[WitnessBinding, ...]
    evidence_path: tuple[str, ...]


def _eval_predicate(predicate: str, asset: DataAsset, context: DataContext) -> bool | None:
    namespace: dict[str, Any] = {
        "__builtins__": _ALLOWED_BUILTINS,
        "asset": _AssetView(asset, context),
    }
    try:
        result = eval(predicate, namespace)  # noqa: S307
        return bool(result) if isinstance(result, bool) else None
    except Exception:
        return None


def _build_evidence_path(urn: str, context: DataContext) -> tuple[str, ...]:
    path = [urn]
    preds = sorted(context.predecessors(urn))
    if preds:
        path = [preds[0]] + path
    succs = sorted(context.successors(urn))
    if succs:
        path = path + [succs[0]]
    return tuple(path)


def verify_rule(rule: PolicyRule, context: DataContext) -> VerificationResult:
    satisfying: list[WitnessBinding] = []
    failing: list[WitnessBinding] = []

    scope_urn = rule.scope_urn
    if scope_urn not in context.all_urns():
        return VerificationResult(
            rule_id=rule.rule_id,
            kind=FindingKind.INCONCLUSIVE,
            witnesses=(),
            minimal_witness=(),
            evidence_path=(scope_urn,),
        )

    asset = context.asset(scope_urn)
    outcome = _eval_predicate(rule.predicate, asset, context)

    if outcome is None:
        return VerificationResult(
            rule_id=rule.rule_id,
            kind=FindingKind.INCONCLUSIVE,
            witnesses=(),
            minimal_witness=(),
            evidence_path=_build_evidence_path(scope_urn, context),
        )

    binding = WitnessBinding(
        urn=scope_urn,
        evidence=f"predicate '{rule.predicate}' evaluated to {outcome}",
    )

    if outcome:
        satisfying.append(binding)
    else:
        failing.append(binding)

    if failing:
        return VerificationResult(
            rule_id=rule.rule_id,
            kind=FindingKind.COUNTEREXAMPLE,
            witnesses=tuple(sorted(failing + satisfying, key=lambda w: w.urn)),
            minimal_witness=(failing[0],),
            evidence_path=_build_evidence_path(scope_urn, context),
        )

    return VerificationResult(
        rule_id=rule.rule_id,
        kind=FindingKind.PROOF,
        witnesses=tuple(sorted(satisfying, key=lambda w: w.urn)),
        minimal_witness=(),
        evidence_path=_build_evidence_path(scope_urn, context),
    )


def verify_all(context: DataContext) -> list[VerificationResult]:
    results: list[VerificationResult] = []
    seen_rules: set[str] = set()
    for urn in context.all_urns():
        for rule in context.rules_for(urn):
            if rule.rule_id in seen_rules:
                continue
            seen_rules.add(rule.rule_id)
            results.append(verify_rule(rule, context))
    return sorted(results, key=lambda r: r.rule_id)
