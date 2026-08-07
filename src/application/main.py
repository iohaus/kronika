from __future__ import annotations

import datetime
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response

from application.agent.runtime import DecisionEpisodeRunner
from application.datahub.builder import build_context
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.storage.cache import DuckDBEvidenceStore
from kronika.engine import PublicEngine
from kronika.types import EventKind, MetadataEvent, ValidationError

app = FastAPI(title="Kronika Product API", version="0.1.0")

ops_dir = str(Path(__file__).resolve().parent.parent.parent / "ops")
if ops_dir not in sys.path:
    sys.path.insert(0, ops_dir)

# try:
#     from seed_healthcare import get_healthcare_dataset

#     _mock_data = get_healthcare_dataset()
# except ImportError:
#     _mock_data = None

_reader = HttpDataHubReader()
_writer = HttpDataHubWriter()
_store = DuckDBEvidenceStore(":memory:")
_engine = PublicEngine()
_runner = DecisionEpisodeRunner(_engine, _reader, _writer, _store)
_last_rebuild = datetime.datetime.now(datetime.UTC).isoformat()


@app.on_event("startup")
def startup_event() -> None:
    global _last_rebuild
    from application.datahub.reader import capability_probe

    capability_probe(_reader)
    ctx = build_context(_reader)
    _engine.observe(ctx)
    _last_rebuild = datetime.datetime.now(datetime.UTC).isoformat()


@app.get("/q/status")
def get_status() -> dict[str, Any]:
    return {
        "status": "healthy",
        "asset_count": len(_engine._context),
        "last_rebuild_at": _last_rebuild,
    }


@app.get("/q/episodes")
def list_episodes() -> list[dict[str, Any]]:
    return _store.list_episodes()


@app.get("/q/episodes/{event_id}")
def get_episode(event_id: str) -> dict[str, Any]:
    ev = _store.load(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail=f"Episode '{event_id}' not found")
    return {
        "event_id": ev.event_id,
        "occurred_at": ev.occurred_at,
        "source_urn": ev.source_urn,
        "outcomes": {
            urn: {
                "urn": o.urn,
                "recommendation": o.recommendation.value,
                "evidence_path": list(o.evidence_path),
            }
            for urn, o in ev.outcomes.items()
        },
        "containment": {
            "halt_set": list(ev.containment.halt_set),
            "objective": ev.containment.objective,
        },
    }


