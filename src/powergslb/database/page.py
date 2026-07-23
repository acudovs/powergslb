"""Search, sort and paging carrier for the SQL-side w2ui read pipeline."""

from dataclasses import dataclass
from typing import Any

__all__ = ['PageRequest', 'SearchClause', 'SortClause']

# A combo search whose whole text is one of these is a "match all".
_WILDCARDS = frozenset('*+.?^$')


def _text(value: Any) -> str:
    """Narrow a raw query value to a string.

    :param value: The raw identifier from the parsed query.
    :returns: The value when it is a string, the empty string otherwise.
    """
    return value if isinstance(value, str) else ''


@dataclass(frozen=True, kw_only=True)
class SearchClause:
    """One w2ui search clause, its identifiers narrowed to strings.

    An identifier the query did not post as a string is empty, which no whitelist accepts.

    :param field: The exposed field name to search.
    :param type: The clause type (text, int, date).
    :param operator: The comparison operator, valid for the type.
    :param value: The raw value, interpreted by the type's clause builder.
    """
    field: str = ''
    type: str = ''
    operator: str = ''
    value: Any = None

    @classmethod
    def from_clause(cls, clause: dict[str, Any]) -> 'SearchClause':
        """Build a clause from one raw w2ui search dict.

        :param clause: The raw search clause from the parsed query.
        :returns: The narrowed clause.
        """
        return cls(field=_text(clause.get('field')), type=_text(clause.get('type')),
                   operator=_text(clause.get('operator')), value=clause.get('value'))


@dataclass(frozen=True, kw_only=True)
class SortClause:
    """One w2ui sort clause, its identifiers narrowed to strings.

    :param field: The exposed field name to sort by.
    :param direction: The sort direction; desc sorts descending, anything else ascending.
    """
    field: str = ''
    direction: str = ''

    @classmethod
    def from_clause(cls, clause: dict[str, Any]) -> 'SortClause':
        """Build a clause from one raw w2ui sort dict.

        :param clause: The raw sort clause from the parsed query.
        :returns: The narrowed clause.
        """
        return cls(field=_text(clause.get('field')), direction=_text(clause.get('direction')))


def _clauses(value: Any) -> tuple[dict[str, Any], ...]:
    """Keep only the dict clauses of a list value.

    :param value: The raw search or sort value from the parsed query.
    :returns: The dict clauses, or an empty tuple when the value is not a list.
    """
    if not isinstance(value, list):
        return ()
    return tuple(clause for clause in value if isinstance(clause, dict))


def _searches(query: dict[str, Any]) -> tuple[SearchClause, ...]:
    """Build search clauses from the query.

    A grid posts a list of clause dicts; a combo posts a flat typed string. A combo string that is a single
    wildcard character is a "match all" shortcut and yields no clause, so the page lists unfiltered.

    :param query: The parsed w2ui query.
    :returns: The search clauses, or an empty tuple when there is nothing to search.
    """
    search = query.get('search')
    if not search:
        return ()

    if isinstance(search, str):
        if search in _WILDCARDS:
            return ()
        return (SearchClause(field=_text(query.get('field')), type='text', operator='contains', value=search),)

    return tuple(SearchClause.from_clause(clause) for clause in _clauses(search))


@dataclass(frozen=True, kw_only=True)
class PageRequest:
    """The search, sort and paging parameters of one w2ui read request.

    :param searches: The w2ui search clauses.
    :param or_logic: Whether the searches combine with OR instead of AND.
    :param sorts: The w2ui sort clauses.
    :param limit: The page size, or None for an unbounded read.
    :param offset: The page start, or None when only a cap (max) was requested.
    """
    searches: tuple[SearchClause, ...] = ()
    or_logic: bool = False
    sorts: tuple[SortClause, ...] = ()
    limit: int | None = None
    offset: int | None = None

    @classmethod
    def from_query(cls, query: dict[str, Any]) -> 'PageRequest':
        """Translate a parsed w2ui query into a PageRequest.

        limit+offset (grid paging) wins over max (get-items cap), which maps to limit only.

        :param query: The parsed w2ui query.
        :returns: The translated request.
        :raises ValueError: When a paging value does not parse as an int.
        """
        limit: int | None = None
        offset: int | None = None
        try:
            if 'limit' in query and 'offset' in query:
                limit = int(query['limit'])
                offset = int(query['offset'])
            elif 'max' in query:
                limit = int(query['max'])
        except (TypeError, ValueError) as e:
            raise ValueError('invalid paging value') from e

        return cls(searches=_searches(query),
                   or_logic=query.get('searchLogic') == 'OR',
                   sorts=tuple(SortClause.from_clause(clause) for clause in _clauses(query.get('sort'))),
                   limit=limit,
                   offset=offset)
