# pylint: disable=missing-function-docstring, redefined-outer-name

"""Tests for the W2UIMixIn router.

Token resolution against the TABLES registry (an unregistered token raises ValueError before any
SQL is built) and one delegation smoke test per public method; the table behavior itself is
covered in test_tables.py. A fake mixin subclass records every (operation, params) call.
"""

from typing import Any

import pytest

from powergslb.database.mysql.w2ui import W2UIMixIn
from powergslb.system.password import hash_password


class _FakeW2UIDatabase(W2UIMixIn):
    """Record calls; select returns select_result, modify returns affected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.select_result: list[dict[str, Any]] = []
        self.affected = 1

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append((' '.join(operation.split()), params))
        return self.select_result

    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        self.calls.append((' '.join(operation.split()), params))
        return self.affected

    def execute_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> int:
        return sum(self.modify(operation, params) for operation, params in statements)


@pytest.fixture
def database() -> _FakeW2UIDatabase:
    return _FakeW2UIDatabase()


# an unregistered token is rejected before any SQL is built

def test_delete_unknown_token_raises(database: _FakeW2UIDatabase) -> None:
    with pytest.raises(ValueError, match="'bogus' not implemented"):
        database.delete_data('bogus', [1])
    assert database.calls == []


def test_get_unknown_token_raises(database: _FakeW2UIDatabase) -> None:
    with pytest.raises(ValueError, match="'bogus' not implemented"):
        database.get_data('bogus')


def test_save_unknown_token_raises(database: _FakeW2UIDatabase) -> None:
    with pytest.raises(ValueError, match="'bogus' not implemented"):
        database.save_data('bogus', 0)


def test_status_token_rejects_writes(database: _FakeW2UIDatabase) -> None:
    # the status grid is registered read-only; its save/remove raise so the inherited records writes stay unreachable
    with pytest.raises(ValueError, match='read-only'):
        database.delete_data('status', [1])
    with pytest.raises(ValueError, match='read-only'):
        database.save_data('status', 0)


# delegation: each public method drives its table with the mixin as executor

def test_check_user_delegates_to_users(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'password': hash_password('secret')}]
    assert database.check_user('admin', 'secret') == [{'valid': 1}]
    assert database.calls[-1][1] == ('admin',)


def test_get_data_delegates_to_the_table(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'recid': 1}]
    assert database.get_data('domains') == ([{'recid': 1}], 1)
    assert 'FROM `domains`' in database.calls[-1][0]


def test_get_data_status_reads_the_down_ids(database: _FakeW2UIDatabase) -> None:
    database.get_data('status', down_ids=[7])
    sql, params = database.calls[-1]
    assert "THEN 'Off' ELSE 'On' END AS `status`" in sql
    assert params == (7,)


def test_save_data_delegates_to_the_table(database: _FakeW2UIDatabase) -> None:
    assert database.save_data('domains', 0, domain='example.com', description='') == 1
    sql, params = database.calls[-1]
    assert sql.startswith('INSERT INTO `domains`')
    assert params == ('example.com', '')


def test_delete_data_delegates_to_the_table(database: _FakeW2UIDatabase) -> None:
    database.affected = 2
    assert database.delete_data('domains', [1, 2]) == 2
    sql, params = database.calls[-1]
    assert sql.startswith('DELETE FROM `domains`')
    assert params == (1, 2)
