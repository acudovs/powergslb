# pylint: disable=missing-function-docstring, protected-access

"""Tests for the RoutingPolicy base class and the cached resolver.

The type registry (__init_subclass__), create() building and validating a policy from parsed policy JSON,
__post_init__ type-checking, and resolve() caching one frozen instance per raw policy_json string.
"""

from dataclasses import dataclass
from typing import Annotated, Any

import pytest

from powergslb.routing.base import RoutingPolicy
from powergslb.routing.round_robin import RoundRobin
from powergslb.routing.sticky_hash import StickyHash
from powergslb.routing.weighted_random import WeightedRandom


@pytest.fixture(autouse=True)
def _clear_resolve_cache() -> Any:
    """Drop the lru_cache so cached frozen instances do not leak across tests."""
    RoutingPolicy.resolve.cache_clear()
    yield
    RoutingPolicy.resolve.cache_clear()


# registry

def test_builtin_types_are_registered() -> None:
    assert {'round-robin', 'weighted-random', 'sticky-hash'} <= set(RoutingPolicy._registry)


def test_duplicate_type_name_raises() -> None:
    with pytest.raises(ValueError, match='duplicate routing policy type'):
        class _Dup(RoutingPolicy):  # the 'round-robin' token is already registered
            name = 'round-robin'

            def select(self, candidates: Any, context: Any) -> Any:
                return candidates


# create: success

def test_create_returns_typed_policy() -> None:
    assert isinstance(RoutingPolicy.create({'type': 'round-robin'}), RoundRobin)
    assert isinstance(RoutingPolicy.create({'type': 'weighted-random'}), WeightedRandom)
    assert isinstance(RoutingPolicy.create({'type': 'sticky-hash'}), StickyHash)


def test_create_omits_params_uses_defaults() -> None:
    policy = RoutingPolicy.create({'type': 'round-robin'})
    assert isinstance(policy, RoundRobin) and policy.max_answers == 8
    weighted = RoutingPolicy.create({'type': 'weighted-random'})
    assert isinstance(weighted, WeightedRandom) and weighted.max_answers == 1
    sticky = RoutingPolicy.create({'type': 'sticky-hash'})
    assert (isinstance(sticky, StickyHash)
            and (sticky.max_answers, sticky.ipv4_prefix, sticky.ipv6_prefix) == (1, 24, 64))


def test_create_param_overrides_default() -> None:
    policy = RoutingPolicy.create({'type': 'round-robin', 'max_answers': 2})
    assert isinstance(policy, RoundRobin) and policy.max_answers == 2


# create: failures

def test_create_missing_type_key_raises() -> None:
    with pytest.raises(ValueError, match="routing policy parameter 'type' invalid"):
        RoutingPolicy.create({'max_answers': 2})


def test_create_empty_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown routing policy type ''"):
        RoutingPolicy.create({'type': ''})


def test_create_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match='unknown routing policy type'):
        RoutingPolicy.create({'type': 'geoproximity'})


@pytest.mark.parametrize('policy_type', [['round-robin'], {'round-robin': True}, 123, [], 0])
def test_create_non_string_type_raises(policy_type: Any) -> None:
    with pytest.raises(ValueError, match="routing policy parameter 'type' invalid"):
        RoutingPolicy.create({'type': policy_type})


def test_create_unexpected_params_raises() -> None:
    with pytest.raises(ValueError, match='unexpected routing policy parameters'):
        RoutingPolicy.create({'type': 'weighted-random', 'bogus': 2})


def test_create_wrong_param_type_raises() -> None:
    with pytest.raises(ValueError, match="routing policy parameter 'max_answers' invalid"):
        RoutingPolicy.create({'type': 'round-robin', 'max_answers': 'eight'})


def test_create_bool_for_int_param_raises() -> None:
    # bool is a subclass of int, so max_answers=True must be rejected, not silently accepted as 1.
    with pytest.raises(ValueError, match="routing policy parameter 'max_answers' invalid"):
        RoutingPolicy.create({'type': 'round-robin', 'max_answers': True})


