from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from application.datahub.builder import build_context
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from application.llm.adapter import LocalLLMAdapter
from application.main import app
from application.storage.cache import DuckDBEvidenceStore
from kronika.engine import PublicEngine
from kronika.evidence import assemble
from kronika.impact import ImpactEngine
from kronika.rules import RuleEngine
from kronika.types import EventKind, MetadataEvent
from tests.fixtures import _urn, get_healthcare_dataset


@pytest.fixture
def test_client() -> TestClient:
    return TestClient(app)


class TestLocalLLMAdapter:
    def test_explain_audiences(self) -> None:
        seed = get_healthcare_dataset()
        ctx = build_context(HttpDataHubReader(mock_data=seed))
        evt = MetadataEvent(
            event_id="evt-llm-001",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("raw_patients"),
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T20:00:00Z",
        )
        engine = ImpactEngine()
        rules = RuleEngine()
        after_ctx, impact = engine.analyze(ctx, evt)
        rule_res = rules.evaluate_all(after_ctx)
        evidence = assemble(evt, impact, rule_res, consumer_counts={})

        adapter = LocalLLMAdapter()
        eng_text = adapter.explain(evidence, "ENGINEER")
        assert "ENGINEERING DIAGNOSTIC REPORT:" in eng_text
        assert _urn("raw_patients") in eng_text

        owner_text = adapter.explain(evidence, "OWNER")
        assert "DATA OWNER NOTICE:" in owner_text

        exec_text = adapter.explain(evidence, "EXECUTIVE")
        assert "EXECUTIVE SUMMARY:" in exec_text

    def test_explain_no_banned_vocabulary(self) -> None:
        banned = [
            "sste",
            "ssts",
            "lattice",
            "transition_operator",
            "world_state",
            "semantic_state",
            "monotone",
            "formal_model",
        ]
        seed = get_healthcare_dataset()
        ctx = build_context(HttpDataHubReader(mock_data=seed))
        evt = MetadataEvent(
            event_id="evt-llm-002",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("raw_patients"),
            columns=None,
            payload=(),
            occurred_at="2026-07-25T20:00:00Z",
        )
        engine = ImpactEngine()
        rules = RuleEngine()
        after_ctx, impact = engine.analyze(ctx, evt)
        rule_res = rules.evaluate_all(after_ctx)
        evidence = assemble(evt, impact, rule_res, consumer_counts={})

        adapter = LocalLLMAdapter()
        text = adapter.explain(evidence, "ENGINEER").lower()
        for term in banned:
            assert term not in text, f"LLM output contains spec term '{term}'"


class TestPhase6Endpoints:
    def test_audit_endpoint(self, test_client: TestClient) -> None:
        resp = test_client.get("/q/audit")
        assert resp.status_code == 200
        assert resp.json()["status"] == "CLEAN"

    def test_replay_endpoint(self, test_client: TestClient) -> None:
        resp = test_client.get("/q/replay")
        assert resp.status_code == 200
        assert resp.json()["status"] == "EQUIVALENT"

    def test_metrics_endpoint(self, test_client: TestClient) -> None:
        resp = test_client.get("/metrics")
        assert resp.status_code == 200
        assert "kronika_world_model_size" in resp.text
        assert "kronika_episodes_total" in resp.text


class TestIdempotencyAndChaos:
    def test_re_running_same_event_is_idempotent(self) -> None:
        seed = get_healthcare_dataset()
        reader = HttpDataHubReader(mock_data=seed)
        writer = HttpDataHubWriter(mock_mode=True)
        DuckDBEvidenceStore(":memory:")
        engine = PublicEngine()

        evt = MetadataEvent(
            event_id="evt-idempotent-001",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("raw_patients"),
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T21:00:00Z",
        )

        ctx1 = build_context(reader)
        engine.observe(ctx1)
        dec1 = engine.reason(evt)
        actions1 = engine.plan(dec1)

        for a in actions1:
            if not a.requires_human_approval:
                writer.add_annotation(a.target_urn, "tag", "val", evt.event_id)

        count_incidents_1 = len(writer.incidents_written)
        count_annotations_1 = len(writer.annotations_written)

        ctx2 = build_context(reader)
        engine.observe(ctx2)
        dec2 = engine.reason(evt)
        actions2 = engine.plan(dec2)

        for a in actions2:
            if not a.requires_human_approval:
                writer.add_annotation(a.target_urn, "tag", "val", evt.event_id)

        count_incidents_2 = len(writer.incidents_written)
        count_annotations_2 = len(writer.annotations_written)

        assert dec1.evidence.containment.halt_set == dec2.evidence.containment.halt_set
        assert count_incidents_1 == count_incidents_2
        assert count_annotations_1 == count_annotations_2


class TestCapabilityProbe:
    def test_capability_probe_success(self) -> None:
        from application.datahub.reader import (
            HttpDataHubReader,
            capability_probe,
        )

        seed = get_healthcare_dataset()
        reader = HttpDataHubReader(mock_data=seed)
        capability_probe(reader)

    def test_capability_probe_failure_raises_configuration_error(self) -> None:
        from application.datahub.reader import (
            ConfigurationError,
            HttpDataHubReader,
            capability_probe,
        )

        reader = HttpDataHubReader(server_url="http://invalid-host-000:9999", timeout=0.1)
        with pytest.raises(ConfigurationError) as exc_info:
            capability_probe(reader)
        assert "list_entities" in str(exc_info.value)


class TestLLMIsolation:
    def test_llm_adapter_has_no_core_engine_imports(self) -> None:
        import ast
        from pathlib import Path

        adapter_path = (
            Path(__file__).parent.parent.parent / "src" / "application" / "llm" / "adapter.py"
        )
        tree = ast.parse(adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path))

        forbidden_names = {"PublicEngine", "ImpactEngine", "RuleEngine", "DataContext"}
        imported_names: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported_names.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imported_names.add(node.module)
                for alias in node.names:
                    imported_names.add(alias.name)

        violations = imported_names & forbidden_names
        assert not violations, f"LLMAdapter imports engine/reasoning symbols: {violations}"
