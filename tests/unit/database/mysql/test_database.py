# pylint: disable=missing-function-docstring

"""Tests for MySQLDatabase.

The SQL flattener, the context-manager protocol, autocommit injection, and the _select (rows-as-dicts) / _modify
(affected rowcount) split over a shared _cursor helper. MySQLDatabase subclasses the live mysql.connector
connection, so instances are built with __new__ (skipping the connecting __init__) and the cursor is faked.
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


def _db_with_cursor(cursor: _FakeCursor) -> MySQLDatabase:
    database = MySQLDatabase.__new__(MySQLDatabase)
    database.cursor = lambda **_kwargs: cursor  # type: ignore[method-assign, assignment]
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


def test_exit_disconnects() -> None:
    database = MySQLDatabase.__new__(MySQLDatabase)
    calls = []
    database.disconnect = lambda: calls.append(True)  # type: ignore[method-assign, misc]
    database.__exit__(None, None, None)
    assert calls == [True]


def test_init_forces_autocommit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_super_init(self: Any, **kwargs: Any) -> None:  # pylint: disable=unused-argument
        captured.update(kwargs)

    monkeypatch.setattr(mysql.connector.MySQLConnection, '__init__', fake_super_init)
    MySQLDatabase(host='127.0.0.1', user='u')
    assert captured == {'host': '127.0.0.1', 'user': 'u', 'autocommit': True}


def test_select_returns_list_of_dicts() -> None:
    cursor = _FakeCursor(description=[('a',), ('b',)], rows=[(1, 2), (3, 4)], rowcount=2)
    database = _db_with_cursor(cursor)
    result = database._select('SELECT a, b FROM t')  # pylint: disable=protected-access
    assert result == [{'a': 1, 'b': 2}, {'a': 3, 'b': 4}]
    assert cursor.closed is True


def test_select_shapes_rows_by_description_regardless_of_keyword() -> None:
    # A row-returning statement that does not start with 'SELECT' (a CTE, SHOW, lowercase) still yields row dicts:
    # _select reads the columns from cursor.description, not from the SQL's leading keyword.
    cursor = _FakeCursor(description=[('a',), ('b',)], rows=[(1, 2)], rowcount=1)
    database = _db_with_cursor(cursor)
    result = database._select('WITH t AS (SELECT 1) SELECT a, b FROM t')  # pylint: disable=protected-access
    assert result == [{'a': 1, 'b': 2}]


def test_modify_returns_rowcount() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=7)
    database = _db_with_cursor(cursor)
    result = database._modify('UPDATE t SET x = %s', (1,))  # pylint: disable=protected-access
    assert result == 7
    assert cursor.executed == ('UPDATE t SET x = %s', (1,))
    assert cursor.closed is True


def test_modify_flattens_operation_before_running() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0)
    database = _db_with_cursor(cursor)
    database._modify('DELETE FROM t\n  WHERE id = %s', (1,))  # pylint: disable=protected-access
    assert cursor.executed is not None
    assert cursor.executed[0] == 'DELETE FROM t WHERE id = %s'


def test_select_closes_cursor_even_on_error() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0, raise_on_execute=RuntimeError('boom'))
    database = _db_with_cursor(cursor)
    with pytest.raises(RuntimeError):
        database._select('SELECT 1')  # pylint: disable=protected-access
    assert cursor.closed is True


class _TxDatabase(MySQLDatabase):
    """MySQLDatabase whose autocommit is a plain attribute (shadowing the inherited property), recording the
    commit/rollback/autocommit-toggle sequence so the transaction control flow can be asserted offline."""
    autocommit = True  # shadows mysql.connector's autocommit property with a settable plain attribute

    def __init__(self, cursor: _FakeCursor) -> None:  # pylint: disable=super-init-not-called
        self.events: list[str] = []
        self.cursor = lambda **_kwargs: cursor  # type: ignore[method-assign, assignment]

    def __setattr__(self, name: str, value: Any) -> None:
        if name == 'autocommit':
            self.events.append(f'autocommit={value}')
        object.__setattr__(self, name, value)

    def commit(self) -> None:
        self.events.append('commit')

    def rollback(self) -> None:
        self.events.append('rollback')


def test_execute_transaction_commits_and_sums_rowcounts() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=3)
    database = _TxDatabase(cursor)
    total = database._execute_transaction(  # pylint: disable=protected-access
        [('INSERT INTO t VALUES (%s)', (1,)), ('UPDATE t SET x = %s', (2,))])
    assert total == 6  # 3 + 3
    assert database.events == ['autocommit=False', 'commit', 'autocommit=True']


def test_execute_transaction_rolls_back_and_reraises_on_error() -> None:
    cursor = _FakeCursor(description=None, rows=[], rowcount=0, raise_on_execute=RuntimeError('boom'))
    database = _TxDatabase(cursor)
    with pytest.raises(RuntimeError):
        database._execute_transaction(  # pylint: disable=protected-access
            [('INSERT INTO t VALUES (%s)', (1,))])
    # rolled back, never committed, and autocommit restored even on the error path
    assert database.events == ['autocommit=False', 'rollback', 'autocommit=True']
