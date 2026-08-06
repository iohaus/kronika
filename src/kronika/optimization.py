from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from kronika.data_context import DataContext
from kronika.dimensions import Dimension, StatusLevel
from kronika.types import DataAsset

_CRITICALITY_WEIGHTS: dict[str, float] = {
    "critical": 3.0,
    "pii": 2.5,
    "internal": 1.5,
}
_DEFAULT_WEIGHT = 1.0
_DEPRECATED_WEIGHT = 0.5


@dataclass(frozen=True)
class ContainmentResult:
    halt_set: frozenset[str]
    objective: str
    rationale: dict[str, str]


def _asset_weight(asset: DataAsset) -> float:
    if "deprecated" in asset.tags:
        return _DEPRECATED_WEIGHT
    return max(
        (_CRITICALITY_WEIGHTS.get(t, _DEFAULT_WEIGHT) for t in asset.tags),
        default=_DEFAULT_WEIGHT,
    )


def _consumer_count(urn: str, context: DataContext) -> int:
    seen: set[str] = set()
    queue: deque[str] = deque(context.successors(urn))
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for s in context.successors(node):
            if s not in seen:
                queue.append(s)
    return len(seen)


def _score(urn: str, context: DataContext) -> float:
    asset = context.asset(urn)
    return _asset_weight(asset) * _consumer_count(urn, context)


def _halt_candidates(context: DataContext) -> list[str]:
    return sorted(
        urn
        for urn in context.all_urns()
        if context.asset(urn).get_status(Dimension.INTEGRITY) == StatusLevel.CRITICAL
    )


def _reachable_predecessors(urn: str, context: DataContext) -> set[str]:
    seen: set[str] = set()
    queue: deque[str] = deque([urn])
    while queue:
        node = queue.popleft()
        if node in seen:
            continue
        seen.add(node)
        for pred in context.predecessors(node):
            if pred not in seen:
                queue.append(pred)
    return seen


def solve(context: DataContext) -> ContainmentResult:
    candidates = _halt_candidates(context)

    if not candidates:
        return ContainmentResult(
            halt_set=frozenset(),
            objective="No assets require halting.",
            rationale={},
        )

    candidate_set = frozenset(candidates)

    # For each halt candidate, compute the set of ancestors (including itself)
    # that could serve as a cut point. The minimum cut is the smallest set of
    # nodes that covers all candidates, where a node "covers" a candidate if
    # it is the candidate itself or an ancestor of it.
    #
    # Greedy minimum vertex cover:
    # 1. Score each candidate by (weight x consumer_count).
    # 2. For each candidate (highest score first), if not yet covered,
    #    add its highest-scoring uncovered ancestor to the cut set.
    # 3. Ties broken lexicographically by URN.

    # Collect all potential cut nodes (ancestors of all candidates).
    ancestor_map: dict[str, frozenset[str]] = {
        c: frozenset(_reachable_predecessors(c, context)) for c in candidates
    }

    covered: set[str] = set()
    halt_set: set[str] = set()
    rationale: dict[str, str] = {}

    scored_candidates = sorted(
        candidates,
        key=lambda u: (-_score(u, context), u),
    )

    for candidate in scored_candidates:
        if candidate in covered:
            continue

        ancestors = ancestor_map[candidate]
        eligible = sorted(
            ancestors & candidate_set | {candidate},
            key=lambda u: (-_score(u, context), u),
        )

        chosen = eligible[0] if eligible else candidate
        halt_set.add(chosen)
        score_val = _score(chosen, context)
        c_count = sum(1 for c in candidates if chosen in ancestor_map[c] or chosen == c)
        rationale[chosen] = f"score={score_val:.1f}; covers {c_count} candidate(s)"

        for c in candidates:
            if chosen in ancestor_map[c] or chosen == c:
                covered.add(c)

    objective = (
        f"Halt {len(halt_set)} asset(s) to contain impact from "
        f"{len(candidates)} critical asset(s). "
        f"Halt set: {', '.join(sorted(halt_set))}."
    )

    return ContainmentResult(
        halt_set=frozenset(halt_set),
        objective=objective,
        rationale=rationale,
    )
