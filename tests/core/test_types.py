from __future__ import annotations

import pytest

from kronika.dimensions import Dimension, StatusLevel
from kronika.types import (
    ColumnLineage,
    DataAsset,
    EdgeKind,
    EventKind,
    LineageEdge,
    MetadataEvent,
    PolicyRule,
    ValidationError,
)

_URN_A = "urn:li:dataset:(urn:li:dataPlatform:hive,raw_patients,PROD)"
_URN_B = "urn:li:dataset:(urn:li:dataPlatform:hive,staging_patients,PROD)"
_URN_OWNER = "urn:li:corpuser:clinical_team"
_URN_DOMAIN = "urn:li:domain:healthcare"
_URN_GLOSSARY = "urn:li:glossaryTerm:BillingAmount"


class TestDataAsset:
    def test_healthy_factory(self) -> None:
        asset = DataAsset.healthy(_URN_A)
        assert asset.urn == _URN_A
        assert all(s == StatusLevel.HEALTHY for s in asset.status)
        assert asset.tags == frozenset()
        assert asset.owner_urn is None
        assert asset.domain_urn is None

    def test_healthy_with_optional_fields(self) -> None:
        asset = DataAsset.healthy(
            _URN_A,
            tags=frozenset({"pii", "critical"}),
            owner_urn=_URN_OWNER,
            domain_urn=_URN_DOMAIN,
        )
        assert "pii" in asset.tags
        assert asset.owner_urn == _URN_OWNER

    def test_get_status(self) -> None:
        asset = DataAsset.healthy(_URN_A)
        for dim in Dimension:
            assert asset.get_status(dim) == StatusLevel.HEALTHY

    def test_with_status_returns_new_instance(self) -> None:
        asset = DataAsset.healthy(_URN_A)
        updated = asset.with_status(Dimension.INTEGRITY, StatusLevel.CRITICAL)
        assert asset.get_status(Dimension.INTEGRITY) == StatusLevel.HEALTHY
        assert updated.get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL
        assert updated.urn == asset.urn

    def test_with_status_does_not_affect_other_dimensions(self) -> None:
        asset = DataAsset.healthy(_URN_A)
        updated = asset.with_status(Dimension.TRUST, StatusLevel.DEGRADED)
        for dim in Dimension:
            if dim != Dimension.TRUST:
                assert updated.get_status(dim) == StatusLevel.HEALTHY

    def test_invalid_urn_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataAsset.healthy("not-a-urn")
        assert exc_info.value.field == "asset.urn"
        assert exc_info.value.code == "invalid_urn"

    def test_wrong_status_length_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataAsset(
                urn=_URN_A,
                status=(StatusLevel.HEALTHY,),
                tags=frozenset(),
                owner_urn=None,
                domain_urn=None,
            )
        assert exc_info.value.field == "asset.status"

    def test_invalid_owner_urn_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            DataAsset.healthy(_URN_A, owner_urn="not-a-urn")
        assert exc_info.value.field == "asset.owner_urn"

    def test_hashable(self) -> None:
        a = DataAsset.healthy(_URN_A)
        b = DataAsset.healthy(_URN_A)
        assert hash(a) == hash(b)
        assert {a, b} == {a}

    def test_frozen(self) -> None:
        asset = DataAsset.healthy(_URN_A)
        with pytest.raises((AttributeError, TypeError)):
            asset.urn = "urn:li:dataset:(urn:li:dataPlatform:hive,other,PROD)"  # type: ignore[misc]


class TestColumnLineage:
    def test_valid_mapping(self) -> None:
        mapping = ColumnLineage(dst_column="gender", src_columns=frozenset({"gender_clean"}))
        assert mapping.dst_column == "gender"
        assert "gender_clean" in mapping.src_columns

    def test_empty_dst_column_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ColumnLineage(dst_column="", src_columns=frozenset({"x"}))
        assert exc_info.value.field == "column_lineage.dst_column"

    def test_empty_src_columns_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            ColumnLineage(dst_column="x", src_columns=frozenset())
        assert exc_info.value.field == "column_lineage.src_columns"

    def test_hashable(self) -> None:
        m1 = ColumnLineage(dst_column="x", src_columns=frozenset({"a"}))
        m2 = ColumnLineage(dst_column="x", src_columns=frozenset({"a"}))
        assert m1 == m2
        assert hash(m1) == hash(m2)