@pytest.mark.parametrize('max_answers', [0, -1])
def test_create_non_positive_max_answers_raises(max_answers: int) -> None:
    with pytest.raises(ValueError, match="routing policy parameter 'max_answers' invalid"):
        RoutingPolicy.create({'type': 'round-robin', 'max_answers': max_answers})


@pytest.mark.parametrize('prefix', [-1, 33])
def test_create_ipv4_prefix_out_of_range_raises(prefix: int) -> None:
    with pytest.raises(ValueError, match="routing policy parameter 'ipv4_prefix' invalid"):
        RoutingPolicy.create({'type': 'sticky-hash', 'ipv4_prefix': prefix})


@pytest.mark.parametrize('prefix', [-1, 129])
def test_create_ipv6_prefix_out_of_range_raises(prefix: int) -> None:
    with pytest.raises(ValueError, match="routing policy parameter 'ipv6_prefix' invalid"):
        RoutingPolicy.create({'type': 'sticky-hash', 'ipv6_prefix': prefix})


@pytest.mark.parametrize('prefix', [0, 32])
def test_create_ipv4_prefix_boundaries_accepted(prefix: int) -> None:
    policy = RoutingPolicy.create({'type': 'sticky-hash', 'ipv4_prefix': prefix})
    assert isinstance(policy, StickyHash) and policy.ipv4_prefix == prefix


def test_create_missing_required_param_raises() -> None:
    # No shipped policy has a required field, so a throwaway subclass exercises the missing-params branch.
    @dataclass(frozen=True, kw_only=True)
    class _Required(RoutingPolicy):
        name = '_required'

        must_set: Annotated[int, 'documentation']  # required (no default), non-callable metadata

        def select(self, candidates: Any, context: Any) -> Any:
            return candidates

    try:
        with pytest.raises(ValueError, match='missing routing policy parameters'):
            RoutingPolicy.create({'type': '_required'})
        # Constructing with the field set also exercises the non-callable-metadata skip in __post_init__.
        policy = RoutingPolicy.create({'type': '_required', 'must_set': 5})
        assert isinstance(policy, _Required) and policy.must_set == 5
    finally:
        RoutingPolicy._registry.pop('_required', None)


def test_policy_is_frozen() -> None:
    policy = RoundRobin(max_answers=4)
    with pytest.raises(Exception):  # noqa: B017  frozen dataclass forbids attribute assignment
        policy.max_answers = 2  # type: ignore[misc]


# resolve: caching

def test_resolve_same_string_returns_same_instance() -> None:
    first = RoutingPolicy.resolve('{"type": "round-robin"}')
    assert first is RoutingPolicy.resolve('{"type": "round-robin"}')
    assert isinstance(first, RoundRobin)


def test_resolve_distinct_strings_distinct_instances() -> None:
    assert (RoutingPolicy.resolve('{"type": "round-robin"}')
            is not RoutingPolicy.resolve('{"type": "weighted-random"}'))
    # the same policy with different params is a distinct cache key and instance
    assert (RoutingPolicy.resolve('{"type": "round-robin", "max_answers": 2}')
            is not RoutingPolicy.resolve('{"type": "round-robin"}'))


def test_resolve_invalid_json_raises_and_is_not_cached() -> None:
    with pytest.raises(ValueError):
        RoutingPolicy.resolve('{not json}')
    with pytest.raises(ValueError):
        RoutingPolicy.resolve('{not json}')  # lru_cache does not cache exceptions; it still raises


def test_resolve_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match='unknown routing policy type'):
        RoutingPolicy.resolve('{"type": "nope"}')


@pytest.mark.parametrize('policy_json', ['42', '"round-robin"', '[{"type": "round-robin"}]', 'null', 'true'])
def test_resolve_non_object_json_raises(policy_json: str) -> None:
    # valid JSON that is not an object (passes the DB JSON_VALID check) is rejected before create()
    with pytest.raises(ValueError, match='policy_json must be a JSON object'):
        RoutingPolicy.resolve(policy_json)
