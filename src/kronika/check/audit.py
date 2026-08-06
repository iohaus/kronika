from __future__ import annotations

from dataclasses import dataclass

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.evidence import EvidenceRecord, Recommendation
from kronika.impact import ImpactResult
from kronika.types import MetadataEvent


@dataclass(frozen=True)
class ConsistencyViolation:
    rule: str
    detail: str


class DataAudit:
    def check(
        self,
        before: DataContext,
        after: DataContext,
        event: MetadataEvent,
        impact_result: ImpactResult,
        evidence: EvidenceRecord | None = None,
    ) -> list[ConsistencyViolation]:
        violations: list[ConsistencyViolation] = []
        violations.extend(self._check_no_improvement(before, after, impact_result))
        violations.extend(self._check_graph_conservatism(before, after, impact_result))
        violations.extend(self._check_evidence_completeness(impact_result, evidence))
        violations.extend(self._check_no_false_clear(before, after, event, impact_result, evidence))
        return violations

    def check_idempotency(
        self,
        context: DataContext,
        event: MetadataEvent,
        engine: object,
    ) -> list[ConsistencyViolation]:
        from kronika.impact import ImpactEngine

        assert isinstance(engine, ImpactEngine)
        after1, _result1 = engine.analyze(context, event)
        after2, _result2 = engine.analyze(after1, event)
        violations: list[ConsistencyViolation] = []
        for urn in after1.all_urns():
            s1 = after1.asset(urn).status
            s2 = after2.asset(urn).status
            if s1 != s2:
                violations.append(
                    ConsistencyViolation(
                        rule="idempotency",
                        detail=f"{urn}: status differs after second pass: {s1} vs {s2}",
                    )
                )
        return violations

    @staticmethod
    def _check_no_improvement(
        before: DataContext,
        after: DataContext,
        impact_result: ImpactResult,
    ) -> list[ConsistencyViolation]:
        violations: list[ConsistencyViolation] = []
        for urn in impact_result.changed:
            b = before.asset(urn)
            a = after.asset(urn)
            for dim in Dimension:
                if a.get_status(dim) > b.get_status(dim):
                    violations.append(
                        ConsistencyViolation(
                            rule="no_improvement",
                            detail=(
                                f"{urn} dim={dim.name}: status improved from "
                                f"{b.get_status(dim).name} to {a.get_status(dim).name}"
                            ),
                        )
                    )
        return violations

    @staticmethod
    def _check_graph_conservatism(
        before: DataContext,
        after: DataContext,
        impact_result: ImpactResult,
    ) -> list[ConsistencyViolation]:
        violations: list[ConsistencyViolation] = []
        reachable = ImpactEngine_reachable(before, impact_result.source_urn)
        for urn in impact_result.changed:
            if urn not in reachable:
                violations.append(
                    ConsistencyViolation(
                        rule="graph_conservatism",
                        detail=(
                            f"{urn} was changed but is not reachable from source "
                            f"{impact_result.source_urn}"
                        ),
                    )
                )
        return violations

    @staticmethod
    def _check_evidence_completeness(
        impact_result: ImpactResult,
        evidence: EvidenceRecord | None,
    ) -> list[ConsistencyViolation]:
        if evidence is None:
            return []
        violations: list[ConsistencyViolation] = []
        for urn, outcome in evidence.outcomes.items():
            if outcome.recommendation == Recommendation.HALT:
                if not outcome.evidence_path:
                    violations.append(
                        ConsistencyViolation(
                            rule="evidence_completeness",
                            detail=f"{urn} has HALT recommendation but empty evidence path",
                        )
                    )
                elif (
                    outcome.evidence_path[0] != impact_result.source_urn
                    and urn != impact_result.source_urn
                ):
                    violations.append(
                        ConsistencyViolation(
                            rule="evidence_completeness",
                            detail=(
                                f"{urn} evidence path does not start at source "
                                f"{impact_result.source_urn}"
                            ),
                        )
                    )
        return violations

    @staticmethod
    def _check_no_false_clear(
        before: DataContext,
        after: DataContext,
        event: MetadataEvent,
        impact_result: ImpactResult,
        evidence: EvidenceRecord | None,
    ) -> list[ConsistencyViolation]:
        if evidence is None:
            return []
        violations: list[ConsistencyViolation] = []
        reachable = ImpactEngine_reachable(before, event.source_urn)
        for urn, outcome in evidence.outcomes.items():
            if (
                outcome.recommendation == Recommendation.CLEAR
                and urn in reachable
                and urn != event.source_urn
            ):
                if any(
                    s == StatusLevel.CRITICAL
                    for s in impact_result.changed.get(event.source_urn, _NullDelta).before.status
                    if False
                ):
                    pass
                source_delta = impact_result.changed.get(event.source_urn)
                if source_delta is not None:
                    source_was_critical = any(
                        s == StatusLevel.CRITICAL for s in source_delta.after.status
                    )
                    if source_was_critical and outcome.recommendation == Recommendation.CLEAR:
                        violations.append(
                            ConsistencyViolation(
                                rule="no_false_clear",
                                detail=f"{urn} is CLEAR but is reachable from a CRITICAL source",
                            )
                        )
        return violations


def ImpactEngine_reachable(context: DataContext, source: str) -> set[str]:
    from collections import deque

    seen: set[str] = set()
    q: deque[str] = deque([source])
    while q:
        urn = q.popleft()
        if urn in seen:
            continue
        seen.add(urn)
        for s in context.successors(urn):
            if s not in seen:
                q.append(s)
    return seen


class _NullDelta:
    before = type("_", (), {"status": ()})()
