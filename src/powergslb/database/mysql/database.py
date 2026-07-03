"""MySQL/MariaDB connection and statement execution."""

import contextlib
import logging
from collections.abc import Iterator
from typing import Any, Self

import mysql.connector

from powergslb.database.mysql.powerdns import PowerDNSDatabaseMixIn
from powergslb.database.mysql.w2ui import W2UIDatabaseMixIn

__all__ = ['MySQLDatabase']


class MySQLDatabase(PowerDNSDatabaseMixIn, W2UIDatabaseMixIn, mysql.connector.MySQLConnection):
    """MySQL/MariaDB connection with the PowerDNS and w2ui query mixins; usable as a context manager.

    Runs with autocommit on (not user-configurable), so every single-statement write persists on its own;
    only _execute_transaction suspends autocommit to group statements and commit() them as a unit.

    :param kwargs: mysql.connector connect arguments (database, user, password, host, port, unix_socket, ...).
    """
    Error = mysql.connector.Error

    def __init__(self, **kwargs: Any) -> None:
        kwargs['autocommit'] = True
        super().__init__(**kwargs)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: Any) -> None:
        self.disconnect()

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

        cursor = self.cursor(buffered=True)
        try:
            cursor.execute(operation, params)
            yield cursor
        finally:
            cursor.close()

    def _select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a result-set statement (SELECT, WITH...SELECT, SHOW, ...) and return its rows as dicts.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The result rows, each keyed by column name.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows returned', cursor.rowcount)
            column_names = [column[0] for column in cursor.description]
            return [dict(zip(column_names, row)) for row in cursor]

    def _modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        """Execute a write statement (INSERT, UPDATE, DELETE, ...) and return the affected row count.

        :param operation: The SQL statement to execute.
        :param params: Statement placeholder values.
        :returns: The number of rows the statement affected.
        """
        with self._cursor(operation, params) as cursor:
            logging.debug('%s rows affected', cursor.rowcount)
            return cursor.rowcount

    def _execute_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> int:
        """Run statements in one transaction on this connection; return the summed affected-row count.

        autocommit stays on for every single-statement path; this executor suspends it for its own duration and
        restores it in finally, so an exception cannot return a connection to the pool mid-transaction. All
        statements run on this connection, so LAST_INSERT_ID() carries across them.

        :param statements: The (operation, params) pairs to run in order.
        :returns: The total number of rows affected across all statements.
        """
        self.autocommit = False
        total = 0
        try:
            for operation, params in statements:
                total += self._modify(operation, params)
            self.commit()
        except Exception:
            self.rollback()
            raise
        finally:
            self.autocommit = True

        return total
