from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from kronika.check.audit import DataAudit
from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.impact import ImpactEngine, _propagate_one_dim, _translate_columns
from kronika.types import ColumnLineage, DataAsset, EdgeKind, EventKind, LineageEdge, MetadataEvent

_P = "urn:li:dataPlatform:hive"


def _urn(name: str) -> str:
    return f"urn:li:dataset:({_P},{name},PROD)"


def _asset(name: str, **kwargs: object) -> DataAsset:
    return DataAsset.healthy(_urn(name), **kwargs)  # type: ignore[arg-type]


def _event(
    source: str,
    kind: EventKind = EventKind.QUALITY_OBSERVATION,
    columns: frozenset[str] | None = None,
    payload: tuple[tuple[str, str], ...] = (),
) -> MetadataEvent:
    return MetadataEvent(
        event_id="evt-001",
        kind=kind,
        source_urn=_urn(source),
        columns=columns,
        payload=payload,
        occurred_at="2026-07-25T10:00:00Z",
    )


def _edge(
    src: str, dst: str, kind: EdgeKind = EdgeKind.IDENTITY, columns: frozenset[str] | None = None
) -> LineageEdge:
    """`columns` is a flat set of self-mapping column names (same name upstream and
    downstream) — a convenience wrapper over the real per-pair `ColumnLineage` shape,
    sufficient for tests that don't specifically exercise a rename."""
    column_lineage = (
        frozenset(ColumnLineage(dst_column=c, src_columns=frozenset({c})) for c in columns)
        if columns is not None
        else None
    )
    return LineageEdge(src=_urn(src), dst=_urn(dst), kind=kind, column_lineage=column_lineage)


_ENGINE = ImpactEngine()
_AUDIT = DataAudit()

_URN_RAW = _urn("raw_patients")
_URN_STAGING = _urn("staging_patients")
_URN_BILLING = _urn("mart_billing")
_URN_DEMO = _urn("mart_demographics")


def _healthcare_context() -> DataContext:
    return DataContext.build(
        assets=[
            _asset("raw_patients", tags=frozenset({"pii", "internal"})),
            _asset("staging_patients", tags=frozenset({"pii"})),
            _asset("mart_billing", tags=frozenset({"critical"})),
            _asset("mart_demographics", tags=frozenset({"internal"})),
        ],
        edges=[
            _edge(
                "raw_patients",
                "staging_patients",
                EdgeKind.IDENTITY,
                frozenset({"billing_amount", "patient_id"}),
            ),
            _edge(
                "staging_patients",
                "mart_billing",
                EdgeKind.PROJECTION,
                frozenset({"billing_amount"}),
            ),
            _edge(
                "staging_patients",
                "mart_demographics",
                EdgeKind.PROJECTION,
                frozenset({"patient_id"}),
            ),
        ],
        rules=[],
    )


def _mapping(dst: str, *src: str) -> ColumnLineage:
    return ColumnLineage(dst_column=dst, src_columns=frozenset(src))


class TestTranslateColumns:
    def test_unknown_upstream_stays_unknown(self) -> None:
        assert _translate_columns(None, frozenset({_mapping("y", "x")})) is None

    def test_proven_irrelevant_upstream_stays_irrelevant(self) -> None:
        assert _translate_columns(frozenset(), frozenset({_mapping("y", "x")})) == frozenset()

    def test_proven_irrelevant_not_widened_by_missing_lineage(self) -> None:
        assert _translate_columns(frozenset(), None) == frozenset()

    def test_missing_lineage_widens_relevant_upstream_to_unknown(self) -> None:
        assert _translate_columns(frozenset({"billing_amount"}), None) is None

    def test_real_mapping_translates_matching_column(self) -> None:
        translated = _translate_columns(
            frozenset({"billing_amount"}), frozenset({_mapping("billing_amount", "billing_amount")})
        )
        assert translated == frozenset({"billing_amount"})

    def test_real_mapping_excludes_non_matching_column(self) -> None:
        translated = _translate_columns(
            frozenset({"patient_id"}), frozenset({_mapping("billing_amount", "billing_amount")})
        )
        assert translated == frozenset()

    def test_real_mapping_translates_renamed_column(self) -> None:
        translated = _translate_columns(
            frozenset({"gender_clean"}), frozenset({_mapping("gender", "gender_clean")})
        )
        assert translated == frozenset({"gender"})

    def test_real_mapping_handles_n_to_one_derivation(self) -> None:
        translated = _translate_columns(
            frozenset({"discharge_date"}),
            frozenset({_mapping("length_of_stay_days", "date_of_admission", "discharge_date")}),
        )
        assert translated == frozenset({"length_of_stay_days"})


