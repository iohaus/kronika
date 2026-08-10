from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from kronika.dimensions import Dimension, StatusLevel
from kronika.impact import ImpactResult
from kronika.rules import RuleOutcome, RuleResult
from kronika.types import MetadataEvent

_CRITICALITY_WEIGHTS: dict[str, float] = {
    "critical": 3.0,
    "pii": 2.5,
    "internal": 1.5,
}
_DEFAULT_WEIGHT = 1.0
_DEPRECATED_WEIGHT = 0.5


@unique
class Recommendation(Enum):
    HALT = "HALT"
    MONITOR = "MONITOR"
    CLEAR = "CLEAR"


@dataclass(frozen=True)
class AssetOutcome:
    urn: str
    recommendation: Recommendation
    status_before: tuple[StatusLevel, ...]
    status_after: tuple[StatusLevel, ...]
    evidence_path: tuple[str, ...]
    rule_results: tuple[RuleResult, ...]


@dataclass(frozen=True)
class ContainmentPlan:
    halt_set: frozenset[str]
    objective: str
    rationale: dict[str, str]


@dataclass(frozen=True)
class EvidenceRecord:
    event_id: str
    occurred_at: str
    source_urn: str
    trigger_columns: frozenset[str] | None
    trigger_detail: str | None
    outcomes: dict[str, AssetOutcome]
    containment: ContainmentPlan


def _asset_weight(tags: frozenset[str]) -> float:
    if "deprecated" in tags:
        return _DEPRECATED_WEIGHT
    return max(
        (_CRITICALITY_WEIGHTS.get(t, _DEFAULT_WEIGHT) for t in tags), default=_DEFAULT_WEIGHT
    )


def _recommend(
    after_status: tuple[StatusLevel, ...], rule_results: list[RuleResult]
) -> Recommendation:
    has_critical_integrity = after_status[Dimension.INTEGRITY] == StatusLevel.CRITICAL
    has_violated_rule = any(r.outcome == RuleOutcome.VIOLATED for r in rule_results)

    if has_critical_integrity or has_violated_rule:
        return Recommendation.HALT

    has_degraded = any(s == StatusLevel.DEGRADED for s in after_status)
    if has_degraded:
        return Recommendation.MONITOR

    return Recommendation.CLEAR


def _build_containment(
    outcomes: dict[str, AssetOutcome],
    consumer_counts: dict[str, int],
) -> ContainmentPlan:
    halt_candidates = {
        urn for urn, o in outcomes.items() if o.recommendation == Recommendation.HALT
    }

    if not halt_candidates:
        return ContainmentPlan(
            halt_set=frozenset(),
            objective="No assets require halting.",
            rationale={},
        )

    def score(urn: str, tags: frozenset[str]) -> float:
        return _asset_weight(tags) * consumer_counts.get(urn, 0)

    sorted(
        halt_candidates,
        key=lambda urn: (-score(urn, outcomes[urn].status_after[0:0] or frozenset()), urn),
    )

    rationale: dict[str, str] = {}
    for urn in halt_candidates:
        s = score(urn, frozenset())
        rationale[urn] = f"score={s:.1f}"

    objective = (
        f"Halt {len(halt_candidates)} asset(s) to contain impact. "
        f"Candidates: {', '.join(sorted(halt_candidates))}."
    )

    return ContainmentPlan(
        halt_set=frozenset(halt_candidates),
        objective=objective,
        rationale=rationale,
    )


def assemble(
    event: MetadataEvent,
    impact_result: ImpactResult,
    rule_results: dict[str, list[RuleResult]],
    consumer_counts: dict[str, int],
) -> EvidenceRecord:
    outcomes: dict[str, AssetOutcome] = {}

    for urn, delta in sorted(impact_result.changed.items()):
        asset_rules = rule_results.get(urn, [])
        rec = _recommend(delta.after.status, asset_rules)
        path = impact_result.evidence_paths.get(urn, (urn,))
        outcomes[urn] = AssetOutcome(
            urn=urn,
            recommendation=rec,
            status_before=delta.before.status,
            status_after=delta.after.status,
            evidence_path=path,
            rule_results=tuple(sorted(asset_rules, key=lambda r: r.rule_id)),
        )

    containment = _build_containment(outcomes, consumer_counts)

    return EvidenceRecord(
        event_id=event.event_id,
        occurred_at=event.occurred_at,
        source_urn=event.source_urn,
        trigger_columns=event.columns,
        trigger_detail=event.payload_value("detail"),
        outcomes=outcomes,
        containment=containment,
    )
