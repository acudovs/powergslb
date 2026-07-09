# pylint: disable=missing-function-docstring

"""Tests for PageRequest.from_query.

The w2ui query to SQL-paging translation: limit+offset vs max precedence, ValueError on non-int paging
values, the list-of-dicts-only shape gate on sort, and the dual-shape search gate - a grid posts a list
of clause dicts, while a w2ui combo posts a flat search=<typed text> string on get-items that becomes a
single contains clause on its field.
"""

from typing import Any

import pytest

from powergslb.database import PageRequest


def test_absent_keys_yield_unpaged_request() -> None:
    page = PageRequest.from_query({})
    assert page == PageRequest()
    assert not page.searches and not page.sorts
    assert not page.or_logic
    assert page.limit is None and page.offset is None


def test_limit_and_offset() -> None:
    page = PageRequest.from_query({'limit': '25', 'offset': '50'})
    assert (page.limit, page.offset) == (25, 50)


def test_max_maps_to_limit_only() -> None:
    page = PageRequest.from_query({'max': '250'})
    assert page.limit == 250
    assert page.offset is None


def test_limit_offset_wins_over_max() -> None:
    page = PageRequest.from_query({'limit': '10', 'offset': '0', 'max': '250'})
    assert (page.limit, page.offset) == (10, 0)


@pytest.mark.parametrize('query', [
    {'limit': 'x', 'offset': '0'},
    {'limit': '1', 'offset': 'x'},
    {'max': 'x'},
    {'limit': ['1', '2'], 'offset': '0'},  # a repeated key parses to a list; TypeError normalizes to ValueError
    {'max': ['1', '2']},
])
def test_non_int_paging_value_raises(query: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        PageRequest.from_query(query)


def test_search_and_sort_lists_pass_through() -> None:
    search = [{'field': 'domain', 'type': 'text', 'operator': 'is', 'value': 'x'}]
    sort = [{'field': 'domain', 'direction': 'asc'}]
    page = PageRequest.from_query({'search': search, 'sort': sort, 'searchLogic': 'OR'})
    assert page.searches == tuple(search)
    assert page.sorts == tuple(sort)
    assert page.or_logic


def test_search_logic_defaults_to_and() -> None:
    assert not PageRequest.from_query({'searchLogic': 'AND'}).or_logic
    assert not PageRequest.from_query({}).or_logic


def test_flat_search_string_becomes_contains_clause() -> None:
    # the w2ui combo posts get-items as a flat search=<typed text> string; it becomes a contains clause on its field
    page = PageRequest.from_query({'search': 'typed', 'field': 'domain', 'max': '250'})
    assert page.searches == ({'field': 'domain', 'type': 'text', 'operator': 'contains', 'value': 'typed'},)
    assert page.limit == 250


def test_empty_flat_search_string_is_dropped() -> None:
    # an untyped combo posts an empty search string; it lists its capped page unfiltered
    page = PageRequest.from_query({'search': '', 'field': 'domain', 'max': '250'})
    assert not page.searches
    assert page.limit == 250


def test_non_list_sort_is_dropped() -> None:
    assert not PageRequest.from_query({'sort': {'field': 'domain'}}).sorts


def test_non_dict_entries_are_dropped() -> None:
    page = PageRequest.from_query({'search': ['stray', {'field': 'domain'}], 'sort': [1, 2]})
    assert page.searches == ({'field': 'domain'},)
    assert not page.sorts
