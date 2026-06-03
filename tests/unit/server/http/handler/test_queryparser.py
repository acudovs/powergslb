# pylint: disable=missing-function-docstring, protected-access

"""Tests for the w2ui query-string parser (querystring-parser fork).

Covers the public parse_query for flat, nested, indexed and array forms, plus the private helpers for the
bracket/number/normalisation edge branches.
"""

import pytest

from powergslb.server.http.handler import queryparser
from powergslb.server.http.handler.queryparser import QueryParserError, parse_query


# parse_query: top-level behaviour

def test_empty_string_returns_empty_dict() -> None:
    assert not parse_query('')


def test_flat_pairs() -> None:
    assert parse_query('cmd=get-records&data=records') == {'cmd': 'get-records', 'data': 'records'}


def test_nested_object_indexed_into_list() -> None:
    # sort[0][field]=... -> {'sort': [{'field': ..., 'direction': ...}]}
    result = parse_query('sort[0][field]=domain&sort[0][direction]=asc')
    assert result == {'sort': [{'field': 'domain', 'direction': 'asc'}]}


def test_empty_bracket_array_collects_values_as_list() -> None:
    # search[]=1&search[]=2 -> the '' first-key normalises to a plain list, values coerced to int
    assert parse_query('search[]=1&search[]=2') == {'search': [1, 2]}


def test_indexed_without_variable_name_normalises_to_list() -> None:
    assert parse_query('[0][1]=v') == {0: ['v']}


def test_deeply_nested_string_keys() -> None:
    assert parse_query('a[b][c]=v') == {'a': {'b': {'c': 'v'}}}


def test_quoted_key_is_unquoted() -> None:
    assert parse_query("a['b']=v") == {'a': {'b': 'v'}}


def test_repeated_scalar_key_becomes_list() -> None:
    assert parse_query('x=1&x=2') == {'x': ['1', '2']}


def test_third_repeated_value_appends_to_existing_list() -> None:
    assert parse_query('x=1&x=2&x=3') == {'x': ['1', '2', '3']}


def test_unclosed_bracket_raises_queryparsererror() -> None:
    with pytest.raises(QueryParserError):
        parse_query('[abc=v')


# _get_key

def test_get_key_no_brackets_returns_none() -> None:
    assert queryparser._get_key('plain') is None


def test_get_key_strips_surrounding_quotes() -> None:
    assert queryparser._get_key("['key']") == 'key'


def test_get_key_without_quotes() -> None:
    assert queryparser._get_key('[key]') == 'key'


# _has_variable_name

@pytest.mark.parametrize('text, expected', [('var[0]', True), ('[0]', False), ('plain', False)])
def test_has_variable_name(text: str, expected: bool) -> None:
    assert queryparser._has_variable_name(text) is expected


# _is_number

@pytest.mark.parametrize('text, expected', [
    ('5', True), ('-5', True), ('+5', True), ('0', True), ('', False), ('-', False), ('abc', False), ('1.5', False),
])
def test_is_number(text: str, expected: bool) -> None:
    assert queryparser._is_number(text) is expected


# _more_than_one_index

@pytest.mark.parametrize('text, expected', [('[a][b]', True), ('[a]', False), ('plain', False)])
def test_more_than_one_index(text: str, expected: bool) -> None:
    assert queryparser._more_than_one_index(text) is expected


# _normalize

def test_normalize_passthrough_non_dict() -> None:
    assert queryparser._normalize('scalar') == 'scalar'


def test_normalize_int_keyed_dict_to_list() -> None:
    assert queryparser._normalize({'k': {0: 'a', 1: 'b'}}) == {'k': ['a', 'b']}


def test_normalize_empty_first_key_takes_first_value() -> None:
    assert queryparser._normalize({'k': {'': [1, 2]}}) == {'k': [1, 2]}


def test_normalize_recurses_string_keyed_dict() -> None:
    assert queryparser._normalize({'k': {'inner': 'v'}}) == {'k': {'inner': 'v'}}


def test_normalize_scalar_value_kept() -> None:
    assert queryparser._normalize({'k': 'v'}) == {'k': 'v'}
