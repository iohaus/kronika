from __future__ import annotations

import pytest

from kronika.dimensions import (
    DIMENSION_COUNT,
    Dimension,
    StatusLevel,
    bottom,
    lower_by_one,
    top,
    worse_of,
)


def test_dimension_count() -> None:
    assert DIMENSION_COUNT == 8
    assert len(Dimension) == 8


def test_status_level_ordering() -> None:
    assert StatusLevel.CRITICAL < StatusLevel.DEGRADED < StatusLevel.HEALTHY


def test_worse_of_same() -> None:
    for level in StatusLevel:
        assert worse_of(level, level) == level


def test_worse_of_asymmetric() -> None:
    assert worse_of(StatusLevel.HEALTHY, StatusLevel.CRITICAL) == StatusLevel.CRITICAL
    assert worse_of(StatusLevel.DEGRADED, StatusLevel.HEALTHY) == StatusLevel.DEGRADED
    assert worse_of(StatusLevel.CRITICAL, StatusLevel.DEGRADED) == StatusLevel.CRITICAL


def test_worse_of_is_commutative() -> None:
    levels = list(StatusLevel)
    for a in levels:
        for b in levels:
            assert worse_of(a, b) == worse_of(b, a)


def test_lower_by_one() -> None:
    assert lower_by_one(StatusLevel.HEALTHY) == StatusLevel.DEGRADED
    assert lower_by_one(StatusLevel.DEGRADED) == StatusLevel.CRITICAL
    assert lower_by_one(StatusLevel.CRITICAL) == StatusLevel.CRITICAL


def test_top_and_bottom() -> None:
    assert top() == StatusLevel.HEALTHY
    assert bottom() == StatusLevel.CRITICAL


@pytest.mark.parametrize("dim", list(Dimension))
def test_dimension_values_are_unique(dim: Dimension) -> None:
    assert dim.value in range(DIMENSION_COUNT)
