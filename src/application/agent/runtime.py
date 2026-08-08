from __future__ import annotations

import logging

from application.datahub.builder import build_context
from application.llm.adapter import LocalLLMAdapter
from application.storage.cache import DuckDBEvidenceStore
from kronika.engine import PublicEngine
from kronika.ports import (
    DataHubReader,
    DataHubWriter,
    DecisionRecord,
    LLMAdapter,
    RecommendedAction,
)
from kronika.types import MetadataEvent

log = logging.getLogger("kronika.application.runner")


class DecisionEpisodeRunner:
    def __init__(
        self,
        engine: PublicEngine,
        reader: DataHubReader,
        writer: DataHubWriter,
        store: DuckDBEvidenceStore,
        llm: LLMAdapter | None = None,
    ) -> None:
        self.engine = engine
        self.reader = reader
        self.writer = writer
        self.store = store
        self.llm = llm or LocalLLMAdapter()

    def run_episode(self, event: MetadataEvent) -> tuple[DecisionRecord, list[RecommendedAction]]:
        log.info(
            "run_episode: start | event_id=%s kind=%s source_urn=%s",
            event.event_id,
            event.kind.value,
            event.source_urn,
        )

        log.debug("run_episode: building data context from reader")
        ctx = build_context(self.reader)
        self.engine.observe(ctx)

        log.debug("run_episode: reasoning over event")
        decision = self.engine.reason(event)

        log.debug("run_episode: planning actions")
        actions = self.engine.plan(decision)

        approval_count = sum(1 for a in actions if a.requires_human_approval)
        autonomous_count = len(actions) - approval_count
        log.info(
            "run_episode: actions planned | total=%d pending_approval=%d autonomous=%d",
            len(actions),
            approval_count,
            autonomous_count,
        )

        # Generate audience-tailored explanations and surface them in the log so
        # they are always observable in kronika.log regardless of UI state.
        for audience in ("ENGINEER", "OWNER", "EXECUTIVE"):
            explanation = self.llm.explain(decision.evidence, audience=audience)
            indented = "\n\t".join(explanation.splitlines())
            log.info(
                "run_episode: llm_rationale [%s] event_id=%s\n\n\t%s\n",
                audience,
                event.event_id,
                indented,
            )

        for action in actions:
            if action.requires_human_approval:
                log.info(
                    "run_episode: queuing action | action_id=%s kind=%s target_urn=%s",
                    action.action_id,
                    action.kind,
                    action.target_urn,
                )
                self.store.save_pending_action(action, event.event_id)
            else:
                if action.kind == "ADD_MONITORING_TAG":
                    log.info(
                        "run_episode: executing autonomous annotation | action_id=%s target_urn=%s",
                        action.action_id,
                        action.target_urn,
                    )
                    self.writer.add_annotation(
                        urn=action.target_urn,
                        key="kronika_monitoring",
                        value="true",
                        event_id=event.event_id,
                    )

        log.debug("run_episode: persisting evidence record | event_id=%s", event.event_id)
        self.store.save(decision.evidence)
        self.engine.transition([a for a in actions if not a.requires_human_approval])

        log.info(
            "run_episode: complete | event_id=%s halt_set=%s",
            event.event_id,
            sorted(decision.evidence.containment.halt_set),
        )
        return decision, actions
