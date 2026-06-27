"""round-robin routing policy."""

import random
from dataclasses import dataclass
from typing import Any

from powergslb.client import ClientContext
from powergslb.routing.base import Positive, RoutingPolicy

__all__ = ['RoundRobin']


@dataclass(frozen=True, kw_only=True)
class RoundRobin(RoutingPolicy):
    """Answer the highest weight tier, capped at 'max_answers' records.

    'weight' is read as a tier: the highest-weight group of candidates wins. A tier of 'max_answers' or fewer is
    returned whole; a larger tier is randomly subsampled to 'max_answers' to bound UDP fragmentation and TC=1
    truncation on large RRsets.

    :param max_answers: Maximum records returned from the winning tier.
    """
    name = 'round-robin'

    max_answers: Positive = 8

    def select(self, candidates: list[dict[str, Any]], context: ClientContext) -> list[dict[str, Any]]:
        tier = self.highest_tier(candidates)
        if len(tier) <= self.max_answers:
            return tier
        return random.sample(tier, self.max_answers)
