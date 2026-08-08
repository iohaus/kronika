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
        seed["datasets"][0]["assertions"] = [
            {
                "status": "FAILED",
                "columns": ["billing_amount"],
                "occurred_at": "2026-07-25T19:00:00Z",
            }
        ]
        reader = HttpDataHubReader(mock_data=seed)
        listener = EventListener(reader)

        events1 = listener.poll_events()
        assert len(events1) == 1
        assert events1[0].source_urn == _urn("raw_patients")

        events2 = listener.poll_events()
        assert len(events2) == 0


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
        wh_payload = {
            "event_id": "wh-test-999",
            "event_type": "QUALITY_OBSERVATION",
            "source_urn": _urn("raw_patients"),
            "columns": ["billing_amount"],
        }
        wh_resp = test_client.post("/webhooks/datahub", json=wh_payload)
        assert wh_resp.status_code == 200

        pending_resp = test_client.get("/q/pending")
        assert pending_resp.status_code == 200
        pending = pending_resp.json()
        assert len(pending) > 0

        action_id = pending[0]["action_id"]
        approve_resp = test_client.post(f"/q/pending/{action_id}/approve")
        assert approve_resp.status_code == 200
        assert approve_resp.json()["status"] == "APPROVED"

    def test_webhook_malformed_returns_400(self, test_client: TestClient) -> None:
        resp = test_client.post("/webhooks/datahub", json={"source_urn": "bad-urn"})
        assert resp.status_code == 400
