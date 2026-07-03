"""Parser for PHP-style nested query strings (a[b][0]=v), as sent by the w2ui frontend."""

from typing import Any
from urllib.parse import parse_qsl

__all__ = ['QueryParserError', 'parse_query']


class QueryParserError(Exception):
    """Raised when the query string is malformed."""


def _get_key(s: str) -> str | None:
    """Return the text between the first [ and ], stripping surrounding quotes; None when brackets are missing.

    :param s: The key text to scan.
    :returns: The bracketed key without quotes, or None when there is no complete bracket pair.
    """
    start = s.find('[')
    end = s.find(']')
    if start == -1 or end == -1:
        return None
    if s[start + 1] == "'":
        start += 1
    if s[end - 1] == "'":
        end -= 1
    return s[start + 1:end]  # without brackets


def _has_variable_name(s: str) -> bool:
    """Return True when a variable name precedes the first [.

    :param s: The key text to scan.
    :returns: True when the key starts with a variable name.
    """
    return s.find('[') > 0


def _is_number(s: str) -> bool:
    """Return True when s is an optionally signed integer literal (a list index).

    :param s: The text to test.
    :returns: True when the text is an integer literal.
    """
    if len(s) > 0 and s[0] in ('-', '+'):
        return s[1:].isdigit()
    return s.isdigit()


def _more_than_one_index(s: str, brackets: int = 2) -> bool:
    """Return True when s contains at least `brackets` complete [...] groups.

    :param s: The key text to scan.
    :param brackets: The number of complete bracket groups to require.
    :returns: True when the key has at least that many bracket groups.
    """
    start = 0
    brackets_num = 0
    while start != -1 and brackets_num < brackets:
        start = s.find('[', start)
        if start == -1:
            break
        start = s.find(']', start)
        brackets_num += 1
    if start != -1:
        return True
    return False


def _normalize(d: Any) -> Any:
    """Convert int-keyed dicts produced by parsing into lists, recursively.

    {'abc': {0: 'xyz', 1: 'pqr'}} becomes {'abc': ['xyz', 'pqr']}; index gaps are not filled, so
    {10: 'xyz', 12: 'pqr'} still yields a two-element list. An '' key unwraps to its single value.

    :param d: The parsed value to normalize; a non-dict passes through unchanged.
    :returns: The value with every int-keyed dict converted to a list.
    """
    newd = {}
    if not isinstance(d, dict):
        return d
    for k, v in d.items():
        if isinstance(v, dict):
            first_key = next(iter(v.keys()))
            if isinstance(first_key, int):
                temp_new = []
                for _, v1 in v.items():
                    temp_new.append(_normalize(v1))
                newd[k] = temp_new
            elif first_key == '':
                newd[k] = list(v.values())[0]
            else:
                newd[k] = _normalize(v)
        else:
            newd[k] = v
    return newd


def _parser_helper(key: str, val: str) -> dict[Any, Any]:
    """Parse one key=val pair into a nested dict, one level per bracket group.

    :param key: The form key, possibly with bracket groups.
    :param val: The form value.
    :returns: The single-branch nested dict for the pair.
    :raises QueryParserError: When a bracketed key is malformed.
    """
    start_bracket = key.find('[')
    end_bracket = key.find(']')
    pdict: dict[Any, Any] = {}
    if _has_variable_name(key):  # var['key'][3]
        pdict[key[:start_bracket]] = _parser_helper(key[start_bracket:], val)
    elif _more_than_one_index(key):  # ['key'][3]
        newkey: Any = _get_key(key)
        newkey = int(newkey) if _is_number(newkey) else newkey
        pdict[newkey] = _parser_helper(key[end_bracket + 1:], val)
    else:  # key = val or ['key']
        newkey = key
        if start_bracket != -1:  # ['key']
            newkey = _get_key(key)
            if newkey is None:
                raise QueryParserError
        newkey = int(newkey) if _is_number(newkey) else newkey  # type: ignore[arg-type]
        if key == '[]':  # val is the array key
            val = int(val) if _is_number(val) else val  # type: ignore[assignment]
        pdict[newkey] = val
    return pdict


def parse_query(query_string: str) -> dict[str, Any]:
    """Parse a w2ui query string into a nested dict.

    Bracketed keys nest (a[b][0]=v), repeated keys collect into lists, and int-keyed groups normalize
    to lists, following the application/x-www-form-urlencoded algorithm.

    :param query_string: Percent-encoded query string or POST body.
    :returns: The parsed, normalized query.
    :raises QueryParserError: When a bracketed key is malformed.
    """
    query_dict: dict[str, Any] = {}
    if not query_string:
        return query_dict
    piter = (_parser_helper(key, val) for key, val in parse_qsl(query_string))
    for di in piter:
        k, v = di.popitem()
        tempdict = query_dict
        while k in tempdict and isinstance(v, dict):
            tempdict = tempdict[k]
            k, v = v.popitem()
        if k in tempdict and isinstance(tempdict[k], list):
            tempdict[k].append(v)
        elif k in tempdict:
            tempdict[k] = [tempdict[k], v]
        else:
            tempdict[k] = v
    return _normalize(query_dict)