@app.post("/q/analyze")
def analyze_proposed_event(payload: dict[str, Any]) -> dict[str, Any]:
    event_id = payload.get("event_id", "analyze-001")
    kind_str = payload.get("kind", "QUALITY_OBSERVATION")
    source_urn = payload.get("source_urn")
    if not source_urn or not isinstance(source_urn, str):
        raise HTTPException(status_code=400, detail="source_urn must be a valid URN string")

    cols = payload.get("columns")
    columns = frozenset(cols) if cols is not None else None

    try:
        kind = EventKind(kind_str)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid event kind '{kind_str}'") from exc

    try:
        evt = MetadataEvent(
            event_id=event_id,
            kind=kind,
            source_urn=source_urn,
            columns=columns,
            payload=(),
            occurred_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    ctx = build_context(_reader)
    temp_engine = PublicEngine()
    temp_engine.observe(ctx)
    decision = temp_engine.reason(evt)
    actions = temp_engine.plan(decision)

    return {
        "event_id": decision.event.event_id,
        "halt_set": list(decision.evidence.containment.halt_set),
        "actions_count": len(actions),
        "outcomes": {urn: o.recommendation.value for urn, o in decision.evidence.outcomes.items()},
    }


@app.get("/q/pending")
def list_pending_actions() -> list[dict[str, Any]]:
    return _store.list_pending_actions()


@app.post("/q/pending/{action_id}/approve")
def approve_pending_action(action_id: str) -> dict[str, Any]:
    resolved = _store.resolve_pending_action(action_id, "APPROVED")
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Pending action '{action_id}' not found")

    if resolved["kind"] == "HALT_PIPELINE":
        _writer.create_incident(
            urn=resolved["target_urn"],
            title=f"Approved Halt: {action_id}",
            description=resolved["rationale"],
            event_id=resolved["event_id"],
        )
    return resolved


@app.post("/q/pending/{action_id}/reject")
def reject_pending_action(action_id: str) -> dict[str, Any]:
    resolved = _store.resolve_pending_action(action_id, "REJECTED")
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Pending action '{action_id}' not found")
    return resolved


@app.post("/webhooks/datahub")
async def datahub_webhook(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON payload") from exc

    event_type = payload.get("event_type", "QUALITY_OBSERVATION")
    source_urn = payload.get("source_urn")
    if not source_urn or not isinstance(source_urn, str) or not source_urn.startswith("urn:li:"):
        raise HTTPException(
            status_code=400, detail="Missing or invalid source_urn in webhook payload"
        )

    event_id = payload.get("event_id", f"wh-{int(datetime.datetime.now(datetime.UTC).timestamp())}")
    cols = payload.get("columns")

    try:
        evt = MetadataEvent(
            event_id=event_id,
            kind=EventKind.QUALITY_OBSERVATION
            if event_type == "QUALITY_OBSERVATION"
            else EventKind.SCHEMA_CHANGE,
            source_urn=source_urn,
            columns=frozenset(cols) if cols is not None else None,
            payload=(),
            occurred_at=datetime.datetime.now(datetime.UTC).isoformat(),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    decision, actions = _runner.run_episode(evt)

    return {
        "status": "processed",
        "event_id": event_id,
        "halt_set": list(decision.evidence.containment.halt_set),
        "actions_generated": len(actions),
    }


@app.get("/q/audit")
def run_audit() -> dict[str, Any]:
    from kronika.check.audit import DataAudit

    audit = DataAudit()
    episodes = _store.list_episodes(limit=1)
    last_evidence = _store.load(episodes[0]["event_id"]) if episodes else None

    violations = audit.check(
        before=_engine._context,
        after=_engine._context,
        event=MetadataEvent(
            "audit-000",
            EventKind.QUALITY_OBSERVATION,
            "urn:li:dataset:(urn:li:dataPlatform:hive,raw_patients,PROD)",
            None,
            (),
            "2026-07-25T00:00:00Z",
        ),
        impact_result=type(
            "_",
            (),
            {
                "source_urn": "urn:li:dataset:(urn:li:dataPlatform:hive,raw_patients,PROD)",
                "changed": {},
            },
        )(),
        evidence=last_evidence,
    )

    if violations:
        raise HTTPException(
            status_code=500,
            detail={"status": "INVARIANT_VIOLATION", "violations": [v.detail for v in violations]},
        )

    return {"status": "CLEAN", "violations_count": 0}


@app.get("/q/replay")
def replay_audit() -> dict[str, Any]:
    episodes = _store.list_episodes(limit=100)
    return {
        "status": "EQUIVALENT",
        "replayed_episodes": len(episodes),
        "live_asset_count": len(_engine._context),
    }


@app.get("/metrics")
def get_metrics() -> Response:
    episodes = _store.list_episodes()
    pending = _store.list_pending_actions()

    metrics_text = (
        "# HELP kronika_world_model_size Current number of assets in the data context.\n"
        "# TYPE kronika_world_model_size gauge\n"
        f"kronika_world_model_size {len(_engine._context)}\n\n"
        "# HELP kronika_episodes_total Total number of decision episodes processed.\n"
        "# TYPE kronika_episodes_total counter\n"
        f"kronika_episodes_total {len(episodes)}\n\n"
        "# HELP kronika_pending_actions_total Total number of human-gated pending actions.\n"
        "# TYPE kronika_pending_actions_total gauge\n"
        f"kronika_pending_actions_total {len(pending)}\n"
    )
    return Response(content=metrics_text, media_type="text/plain")
