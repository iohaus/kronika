from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from application.agent.listener import EventListener
from application.agent.runtime import DecisionEpisodeRunner
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.main import app
from application.storage.cache import DuckDBEvidenceStore
from kronika.engine import PublicEngine
from kronika.types import EventKind, MetadataEvent
from tests.fixtures import _urn, get_healthcare_dataset


@pytest.fixture
def test_client() -> TestClient:
    from application.main import startup_event

    startup_event()
    return TestClient(app)


class TestAgentRuntimeEpisode:
    def test_run_episode(self) -> None:
        seed = get_healthcare_dataset()
        reader = HttpDataHubReader(mock_data=seed)
        writer = HttpDataHubWriter(mock_mode=True)
        store = DuckDBEvidenceStore(":memory:")
        engine = PublicEngine()

        runner = DecisionEpisodeRunner(engine, reader, writer, store)

        event = MetadataEvent(
            event_id="evt-episode-100",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("raw_patients"),
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T18:00:00Z",
        )

        decision, actions = runner.run_episode(event)

        assert decision.event.event_id == "evt-episode-100"
        assert len(actions) > 0

        loaded = store.load("evt-episode-100")
        assert loaded is not None
        assert loaded.event_id == "evt-episode-100"

        pending = store.list_pending_actions()
        assert len(pending) > 0
        assert any(p["kind"] == "HALT_PIPELINE" for p in pending)


class TestEventListener:
    def test_poll_events_deduplication(self) -> None:
        seed = get_healthcare_dataset()
        seed["assertions"] = [
            {
                "dataset_urn": _urn("raw_patients"),
                "status": "FAILED",
                "columns": ["billing_amount"],
                "occurred_at": "2026-07-25T19:00:00Z",
                "severity": "critical",
            }
        ]
        reader = HttpDataHubReader(mock_data=seed)
        listener = EventListener(reader)

        events1 = listener.poll_events()
        assert len(events1) == 1
        assert events1[0].source_urn == _urn("raw_patients")
        assert events1[0].payload_value("source") == "datahub_assertion"

        events2 = listener.poll_events()
        assert len(events2) == 0

    def test_poll_events_to_pending_action_end_to_end(self) -> None:
        """A real DataHub-detected assertion failure, polled with no human-typed
        claim involved, flows all the way to a HALT_PIPELINE pending action."""
        seed = get_healthcare_dataset()
        seed["assertions"] = [
            {
                "dataset_urn": _urn("raw_patients"),
                "status": "FAILED",
                "columns": ["billing_amount"],
                "occurred_at": "2026-07-25T20:00:00Z",
                "severity": "critical",
            }
        ]
        reader = HttpDataHubReader(mock_data=seed)
        writer = HttpDataHubWriter(mock_mode=True)
        store = DuckDBEvidenceStore(":memory:")
        engine = PublicEngine()
        runner = DecisionEpisodeRunner(engine, reader, writer, store)
        listener = EventListener(reader)

        events = listener.poll_events()
        assert len(events) == 1

        decision, _actions = runner.run_episode(events[0])
        assert decision.event.source_urn == _urn("raw_patients")

        pending = store.list_pending_actions()
        assert any(p["kind"] == "HALT_PIPELINE" for p in pending)

    def test_governance_action_writes_incident_on_approve(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Regression: RAISE_GOVERNANCE_INCIDENT actions previously had no execution
        handler in the approval endpoints — approving one silently dropped it with
        no DataHub write. See kronika.doc/demo_script.md log-consistency findings,
        2026-08-10."""
        import application.main as main_module

        seed = get_healthcare_dataset()
        reader = HttpDataHubReader(mock_data=seed)
        writer = HttpDataHubWriter(mock_mode=True)
        store = DuckDBEvidenceStore(":memory:")
        engine = PublicEngine()
        runner = DecisionEpisodeRunner(engine, reader, writer, store)

        event = MetadataEvent(
            event_id="evt-governance-100",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("raw_patients"),
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T18:00:00Z",
        )
        _decision, actions = runner.run_episode(event)
        governance_actions = [a for a in actions if a.kind == "RAISE_GOVERNANCE_INCIDENT"]
        assert governance_actions, (
            "fixture's pii_must_have_owner violation on raw_patients should have "
            "produced a RAISE_GOVERNANCE_INCIDENT action"
        )
        assert not writer.incidents_written

        monkeypatch.setattr(main_module, "_store", store)
        monkeypatch.setattr(main_module, "_writer", writer)
        client = TestClient(main_module.app)

        resp = client.post("/q/pending/approve-all")
        assert resp.status_code == 200

        written_urns = {inc["urn"] for inc in writer.incidents_written}
        assert governance_actions[0].target_urn in written_urns


class TestProductAPIEndpoints:
    def test_get_status(self, test_client: TestClient) -> None:
        resp = test_client.get("/q/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["asset_count"] >= 4

    def test_post_analyze_precheck(self, test_client: TestClient) -> None:
        payload = {
            "event_id": "check-001",
            "kind": "QUALITY_OBSERVATION",
            "source_urn": _urn("raw_patients"),
            "columns": ["billing_amount"],
        }
        resp = test_client.post("/q/analyze", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["event_id"] == "check-001"
        assert any("mart_billing" in h for h in data["halt_set"])

    def test_post_analyze_malformed_urn_returns_400(self, test_client: TestClient) -> None:
        payload = {
            "event_id": "check-002",
            "kind": "QUALITY_OBSERVATION",
            "source_urn": "invalid-urn",
        }
        resp = test_client.post("/q/analyze", json=payload)
        assert resp.status_code == 400

    def test_pending_actions_workflow(self, test_client: TestClient) -> None:
        # Seeds the pending queue via /q/poll-now against the real local DataHub
        # instance, which must have a real FAILED assertion authored on
        # raw_patients.billing_amount (tooling/kronika-tools/.../add_assertions.py) —
        # no manually-typed claim is involved, this exercises the real detection path.
        poll_resp = test_client.post("/q/poll-now")
        assert poll_resp.status_code == 200
        assert poll_resp.json()["episodes_processed"] >= 1

        pending_resp = test_client.get("/q/pending")
        assert pending_resp.status_code == 200
        pending = pending_resp.json()
        assert len(pending) > 0

        action_id = pending[0]["action_id"]
        approve_resp = test_client.post(f"/q/pending/{action_id}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "APPROVED"
