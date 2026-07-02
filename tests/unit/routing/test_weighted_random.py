# pylint: disable=missing-function-docstring

"""Tests for the weighted-random routing policy.

_weighted_pick is exercised with known draws (no RNG seeding) on content-sorted candidates, so a given draw always
lands in the same cumulative bucket; select() sorts once before the pick loop, so its result is order-independent.
select() returns exactly one record and degrades an all-zero-weight tier to an equal pick (never random.randrange(0)).
"""

import random
from typing import Any

import netaddr
import pytest

from powergslb.client import ClientContext
from powergslb.routing.weighted_random import WeightedRandom, _weighted_pick

CONTEXT = ClientContext(netaddr.IPNetwork('192.0.2.7'))


def _record(content: str, weight: int) -> dict[str, Any]:
    return {'id': 0, 'content': content, 'weight': weight, 'qname': 'example.com'}


# _weighted_pick: deterministic walk over content-sorted cumulative weights

def test_weighted_pick_known_draws() -> None:
    # content-sorted: a(weight 1) -> [0,1), b(weight 2) -> [1,3), c(weight 3) -> [3,6)
    records = [_record('a', 1), _record('b', 2), _record('c', 3)]
    assert _weighted_pick(records, 0)['content'] == 'a'
    assert _weighted_pick(records, 1)['content'] == 'b'
    assert _weighted_pick(records, 2)['content'] == 'b'
    assert _weighted_pick(records, 3)['content'] == 'c'
    assert _weighted_pick(records, 5)['content'] == 'c'


def test_select_is_order_independent() -> None:
    # select sorts by content before picking, so input order does not change which record a fixed RNG draws.
    ordered = [_record('a', 1), _record('b', 2), _record('c', 3)]
    shuffled = [_record('c', 3), _record('a', 1), _record('b', 2)]
    random.seed(0)
    from_ordered = WeightedRandom().select(ordered, CONTEXT)[0]['content']
    random.seed(0)
    from_shuffled = WeightedRandom().select(shuffled, CONTEXT)[0]['content']
    assert from_ordered == from_shuffled


def test_weighted_pick_skips_zero_weight_records() -> None:
    # a has weight 0, so draw 0 lands in b's bucket [0,1)
    records = [_record('a', 0), _record('b', 1)]
    assert _weighted_pick(records, 0)['content'] == 'b'


# select

def test_default_max_answers_is_one() -> None:
    assert WeightedRandom().max_answers == 1


def test_select_returns_one_record_by_default() -> None:
    records = [_record('a', 1), _record('b', 2), _record('c', 3)]
    result = WeightedRandom().select(records, CONTEXT)
    assert len(result) == 1 and result[0] in records


def test_select_empty_returns_empty() -> None:
    assert not WeightedRandom().select([], CONTEXT)
    assert not WeightedRandom(max_answers=3).select([], CONTEXT)


def test_select_all_zero_weight_degrades_to_equal_pick() -> None:
    # An all-zero tier must not call random.randrange(0); it picks one record evenly instead.
    records = [_record('a', 0), _record('b', 0), _record('c', 0)]
    result = WeightedRandom().select(records, CONTEXT)
    assert len(result) == 1 and result[0] in records


# select: max_answers > 1 samples without replacement

def test_select_caps_at_max_answers_and_is_distinct() -> None:
    records = [_record(c, w) for c, w in [('a', 1), ('b', 2), ('c', 3), ('d', 4)]]
    result = WeightedRandom(max_answers=2).select(records, CONTEXT)
    assert len(result) == 2
    contents = [r['content'] for r in result]
    assert len(set(contents)) == 2  # without replacement: no duplicates
    assert set(contents) <= {'a', 'b', 'c', 'd'}


def test_select_max_answers_above_count_returns_all() -> None:
    records = [_record('a', 1), _record('b', 2), _record('c', 3)]
    result = WeightedRandom(max_answers=10).select(records, CONTEXT)
    assert {r['content'] for r in result} == {'a', 'b', 'c'}


def test_select_multi_all_zero_weight_samples_evenly() -> None:
    # An all-zero set with max_answers > 1 fills via an equal sample, never random.randrange(0).
    records = [_record('a', 0), _record('b', 0), _record('c', 0)]
    result = WeightedRandom(max_answers=2).select(records, CONTEXT)
    assert len(result) == 2 and len({r['content'] for r in result}) == 2


def test_select_multi_mixed_zero_weight_fills_remainder() -> None:
    # One heavy record is drawn first; the remaining zero-weight records fill the rest by equal sample.
    records = [_record('heavy', 100), _record('z1', 0), _record('z2', 0)]
    result = WeightedRandom(max_answers=3).select(records, CONTEXT)
    assert {r['content'] for r in result} == {'heavy', 'z1', 'z2'}


def test_select_distribution_follows_weights() -> None:
    # A heavily weighted record dominates the sample; assert a coarse split, not an exact one.
    records = [_record('rare', 1), _record('common', 99)]
    counts = {'rare': 0, 'common': 0}
    for _ in range(2000):
        counts[WeightedRandom().select(records, CONTEXT)[0]['content']] += 1
    assert counts['common'] > counts['rare']
    assert counts['rare'] > 0  # the rare record is still reachable


@pytest.mark.parametrize('draw', [0, 3, 5])
def test_weighted_pick_returns_a_candidate(draw: int) -> None:
    records = [_record('a', 1), _record('b', 2), _record('c', 3)]
    assert _weighted_pick(records, draw) in records


def test_weighted_pick_draw_at_or_above_total_returns_last() -> None:
    # The caller guarantees draw < total; a draw past the end falls through to the defensive last candidate.
    records = [_record('a', 1), _record('b', 2)]  # content-sorted; total 3
    assert _weighted_pick(records, 3)['content'] == 'b'


def test_network_prefix_is_none() -> None:
    # weighted-random varies per query, not per client network, so it contributes no ECS scope.
    assert WeightedRandom().network_prefix(CONTEXT) is None
