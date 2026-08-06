from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.optimization import (
    _DEFAULT_WEIGHT,
    _asset_weight,
    _consumer_count,
    solve,
)
from kronika.types import DataAsset, EdgeKind, LineageEdge

_P = "urn:li:dataPlatform:hive"


def _urn(name: str) -> str:
    return f"urn:li:dataset:({_P},{name},PROD)"


def _asset(name: str, *, tags: frozenset[str] | None = None) -> DataAsset:
    return DataAsset.healthy(_urn(name), tags=tags or frozenset())


def _critical(name: str, *, tags: frozenset[str] | None = None) -> DataAsset:
    base = DataAsset.healthy(_urn(name), tags=tags or frozenset())
    return base.with_status(Dimension.INTEGRITY, StatusLevel.CRITICAL)


def _edge(src: str, dst: str) -> LineageEdge:
    return LineageEdge(_urn(src), _urn(dst), EdgeKind.IDENTITY, None)


class TestAssetWeight:
    def test_untagged_uses_default(self) -> None:
        assert _asset_weight(_asset("a")) == _DEFAULT_WEIGHT

    def test_critical_tag(self) -> None:
        assert _asset_weight(_asset("a", tags=frozenset({"critical"}))) == 3.0

    def test_pii_tag(self) -> None:
        assert _asset_weight(_asset("a", tags=frozenset({"pii"}))) == 2.5

    def test_deprecated_overrides_all(self) -> None:
        assert _asset_weight(_asset("a", tags=frozenset({"critical", "deprecated"}))) == 0.5

    def test_multiple_tags_uses_max(self) -> None:
        assert _asset_weight(_asset("a", tags=frozenset({"pii", "internal"}))) == 2.5


class TestConsumerCount:
    def test_no_successors(self) -> None:
        ctx = DataContext.build(assets=[_asset("a")], edges=[], rules=[])
        assert _consumer_count(_urn("a"), ctx) == 0

    def test_direct_successors(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a"), _asset("b"), _asset("c")],
            edges=[_edge("a", "b"), _edge("a", "c")],
            rules=[],
        )
        assert _consumer_count(_urn("a"), ctx) == 2

    def test_transitive_consumers(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a"), _asset("b"), _asset("c")],
            edges=[_edge("a", "b"), _edge("b", "c")],
            rules=[],
        )
        assert _consumer_count(_urn("a"), ctx) == 2


class TestSolveEmpty:
    def test_no_critical_assets_returns_empty(self) -> None:
        ctx = DataContext.build(assets=[_asset("a"), _asset("b")], edges=[], rules=[])
        result = solve(ctx)
        assert result.halt_set == frozenset()
        assert "No assets" in result.objective


class TestSolveStar:
    def test_source_critical_all_downstream_covered(self) -> None:
        ctx = DataContext.build(
            assets=[
                _critical("src", tags=frozenset({"critical"})),
                _asset("d1"),
                _asset("d2"),
                _asset("d3"),
            ],
            edges=[_edge("src", "d1"), _edge("src", "d2"), _edge("src", "d3")],
            rules=[],
        )
        result = solve(ctx)
        assert _urn("src") in result.halt_set
        assert len(result.halt_set) == 1


class TestSolveLinearChain:
    def test_first_critical_in_chain(self) -> None:
        ctx = DataContext.build(
            assets=[_critical("a"), _asset("b"), _asset("c")],
            edges=[_edge("a", "b"), _edge("b", "c")],
            rules=[],
        )
        result = solve(ctx)
        assert _urn("a") in result.halt_set

    def test_middle_critical_isolated(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a"), _critical("b", tags=frozenset({"critical"})), _asset("c")],
            edges=[_edge("a", "b"), _edge("b", "c")],
            rules=[],
        )
        result = solve(ctx)
        assert result.halt_set != frozenset()
        assert _urn("b") in result.halt_set or _urn("a") in result.halt_set


