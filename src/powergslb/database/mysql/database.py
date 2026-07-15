"""MySQL/MariaDB connection and statement execution."""

import contextlib
import logging
from collections.abc import Iterator
from typing import Any, Self, cast

import mysql.connector
from mysql.connector.abstracts import MySQLConnectionAbstract

from powergslb.database.mysql.masked import Masked
from powergslb.database.mysql.powerdns import PowerDNSMixIn
from powergslb.database.mysql.w2ui import W2UIMixIn

__all__ = ['MySQLDatabase']


class MySQLDatabase(PowerDNSMixIn, W2UIMixIn):
    """MySQL/MariaDB query facade over a mysql.connector connection; usable as a context manager.

    Runs with autocommit on (not user-configurable), so every single-statement write persists on its own;
    only execute_transaction suspends autocommit to group statements and commit() them as a unit.

    :param kwargs: mysql.connector connect arguments (database, user, password, host, port, unix_socket, ...).
    """
    Error = mysql.connector.Error

    def __init__(self, **kwargs: Any) -> None:
        kwargs['autocommit'] = True
        # connect() returns MySQLConnection or CMySQLConnection (C-extension) when the connector ships it.
        self._connection = cast(MySQLConnectionAbstract, mysql.connector.connect(**kwargs))

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: Any) -> None:
        self._connection.close()

    @staticmethod
    def join_operation(operation: str) -> str:
        """Collapse a multiline SQL string into a single space-separated line.

        :param operation: The SQL statement text.
        :returns: The statement as one line with surrounding whitespace stripped.
        """
        return ' '.join(filter(None, (line.strip() for line in operation.splitlines())))

    @contextlib.contextmanager
    def _cursor(self, operation: str, params: tuple[Any, ...]) -> Iterator[Any]:
        """Run one SQL statement on a fresh buffered cursor and yield it, closing it on exit.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :yields: The buffered cursor with the statement executed.
        """
        operation = self.join_operation(operation)
        if params:
            logging.debug('"%s" %% %s', operation, params)
        else:
            logging.debug('"%s"', operation)

        cursor = self._connection.cursor(buffered=True)
        try:
            cursor.execute(operation, self._unwrap_params(params))
            yield cursor
        finally:
            cursor.close()

    @staticmethod
    def _unwrap_params(params: tuple[Any, ...]) -> tuple[Any, ...]:
        """Unwrap any Masked parameter to its real value before execution.

        :param params: The statement placeholder values, some possibly Masked.
        :returns: The parameters with every Masked replaced by its value.
        """
        return tuple(param.value if isinstance(param, Masked) else param for param in params)

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement (SELECT, WITH...SELECT, SHOW, ...) and return its rows as dicts.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The result rows, each keyed by column name.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows returned', cursor.rowcount)
            column_names = [column[0] for column in cursor.description]
            return [dict(zip(column_names, row)) for row in cursor]

    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write statement (INSERT, UPDATE, DELETE, ...) and return the affected row count.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The number of rows the statement affected.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows affected', cursor.rowcount)
            return cursor.rowcount

    def execute_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> int:
        """Run statements in one transaction on this connection; return the summed affected-row count.

        autocommit stays on for every single-statement path; this executor suspends it for its own duration and
        restores it in finally, so an exception cannot leave the connection mid-transaction. All statements run on
        the one connection, so LAST_INSERT_ID() carries across them.

        :param statements: The (operation, params) pairs to run in order.
        :returns: The total number of rows affected across all statements.
        """
        self._connection.autocommit = False
        total = 0
        try:
            for operation, params in statements:
                total += self.modify(operation, params)
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        finally:
            self._connection.autocommit = True

        return total
