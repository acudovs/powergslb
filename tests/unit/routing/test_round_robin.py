# pylint: disable=missing-function-docstring

"""Tests for the round-robin routing policy.

The highest live weight tier wins; a tier of max_answers or fewer is returned whole, a larger tier is randomly
subsampled to max_answers (a per-query draw bounded by the cap).
"""

from typing import Any

import netaddr

from powergslb.client import ClientContext
from powergslb.routing.round_robin import RoundRobin

CONTEXT = ClientContext(netaddr.IPAddress('192.0.2.7'))


def _record(content: str, weight: int) -> dict[str, Any]:
    return {'id': 0, 'content': content, 'weight': weight, 'qname': 'example.com'}


def test_default_max_answers_is_eight() -> None:
    assert RoundRobin().max_answers == 8


def test_empty_input_returns_empty() -> None:
    assert not RoundRobin(max_answers=8).select([], CONTEXT)


def test_picks_highest_weight_tier() -> None:
    records = [_record('low', 10), _record('high1', 20), _record('high2', 20)]
    result = RoundRobin(max_answers=8).select(records, CONTEXT)
    assert sorted(r['content'] for r in result) == ['high1', 'high2']


def test_returns_smaller_tier_whole() -> None:
    records = [_record('a', 5), _record('b', 5), _record('c', 5)]
    result = RoundRobin(max_answers=8).select(records, CONTEXT)
    assert sorted(r['content'] for r in result) == ['a', 'b', 'c']


def test_caps_oversized_tier_to_max_answers() -> None:
    records = [_record(f'a{i}', 0) for i in range(20)]
    result = RoundRobin(max_answers=8).select(records, CONTEXT)
    assert len(result) == 8
    # the subsample is drawn from the tier and carries no duplicates
    contents = [r['content'] for r in result]
    assert len(set(contents)) == 8
    assert set(contents) <= {r['content'] for r in records}


def test_cap_of_one_returns_single_record() -> None:
    records = [_record(f'a{i}', 7) for i in range(5)]
    assert len(RoundRobin(max_answers=1).select(records, CONTEXT)) == 1
