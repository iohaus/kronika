from __future__ import annotations

import pytest

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.types import DataAsset, EdgeKind, LineageEdge, PolicyRule
from kronika.verification import FindingKind, VerificationResult, verify_all, verify_rule

_P = "urn:li:dataPlatform:hive"


def _urn(name: str) -> str:
    return f"urn:li:dataset:({_P},{name},PROD)"


def _asset(name: str, *, owner: str | None = None, tags: frozenset[str] | None = None) -> DataAsset:
    return DataAsset(
        urn=_urn(name),
        status=tuple(StatusLevel.HEALTHY for _ in range(8)),
        tags=tags or frozenset(),
        owner_urn=owner,
        domain_urn=None,
    )


def _rule(rule_id: str, predicate: str, scope: str) -> PolicyRule:
    return PolicyRule(
        rule_id=rule_id,
        dimension=Dimension.COMPLIANCE,
        predicate=predicate,
        scope_urn=_urn(scope),
        glossary_urn=None,
    )


_OWNER_URN = "urn:li:corpuser:clinical_team"

_URN_RAW = _urn("raw_patients")
_URN_STAGING = _urn("staging_patients")
_URN_BILLING = _urn("mart_billing")
_URN_DEMO = _urn("mart_demographics")


def _healthcare_context(with_rules: bool = True) -> DataContext:
    assets = [
        _asset("raw_patients", tags=frozenset({"pii", "internal"})),
        _asset("staging_patients", owner=_OWNER_URN, tags=frozenset({"pii"})),
        _asset("mart_billing", tags=frozenset({"critical"})),
        _asset("mart_demographics", owner=_OWNER_URN, tags=frozenset({"internal"})),
    ]
    rules = []
    if with_rules:
        rules = [
            _rule("pii_must_have_owner", "asset.has_owner", "raw_patients"),
            _rule("billing_must_be_critical", "asset.has_tag('critical')", "mart_billing"),
        ]
    return DataContext.build(
        assets=assets,
        edges=[
            LineageEdge(_URN_RAW, _URN_STAGING, EdgeKind.IDENTITY, None),
            LineageEdge(_URN_STAGING, _URN_BILLING, EdgeKind.PROJECTION, frozenset({"billing_amount"})),
            LineageEdge(_URN_STAGING, _URN_DEMO, EdgeKind.PROJECTION, frozenset({"patient_id"})),
        ],
        rules=rules,
    )


class TestVerifyRuleCounterexample:
    def test_pii_must_have_owner_fails_for_raw(self) -> None:
        ctx = _healthcare_context()
        rule = ctx.rules_for(_URN_RAW)[0]
        result = verify_rule(rule, ctx)
        assert result.kind == FindingKind.COUNTEREXAMPLE
        assert result.rule_id == "pii_must_have_owner"
        assert len(result.minimal_witness) == 1
        assert result.minimal_witness[0].urn == _URN_RAW

    def test_counterexample_has_evidence_path(self) -> None:
        ctx = _healthcare_context()
        rule = ctx.rules_for(_URN_RAW)[0]
        result = verify_rule(rule, ctx)
        assert len(result.evidence_path) >= 1
        assert _URN_RAW in result.evidence_path


class TestVerifyRuleProof:
    def test_billing_must_be_critical_succeeds(self) -> None:
        ctx = _healthcare_context()
        rule = ctx.rules_for(_URN_BILLING)[0]
        result = verify_rule(rule, ctx)
        assert result.kind == FindingKind.PROOF
        assert result.rule_id == "billing_must_be_critical"
        assert result.minimal_witness == ()

    def test_proof_has_witness_bindings(self) -> None:
        ctx = _healthcare_context()
        rule = ctx.rules_for(_URN_BILLING)[0]
        result = verify_rule(rule, ctx)
        assert len(result.witnesses) >= 1
        assert result.witnesses[0].urn == _URN_BILLING


class TestVerifyRuleInconclusive:
    def test_broken_predicate_is_inconclusive(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[_rule("bad_rule", "1 / 0", "a")],
            edges=[],
        )
        rule = ctx.rules_for(_urn("a"))[0]
        result = verify_rule(rule, ctx)
        assert result.kind == FindingKind.INCONCLUSIVE

    def test_scope_not_in_context_is_inconclusive(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[_rule("r1", "asset.has_owner", "a")],
            edges=[],
        )
        absent_rule = PolicyRule(
            rule_id="absent_scope",
            dimension=Dimension.COMPLIANCE,
            predicate="asset.has_owner",
            scope_urn=_urn("does_not_exist"),
            glossary_urn=None,
        )
        result = verify_rule(absent_rule, ctx)
        assert result.kind == FindingKind.INCONCLUSIVE


class TestVerifyAll:
    def test_all_rules_evaluated_not_short_circuited(self) -> None:
        ctx = _healthcare_context()
        results = verify_all(ctx)
        rule_ids = {r.rule_id for r in results}
        assert "pii_must_have_owner" in rule_ids
        assert "billing_must_be_critical" in rule_ids
        assert len(results) == 2

    def test_results_sorted_by_rule_id(self) -> None:
        ctx = _healthcare_context()
        results = verify_all(ctx)
        ids = [r.rule_id for r in results]
        assert ids == sorted(ids)

    def test_no_rules_returns_empty(self) -> None:
        ctx = _healthcare_context(with_rules=False)
        results = verify_all(ctx)
        assert results == []

    def test_mixed_results(self) -> None:
        ctx = _healthcare_context()
        results = verify_all(ctx)
        kinds = {r.rule_id: r.kind for r in results}
        assert kinds["pii_must_have_owner"] == FindingKind.COUNTEREXAMPLE
        assert kinds["billing_must_be_critical"] == FindingKind.PROOF

    def test_no_duplicate_rules(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a")],
            rules=[_rule("r1", "asset.has_owner", "a")],
            edges=[],
        )
        results = verify_all(ctx)
        rule_ids = [r.rule_id for r in results]
        assert len(rule_ids) == len(set(rule_ids))
