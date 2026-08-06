from __future__ import annotations

import datetime
import logging
import sys
from pathlib import Path
from typing import Any

from PyQt6.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

# Add ops to path to import seed dataset if available
ops_dir = str(Path(__file__).parent.parent / "ops")
if ops_dir not in sys.path:
    sys.path.insert(0, ops_dir)

try:
    # pyrefly: ignore [missing-import]
    from seed_healthcare import get_healthcare_dataset
    _INITIAL_MOCK_DATA = get_healthcare_dataset()
except ImportError:
    _INITIAL_MOCK_DATA = None

from application.agent.runtime import DecisionEpisodeRunner
from application.datahub.builder import build_context
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.storage.cache import DuckDBEvidenceStore
from kronika.engine import PublicEngine
from kronika.types import EventKind, MetadataEvent

log = logging.getLogger("kronika.bridge")


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

        self._mock_data = _INITIAL_MOCK_DATA
        self._reader = HttpDataHubReader(mock_data=self._mock_data)
        self._writer = HttpDataHubWriter(mock_mode=True)
        self._store = DuckDBEvidenceStore(":memory:")
        self._engine = PublicEngine()
        self._runner = DecisionEpisodeRunner(self._engine, self._reader, self._writer, self._store)

        # Initial engine build
        ctx = build_context(self._reader)
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

            self._nodes.append({
                "urn": urn,
                "name": name,
                "status": status_str,
                "domain": domain,
                "owner": asset.owner_urn or "Unassigned",
                "tags": tags,
                "is_halted": is_halted,
            })

        # Build edge representations
        for edge in self._engine._context._edges:
            cols = list(edge.columns) if edge.columns else []
            self._edges.append({
                "src": edge.src,
                "dst": edge.dst,
                "src_name": _short_urn(edge.src),
                "dst_name": _short_urn(edge.dst),
                "kind": edge.kind.value,
                "columns": cols,
            })

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
        if self._selected_urn != urn:
            self._selected_urn = urn
            self.selectedAssetChanged.emit()

    @pyqtSlot(str, str)
    def triggerQualityAnomaly(self, source_urn: str, column_name: str) -> None:
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

        rationale = f"Negative quality observation on {column_name or 'dataset'} violates validity constraints. Propagation blast radius identified."
        if halt_set:
            rationale += f" Automatic containment recommended for {', '.join(_short_urn(h) for h in halt_set)}."

        self._current_episode = {
            "event_id": event_id,
            "status": "CONTAINMENT_RECOMMENDED" if halt_set else "ANALYZED",
            "source_urn": source_urn,
            "target_urn": halt_set[0] if halt_set else "",
            "halt_set": halt_set,
            "outcomes": outcomes_map,
            "rationale": rationale,
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
        self._log("INFO", f"Episode {event_id} evaluation complete. Recommended actions: {len(actions)}")

    @pyqtSlot(str)
    def approveAction(self, action_id: str) -> None:
        if not action_id:
            pending = self._store.list_pending_actions()
            if pending:
                action_id = pending[0]["action_id"]

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
            self.pendingActionsChanged.emit()
            self.episodeUpdated.emit()
            self._rebuild_graph_models()
        elif self._current_episode.get("halt_set"):
            target_urn = self._current_episode.get("target_urn") or self._current_episode["halt_set"][0]
            self._writer.create_incident(
                urn=target_urn,
                title="Approved Halt: Containment Action",
                description=self._current_episode.get("rationale", ""),
                event_id=self._current_episode.get("event_id", ""),
            )
            self._log("INFO", f"Action APPROVED by operator for target {target_urn}.")
            self._log("WARN", f"INCIDENT CREATED: Pipeline halted for {target_urn}")
            self._current_episode["status"] = "APPROVED"
            self.pendingActionsChanged.emit()
            self.episodeUpdated.emit()
            self._rebuild_graph_models()

    @pyqtSlot(str)
    def rejectAction(self, action_id: str) -> None:
        if not action_id:
            pending = self._store.list_pending_actions()
            if pending:
                action_id = pending[0]["action_id"]

        resolved = self._store.resolve_pending_action(action_id, "REJECTED")
        self._current_episode["status"] = "REJECTED"
        self._current_episode["halt_set"] = []
        self._system_status = "HEALTHY"

        if resolved:
            self._log("INFO", f"Action {action_id} REJECTED by operator — pipeline halt overridden.")
        else:
            self._log("INFO", "Action REJECTED by operator — pipeline halt overridden.")

        self.pendingActionsChanged.emit()
        self.episodeUpdated.emit()
        self._rebuild_graph_models()

    @pyqtSlot()
    def resetDemo(self) -> None:
        self._store = DuckDBEvidenceStore(":memory:")
        self._engine = PublicEngine()
        self._runner = DecisionEpisodeRunner(self._engine, self._reader, self._writer, self._store)
        ctx = build_context(self._reader)
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
        self._log("INFO", "World model and decision store reset.")