class TestSolveDiamond:
    def test_shared_upstream_preferred_over_two_separate(self) -> None:
        # A → B → D (B is critical, D is critical)
        # A → C → D (C is healthy)
        # Halting A covers both B and D; halting B alone covers B but not D.
        # Optimal: halt A (covers all critical assets with count 1).
        ctx = DataContext.build(
            assets=[
                _asset("a"),
                _critical("b", tags=frozenset({"critical"})),
                _asset("c"),
                _critical("d", tags=frozenset({"critical"})),
            ],
            edges=[
                _edge("a", "b"),
                _edge("a", "c"),
                _edge("b", "d"),
                _edge("c", "d"),
            ],
            rules=[],
        )
        result = solve(ctx)
        assert len(result.halt_set) <= 2


class TestSolveDeterminism:
    def test_same_context_same_result(self) -> None:
        ctx = DataContext.build(
            assets=[_critical("x"), _critical("y"), _asset("z")],
            edges=[_edge("x", "z"), _edge("y", "z")],
            rules=[],
        )
        r1 = solve(ctx)
        r2 = solve(ctx)
        assert r1 == r2

    def test_halt_set_is_sorted_output(self) -> None:
        ctx = DataContext.build(
            assets=[_critical("alpha"), _critical("beta"), _critical("gamma")],
            edges=[],
            rules=[],
        )
        r1 = solve(ctx)
        r2 = solve(ctx)
        assert r1.halt_set == r2.halt_set


class TestSolveRationale:
    def test_rationale_keys_are_in_halt_set(self) -> None:
        ctx = DataContext.build(
            assets=[_critical("a", tags=frozenset({"critical"})), _asset("b")],
            edges=[_edge("a", "b")],
            rules=[],
        )
        result = solve(ctx)
        for key in result.rationale:
            assert key in result.halt_set

    def test_rationale_non_empty_for_halt(self) -> None:
        ctx = DataContext.build(assets=[_critical("a")], edges=[], rules=[])
        result = solve(ctx)
        assert result.rationale


@st.composite
def acyclic_ctx_with_some_critical(draw: st.DrawFn) -> DataContext:
    n = draw(st.integers(min_value=2, max_value=10))
    names = [f"asset_{i}" for i in range(n)]
    levels = {name: draw(st.integers(0, n)) for name in names}

    assets: list[DataAsset] = []
    for name in names:
        is_crit = draw(st.booleans())
        base = DataAsset.healthy(_urn(name))
        if is_crit:
            base = base.with_status(Dimension.INTEGRITY, StatusLevel.CRITICAL)
        assets.append(base)

    possible = [
        (names[i], names[j])
        for i in range(n)
        for j in range(n)
        if levels[names[i]] < levels[names[j]]
    ]
    chosen = draw(
        st.lists(
            st.sampled_from(possible) if possible else st.nothing(),
            max_size=min(len(possible), 12),
            unique=True,
        )
    )
    edges = [LineageEdge(_urn(s), _urn(d), EdgeKind.IDENTITY, None) for s, d in chosen]
    return DataContext.build(assets=assets, edges=edges, rules=[])


@given(ctx=acyclic_ctx_with_some_critical())
@settings(max_examples=200)
def test_property_halt_set_only_contains_critical_or_ancestors(ctx: DataContext) -> None:
    result = solve(ctx)
    from kronika.optimization import _reachable_predecessors

    critical_urns = {
        urn
        for urn in ctx.all_urns()
        if ctx.asset(urn).get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL
    }
    for halted in result.halt_set:
        ancestors_of_any_critical = any(
            halted in _reachable_predecessors(c, ctx) or halted == c for c in critical_urns
        )
        assert ancestors_of_any_critical, (
            f"{halted} is in halt_set but is not a critical asset or ancestor of one"
        )


@given(ctx=acyclic_ctx_with_some_critical())
@settings(max_examples=200)
def test_property_solve_is_deterministic(ctx: DataContext) -> None:
    r1 = solve(ctx)
    r2 = solve(ctx)
    assert r1 == r2


@given(ctx=acyclic_ctx_with_some_critical())
@settings(max_examples=200)
def test_property_all_critical_covered(ctx: DataContext) -> None:
    from kronika.optimization import _reachable_predecessors

    result = solve(ctx)
    critical_urns = {
        urn
        for urn in ctx.all_urns()
        if ctx.asset(urn).get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL
    }
    for crit in critical_urns:
        ancestors = _reachable_predecessors(crit, ctx)
        covered = crit in result.halt_set or bool(result.halt_set & ancestors)
        assert covered, f"Critical asset {crit} not covered by halt_set {result.halt_set}"
