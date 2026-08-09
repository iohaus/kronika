from __future__ import annotations

import json
import logging
from typing import Any

from kronika.evidence import EvidenceRecord
from kronika.ports import EvidenceStore, RecommendedAction

log = logging.getLogger("kronika.application.store")


class DuckDBEvidenceStore(EvidenceStore):
    def __init__(self, db_path: str = ":memory:") -> None:
        self.db_path = db_path
        self._memory_evidence: dict[str, EvidenceRecord] = {}
        self._memory_pending: dict[str, dict[str, Any]] = {}
        self._memory_explanations: dict[str, dict[str, dict[str, Any]]] = {}
        self._use_duckdb = False

        if db_path != ":memory:":
            try:
                import duckdb

                self.conn = duckdb.connect(db_path)
                self._init_db()
                self._use_duckdb = True
                log.info("DuckDBEvidenceStore: DuckDB backend initialised | path=%s", db_path)
            except Exception as exc:
                self._use_duckdb = False
                log.warning(
                    "DuckDBEvidenceStore: DuckDB unavailable, falling back to in-memory | error=%s",
                    exc,
                )
        else:
            log.info("DuckDBEvidenceStore: in-memory mode (no persistence)")

    def _init_db(self) -> None:
        if not self._use_duckdb:
            return
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS evidence_records (
                event_id VARCHAR PRIMARY KEY,
                occurred_at VARCHAR,
                source_urn VARCHAR,
                payload_json VARCHAR
            );
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS pending_actions (
                action_id VARCHAR PRIMARY KEY,
                event_id VARCHAR,
                kind VARCHAR,
                target_urn VARCHAR,
                rationale VARCHAR,
                requires_human_approval BOOLEAN,
                status VARCHAR
            );
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS llm_explanations (
                event_id VARCHAR,
                audience VARCHAR,
                text VARCHAR,
                degraded BOOLEAN,
                PRIMARY KEY (event_id, audience)
            );
        """)

    def save(self, evidence: EvidenceRecord) -> None:
        log.info(
            "store.save: persisting evidence | event_id=%s source_urn=%s outcomes=%d halt_set=%s",
            evidence.event_id,
            evidence.source_urn,
            len(evidence.outcomes),
            sorted(evidence.containment.halt_set),
        )
        payload = {
            "event_id": evidence.event_id,
            "occurred_at": evidence.occurred_at,
            "source_urn": evidence.source_urn,
            "outcomes": {
                urn: {
                    "urn": o.urn,
                    "recommendation": o.recommendation.value,
                    "status_before": [s.value for s in o.status_before],
                    "status_after": [s.value for s in o.status_after],
                    "evidence_path": list(o.evidence_path),
                    "rule_results": [
                        {"rule_id": r.rule_id, "outcome": r.outcome.value, "witness": r.witness}
                        for r in o.rule_results
                    ],
                }
                for urn, o in evidence.outcomes.items()
            },
            "containment": {
                "halt_set": list(evidence.containment.halt_set),
                "objective": evidence.containment.objective,
                "rationale": evidence.containment.rationale,
            },
        }

        self._memory_evidence[evidence.event_id] = evidence

        if self._use_duckdb:
            payload_str = json.dumps(payload)
            self.conn.execute(
                "INSERT OR REPLACE INTO evidence_records VALUES (?, ?, ?, ?)",
                [evidence.event_id, evidence.occurred_at, evidence.source_urn, payload_str],
            )
            log.debug("store.save: DuckDB write complete | event_id=%s", evidence.event_id)

    def load(self, event_id: str) -> EvidenceRecord | None:
        return self._memory_evidence.get(event_id)

    def list_episodes(self, limit: int = 50) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for ev in list(self._memory_evidence.values())[-limit:]:
            results.append(
                {
                    "event_id": ev.event_id,
                    "occurred_at": ev.occurred_at,
                    "source_urn": ev.source_urn,
                    "outcomes_count": len(ev.outcomes),
                    "halt_set": list(ev.containment.halt_set),
                }
            )
        return results

    def save_pending_action(self, action: RecommendedAction, event_id: str) -> None:
        log.info(
            "store.save_pending_action: queuing | action_id=%s kind=%s target_urn=%s event_id=%s",
            action.action_id,
            action.kind,
            action.target_urn,
            event_id,
        )
        record = {
            "action_id": action.action_id,
            "event_id": event_id,
            "kind": action.kind,
            "target_urn": action.target_urn,
            "rationale": action.rationale,
            "requires_human_approval": action.requires_human_approval,
            "status": "PENDING",
        }
        self._memory_pending[action.action_id] = record

        if self._use_duckdb:
            self.conn.execute(
                "INSERT OR REPLACE INTO pending_actions VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    action.action_id,
                    event_id,
                    action.kind,
                    action.target_urn,
                    action.rationale,
                    action.requires_human_approval,
                    "PENDING",
                ],
            )

    def list_pending_actions(self) -> list[dict[str, Any]]:
        result = [r for r in self._memory_pending.values() if r["status"] == "PENDING"]
        log.debug("store.list_pending_actions: pending_count=%d", len(result))
        return result

    def resolve_pending_action(self, action_id: str, status: str) -> dict[str, Any] | None:
        target_id = action_id
        if target_id not in self._memory_pending:
            # Fallback: check if action_id is an event_id and find pending action
            matching = [
                aid
                for aid, r in self._memory_pending.items()
                if r.get("event_id") == action_id and r.get("status") == "PENDING"
            ]
            if matching:
                target_id = matching[0]

        if target_id not in self._memory_pending:
            log.warning("store.resolve_pending_action: unknown action | action_id=%s", action_id)
            return None

        record = self._memory_pending[target_id]
        previous_status = record["status"]
        record["status"] = status.upper()
        log.info(
            "store.resolve_pending_action: resolved | action_id=%s %s → %s",
            target_id,
            previous_status,
            record["status"],
        )

        if self._use_duckdb:
            self.conn.execute(
                "UPDATE pending_actions SET status = ? WHERE action_id = ?",
                [status.upper(), target_id],
            )
            log.debug(
                "store.resolve_pending_action: DuckDB update complete | action_id=%s", target_id
            )
        return record

    def save_explanation(self, event_id: str, audience: str, text: str, degraded: bool) -> None:
        record = {"text": text, "degraded": degraded}
        self._memory_explanations.setdefault(event_id, {})[audience] = record
        log.debug(
            "store.save_explanation: saved | event_id=%s audience=%s degraded=%s",
            event_id,
            audience,
            degraded,
        )

        if self._use_duckdb:
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_explanations VALUES (?, ?, ?, ?)",
                [event_id, audience, text, degraded],
            )

    def load_explanations(self, event_id: str) -> dict[str, dict[str, Any]]:
        return self._memory_explanations.get(event_id, {})

    def resolve_all_pending_actions(self, status: str) -> list[dict[str, Any]]:
        pending = [r for r in self._memory_pending.values() if r.get("status") == "PENDING"]
        resolved: list[dict[str, Any]] = []
        for r in pending:
            res = self.resolve_pending_action(r["action_id"], status)
            if res:
                resolved.append(res)
        log.info(
            "store.resolve_all_pending_actions: batch resolved %d actions to %s",
            len(resolved),
            status,
        )
        return resolved