class TestLineageEdge:
    def test_valid_edge(self) -> None:
        edge = LineageEdge(src=_URN_A, dst=_URN_B, kind=EdgeKind.IDENTITY, column_lineage=None)
        assert edge.src == _URN_A
        assert edge.column_lineage is None

    def test_column_level_edge(self) -> None:
        edge = LineageEdge(
            src=_URN_A,
            dst=_URN_B,
            kind=EdgeKind.PROJECTION,
            column_lineage=frozenset(
                {
                    ColumnLineage(
                        dst_column="billing_amount", src_columns=frozenset({"billing_amount"})
                    )
                }
            ),
        )
        assert any(m.dst_column == "billing_amount" for m in edge.column_lineage)  # type: ignore[union-attr]

    def test_renamed_column_mapping(self) -> None:
        edge = LineageEdge(
            src=_URN_A,
            dst=_URN_B,
            kind=EdgeKind.PROJECTION,
            column_lineage=frozenset(
                {ColumnLineage(dst_column="gender", src_columns=frozenset({"gender_clean"}))}
            ),
        )
        mapping = next(iter(edge.column_lineage))  # type: ignore[arg-type]
        assert mapping.dst_column == "gender"
        assert mapping.src_columns == frozenset({"gender_clean"})

    def test_self_loop_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            LineageEdge(src=_URN_A, dst=_URN_A, kind=EdgeKind.IDENTITY, column_lineage=None)
        assert exc_info.value.code == "self_loop"

    def test_invalid_src_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            LineageEdge(src="bad", dst=_URN_B, kind=EdgeKind.IDENTITY, column_lineage=None)
        assert exc_info.value.field == "edge.src"

    def test_hashable(self) -> None:
        e1 = LineageEdge(src=_URN_A, dst=_URN_B, kind=EdgeKind.IDENTITY, column_lineage=None)
        e2 = LineageEdge(src=_URN_A, dst=_URN_B, kind=EdgeKind.IDENTITY, column_lineage=None)
        assert e1 == e2
        assert hash(e1) == hash(e2)


class TestPolicyRule:
    def test_valid_rule(self) -> None:
        rule = PolicyRule(
            rule_id="billing_nonneg",
            dimension=Dimension.INTEGRITY,
            predicate="billing_amount >= 0",
            scope_urn=_URN_A,
            glossary_urn=_URN_GLOSSARY,
        )
        assert rule.rule_id == "billing_nonneg"

    def test_empty_predicate_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PolicyRule(
                rule_id="x",
                dimension=Dimension.INTEGRITY,
                predicate="   ",
                scope_urn=_URN_A,
                glossary_urn=None,
            )
        assert exc_info.value.field == "rule.predicate"

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            PolicyRule(
                rule_id="",
                dimension=Dimension.INTEGRITY,
                predicate="x > 0",
                scope_urn=_URN_A,
                glossary_urn=None,
            )
        assert exc_info.value.field == "rule.rule_id"


class TestMetadataEvent:
    def _make_event(self, **overrides: object) -> MetadataEvent:
        defaults: dict[str, object] = {
            "event_id": "evt-001",
            "kind": EventKind.QUALITY_OBSERVATION,
            "source_urn": _URN_A,
            "columns": None,
            "payload": (("check", "failed"),),
            "occurred_at": "2026-07-25T10:00:00Z",
        }
        defaults.update(overrides)
        return MetadataEvent(**defaults)  # type: ignore[arg-type]

    def test_valid_event(self) -> None:
        evt = self._make_event()
        assert evt.event_id == "evt-001"
        assert evt.payload_value("check") == "failed"
        assert evt.payload_value("missing") is None

    def test_empty_id_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            self._make_event(event_id="")
        assert exc_info.value.field == "event.event_id"

    def test_invalid_source_raises(self) -> None:
        with pytest.raises(ValidationError) as exc_info:
            self._make_event(source_urn="not-a-urn")
        assert exc_info.value.field == "event.source_urn"

    def test_column_event(self) -> None:
        evt = self._make_event(columns=frozenset({"billing_amount", "patient_id"}))
        assert "billing_amount" in evt.columns  # type: ignore[operator]
