from __future__ import annotations

import logging
from dataclasses import dataclass, field

from kronika.data_context import DataContext
from kronika.evidence import Recommendation, assemble
from kronika.impact import ImpactEngine
from kronika.ports import DecisionRecord, RecommendedAction
from kronika.rules import RuleEngine
from kronika.types import MetadataEvent
from kronika.verification import verify_all

_ACTION_KIND_HALT = "HALT_PIPELINE"
_ACTION_KIND_MONITOR = "ADD_MONITORING_TAG"
_ACTION_KIND_CLEAR = "CLEAR_INCIDENT"
_ACTION_KIND_GOVERNANCE = "RAISE_GOVERNANCE_INCIDENT"

log = logging.getLogger("kronika.engine")


@dataclass
class PublicEngine:
    _context: DataContext = field(default_factory=DataContext.empty)
    _impact_engine: ImpactEngine = field(default_factory=ImpactEngine)
    _rule_engine: RuleEngine = field(default_factory=RuleEngine)

    def observe(self, context: DataContext) -> None:
        asset_count = len(list(context.all_urns()))
        log.info("observe: context loaded | assets=%d", asset_count)
        self._context = context
        log.debug("observe: asset URNs=%s", sorted(context.all_urns()))

    def reason(self, event: MetadataEvent) -> DecisionRecord:
        log.info(
            "reason: processing event | event_id=%s kind=%s source_urn=%s",
            event.event_id,
            event.kind.value,
            event.source_urn,
        )
        context_before = self._context
        context_after, impact_result = self._impact_engine.analyze(context_before, event)
        rule_results = self._rule_engine.evaluate_all(context_after)
        context_after = self._rule_engine.apply_violations(context_after, rule_results)
        _, impact_result = self._impact_engine.analyze(context_before, event)

        changed_count = len(impact_result.changed)
        log.info(
            "reason: impact analysis complete | changed_assets=%d source_urn=%s",
            changed_count,
            event.source_urn,
        )
        log.debug("reason: changed asset URNs=%s", sorted(impact_result.changed.keys()))

        consumer_counts = {
            urn: len(self._consumer_urns(urn, context_before)) for urn in impact_result.changed
        }

        evidence = assemble(event, impact_result, rule_results, consumer_counts)
        record = DecisionRecord(
            event=event,
            context_before=context_before,
            context_after=context_after,
            evidence=evidence,
        )
        log.debug(
            "reason: decision record assembled | outcomes=%d halt_set=%s",
            len(evidence.outcomes),
            sorted(evidence.containment.halt_set),
        )
        return record

    def plan(self, decision: DecisionRecord) -> list[RecommendedAction]:
        log.info(
            "plan: building action plan | event_id=%s outcomes=%d",
            decision.event.event_id,
            len(decision.evidence.outcomes),
        )
        actions: list[RecommendedAction] = []
        action_index = 0

        for urn, outcome in sorted(decision.evidence.outcomes.items()):
            action_index += 1
            action_id = f"{decision.event.event_id}-{action_index:03d}"

            if outcome.recommendation == Recommendation.HALT:
                log.info(
                    "plan: HALT action planned | action_id=%s target_urn=%s requires_approval=True",
                    action_id,
                    urn,
                )
                actions.append(
                    RecommendedAction(
                        action_id=action_id,
                        kind=_ACTION_KIND_HALT,
                        target_urn=urn,
                        rationale=(
                            f"INTEGRITY=CRITICAL; evidence path: "
                            f"{' → '.join(outcome.evidence_path)}"
                        ),
                        requires_human_approval=True,
                    )
                )

            elif outcome.recommendation == Recommendation.MONITOR:
                log.info(
                    "plan: MONITOR action planned | action_id=%s target_urn=%s requires_approval=False",
                    action_id,
                    urn,
                )
                actions.append(
                    RecommendedAction(
                        action_id=action_id,
                        kind=_ACTION_KIND_MONITOR,
                        target_urn=urn,
                        rationale=(
                            "One or more quality dimensions are DEGRADED; "
                            "monitoring tag added autonomously."
                        ),
                        requires_human_approval=False,
                    )
                )

        verification_results = verify_all(decision.context_after)
        for vr in verification_results:
            if vr.kind.value == "COUNTEREXAMPLE":
                action_index += 1
                action_id = f"{decision.event.event_id}-{action_index:03d}"
                target = vr.minimal_witness[0].urn if vr.minimal_witness else vr.evidence_path[0]
                log.info(
                    "plan: GOVERNANCE action planned | action_id=%s rule_id=%s target_urn=%s",
                    action_id,
                    vr.rule_id,
                    target,
                )
                actions.append(
                    RecommendedAction(
                        action_id=action_id,
                        kind=_ACTION_KIND_GOVERNANCE,
                        target_urn=target,
                        rationale=(
                            f"Governance rule '{vr.rule_id}' violated: "
                            f"{vr.minimal_witness[0].evidence}"
                            if vr.minimal_witness
                            else f"Governance rule '{vr.rule_id}' violated: see rule"
                        ),
                        requires_human_approval=True,
                    )
                )

        result = sorted(actions, key=lambda a: a.action_id)
        log.info(
            "plan: action plan complete | total_actions=%d halt_actions=%d",
            len(result),
            sum(1 for a in result if a.kind == _ACTION_KIND_HALT),
        )
        return result

    def transition(self, actions: list[RecommendedAction]) -> DataContext:
        log.info(
            "transition: applying %d autonomous action(s)",
            sum(1 for a in actions if not a.requires_human_approval),
        )
        updated = self._context
        for action in actions:
            if action.requires_human_approval:
                log.debug(
                    "transition: skipping human-approval action | action_id=%s kind=%s",
                    action.action_id,
                    action.kind,
                )
                continue
            if action.kind == _ACTION_KIND_MONITOR and action.target_urn in updated.all_urns():
                log.info(
                    "transition: applying monitoring tag | target_urn=%s",
                    action.target_urn,
                )
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
