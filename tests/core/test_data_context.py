from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.types import (
    ColumnLineage,
    DataAsset,
    EdgeKind,
    LineageEdge,
    PolicyRule,
    ValidationError,
)

_P = "urn:li:dataPlatform:hive"


def _urn(name: str) -> str:
    return f"urn:li:dataset:({_P},{name},PROD)"


_URN_A = _urn("raw_patients")
_URN_B = _urn("staging_patients")
_URN_C = _urn("mart_billing")
_URN_D = _urn("mart_demographics")

_EDGE_AB = LineageEdge(src=_URN_A, dst=_URN_B, kind=EdgeKind.IDENTITY, column_lineage=None)
_EDGE_BC = LineageEdge(
    src=_URN_B,
    dst=_URN_C,
    kind=EdgeKind.PROJECTION,
    column_lineage=frozenset(
        {ColumnLineage(dst_column="billing_amount", src_columns=frozenset({"billing_amount"}))}
    ),
)
_EDGE_BD = LineageEdge(
    src=_URN_B,
    dst=_URN_D,
    kind=EdgeKind.PROJECTION,
    column_lineage=frozenset(
        {ColumnLineage(dst_column="patient_id", src_columns=frozenset({"patient_id"}))}
    ),
)


def _assets(*names: str) -> list[DataAsset]:
    return [DataAsset.healthy(_urn(n)) for n in names]


def _edges(*edges: LineageEdge) -> list[LineageEdge]:
    return list(edges)


class TestDataContextConstruction:
    def test_empty(self) -> None:
        ctx = DataContext.empty()
        assert len(ctx) == 0
        assert ctx.cycle_nodes == frozenset()

    def test_build_valid(self) -> None:
        ctx = DataContext.build(
            assets=_assets("raw_patients", "staging_patients", "mart_billing", "mart_demographics"),
            edges=[_EDGE_AB, _EDGE_BC, _EDGE_BD],
            rules=[],
        )
        assert len(ctx) == 4
        assert ctx.cycle_nodes == frozenset()

    def test_duplicate_urns_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataContext.build(
                assets=[DataAsset.healthy(_URN_A), DataAsset.healthy(_URN_A)],
                edges=[],
                rules=[],
            )
        assert exc_info.value.code == "duplicate urns"

    def test_dangling_edge_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataContext.build(
                assets=_assets("raw_patients"),
                edges=[_EDGE_AB],
                rules=[],
            )
        assert "dangling" in exc_info.value.code

    def test_key_identity_mismatch_raises(self) -> None:
        a = DataAsset.healthy(_URN_A)
        with pytest.raises(ValidationError) as exc_info:
            DataContext({"wrong_key": a}, frozenset(), {})
        assert "mismatch" in exc_info.value.code


class TestDataContextTraversal:
    def setup_method(self) -> None:
        self.ctx = DataContext.build(
            assets=_assets("raw_patients", "staging_patients", "mart_billing", "mart_demographics"),
            edges=[_EDGE_AB, _EDGE_BC, _EDGE_BD],
            rules=[],
        )

    def test_successors(self) -> None:
        assert self.ctx.successors(_URN_A) == {_URN_B}
        assert self.ctx.successors(_URN_B) == {_URN_C, _URN_D}
        assert self.ctx.successors(_URN_C) == frozenset()

    def test_predecessors(self) -> None:
        assert self.ctx.predecessors(_URN_B) == {_URN_A}
        assert self.ctx.predecessors(_URN_C) == {_URN_B}
        assert self.ctx.predecessors(_URN_A) == frozenset()

    def test_edges_from_sorted_by_dst(self) -> None:
        edges = self.ctx.edges_from(_URN_B)
        assert [e.dst for e in edges] == sorted(e.dst for e in edges)

    def test_all_urns_sorted(self) -> None:
        urns = self.ctx.all_urns()
        assert urns == sorted(urns)

    def test_asset_not_found_raises(self) -> None:
        with pytest.raises(KeyError, match="not found"):
            self.ctx.asset("urn:li:dataset:(urn:li:dataPlatform:hive,unknown,PROD)")

    def test_replace_asset(self) -> None:
        original = self.ctx.asset(_URN_C)
        updated_asset = original.with_status(Dimension.INTEGRITY, StatusLevel.CRITICAL)
        new_ctx = self.ctx.replace_asset(updated_asset)
        assert new_ctx.asset(_URN_C).get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL
        assert self.ctx.asset(_URN_C).get_status(Dimension.INTEGRITY) == StatusLevel.HEALTHY

    def test_replace_unknown_asset_raises(self) -> None:
        unknown = DataAsset.healthy(_urn("unknown"))
        with pytest.raises(KeyError):
            self.ctx.replace_asset(unknown)


