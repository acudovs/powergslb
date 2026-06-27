"""weighted-random routing policy."""

import random
from dataclasses import dataclass
from typing import Any

from powergslb.client import ClientContext
from powergslb.routing.base import Positive, RoutingPolicy

__all__ = ['WeightedRandom']


def _weighted_pick(candidates: list[dict[str, Any]], draw: int) -> dict[str, Any]:
    """Pick one record by walking cumulative 'weight' against 'draw'.

    Order-sensitive: the same 'draw' over the same candidate order returns the same record.

    :param candidates: Non-empty records to choose from, in a stable order, with a positive weight total.
    :param draw: A value in [0, sum of weights) selecting the cumulative bucket.
    :returns: The chosen record.
    """
    cumulative = 0
    for candidate in candidates:
        cumulative += candidate['weight']
        if draw < cumulative:
            return candidate
    return candidates[-1]  # unreachable while draw < total; a defensive fallback


@dataclass(frozen=True, kw_only=True)
class WeightedRandom(RoutingPolicy):
    """Answer up to 'max_answers' records, weighted-random across all candidates without replacement.

    'weight' is read as a proportion: a record's chance of being drawn is its weight over the remaining total. With
    the default 'max_answers' of 1 each query picks one record by weight, so the proportional split is exact across
    queries. A 'max_answers' above 1 returns several weighted records per answer, so the split then holds only
    statistically. An all-zero-weight set degrades to an equal sample.

    :param max_answers: Maximum records returned (default 1, a single proportional pick).
    """
    name = 'weighted-random'

    max_answers: Positive = 1

    def select(self, candidates: list[dict[str, Any]], context: ClientContext) -> list[dict[str, Any]]:
        count = min(self.max_answers, len(candidates))
        remaining = sorted(candidates, key=lambda record: record['content'])
        selected: list[dict[str, Any]] = []
        while len(selected) < count:
            total = sum(candidate['weight'] for candidate in remaining)
            if total == 0:
                # the remaining records are all zero-weight: fill the rest with an equal sample (randrange(0) raises)
                selected.extend(random.sample(remaining, count - len(selected)))
                break
            chosen = _weighted_pick(remaining, random.randrange(total))
            selected.append(chosen)
            remaining.remove(chosen)
        return selected
