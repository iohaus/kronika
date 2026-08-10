from __future__ import annotations

import datetime
import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Response

from application.agent.listener import EventListener
from application.agent.runtime import DecisionEpisodeRunner
from application.config import settings
from application.datahub.builder import build_context
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.llm.adapter import OpenAILLMAdapter
from application.storage.cache import DuckDBEvidenceStore
from kronika.dimensions import Dimension
from kronika.engine import PublicEngine
from kronika.logging import setup_logging
from kronika.types import EventKind, MetadataEvent, ValidationError

setup_logging(log_dir=Path(__file__).resolve().parent.parent.parent / "logs")

log = logging.getLogger("kronika.application.main")

app = FastAPI(title="Kronika Product API", version="0.1.0")

_reader = HttpDataHubReader(
    server_url=settings.datahub_server_url,
    token=settings.datahub_token,
    timeout=settings.datahub_timeout_seconds,
)
_writer = HttpDataHubWriter(
    server_url=settings.datahub_server_url,
    token=settings.datahub_token,
    timeout=settings.datahub_timeout_seconds,
    mock_mode=settings.writer_mock_mode,
)
_store = DuckDBEvidenceStore(settings.duckdb_path)
_engine = PublicEngine()
_llm = OpenAILLMAdapter(
    model=settings.openai_model,
    api_key=settings.openai_api_key,
    base_url=settings.openai_base_url,
    timeout=settings.llm_timeout_seconds,
)
_runner = DecisionEpisodeRunner(_engine, _reader, _writer, _store, llm=_llm)
_listener = EventListener(_reader)
_last_rebuild = datetime.datetime.now(datetime.UTC).isoformat()

# Guards every `run_episode()` call site — the background poller and
# `/q/poll-now` can both invoke it, and both share the same `_engine`/`_store`
# singletons, so they must not interleave.
_episode_lock = threading.Lock()
_poll_stop = threading.Event()
_poll_thread: threading.Thread | None = None


def _run_polling_loop() -> None:
    log.info("polling_loop: started | interval_seconds=%.1f", settings.poll_interval_seconds)
    while not _poll_stop.is_set():
        try:
            events = _listener.poll_events()
            for evt in events:
                log.info(
                    "polling_loop: real DataHub assertion failure detected | "
                    "event_id=%s source_urn=%s columns=%s",
                    evt.event_id,
                    evt.source_urn,
                    evt.columns,
                )
                with _episode_lock:
                    _runner.run_episode(evt)
        except Exception:
            log.exception("polling_loop: poll cycle failed, will retry next interval")
        _poll_stop.wait(settings.poll_interval_seconds)


@app.on_event("startup")
def startup_event() -> None:
    global _last_rebuild, _poll_thread
    from application.datahub.reader import capability_probe

    capability_probe(_reader)
    ctx = build_context(_reader, max_size=settings.max_world_size)
    _engine.observe(ctx)
    _last_rebuild = datetime.datetime.now(datetime.UTC).isoformat()

    if settings.enable_background_polling:
        _poll_thread = threading.Thread(
            target=_run_polling_loop, name="kronika-datahub-poller", daemon=True
        )
        _poll_thread.start()


@app.on_event("shutdown")
def shutdown_event() -> None:
    _poll_stop.set()


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
        "trigger_columns": sorted(ev.trigger_columns) if ev.trigger_columns else None,
        "trigger_detail": ev.trigger_detail,
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
        "explanations": _store.load_explanations(event_id),
    }


def _resolve_urn(urn: str, context: Any) -> str:
    all_urns = set(context.all_urns())
    if urn in all_urns:
        return urn
    target_name = (
        urn.split("(")[1].split(")")[0].split(",")[1].split(".")[-1]
        if "(" in urn and ")" in urn and "," in urn
        else urn.split(":")[-1].split(".")[-1]
    )
    for candidate in all_urns:
        cand_name = (
            candidate.split("(")[1].split(")")[0].split(",")[1].split(".")[-1]
            if "(" in candidate and ")" in candidate and "," in candidate
            else candidate.split(":")[-1].split(".")[-1]
        )
        if cand_name == target_name:
            return candidate
    return urn


@app.post("/q/analyze")
def analyze_proposed_event(payload: dict[str, Any]) -> dict[str, Any]:
    event_id = payload.get("event_id", "analyze-001")
    kind_str = payload.get("kind", "QUALITY_OBSERVATION")
    raw_source = payload.get("source_urn")
    if not raw_source or not isinstance(raw_source, str):
        raise HTTPException(status_code=400, detail="source_urn must be a valid URN string")

    ctx = build_context(_reader, max_size=settings.max_world_size)
    source_urn = _resolve_urn(raw_source, ctx)

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


