"""Search, sort and paging carrier for the SQL-side w2ui read pipeline."""

from dataclasses import dataclass
from typing import Any

__all__ = ['PageRequest']


def _clauses(value: Any) -> tuple[dict[str, Any], ...]:
    """Keep only the dict clauses of a list value; any other shape yields no clauses.

    :param value: The raw search or sort value from the parsed query.
    :returns: The dict clauses, or an empty tuple when the value is not a list.
    """
    if not isinstance(value, list):
        return ()
    return tuple(clause for clause in value if isinstance(clause, dict))


def _searches(query: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Build search clauses from the query.

    A grid posts a list of clause dicts; a combo posts a flat typed string.

    :param query: The parsed w2ui query.
    :returns: The search clauses, or an empty tuple when there is nothing to search.
    """
    search = query.get('search')
    if not search:
        return ()
    if isinstance(search, str):
        return ({'field': query.get('field'), 'type': 'text', 'operator': 'contains', 'value': search},)
    return _clauses(search)


@dataclass(frozen=True, kw_only=True)
class PageRequest:
    """The search, sort and paging parameters of one w2ui read request.

    :param searches: The w2ui search clauses (field, type, operator, value).
    :param or_logic: Whether the searches combine with OR instead of AND.
    :param sorts: The w2ui sort clauses (field, direction).
    :param limit: The page size, or None for an unbounded read.
    :param offset: The page start, or None when only a cap (max) was requested.
    """
    searches: tuple[dict[str, Any], ...] = ()
    or_logic: bool = False
    sorts: tuple[dict[str, Any], ...] = ()
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
                   sorts=_clauses(query.get('sort')),
                   limit=limit,
                   offset=offset)
