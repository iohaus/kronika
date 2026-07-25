from __future__ import annotations

from dataclasses import dataclass, field

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.evidence import EvidenceRecord, Recommendation, assemble
from kronika.impact import ImpactEngine, ImpactResult
from kronika.optimization import ContainmentResult, solve
from kronika.ports import DecisionRecord, RecommendedAction
from kronika.rules import RuleEngine
from kronika.types import MetadataEvent
from kronika.verification import VerificationResult, verify_all

_ACTION_KIND_HALT = "HALT_PIPELINE"
_ACTION_KIND_MONITOR = "ADD_MONITORING_TAG"
_ACTION_KIND_CLEAR = "CLEAR_INCIDENT"
_ACTION_KIND_GOVERNANCE = "RAISE_GOVERNANCE_INCIDENT"


@dataclass
class PublicEngine:
    _context: DataContext = field(default_factory=DataContext.empty)
    _impact_engine: ImpactEngine = field(default_factory=ImpactEngine)
    _rule_engine: RuleEngine = field(default_factory=RuleEngine)

    def observe(self, context: DataContext) -> None:
        self._context = context

    def reason(self, event: MetadataEvent) -> DecisionRecord:
        context_before = self._context
        context_after, impact_result = self._impact_engine.analyze(context_before, event)
        rule_results = self._rule_engine.evaluate_all(context_after)
        context_after = self._rule_engine.apply_violations(context_after, rule_results)
        _, impact_result = self._impact_engine.analyze(context_before, event)

        consumer_counts = {
            urn: len(self._consumer_urns(urn, context_before))
            for urn in impact_result.changed
        }

        evidence = assemble(event, impact_result, rule_results, consumer_counts)
        return DecisionRecord(
            event=event,
            context_before=context_before,
            context_after=context_after,
            evidence=evidence,
        )

    def plan(self, decision: DecisionRecord) -> list[RecommendedAction]:
        actions: list[RecommendedAction] = []
        action_index = 0

        for urn, outcome in sorted(decision.evidence.outcomes.items()):
            action_index += 1
            action_id = f"{decision.event.event_id}-{action_index:03d}"

            if outcome.recommendation == Recommendation.HALT:
                actions.append(RecommendedAction(
                    action_id=action_id,
                    kind=_ACTION_KIND_HALT,
                    target_urn=urn,
                    rationale=(
                        f"INTEGRITY=CRITICAL; evidence path: "
                        f"{' → '.join(outcome.evidence_path)}"
                    ),
                    requires_human_approval=True,
                ))

            elif outcome.recommendation == Recommendation.MONITOR:
                actions.append(RecommendedAction(
                    action_id=action_id,
                    kind=_ACTION_KIND_MONITOR,
                    target_urn=urn,
                    rationale=(
                        f"One or more quality dimensions are DEGRADED; "
                        f"monitoring tag added autonomously."
                    ),
                    requires_human_approval=False,
                ))

        verification_results = verify_all(decision.context_after)
        for vr in verification_results:
            if vr.kind.value == "COUNTEREXAMPLE":
                action_index += 1
                action_id = f"{decision.event.event_id}-{action_index:03d}"
                target = vr.minimal_witness[0].urn if vr.minimal_witness else vr.evidence_path[0]
                actions.append(RecommendedAction(
                    action_id=action_id,
                    kind=_ACTION_KIND_GOVERNANCE,
                    target_urn=target,
                    rationale=f"Governance rule '{vr.rule_id}' violated: {vr.minimal_witness[0].evidence if vr.minimal_witness else 'see rule'}",
                    requires_human_approval=True,
                ))

        return sorted(actions, key=lambda a: a.action_id)

    def transition(self, actions: list[RecommendedAction]) -> DataContext:
        updated = self._context
        for action in actions:
            if action.requires_human_approval:
                continue
            if action.kind == _ACTION_KIND_MONITOR:
                if action.target_urn in updated.all_urns():
                    asset = updated.asset(action.target_urn)
                    tagged = _add_monitoring_tag(asset)
                    updated = updated.replace_asset(tagged)
        self._context = updated
        return updated

    @staticmethod
    def _consumer_urns(urn: str, context: DataContext) -> set[str]:
        from collections import deque
        seen: set[str] = set()
        queue: deque[str] = deque(context.successors(urn))
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            for s in context.successors(node):
                if s not in seen:
                    queue.append(s)
        return seen


def _add_monitoring_tag(asset: object) -> object:
    from kronika.types import DataAsset
    assert isinstance(asset, DataAsset)
    return DataAsset(
        urn=asset.urn,
        status=asset.status,
        tags=asset.tags | frozenset({"kronika:monitoring"}),
        owner_urn=asset.owner_urn,
        domain_urn=asset.domain_urn,
    )