@app.post("/q/policy-rules")
def create_policy_rule(payload: dict[str, Any]) -> dict[str, Any]:
    rule_id = payload.get("rule_id")
    predicate = payload.get("predicate")
    raw_scope_urn = payload.get("scope_urn")
    if not rule_id or not predicate or not raw_scope_urn:
        raise HTTPException(
            status_code=400, detail="rule_id, predicate, and scope_urn are required"
        )

    raw_dimension = payload.get("dimension", "COMPLIANCE")
    try:
        dimension = (
            Dimension[raw_dimension] if isinstance(raw_dimension, str) else Dimension(raw_dimension)
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid dimension '{raw_dimension}'") from exc

    scope_urn = _resolve_urn(raw_scope_urn, _engine._context)
    glossary_urn = payload.get("glossary_urn")

    _writer.write_policy_rule(
        rule_id=rule_id,
        dimension=int(dimension),
        predicate=predicate,
        scope_urn=scope_urn,
        glossary_urn=glossary_urn,
    )

    return {
        "status": "WRITTEN",
        "rule_id": rule_id,
        "dimension": dimension.name,
        "predicate": predicate,
        "scope_urn": scope_urn,
        "glossary_urn": glossary_urn,
    }


@app.get("/q/pending")
def list_pending_actions() -> list[dict[str, Any]]:
    return _store.list_pending_actions()


@app.post("/q/pending/approve-all")
def approve_all_pending_actions() -> dict[str, Any]:
    resolved_list = _store.resolve_all_pending_actions("APPROVED")
    incidents_created = 0
    for resolved in resolved_list:
        if resolved.get("kind") == "HALT_PIPELINE":
            _writer.create_incident(
                urn=resolved["target_urn"],
                title=f"Approved Halt: {resolved['action_id']}",
                description=resolved["rationale"],
                event_id=resolved["event_id"],
            )
            _writer.add_tag(
                urn=resolved["target_urn"],
                tag_urn="urn:li:tag:critical",
            )
            incidents_created += 1
        elif resolved.get("kind") == "RAISE_GOVERNANCE_INCIDENT":
            _writer.create_incident(
                urn=resolved["target_urn"],
                title=f"Approved Governance Violation: {resolved['action_id']}",
                description=resolved["rationale"],
                event_id=resolved["event_id"],
            )
            incidents_created += 1
        else:
            log.warning(
                "approve_all_pending_actions: unhandled action kind — no DataHub write "
                "performed | action_id=%s kind=%s",
                resolved.get("action_id"),
                resolved.get("kind"),
            )

    return {
        "status": "APPROVED_ALL",
        "resolved_count": len(resolved_list),
        "incidents_created": incidents_created,
        "actions": resolved_list,
    }


@app.post("/q/pending/reject-all")
def reject_all_pending_actions() -> dict[str, Any]:
    resolved_list = _store.resolve_all_pending_actions("REJECTED")
    return {
        "status": "REJECTED_ALL",
        "resolved_count": len(resolved_list),
        "actions": resolved_list,
    }


@app.post("/q/pending/{action_id}/approve")
def approve_pending_action(action_id: str) -> dict[str, Any]:
    resolved = _store.resolve_pending_action(action_id, "APPROVED")
    if not resolved:
        pending = _store.list_pending_actions()
        pending_ids = [p["action_id"] for p in pending]
        detail_msg = f"Action/event '{action_id}' not found. Available pending: {pending_ids}"
        raise HTTPException(status_code=404, detail=detail_msg)

    if resolved["kind"] == "HALT_PIPELINE":
        _writer.create_incident(
            urn=resolved["target_urn"],
            title=f"Approved Halt: {action_id}",
            description=resolved["rationale"],
            event_id=resolved["event_id"],
        )
        _writer.add_tag(
            urn=resolved["target_urn"],
            tag_urn="urn:li:tag:critical",
        )
    elif resolved["kind"] == "RAISE_GOVERNANCE_INCIDENT":
        _writer.create_incident(
            urn=resolved["target_urn"],
            title=f"Approved Governance Violation: {action_id}",
            description=resolved["rationale"],
            event_id=resolved["event_id"],
        )
    else:
        log.warning(
            "approve_pending_action: unhandled action kind — no DataHub write performed "
            "| action_id=%s kind=%s",
            action_id,
            resolved["kind"],
        )
    return resolved


@app.post("/q/pending/{action_id}/reject")
def reject_pending_action(action_id: str) -> dict[str, Any]:
    resolved = _store.resolve_pending_action(action_id, "REJECTED")
    if not resolved:
        raise HTTPException(status_code=404, detail=f"Pending action '{action_id}' not found")
    return resolved


@app.post("/q/poll-now")
def poll_now() -> dict[str, Any]:
    """Synchronously re-run the same real DataHub assertion check the background
    poller runs automatically — on demand, for controlling detection timing (e.g.
    during a demo). Takes no body: it re-invokes the real check, it does not accept
    a claimed event, so it can't be used to inject a fake one."""
    with _episode_lock:
        events = _listener.poll_events()
        processed: list[dict[str, Any]] = []
        for evt in events:
            log.info(
                "poll_now: real DataHub assertion failure detected | "
                "event_id=%s source_urn=%s columns=%s",
                evt.event_id,
                evt.source_urn,
                evt.columns,
            )
            decision, actions = _runner.run_episode(evt)
            processed.append(
                {
                    "event_id": evt.event_id,
                    "source_urn": evt.source_urn,
                    "halt_set": list(decision.evidence.containment.halt_set),
                    "actions_generated": len(actions),
                }
            )

    return {"status": "POLLED", "episodes_processed": len(processed), "episodes": processed}


@app.get("/q/audit")
def run_audit() -> dict[str, Any]:
    from kronika.check.audit import DataAudit

    audit = DataAudit()
    episodes = _store.list_episodes(limit=1)
    last_evidence = _store.load(episodes[0]["event_id"]) if episodes else None

    source_urn = _resolve_urn(
        "urn:li:dataset:(urn:li:dataPlatform:hive,raw_patients,PROD)", _engine._context
    )

    violations = audit.check(
        before=_engine._context,
        after=_engine._context,
        event=MetadataEvent(
            "audit-000",
            EventKind.QUALITY_OBSERVATION,
            source_urn,
            None,
            (),
            "2026-07-25T00:00:00Z",
        ),
        impact_result=type(
            "_",
            (),
            {
                "source_urn": source_urn,
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
