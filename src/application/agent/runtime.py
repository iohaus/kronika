from __future__ import annotations

from application.datahub.builder import build_context
from application.storage.cache import DuckDBEvidenceStore
from kronika.engine import PublicEngine
from kronika.ports import DataHubReader, DataHubWriter, DecisionRecord, RecommendedAction
from kronika.types import MetadataEvent


class DecisionEpisodeRunner:
    def __init__(
        self,
        engine: PublicEngine,
        reader: DataHubReader,
        writer: DataHubWriter,
        store: DuckDBEvidenceStore,
    ) -> None:
        self.engine = engine
        self.reader = reader
        self.writer = writer
        self.store = store

    def run_episode(self, event: MetadataEvent) -> tuple[DecisionRecord, list[RecommendedAction]]:
        ctx = build_context(self.reader)
        self.engine.observe(ctx)

        decision = self.engine.reason(event)
        actions = self.engine.plan(decision)

        for action in actions:
            if action.requires_human_approval:
                self.store.save_pending_action(action, event.event_id)
            else:
                if action.kind == "ADD_MONITORING_TAG":
                    self.writer.add_annotation(
                        urn=action.target_urn,
                        key="kronika_monitoring",
                        value="true",
                        event_id=event.event_id,
                    )

        self.store.save(decision.evidence)
        self.engine.transition([a for a in actions if not a.requires_human_approval])

        return decision, actions
