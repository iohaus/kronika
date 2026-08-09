from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

from application.agent.runtime import DecisionEpisodeRunner
from application.config import settings
from application.datahub.builder import build_context
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.llm.adapter import OpenAILLMAdapter
from application.storage.cache import DuckDBEvidenceStore
from kronika.engine import PublicEngine
from kronika.logging import setup_logging
from kronika.types import EventKind, MetadataEvent

# Activate file-based logging as early as possible so that all module-level
# logger.getLogger() calls below are routed to kronika.log from the first line.
_LOG_FILE = setup_logging(log_dir=Path(__file__).parent / "logs")

log = logging.getLogger("kronika.bridge")
ui_log = logging.getLogger("kronika.ui")


def _short_urn(urn: str) -> str:
    if not urn:
        return ""
    if "(" in urn and ")" in urn:
        inner = urn.split("(")[1].split(")")[0]
        parts = inner.split(",")
        if len(parts) >= 2:
            return parts[1]
    return urn.split(":")[-1]


class ConsoleBridge(QObject):
    graphUpdated = pyqtSignal()
    selectedAssetChanged = pyqtSignal()
    episodeUpdated = pyqtSignal()
    activityLogged = pyqtSignal(str, str, str)  # timestamp, level, message
    propagationStep = pyqtSignal(str, str, str)  # sourceUrn, targetUrn, impact
    pendingActionsChanged = pyqtSignal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

        self._reader = HttpDataHubReader(
            server_url=settings.datahub_server_url,
            token=settings.datahub_token,
            timeout=settings.datahub_timeout_seconds,
        )
        self._writer = HttpDataHubWriter(
            server_url=settings.datahub_server_url,
            token=settings.datahub_token,
            timeout=settings.datahub_timeout_seconds,
            mock_mode=settings.writer_mock_mode,
        )
        self._store = DuckDBEvidenceStore(settings.duckdb_path)
        self._engine = PublicEngine()
        self._llm = OpenAILLMAdapter(
            model=settings.openai_model,
            api_key=settings.openai_api_key,
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout_seconds,
        )
        self._runner = DecisionEpisodeRunner(
            self._engine, self._reader, self._writer, self._store, llm=self._llm
        )

        # Initial engine build
        ctx = build_context(self._reader, max_size=settings.max_world_size)
        self._engine.observe(ctx)

        self._selected_urn: str | None = None
        self._nodes: list[dict[str, Any]] = []
        self._edges: list[dict[str, Any]] = []
        self._current_episode: dict[str, Any] = {
            "event_id": "ep-idle",
            "status": "IDLE",
            "source_urn": "",
            "target_urn": "",
            "halt_set": [],
            "outcomes": {},
            "rationale": "System nominal. Awaiting metadata event observation.",
            "confidence": 1.0,
            "evidence_path": [],
            "deliberation_steps": [
                {"step": "Metadata observation idle", "done": True},
                {"step": "Constraint propagation stand-by", "done": False},
                {"step": "Blast radius calculation stand-by", "done": False},
                {"step": "Containment optimization complete", "done": False},
            ],
        }
        self._logs: list[dict[str, str]] = []
        self._system_status = "HEALTHY"

        self._rebuild_graph_models()
        log.info(
            "ConsoleBridge init complete | log_file=%s assets=%d edges=%d",
            _LOG_FILE,
            len(self._nodes),
            len(self._edges),
        )
        self._log("INFO", "Kronika Console Bridge initialized with healthcare world model.")

    def _log(self, level: str, message: str) -> None:
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        entry = {"timestamp": timestamp, "level": level, "message": message}
        self._logs.append(entry)
        if len(self._logs) > 200:
            self._logs.pop(0)
        self.activityLogged.emit(timestamp, level, message)

    def _rebuild_graph_models(self) -> None:
        self._nodes.clear()
        self._edges.clear()

        # Build node representations
        for urn in self._engine._context.all_urns():
            asset = self._engine._context.asset(urn)
            name = _short_urn(urn)
            # Find status level
            status_vals = [s.value for s in asset.status]
            worst_status = min(status_vals) if status_vals else 0

            status_str = "OPERATIONAL"
            if worst_status == 0:
                status_str = "CRITICAL"
            elif worst_status == 1:
                status_str = "DEGRADED"

            is_halted = urn in self._current_episode.get("halt_set", [])
            if is_halted:
                status_str = "HALTED"

            domain = asset.domain_urn.split(":")[-1] if asset.domain_urn else "default"
            tags = list(asset.tags)

            self._nodes.append(
                {
                    "urn": urn,
                    "name": name,
                    "status": status_str,
                    "domain": domain,
                    "owner": asset.owner_urn or "Unassigned",
                    "tags": tags,
                    "is_halted": is_halted,
                }
            )

        # Build edge representations
        for edge in self._engine._context._edges:
            cols = (
                sorted({m.dst_column for m in edge.column_lineage}) if edge.column_lineage else []
            )
            self._edges.append(
                {
                    "src": edge.src,
                    "dst": edge.dst,
                    "src_name": _short_urn(edge.src),
                    "dst_name": _short_urn(edge.dst),
                    "kind": edge.kind.value,
                    "columns": cols,
                }
            )

        if self._selected_urn is None and self._nodes:
            self._selected_urn = self._nodes[0]["urn"]

        self.graphUpdated.emit()
        self.selectedAssetChanged.emit()

    @pyqtProperty("QVariantList", notify=graphUpdated)
    def graphNodes(self) -> list[dict[str, Any]]:
        return self._nodes

    @pyqtProperty("QVariantList", notify=graphUpdated)
    def graphEdges(self) -> list[dict[str, Any]]:
        return self._edges

    @pyqtProperty("QVariantMap", notify=selectedAssetChanged)
    def selectedAsset(self) -> dict[str, Any]:
        if not self._selected_urn:
            return {}
        for node in self._nodes:
            if node["urn"] == self._selected_urn:
                upstream = [e["src_name"] for e in self._edges if e["dst"] == self._selected_urn]
                downstream = [e["dst_name"] for e in self._edges if e["src"] == self._selected_urn]
                return {
                    **node,
                    "upstream": upstream,
                    "downstream": downstream,
                }
        return {}

    @pyqtProperty("QVariantMap", notify=episodeUpdated)
    def currentEpisode(self) -> dict[str, Any]:
        return self._current_episode

    @pyqtProperty("QVariantList", notify=pendingActionsChanged)
    def pendingActions(self) -> list[dict[str, Any]]:
        return self._store.list_pending_actions()

    @pyqtProperty("QVariantList", notify=activityLogged)
    def activityLogs(self) -> list[dict[str, str]]:
        return self._logs

    @pyqtProperty(str, notify=episodeUpdated)
    def systemStatus(self) -> str:
        return self._system_status

    @pyqtProperty(int, notify=graphUpdated)
    def assetCount(self) -> int:
        return len(self._nodes)

    @pyqtSlot(str)
    def selectAsset(self, urn: str) -> None:
        ui_log.info("UI: selectAsset | urn=%s", urn)
        if self._selected_urn != urn:
            previous = self._selected_urn
            self._selected_urn = urn
            ui_log.debug("UI: asset selection changed | from=%s to=%s", previous, urn)
            self.selectedAssetChanged.emit()

    def _resolve_urn(self, urn: str) -> str:
        all_urns = set(self._engine._context.all_urns())
        if urn in all_urns:
            return urn
        short_target = _short_urn(urn).split(".")[-1]
        for candidate in all_urns:
            if _short_urn(candidate).split(".")[-1] == short_target:
                return candidate
        return urn

    @pyqtSlot(str, str)
    def triggerQualityAnomaly(self, source_urn: str, column_name: str) -> None:
        source_urn = self._resolve_urn(source_urn)
        ui_log.info(
            "UI: triggerQualityAnomaly | source_urn=%s column_name=%s",
            source_urn,
            column_name,
        )
        self._log("WARN", f"Quality anomaly event injected at {source_urn} (Column: {column_name})")

        event_id = f"ev-q-{int(datetime.datetime.now(datetime.timezone.utc).timestamp())}"
        cols = frozenset([column_name]) if column_name else None

        evt = MetadataEvent(
            event_id=event_id,
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=source_urn,
            columns=cols,
            payload=(),
            occurred_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        )

        decision, actions = self._runner.run_episode(evt)
        halt_set = list(decision.evidence.containment.halt_set)

        # Build evidence propagation path
        evidence_path = []
        outcomes_map = {}
        for urn, outcome in decision.evidence.outcomes.items():
            outcomes_map[_short_urn(urn)] = outcome.recommendation.value
            if outcome.evidence_path:
                evidence_path = [_short_urn(p) for p in outcome.evidence_path]

        if not evidence_path:
            evidence_path = [_short_urn(source_urn)]
            for h in halt_set:
                evidence_path.append(_short_urn(h))

        for i in range(len(evidence_path) - 1):
            self.propagationStep.emit(evidence_path[i], evidence_path[i + 1], "PROPAGATED")

        self._system_status = "CONTAINED" if halt_set else "HEALTHY"

        rationale, rationale_degraded = self._llm.explain(decision.evidence, audience="EXECUTIVE")

        self._current_episode = {
            "event_id": event_id,
            "status": "CONTAINMENT_RECOMMENDED" if halt_set else "ANALYZED",
            "source_urn": source_urn,
            "target_urn": halt_set[0] if halt_set else "",
            "halt_set": halt_set,
            "outcomes": outcomes_map,
            "rationale": rationale,
            "rationale_degraded": rationale_degraded,
            "confidence": 0.98,
            "evidence_path": evidence_path,
            "deliberation_steps": [
                {"step": "Observed quality anomaly event", "done": True},
                {"step": "Propagated semantic state constraints", "done": True},
                {"step": "Calculated downstream blast radius", "done": True},
                {"step": f"Optimized containment: HALT {len(halt_set)} asset(s)", "done": True},
            ],
        }

        self._rebuild_graph_models()
        self.episodeUpdated.emit()
        self.pendingActionsChanged.emit()
        ui_log.info(
            "UI: triggerQualityAnomaly complete | event_id=%s system_status=%s halt_set=%s actions=%d",
            event_id,
            self._system_status,
            halt_set,
            len(actions),
        )
        self._log(
            "INFO", f"Episode {event_id} evaluation complete. Recommended actions: {len(actions)}"
        )

    @pyqtSlot(str)
    def approveAction(self, action_id: str) -> None:
        ui_log.info("UI: approveAction | action_id=%s", action_id or "<auto-select>")
        if not action_id:
            pending = self._store.list_pending_actions()
            if pending:
                action_id = pending[0]["action_id"]
                ui_log.debug("UI: approveAction auto-selected | action_id=%s", action_id)

        resolved = self._store.resolve_pending_action(action_id, "APPROVED")
        if resolved:
            self._log("INFO", f"Action {action_id} APPROVED by operator.")
            if resolved.get("kind") == "HALT_PIPELINE":
                target_urn = resolved.get("target_urn", "")
                self._writer.create_incident(
                    urn=target_urn,
                    title=f"Approved Halt: {action_id}",
                    description=resolved.get("rationale", ""),
                    event_id=resolved.get("event_id", ""),
                )
                self._log("WARN", f"INCIDENT CREATED: Pipeline halted for {target_urn}")
            self._current_episode["status"] = "APPROVED"
            ui_log.info(
                "UI: approveAction resolved | action_id=%s target_urn=%s system_status=%s",
                action_id,
                resolved.get("target_urn", ""),
                self._system_status,
            )
            self.pendingActionsChanged.emit()
            self.episodeUpdated.emit()
            self._rebuild_graph_models()
        elif self._current_episode.get("halt_set"):
            target_urn = (
                self._current_episode.get("target_urn") or self._current_episode["halt_set"][0]
            )
            self._writer.create_incident(
                urn=target_urn,
                title="Approved Halt: Containment Action",
                description=self._current_episode.get("rationale", ""),
                event_id=self._current_episode.get("event_id", ""),
            )
            self._log("INFO", f"Action APPROVED by operator for target {target_urn}.")
            self._log("WARN", f"INCIDENT CREATED: Pipeline halted for {target_urn}")
            self._current_episode["status"] = "APPROVED"
            ui_log.info(
                "UI: approveAction fallback resolved | target_urn=%s system_status=%s",
                target_urn,
                self._system_status,
            )
            self.pendingActionsChanged.emit()
            self.episodeUpdated.emit()
            self._rebuild_graph_models()

    @pyqtSlot(str)
    def rejectAction(self, action_id: str) -> None:
        ui_log.info("UI: rejectAction | action_id=%s", action_id or "<auto-select>")
        if not action_id:
            pending = self._store.list_pending_actions()
            if pending:
                action_id = pending[0]["action_id"]
                ui_log.debug("UI: rejectAction auto-selected | action_id=%s", action_id)

        resolved = self._store.resolve_pending_action(action_id, "REJECTED")
        previous_status = self._system_status
        self._current_episode["status"] = "REJECTED"
        self._current_episode["halt_set"] = []
        self._system_status = "HEALTHY"
        ui_log.info(
            "UI: rejectAction resolved | action_id=%s system_status %s → HEALTHY",
            action_id,
            previous_status,
        )

        if resolved:
            self._log(
                "INFO", f"Action {action_id} REJECTED by operator — pipeline halt overridden."
            )
        else:
            self._log("INFO", "Action REJECTED by operator — pipeline halt overridden.")

        self.pendingActionsChanged.emit()
        self.episodeUpdated.emit()
        self._rebuild_graph_models()

    @pyqtSlot()
    def resetDemo(self) -> None:
        ui_log.info("UI: resetDemo | previous_status=%s", self._system_status)
        self._store = DuckDBEvidenceStore(settings.duckdb_path)
        self._engine = PublicEngine()
        self._runner = DecisionEpisodeRunner(
            self._engine, self._reader, self._writer, self._store, llm=self._llm
        )
        ctx = build_context(self._reader, max_size=settings.max_world_size)
        self._engine.observe(ctx)
        self._system_status = "HEALTHY"
        self._current_episode = {
            "event_id": "ep-reset",
            "status": "IDLE",
            "source_urn": "",
            "target_urn": "",
            "halt_set": [],
            "outcomes": {},
            "rationale": "World model reset to nominal operational baseline.",
            "confidence": 1.0,
            "evidence_path": [],
            "deliberation_steps": [
                {"step": "Metadata observation idle", "done": True},
                {"step": "Constraint propagation stand-by", "done": False},
                {"step": "Blast radius calculation stand-by", "done": False},
                {"step": "Containment optimization complete", "done": False},
            ],
        }
        self._rebuild_graph_models()
        self.episodeUpdated.emit()
        self.pendingActionsChanged.emit()
        ui_log.info(
            "UI: resetDemo complete | assets=%d edges=%d", len(self._nodes), len(self._edges)
        )
        self._log("INFO", "World model and decision store reset.")
