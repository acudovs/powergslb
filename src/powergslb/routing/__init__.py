"""Routing policy strategy: one RoutingPolicy subclass per type, resolved per DNS query per rrset."""

from powergslb.routing.base import RoutingPolicy
from powergslb.routing.round_robin import RoundRobin
from powergslb.routing.sticky_hash import StickyHash
from powergslb.routing.weighted_random import WeightedRandom

__all__ = ['RoundRobin', 'RoutingPolicy', 'StickyHash', 'WeightedRandom']