class TestPropagateOneDim:
    def test_identity_inherit(self) -> None:
        result = _propagate_one_dim(
            Dimension.INTEGRITY,
            EdgeKind.IDENTITY,
            StatusLevel.CRITICAL,
            StatusLevel.HEALTHY,
            None,
        )
        assert result == StatusLevel.CRITICAL

    def test_identity_no_change_when_upstream_better(self) -> None:
        result = _propagate_one_dim(
            Dimension.INTEGRITY,
            EdgeKind.IDENTITY,
            StatusLevel.HEALTHY,
            StatusLevel.DEGRADED,
            None,
        )
        assert result == StatusLevel.DEGRADED

    def test_projection_col_filter_match(self) -> None:
        result = _propagate_one_dim(
            Dimension.INTEGRITY,
            EdgeKind.PROJECTION,
            StatusLevel.CRITICAL,
            StatusLevel.HEALTHY,
            frozenset({"billing_amount"}),
        )
        assert result == StatusLevel.CRITICAL

    def test_projection_col_filter_no_match(self) -> None:
        result = _propagate_one_dim(
            Dimension.INTEGRITY,
            EdgeKind.PROJECTION,
            StatusLevel.CRITICAL,
            StatusLevel.HEALTHY,
            frozenset(),
        )
        from kronika.impact import _NO_CHANGE

        assert result is _NO_CHANGE

    def test_aggregation_caps_critical_to_degraded(self) -> None:
        result = _propagate_one_dim(
            Dimension.INTEGRITY,
            EdgeKind.AGGREGATION,
            StatusLevel.CRITICAL,
            StatusLevel.HEALTHY,
            None,
        )
        assert result == StatusLevel.DEGRADED

    def test_aggregation_degraded_stays_degraded(self) -> None:
        result = _propagate_one_dim(
            Dimension.INTEGRITY,
            EdgeKind.AGGREGATION,
            StatusLevel.DEGRADED,
            StatusLevel.HEALTHY,
            None,
        )
        assert result == StatusLevel.DEGRADED

    def test_trust_aggregation_lower_by_one(self) -> None:
        result = _propagate_one_dim(
            Dimension.TRUST,
            EdgeKind.AGGREGATION,
            StatusLevel.HEALTHY,
            StatusLevel.HEALTHY,
            None,
        )
        assert result == StatusLevel.DEGRADED

    def test_ownership_no_prop(self) -> None:
        from kronika.impact import _NO_CHANGE

        result = _propagate_one_dim(
            Dimension.OWNERSHIP,
            EdgeKind.IDENTITY,
            StatusLevel.CRITICAL,
            StatusLevel.HEALTHY,
            None,
        )
        assert result is _NO_CHANGE

    def test_compliance_recheck(self) -> None:
        from kronika.impact import _NO_CHANGE

        result = _propagate_one_dim(
            Dimension.COMPLIANCE,
            EdgeKind.IDENTITY,
            StatusLevel.CRITICAL,
            StatusLevel.HEALTHY,
            None,
        )
        assert result is _NO_CHANGE