class TestDataContextRules:
    def test_rules_for(self) -> None:
        rule = PolicyRule(
            rule_id="billing_nonneg",
            dimension=Dimension.INTEGRITY,
            predicate="billing_amount >= 0",
            scope_urn=_URN_A,
            glossary_urn=None,
        )
        ctx = DataContext.build(assets=_assets("raw_patients"), edges=[], rules=[rule])
        assert ctx.rules_for(_URN_A) == [rule]
        assert ctx.rules_for(_URN_B if _URN_B in ctx._assets else _URN_A) == [rule]

    def test_rules_sorted_by_id(self) -> None:
        rules = [
            PolicyRule("z_rule", Dimension.TRUST, "True", _URN_A, None),
            PolicyRule("a_rule", Dimension.INTEGRITY, "True", _URN_A, None),
        ]
        ctx = DataContext.build(assets=_assets("raw_patients"), edges=[], rules=rules)
        rule_ids = [r.rule_id for r in ctx.rules_for(_URN_A)]
        assert rule_ids == sorted(rule_ids)

    def test_duplicate_rule_ids_raises(self) -> None:
        rules = [
            PolicyRule("dup", Dimension.INTEGRITY, "True", _URN_A, None),
            PolicyRule("dup", Dimension.TRUST, "True", _URN_A, None),
        ]
        with pytest.raises(ValidationError) as exc_info:
            DataContext.build(assets=_assets("raw_patients"), edges=[], rules=rules)
        assert exc_info.value.code == "duplicate rule_ids"


class TestCycleDetection:
    def test_acyclic_graph_has_no_cycles(self) -> None:
        ctx = DataContext.build(
            assets=_assets("raw_patients", "staging_patients", "mart_billing"),
            edges=[_EDGE_AB, _EDGE_BC],
            rules=[],
        )
        assert ctx.cycle_nodes == frozenset()

    def test_direct_cycle_detected(self) -> None:
        a = _urn("a")
        b = _urn("b")
        ctx = DataContext.build(
            assets=[DataAsset.healthy(a), DataAsset.healthy(b)],
            edges=[
                LineageEdge(a, b, EdgeKind.IDENTITY, None),
                LineageEdge(b, a, EdgeKind.IDENTITY, None),
            ],
            rules=[],
        )
        assert a in ctx.cycle_nodes
        assert b in ctx.cycle_nodes

    def test_three_node_cycle_detected(self) -> None:
        urns = [_urn(n) for n in ["x", "y", "z"]]
        ctx = DataContext.build(
            assets=[DataAsset.healthy(u) for u in urns],
            edges=[
                LineageEdge(urns[0], urns[1], EdgeKind.IDENTITY, None),
                LineageEdge(urns[1], urns[2], EdgeKind.IDENTITY, None),
                LineageEdge(urns[2], urns[0], EdgeKind.IDENTITY, None),
            ],
            rules=[],
        )
        assert ctx.cycle_nodes == frozenset(urns)

    def test_partial_cycle_only_cycle_nodes_flagged(self) -> None:
        safe = _urn("safe")
        a = _urn("cycle_a")
        b = _urn("cycle_b")
        ctx = DataContext.build(
            assets=[DataAsset.healthy(safe), DataAsset.healthy(a), DataAsset.healthy(b)],
            edges=[
                LineageEdge(safe, a, EdgeKind.IDENTITY, None),
                LineageEdge(a, b, EdgeKind.IDENTITY, None),
                LineageEdge(b, a, EdgeKind.IDENTITY, None),
            ],
            rules=[],
        )
        assert safe not in ctx.cycle_nodes
        assert a in ctx.cycle_nodes
        assert b in ctx.cycle_nodes


@st.composite
def valid_dag_context(draw: st.DrawFn) -> DataContext:
    n = draw(st.integers(min_value=1, max_value=12))
    names = [f"asset_{i}" for i in range(n)]
    assets = [DataAsset.healthy(_urn(name)) for name in names]
    levels = {_urn(name): draw(st.integers(min_value=0, max_value=n)) for name in names}

    possible_edges = [
        (_urn(names[i]), _urn(names[j]))
        for i in range(n)
        for j in range(n)
        if levels[_urn(names[i])] < levels[_urn(names[j])]
    ]
    chosen = draw(
        st.lists(
            st.sampled_from(possible_edges) if possible_edges else st.nothing(),
            max_size=min(len(possible_edges), 20),
            unique=True,
        )
    )
    edges = [LineageEdge(src, dst, EdgeKind.IDENTITY, None) for src, dst in chosen]

    return DataContext.build(assets=assets, edges=edges, rules=[])


@given(ctx=valid_dag_context())
@settings(max_examples=200)
def test_acyclic_dag_has_no_cycle_nodes(ctx: DataContext) -> None:
    assert ctx.cycle_nodes == frozenset()


@given(ctx=valid_dag_context())
@settings(max_examples=200)
def test_all_urns_always_sorted(ctx: DataContext) -> None:
    urns = ctx.all_urns()
    assert urns == sorted(urns)


@given(ctx=valid_dag_context())
@settings(max_examples=100)
def test_successors_predecessor_consistency(ctx: DataContext) -> None:
    for urn in ctx.all_urns():
        for succ in ctx.successors(urn):
            assert urn in ctx.predecessors(succ)
        for pred in ctx.predecessors(urn):
            assert urn in ctx.successors(pred)


@given(ctx=valid_dag_context())
@settings(max_examples=100)
def test_replace_asset_is_isolated(ctx: DataContext) -> None:
    if not ctx.all_urns():
        return
    urn = ctx.all_urns()[0]
    original_status = ctx.asset(urn).get_status(Dimension.INTEGRITY)
    updated = ctx.asset(urn).with_status(Dimension.INTEGRITY, StatusLevel.CRITICAL)
    new_ctx = ctx.replace_asset(updated)
    assert ctx.asset(urn).get_status(Dimension.INTEGRITY) == original_status
    assert new_ctx.asset(urn).get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL
    for other_urn in ctx.all_urns():
        if other_urn != urn:
            assert new_ctx.asset(other_urn) == ctx.asset(other_urn)
