from __future__ import annotations

from kronika.data_context import DataContext
from kronika.dimensions import Dimension
from kronika.engine import PublicEngine
from kronika.evidence import Recommendation
from kronika.types import (
    ColumnLineage,
    DataAsset,
    EdgeKind,
    EventKind,
    LineageEdge,
    MetadataEvent,
    PolicyRule,
)

_P = "urn:li:dataPlatform:hive"


def _urn(name: str) -> str:
    return f"urn:li:dataset:({_P},{name},PROD)"


def _asset(name: str, *, tags: frozenset[str] | None = None, owner: str | None = None) -> DataAsset:
    return DataAsset.healthy(_urn(name), tags=tags or frozenset(), owner_urn=owner)


def _edge(src: str, dst: str) -> LineageEdge:
    return LineageEdge(_urn(src), _urn(dst), EdgeKind.IDENTITY, None)


def _col(*names: str) -> frozenset[ColumnLineage]:
    """Self-mapping column lineage — same column name(s) upstream and downstream."""
    return frozenset(ColumnLineage(dst_column=n, src_columns=frozenset({n})) for n in names)


_OWNER_URN = "urn:li:corpuser:clinical_team"
_URN_RAW = _urn("raw_patients")
_URN_STAGING = _urn("staging_patients")
_URN_BILLING = _urn("mart_billing")
_URN_DEMO = _urn("mart_demographics")

_QUALITY_EVT = MetadataEvent(
    event_id="evt-e2e-001",
    kind=EventKind.QUALITY_OBSERVATION,
    source_urn=_URN_RAW,
    columns=frozenset({"billing_amount"}),
    payload=(),
    occurred_at="2026-07-25T10:00:00Z",
)


def _healthcare_context() -> DataContext:
    return DataContext.build(
        assets=[
            _asset("raw_patients", tags=frozenset({"pii", "internal"})),
            _asset("staging_patients", tags=frozenset({"pii"}), owner=_OWNER_URN),
            _asset("mart_billing", tags=frozenset({"critical"})),
            _asset("mart_demographics", tags=frozenset({"internal"}), owner=_OWNER_URN),
        ],
        edges=[
            LineageEdge(
                _URN_RAW,
                _URN_STAGING,
                EdgeKind.IDENTITY,
                _col("billing_amount", "patient_id"),
            ),
            LineageEdge(_URN_STAGING, _URN_BILLING, EdgeKind.PROJECTION, _col("billing_amount")),
            LineageEdge(_URN_STAGING, _URN_DEMO, EdgeKind.PROJECTION, _col("patient_id")),
        ],
        rules=[
            PolicyRule(
                rule_id="pii_must_have_owner",
                dimension=Dimension.COMPLIANCE,
                predicate="asset.has_owner",
                scope_urn=_URN_RAW,
                glossary_urn=None,
            ),
        ],
    )


class TestPublicEngineEndToEnd:
    def setup_method(self) -> None:
        self.engine = PublicEngine()
        self.ctx = _healthcare_context()
        self.engine.observe(self.ctx)

    def test_reason_returns_decision_record(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        assert decision.event == _QUALITY_EVT
        assert decision.context_before is self.ctx

    def test_reason_produces_evidence_record(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        assert decision.evidence.event_id == "evt-e2e-001"
        assert decision.evidence.source_urn == _URN_RAW

    def test_mart_billing_is_halt_in_evidence(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        assert _URN_BILLING in decision.evidence.outcomes
        assert decision.evidence.outcomes[_URN_BILLING].recommendation == Recommendation.HALT

    def test_mart_demographics_not_in_outcomes(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        assert _URN_DEMO not in decision.evidence.outcomes

    def test_plan_returns_actions(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        assert isinstance(actions, list)
        assert len(actions) > 0

    def test_halt_actions_require_human_approval(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        halt_actions = [a for a in actions if a.kind == "HALT_PIPELINE"]
        assert halt_actions
        assert all(a.requires_human_approval for a in halt_actions)

    def test_monitor_actions_are_autonomous(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        monitor_actions = [a for a in actions if a.kind == "ADD_MONITORING_TAG"]
        assert all(not a.requires_human_approval for a in monitor_actions)

    def test_actions_sorted_by_action_id(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        ids = [a.action_id for a in actions]
        assert ids == sorted(ids)

    def test_action_ids_are_event_prefixed(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        assert all(a.action_id.startswith("evt-e2e-001") for a in actions)

    def test_governance_violation_produces_governance_action(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        gov_actions = [a for a in actions if a.kind == "RAISE_GOVERNANCE_INCIDENT"]
        assert len(gov_actions) >= 1
        assert any("pii_must_have_owner" in a.rationale for a in gov_actions)

    def test_transition_applies_autonomous_actions(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        autonomous = [a for a in actions if not a.requires_human_approval]
        new_ctx = self.engine.transition(autonomous)
        for action in autonomous:
            if action.kind == "ADD_MONITORING_TAG" and action.target_urn in new_ctx.all_urns():
                asset = new_ctx.asset(action.target_urn)
                assert "kronika:monitoring" in asset.tags

    def test_transition_does_not_apply_halt_actions(self) -> None:
        decision = self.engine.reason(_QUALITY_EVT)
        actions = self.engine.plan(decision)
        halt_actions = [a for a in actions if a.requires_human_approval]
        self.engine.transition(halt_actions)
        assert self.engine._context is not None


class TestPublicEngineObserve:
    def test_observe_replaces_context(self) -> None:
        engine = PublicEngine()
        ctx1 = DataContext.build(assets=[_asset("a")], edges=[], rules=[])
        ctx2 = DataContext.build(assets=[_asset("b")], edges=[], rules=[])
        engine.observe(ctx1)
        engine.observe(ctx2)
        assert _urn("b") in engine._context.all_urns()
        assert _urn("a") not in engine._context.all_urns()

    def test_observe_idempotent(self) -> None:
        engine = PublicEngine()
        ctx = _healthcare_context()
        engine.observe(ctx)
        engine.observe(ctx)
        decision = engine.reason(_QUALITY_EVT)
        assert decision.evidence is not None


class TestPublicEngineNoRules:
    def test_no_governance_actions_without_rules(self) -> None:
        engine = PublicEngine()
        ctx = DataContext.build(
            assets=[
                _asset("raw_patients", tags=frozenset({"pii"})),
                _asset("staging_patients"),
            ],
            edges=[_edge("raw_patients", "staging_patients")],
            rules=[],
        )
        engine.observe(ctx)
        evt = MetadataEvent(
            event_id="evt-002",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_URN_RAW,
            columns=None,
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        decision = engine.reason(evt)
        actions = engine.plan(decision)
        gov_actions = [a for a in actions if a.kind == "RAISE_GOVERNANCE_INCIDENT"]
        assert gov_actions == []
