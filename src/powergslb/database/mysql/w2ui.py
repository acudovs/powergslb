"""Admin CRUD path: routes each w2ui data token to its table."""

import abc
from typing import Any

from powergslb.database.mysql.tables import Table, TABLES, USERS
from powergslb.database.page import PageRequest

__all__ = ['W2UIMixIn']


class W2UIMixIn(abc.ABC):
    """w2ui related queries: token-dispatched CRUD for every table plus user authentication.

    Every public method resolves the w2ui data token against the TABLES registry and delegates to the table,
    passing itself as the executor.
    """

    @staticmethod
    def _table(data: str) -> Table:
        """Resolve a w2ui data token to its registered table.

        :param data: The table token from the query.
        :returns: The table instance.
        :raises ValueError: When the token names no registered table.
        """
        table = TABLES.get(data)
        if table is None:
            raise ValueError(f"'{data}' not implemented")
        return table

    @abc.abstractmethod
    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement and return its rows as dicts."""

    @abc.abstractmethod
    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write statement and return the affected row count."""

    @abc.abstractmethod
    def execute_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> int:
        """Run statements in one transaction and return the summed affected-row count."""

    def check_user(self, user: str, password: str) -> list[dict[str, Any]]:
        """Return [{'valid': 1}] if the user/password pair is valid, an empty list otherwise.

        :param user: The login name.
        :param password: The plaintext password to verify.
        :returns: [{'valid': 1}] on a valid pair, an empty list otherwise.
        """
        return USERS.check_user(self, user, password)

    def delete_data(self, data: str, ids: list[Any]) -> int:
        """Delete rows of a token's table by key and return the count of deleted rows.

        :param data: The table token from the query.
        :param ids: The key values to delete.
        :returns: The number of rows deleted.
        :raises ValueError: When the token names no registered table.
        """
        return self._table(data).remove(self, ids)

    def get_data(self, data: str, recid: int = 0, page: PageRequest | None = None,
                 **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        """Read a token's table, whole or by recid, searched, sorted and paged in SQL.

        :param data: The table token from the query.
        :param recid: The key value to fetch; 0 fetches every row.
        :param page: The search/sort/paging request; None returns every matching row.
        :param kwargs: Extra arguments the table may consume.
        :returns: The matching rows and the total match count.
        :raises ValueError: When the token names no registered table.
        """
        return self._table(data).get(self, recid, page, **kwargs)

    def save_data(self, data: str, save_recid: int, **fields: Any) -> int:
        """Insert or update one row of a token's table and return the affected-row count.

        :param data: The table token from the query.
        :param save_recid: The key value to update; 0 inserts a new row.
        :param fields: The posted record; only the table's writable columns are read.
        :returns: The number of rows affected.
        :raises ValueError: When the token names no registered table.
        """
        return self._table(data).save(self, save_recid, **fields)