class TestImpactEngineLinear:
    def test_source_changes_integrity(self) -> None:
        ctx = DataContext.build(assets=[_asset("raw_patients")], edges=[], rules=[])
        _, result = _ENGINE.analyze(ctx, _event("raw_patients"))
        assert _URN_RAW in result.changed
        assert (
            result.changed[_URN_RAW].after.get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL
        )

    def test_propagates_downstream(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a"), _asset("b")],
            edges=[_edge("a", "b")],
            rules=[],
        )
        _, result = _ENGINE.analyze(ctx, _event("a"))
        assert _urn("b") in result.changed

    def test_source_only_event_no_unnecessary_changes(self) -> None:
        ctx = DataContext.build(assets=[_asset("a")], edges=[], rules=[])
        _, result = _ENGINE.analyze(ctx, _event("a"))
        assert _urn("a") in result.changed
        assert len(result.changed) == 1

    def test_freshness_event(self) -> None:
        ctx = DataContext.build(assets=[_asset("a")], edges=[], rules=[])
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.FRESHNESS_VIOLATION,
            source_urn=_urn("a"),
            columns=None,
            payload=(("severity", "expired"),),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        delta = result.changed[_urn("a")]
        assert delta.after.get_status(Dimension.FRESHNESS) == StatusLevel.CRITICAL
        assert delta.after.get_status(Dimension.AVAILABILITY) == StatusLevel.DEGRADED

    def test_ownership_does_not_propagate_through_lineage(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a"), _asset("b")],
            edges=[_edge("a", "b")],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.OWNERSHIP_CHANGE,
            source_urn=_urn("a"),
            columns=None,
            payload=(("new_ownership", "orphan"),),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert (
            _urn("b") not in result.changed
            or result.changed[_urn("b")].after.get_status(Dimension.OWNERSHIP)
            == StatusLevel.HEALTHY
        )


class TestImpactEngineColumnLevel:
    def test_column_in_edge_propagates(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("src"), _asset("dst")],
            edges=[_edge("src", "dst", EdgeKind.PROJECTION, frozenset({"billing_amount"}))],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("src"),
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert _urn("dst") in result.changed

    def test_column_not_in_edge_does_not_propagate(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("src"), _asset("dst")],
            edges=[_edge("src", "dst", EdgeKind.PROJECTION, frozenset({"patient_id"}))],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("src"),
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert _urn("dst") not in result.changed

    def test_table_level_edge_propagates_conservatively(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("src"), _asset("dst")],
            edges=[_edge("src", "dst", EdgeKind.PROJECTION, None)],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=_urn("src"),
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert _urn("dst") in result.changed


class TestColumnTranslationRegression:
    """These three prove the original bug (a same-name coincidence heuristic that
    could produce a false CLEAR on a renamed column) is actually fixed, not just
    that the unit-level translation function looks right in isolation."""

    def test_renamed_column_propagates_across_multiple_hops(self) -> None:
        # A --[real: x -> y]--> B --[real: y -> z]--> C. Event implicates "x" on A.
        # Under the OLD flat same-name check this would false-CLEAR at C, since
        # event.columns={"x"} never literally matches C's own column name "z".
        a, b, c = _urn("a"), _urn("b"), _urn("c")
        ctx = DataContext.build(
            assets=[DataAsset.healthy(u) for u in (a, b, c)],
            edges=[
                LineageEdge(a, b, EdgeKind.PROJECTION, frozenset({_mapping("y", "x")})),
                LineageEdge(b, c, EdgeKind.PROJECTION, frozenset({_mapping("z", "y")})),
            ],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=a,
            columns=frozenset({"x"}),
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert c in result.changed
        assert result.changed[c].after.get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL

    def test_missing_lineage_widens_when_upstream_relevant(self) -> None:
        # A --[real: excludes "billing_amount"]--> B --[column_lineage=None]--> C.
        # Event implicates a column that IS mapped through to B (so B has something
        # relevant), but B->C has no authored lineage at all. C must still be
        # touched — conservative widening, never a silent false CLEAR.
        a, b, c = _urn("a"), _urn("b"), _urn("c")
        ctx = DataContext.build(
            assets=[DataAsset.healthy(u) for u in (a, b, c)],
            edges=[
                LineageEdge(
                    a, b, EdgeKind.PROJECTION, frozenset({_mapping("kept", "billing_amount")})
                ),
                LineageEdge(b, c, EdgeKind.PROJECTION, None),
            ],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=a,
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert c in result.changed

    def test_missing_lineage_stays_clear_when_upstream_proven_irrelevant(self) -> None:
        # Same shape, but the event's column does NOT appear in A->B's real mapping
        # at all, so B is proven irrelevant. B->C still lacks lineage. C must NOT be
        # touched — a missing edge elsewhere in the graph shouldn't force a spurious
        # over-propagation past a node real lineage already cleared.
        a, b, c = _urn("a"), _urn("b"), _urn("c")
        ctx = DataContext.build(
            assets=[DataAsset.healthy(u) for u in (a, b, c)],
            edges=[
                LineageEdge(
                    a, b, EdgeKind.PROJECTION, frozenset({_mapping("kept", "unrelated_column")})
                ),
                LineageEdge(b, c, EdgeKind.PROJECTION, None),
            ],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=a,
            columns=frozenset({"billing_amount"}),
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert c not in result.changed


class TestImpactEngineDiamond:
    def test_diamond_both_paths_contribute(self) -> None:
        a, b, c, d = [_urn(n) for n in ["a", "b", "c", "d"]]
        ctx = DataContext.build(
            assets=[DataAsset.healthy(u) for u in [a, b, c, d]],
            edges=[
                LineageEdge(a, b, EdgeKind.IDENTITY, None),
                LineageEdge(a, c, EdgeKind.IDENTITY, None),
                LineageEdge(b, d, EdgeKind.IDENTITY, None),
                LineageEdge(c, d, EdgeKind.IDENTITY, None),
            ],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=a,
            columns=None,
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert d in result.changed
        assert result.changed[d].after.get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL

    def test_non_diamond_path_longer_than_bfs(self) -> None:
        a, b, c, d = [_urn(n) for n in ["src", "mid_a", "mid_b_mid_c", "sink"]]
        ctx = DataContext.build(
            assets=[DataAsset.healthy(u) for u in [a, b, c, d]],
            edges=[
                LineageEdge(a, b, EdgeKind.IDENTITY, None),
                LineageEdge(a, c, EdgeKind.IDENTITY, None),
                LineageEdge(b, c, EdgeKind.IDENTITY, None),
                LineageEdge(c, d, EdgeKind.IDENTITY, None),
            ],
            rules=[],
        )
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=a,
            columns=None,
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert d in result.changed


class TestImpactEngineCycleNode:
    def test_cycle_source_marked_all_critical(self) -> None:
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
        evt = MetadataEvent(
            event_id="e1",
            kind=EventKind.QUALITY_OBSERVATION,
            source_urn=a,
            columns=None,
            payload=(),
            occurred_at="2026-07-25T10:00:00Z",
        )
        _, result = _ENGINE.analyze(ctx, evt)
        assert a in result.changed
        delta = result.changed[a]
        assert all(s == StatusLevel.CRITICAL for s in delta.after.status)


class TestEvidencePaths:
    def test_source_has_singleton_path(self) -> None:
        ctx = DataContext.build(assets=[_asset("a")], edges=[], rules=[])
        _, result = _ENGINE.analyze(ctx, _event("a"))
        assert result.evidence_paths[_urn("a")] == (_urn("a"),)

    def test_linear_chain_path(self) -> None:
        ctx = DataContext.build(
            assets=[_asset("a"), _asset("b"), _asset("c")],
            edges=[_edge("a", "b"), _edge("b", "c")],
            rules=[],
        )
        _, result = _ENGINE.analyze(ctx, _event("a"))
        path_c = result.evidence_paths.get(_urn("c"))
        assert path_c is not None
        assert path_c[0] == _urn("a")
        assert path_c[-1] == _urn("c")


class TestAuditInvariants:
    def test_no_improvement_passes_for_valid_result(self) -> None:
        ctx = _healthcare_context()
        after, result = _ENGINE.analyze(ctx, _event("raw_patients"))
        violations = _AUDIT._check_no_improvement(ctx, after, result)
        assert violations == []

    def test_graph_conservatism_passes(self) -> None:
        ctx = _healthcare_context()
        after, result = _ENGINE.analyze(ctx, _event("raw_patients"))
        violations = _AUDIT._check_graph_conservatism(ctx, after, result)
        assert violations == []

    def test_idempotency(self) -> None:
        ctx = _healthcare_context()
        violations = _AUDIT.check_idempotency(ctx, _event("raw_patients"), _ENGINE)
        assert violations == []


@st.composite
def acyclic_context_and_event(draw: st.DrawFn) -> tuple[DataContext, MetadataEvent]:
    n = draw(st.integers(min_value=2, max_value=10))
    names = [f"asset_{i}" for i in range(n)]
    assets = [DataAsset.healthy(_urn(name)) for name in names]
    levels = {name: draw(st.integers(0, n)) for name in names}

    possible = [
        (names[i], names[j])
        for i in range(n)
        for j in range(n)
        if levels[names[i]] < levels[names[j]]
    ]
    chosen_pairs = draw(
        st.lists(
            st.sampled_from(possible) if possible else st.nothing(),
            max_size=min(len(possible), 15),
            unique=True,
        )
    )
    edges = [LineageEdge(_urn(s), _urn(d), EdgeKind.IDENTITY, None) for s, d in chosen_pairs]
    ctx = DataContext.build(assets=assets, edges=edges, rules=[])

    source_name = draw(st.sampled_from(names))
    evt = MetadataEvent(
        event_id="hyp-001",
        kind=EventKind.QUALITY_OBSERVATION,
        source_urn=_urn(source_name),
        columns=None,
        payload=(),
        occurred_at="2026-07-25T10:00:00Z",
    )
    return ctx, evt


@given(inputs=acyclic_context_and_event())
@settings(max_examples=300)
def test_property_no_improvement(inputs: tuple[DataContext, MetadataEvent]) -> None:
    ctx, event = inputs
    after, result = _ENGINE.analyze(ctx, event)
    violations = _AUDIT._check_no_improvement(ctx, after, result)
    assert violations == [], f"Violations: {violations}"


@given(inputs=acyclic_context_and_event())
@settings(max_examples=300)
def test_property_graph_conservatism(inputs: tuple[DataContext, MetadataEvent]) -> None:
    ctx, event = inputs
    after, result = _ENGINE.analyze(ctx, event)
    violations = _AUDIT._check_graph_conservatism(ctx, after, result)
    assert violations == [], f"Violations: {violations}"


@given(inputs=acyclic_context_and_event())
@settings(max_examples=200)
def test_property_idempotency(inputs: tuple[DataContext, MetadataEvent]) -> None:
    ctx, event = inputs
    violations = _AUDIT.check_idempotency(ctx, event, _ENGINE)
    assert violations == [], f"Violations: {violations}"


@given(inputs=acyclic_context_and_event())
@settings(max_examples=200)
def test_property_source_always_in_changed(inputs: tuple[DataContext, MetadataEvent]) -> None:
    ctx, event = inputs
    _, result = _ENGINE.analyze(ctx, event)
    assert event.source_urn in result.changed


@given(inputs=acyclic_context_and_event())
@settings(max_examples=200)
def test_property_changed_assets_are_reachable(inputs: tuple[DataContext, MetadataEvent]) -> None:
    from kronika.check.audit import ImpactEngine_reachable

    ctx, event = inputs
    _, result = _ENGINE.analyze(ctx, event)
    reachable = ImpactEngine_reachable(ctx, event.source_urn)
    for urn in result.changed:
        assert urn in reachable, f"{urn} changed but not reachable from {event.source_urn}"
