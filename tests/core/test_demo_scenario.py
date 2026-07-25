from __future__ import annotations

import pytest

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.evidence import EvidenceRecord, Recommendation, assemble
from kronika.impact import ImpactEngine
from kronika.rules import RuleEngine, RuleOutcome
from kronika.types import DataAsset, EdgeKind, EventKind, LineageEdge, MetadataEvent, PolicyRule

_P = "urn:li:dataPlatform:hive"


def _urn(name: str) -> str:
    return f"urn:li:dataset:({_P},{name},PROD)"


def _asset(name: str, **kwargs: object) -> DataAsset:
    return DataAsset.healthy(_urn(name), **kwargs)  # type: ignore[arg-type]


_ENGINE = ImpactEngine()
_RULES = RuleEngine()

_URN_RAW = _urn("raw_patients")
_URN_STAGING = _urn("staging_patients")
_URN_BILLING = _urn("mart_billing")
_URN_DEMO = _urn("mart_demographics")

_QUALITY_EVT = MetadataEvent(
    event_id="evt-001",
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
            _asset("staging_patients", tags=frozenset({"pii"})),
            _asset("mart_billing", tags=frozenset({"critical"})),
            _asset("mart_demographics", tags=frozenset({"internal"})),
        ],
        edges=[
            LineageEdge(_URN_RAW, _URN_STAGING, EdgeKind.IDENTITY, None),
            LineageEdge(_URN_STAGING, _URN_BILLING, EdgeKind.PROJECTION, frozenset({"billing_amount"})),
            LineageEdge(_URN_STAGING, _URN_DEMO, EdgeKind.PROJECTION, frozenset({"patient_id"})),
        ],
        rules=[],
    )


class TestHealthcareDemoScenario:
    def setup_method(self) -> None:
        self.ctx = _healthcare_context()
        self.after_ctx, self.impact = _ENGINE.analyze(self.ctx, _QUALITY_EVT)
        self.rule_results = _RULES.evaluate_all(self.after_ctx)
        self.record = assemble(
            _QUALITY_EVT, self.impact, self.rule_results,
            consumer_counts={_URN_BILLING: 12, _URN_DEMO: 3},
        )

    def test_mart_billing_is_halt(self) -> None:
        assert _URN_BILLING in self.record.outcomes
        assert self.record.outcomes[_URN_BILLING].recommendation == Recommendation.HALT

    def test_mart_demographics_is_clear(self) -> None:
        assert _URN_DEMO not in self.record.outcomes, (
            f"mart_demographics should be unchanged (CLEAR implies not in outcomes); "
            f"got {self.record.outcomes.get(_URN_DEMO)}"
        )

    def test_staging_patients_is_halt(self) -> None:
        assert _URN_STAGING in self.record.outcomes
        assert self.record.outcomes[_URN_STAGING].recommendation == Recommendation.HALT

    def test_evidence_path_raw_to_billing(self) -> None:
        outcome = self.record.outcomes[_URN_BILLING]
        assert outcome.evidence_path[0] == _URN_RAW
        assert outcome.evidence_path[-1] == _URN_BILLING

    def test_mart_demographics_not_in_changed(self) -> None:
        assert _URN_DEMO not in self.impact.changed

    def test_halt_set_contains_billing(self) -> None:
        assert _URN_BILLING in self.record.containment.halt_set

    def test_evidence_record_event_id(self) -> None:
        assert self.record.event_id == "evt-001"

    def test_mart_billing_integrity_critical(self) -> None:
        assert self.after_ctx.asset(_URN_BILLING).get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL

    def test_staging_integrity_critical(self) -> None:
        staging_status = self.after_ctx.asset(_URN_STAGING).get_status(Dimension.INTEGRITY)
        assert staging_status == StatusLevel.CRITICAL

    def test_demographics_unchanged(self) -> None:
        demo_after = self.after_ctx.asset(_URN_DEMO)
        demo_before = self.ctx.asset(_URN_DEMO)
        assert demo_after.status == demo_before.status


class TestRuleEngine:
    def test_satisfied_predicate(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a", tags=frozenset({"pii"}))],
            rules=[PolicyRule("r1", Dimension.INTEGRITY, "asset.has_tag('pii')", _urn("a"), None)],
            edges=[],
        )
        result = _RULES.evaluate(ctx.rules_for(_urn("a"))[0], ctx.asset(_urn("a")), ctx)
        assert result.outcome == RuleOutcome.SATISFIED

    def test_violated_predicate(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[PolicyRule("r1", Dimension.INTEGRITY, "asset.has_owner", _urn("a"), None)],
            edges=[],
        )
        result = _RULES.evaluate(ctx.rules_for(_urn("a"))[0], ctx.asset(_urn("a")), ctx)
        assert result.outcome == RuleOutcome.VIOLATED
        assert result.witness is not None

    def test_broken_predicate_is_unknown(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[PolicyRule("r1", Dimension.INTEGRITY, "1 / 0", _urn("a"), None)],
            edges=[],
        )
        result = _RULES.evaluate(ctx.rules_for(_urn("a"))[0], ctx.asset(_urn("a")), ctx)
        assert result.outcome == RuleOutcome.UNKNOWN

    def test_non_bool_predicate_is_unknown(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[PolicyRule("r1", Dimension.INTEGRITY, "'string_result'", _urn("a"), None)],
            edges=[],
        )
        result = _RULES.evaluate(ctx.rules_for(_urn("a"))[0], ctx.asset(_urn("a")), ctx)
        assert result.outcome == RuleOutcome.UNKNOWN

    def test_sandboxed_no_import(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[PolicyRule("r1", Dimension.INTEGRITY, "__import__('os')", _urn("a"), None)],
            edges=[],
        )
        result = _RULES.evaluate(ctx.rules_for(_urn("a"))[0], ctx.asset(_urn("a")), ctx)
        assert result.outcome == RuleOutcome.UNKNOWN

    def test_evaluate_all_returns_per_asset(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a"), _asset("b")],
            rules=[
                PolicyRule("r1", Dimension.INTEGRITY, "asset.has_owner", _urn("a"), None),
                PolicyRule("r2", Dimension.TRUST, "asset.has_owner", _urn("b"), None),
            ],
            edges=[],
        )
        results = _RULES.evaluate_all(ctx)
        assert _urn("a") in results
        assert _urn("b") in results
        assert all(r.outcome == RuleOutcome.VIOLATED for rs in results.values() for r in rs)

    def test_apply_violations_updates_dimension(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[PolicyRule("r1", Dimension.INTEGRITY, "asset.has_owner", _urn("a"), None)],
            edges=[],
        )
        rule_results = _RULES.evaluate_all(ctx)
        updated_ctx = _RULES.apply_violations(ctx, rule_results)
        assert updated_ctx.asset(_urn("a")).get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL

    def test_has_upstream_predicate(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("src"), _asset("dst")],
            rules=[PolicyRule("r1", Dimension.OWNERSHIP, "asset.has_upstream()", _urn("dst"), None)],
            edges=[LineageEdge(_urn("src"), _urn("dst"), EdgeKind.IDENTITY, None)],
        )
        result = _RULES.evaluate(ctx.rules_for(_urn("dst"))[0], ctx.asset(_urn("dst")), ctx)
        assert result.outcome == RuleOutcome.SATISFIED
