# pylint: disable=missing-function-docstring, protected-access

"""Tests for MySQLDatabase.

The SQL flattener, the context-manager protocol, autocommit injection, and the select (rows-as-dicts) / modify
(affected rowcount) split over a shared _cursor helper. MySQLDatabase holds a mysql.connector connection by
composition, so instances are built with __new__ (skipping the connecting __init__) and a fake connection whose
cursor() yields a fake cursor is attached.
"""

from typing import Any

import mysql.connector
import pytest

from powergslb.database.mysql.database import MySQLDatabase


class _FakeCursor:
    """Minimal stand-in for a buffered mysql.connector cursor."""

    def __init__(self, description: Any, rows: list[tuple[Any, ...]], rowcount: int,
                 raise_on_execute: Exception | None = None) -> None:
        self.description = description
        self._rows = rows
        self.rowcount = rowcount
        self._raise = raise_on_execute
        self.executed: tuple[str, tuple[Any, ...]] | None = None
        self.closed = False

    def execute(self, operation: str, params: tuple[Any, ...]) -> None:
        self.executed = (operation, params)
        if self._raise is not None:
            raise self._raise

    def __iter__(self) -> Any:
        return iter(self._rows)

    def close(self) -> None:
        self.closed = True


class _FakeConnection:
    """Minimal stand-in for a mysql.connector connection: yields a fixed cursor and records control calls."""

    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.autocommit = True
        self.events: list[str] = []

    def cursor(self, **_kwargs: Any) -> _FakeCursor:
        return self._cursor

    def __setattr__(self, name: str, value: Any) -> None:
        if name == 'autocommit' and hasattr(self, 'events'):
            self.events.append(f'autocommit={value}')
        object.__setattr__(self, name, value)

    def commit(self) -> None:
        self.events.append('commit')

    def rollback(self) -> None:
        self.events.append('rollback')

    def close(self) -> None:
        self.events.append('close')


def _db_with_cursor(cursor: _FakeCursor) -> MySQLDatabase:
    database = MySQLDatabase.__new__(MySQLDatabase)
    database._connection = _FakeConnection(cursor)  # type: ignore[assignment]
    return database


def test_error_alias_is_mysql_connector_error() -> None:
    assert MySQLDatabase.Error is mysql.connector.Error


def test_join_operation_collapses_whitespace() -> None:
    operation = """
                SELECT 1
                FROM t \
                """
    assert MySQLDatabase.join_operation(operation) == 'SELECT 1 FROM t'


def test_enter_returns_self() -> None:
    database = MySQLDatabase.__new__(MySQLDatabase)
    assert database.__enter__() is database  # pylint: disable=unnecessary-dunder-call


def test_exit_closes_connection() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor)
    database.__exit__(None, None, None)
    assert database._connection.events == ['close']  # type: ignore[attr-defined]


def test_init_connects_with_autocommit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_connect(**kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    # The C-vs-pure choice is delegated to the factory; __init__ only injects autocommit and holds the result.
    monkeypatch.setattr(mysql.connector, 'connect', fake_connect)
    MySQLDatabase(host='127.0.0.1', user='u')
    assert captured == {'host': '127.0.0.1', 'user': 'u', 'autocommit': True}


def test_select_returns_list_of_dicts() -> None:
    cursor = _FakeCursor(description=[('a',), ('b',)], rows=[(1, 2), (3, 4)], rowcount=2)
    database = _db_with_cursor(cursor)
    result = database.select('SELECT a, b FROM t')
    assert result == [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]
    assert cursor.closed is True


def test_select_shapes_rows_by_description_regardless_of_keyword() -> None:
    # A row-returning statement that does not start with 'SELECT' (a CTE, SHOW, lowercase) still yields row dicts:
    # select reads the columns from cursor.description, not from the SQL's leading keyword.
    cursor = _FakeCursor(description=[('a',), ('b',)], rows=[(1, 2)], rowcount=1)
    database = _db_with_cursor(cursor)
    result = database.select('WITH t AS (SELECT 1) SELECT a, b FROM t')
    assert result == [{'a': 1, 'b': 2}]


def test_modify_returns_rowcount() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=7)
    database = _db_with_cursor(cursor)
    result = database.modify('UPDATE t SET x = %s', (1,))
    assert result == 7
    assert cursor.executed == ('UPDATE t SET x = %s', (1,))
    assert cursor.closed is True


def test_modify_flattens_operation_before_running() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor)
    database.modify('DELETE FROM t\n  WHERE id = %s', (1,))
    assert cursor.executed is not None
    assert cursor.executed[0] == 'DELETE FROM t WHERE id = %s'


def test_select_closes_cursor_even_on_error() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0, raise_on_execute=RuntimeError('boom'))
    database = _db_with_cursor(cursor)
    with pytest.raises(RuntimeError):
        database.select('SELECT 1')
    assert cursor.closed is True


def test_execute_transaction_commits_and_sums_rowcounts() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=3)
    database = _db_with_cursor(cursor)
    total = database.execute_transaction([('INSERT INTO t VALUES (%s)', (1,)), ('UPDATE t SET x = %s', (2,))])
    assert total == 6  # 3 + 3
    # autocommit is suspended for the transaction, the statements commit as a unit, then autocommit is restored.
    assert database._connection.events == [  # type: ignore[attr-defined]
        'autocommit=False', 'commit', 'autocommit=True']


def test_execute_transaction_rolls_back_and_reraises_on_error() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0, raise_on_execute=RuntimeError('boom'))
    database = _db_with_cursor(cursor)
    with pytest.raises(RuntimeError):
        database.execute_transaction([('INSERT INTO t VALUES (%s)', (1,))])
    # rolled back, never committed, and autocommit restored even on the error path
    assert database._connection.events == [  # type: ignore[attr-defined]
        'autocommit=False', 'rollback', 'autocommit=True']
