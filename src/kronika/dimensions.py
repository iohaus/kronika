from __future__ import annotations

from enum import IntEnum, unique


@unique
class Dimension(IntEnum):
    INTEGRITY = 0
    TRUST = 1
    AVAILABILITY = 2
    COMPLIANCE = 3
    FRESHNESS = 4
    OWNERSHIP = 5
    DOCUMENTATION = 6
    PRIVACY = 7


DIMENSION_COUNT = len(Dimension)


@unique
class StatusLevel(IntEnum):
    CRITICAL = 0
    DEGRADED = 1
    HEALTHY = 2


def worse_of(a: StatusLevel, b: StatusLevel) -> StatusLevel:
    return StatusLevel(min(a, b))


def lower_by_one(level: StatusLevel) -> StatusLevel:
    return StatusLevel(max(0, level - 1))


def top() -> StatusLevel:
    return StatusLevel.HEALTHY


def bottom() -> StatusLevel:
    return StatusLevel.CRITICAL
