from __future__ import annotations

import pytest
from ops.seed_healthcare import _urn, get_healthcare_dataset

from application.datahub.builder import ContextLimitExceededError, build_context
from application.datahub.reader import HttpDataHubReader
from application.datahub.writer import HttpDataHubWriter
from kronika.engine import PublicEngine
from kronika.evidence import Recommendation
from kronika.types import EventKind, MetadataEvent, ValidationError


def test_reader_mock_data() -> None:
    seed = get_healthcare_dataset()
    reader = HttpDataHubReader(mock_data=seed)
    datasets = reader.list_datasets()
    assert len(datasets) == 4
    assert datasets[0]["urn"] == _urn("raw_patients")
    edges = reader.list_lineage_edges()
    assert len(edges) == 3


def test_reader_boundary_validation_error() -> None:
    invalid_seed = {"datasets": [{"urn": "invalid-non-urn", "tags": []}]}
    reader = HttpDataHubReader(mock_data=invalid_seed)
    with pytest.raises(ValidationError) as exc_info:
        reader.list_datasets()
    assert exc_info.value.code == "invalid_urn"


def test_builder_constructs_valid_context() -> None:
    seed = get_healthcare_dataset()
    reader = HttpDataHubReader(mock_data=seed)
    ctx = build_context(reader)
    assert len(ctx) == 4
    assert _urn("raw_patients") in ctx.all_urns()
    assert len(ctx.rules_for(_urn("raw_patients"))) == 1


def test_builder_max_size_exceeded() -> None:
    seed = get_healthcare_dataset()
    reader = HttpDataHubReader(mock_data=seed)
    with pytest.raises(ContextLimitExceededError):
        build_context(reader, max_size=2)


def test_writer_mock_mode_and_idempotency() -> None:
    writer = HttpDataHubWriter(mock_mode=True)
    writer.create_incident(_urn("mart_billing"), "Pipeline Halt", "Integrity critical", "evt-100")
    writer.create_incident(_urn("mart_billing"), "Pipeline Halt", "Integrity critical", "evt-100")
    assert len(writer.incidents_written) == 1

    writer.add_annotation(_urn("staging_patients"), "status", "MONITOR", "evt-100")
    writer.add_annotation(_urn("staging_patients"), "status", "MONITOR", "evt-100")
    assert len(writer.annotations_written) == 1


def test_end_to_end_datahub_read_reason_write_loop() -> None:
    seed = get_healthcare_dataset()
    reader = HttpDataHubReader(mock_data=seed)
    ctx = build_context(reader)

    engine = PublicEngine()
    engine.observe(ctx)

    event = MetadataEvent(
        event_id="evt-integration-001",
        kind=EventKind.QUALITY_OBSERVATION,
        source_urn=_urn("raw_patients"),
        columns=frozenset({"billing_amount"}),
        payload=(),
        occurred_at="2026-07-25T16:00:00Z",
    )

    decision = engine.reason(event)
    actions = engine.plan(decision)

    assert _urn("mart_billing") in decision.evidence.outcomes
    assert decision.evidence.outcomes[_urn("mart_billing")].recommendation == Recommendation.HALT

    writer = HttpDataHubWriter(mock_mode=True)
    for action in actions:
        if action.kind == "HALT_PIPELINE":
            writer.create_incident(
                urn=action.target_urn,
                title=f"Incident: {action.action_id}",
                description=action.rationale,
                event_id=event.event_id,
            )
        elif action.kind in ("ADD_MONITORING_TAG", "ADD_TAG"):
            writer.add_annotation(
                urn=action.target_urn,
                key="monitoring_tag",
                value="kronika:monitoring",
                event_id=event.event_id,
            )

    assert len(writer.incidents_written) >= 1
    assert any(i["urn"] == _urn("mart_billing") for i in writer.incidents_written)
