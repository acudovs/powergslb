# pylint: disable=missing-function-docstring, redefined-outer-name

"""Tests for the W2UIMixIn router.

Token resolution against the TABLES registry (an unregistered token raises ValueError before any
SQL is built), one delegation smoke test per public method, and the write dispatchers auditing every
write: the rows are built before the write (from the posted record, or from the delete's own read of the
rows it is about to remove) and inserted in the same transaction. The table behavior itself is covered in
test_tables.py. A fake mixin subclass records every (operation, params) call and an ordered event log of
the statements and transaction boundaries.
"""

import contextlib
import datetime
from collections.abc import Iterator
from typing import Any

import pytest

from powergslb.database.mysql.w2ui import W2UIMixIn
from powergslb.database.user import UserContext
from powergslb.system.password import hash_password


class _FakeW2UIDatabase(W2UIMixIn):
    """Record calls; select returns select_result, modify returns affected, events log the statement order.

    A save reads the row back, so select_after (when set) answers every read that follows the write while
    select_result answers the ones before it; insert_id is what an insert reports as its generated key.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.events: list[str] = []
        self.select_result: list[dict[str, Any]] = []
        self.select_after: list[dict[str, Any]] | None = None  # when set, the result of reads after a write
        self.insert_id = 42
        self.affected = 1
        self.raise_on_insert: str | None = None  # when set, modify raises on a statement starting with it

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        operation = ' '.join(operation.split())
        self.calls.append((operation, params))
        self.events.append('select')
        if self.select_after is not None and 'modify' in self.events:
            return self.select_after
        return self.select_result

    def last_insert_id(self) -> int:
        return self.insert_id

    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        operation = ' '.join(operation.split())
        self.calls.append((operation, params))
        self.events.append('modify')
        if self.raise_on_insert is not None and operation.startswith(self.raise_on_insert):
            raise RuntimeError('insert boom')
        return self.affected

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        self.events.append('begin')
        try:
            yield
        except Exception:
            self.events.append('rollback')
            raise
        self.events.append('commit')


_USER = UserContext(1, 'admin', 'Administrator', '203.0.113.1')


@pytest.fixture
def database() -> _FakeW2UIDatabase:
    return _FakeW2UIDatabase()


# an unregistered token is rejected before any SQL is built

def test_delete_unknown_token_raises(database: _FakeW2UIDatabase) -> None:
    with pytest.raises(ValueError, match="'bogus' not implemented"):
        database.delete_data('bogus', [1], _USER)
    assert database.calls == []


def test_get_unknown_token_raises(database: _FakeW2UIDatabase) -> None:
    with pytest.raises(ValueError, match="'bogus' not implemented"):
        database.get_data('bogus')


def test_save_unknown_token_raises(database: _FakeW2UIDatabase) -> None:
    with pytest.raises(ValueError, match="'bogus' not implemented"):
        database.save_data('bogus', 0, _USER)
    assert database.calls == []


def test_status_token_rejects_writes(database: _FakeW2UIDatabase) -> None:
    # the status grid is registered read-only; its save/remove raise so the inherited records writes stay unreachable
    with pytest.raises(ValueError, match='read-only'):
        database.delete_data('status', [1], _USER)
    with pytest.raises(ValueError, match='read-only'):
        database.save_data('status', 0, _USER)


# delegation: each public method drives its table with the mixin as executor

def test_check_user_delegates_to_users(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'id': 1, 'user': 'admin', 'name': 'Administrator', 'password': hash_password('secret')}]
    assert database.check_user('admin', 'secret') == [{'id': 1, 'user': 'admin', 'name': 'Administrator'}]
    assert database.calls[-1][1] == ('admin',)


def test_get_data_audit_routes_to_audit(database: _FakeW2UIDatabase) -> None:
    database.get_data('audit')
    assert 'FROM `audit`' in database.calls[-1][0]


def test_audit_token_rejects_writes(database: _FakeW2UIDatabase) -> None:
    # the audit grid is registered read-only; its save/remove raise like the status grid
    with pytest.raises(ValueError, match='read-only'):
        database.delete_data('audit', [1], _USER)
    with pytest.raises(ValueError, match='read-only'):
        database.save_data('audit', 0, _USER)


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
    database.select_after = [{'recid': 42, 'domain': 'example.com', 'description': ''}]
    assert database.save_data('domains', 0, _USER, domain='example.com', description='') == 1
    sql, params = database.calls[0]
    assert sql.startswith('INSERT INTO `domains`')
    assert params == ('example.com', '')


def test_delete_data_delegates_to_the_table(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'recid': 1}, {'recid': 2}]
    database.affected = 2
    assert database.delete_data('domains', [1, 2], _USER) == 2
    sql, params = database.calls[1]
    assert sql.startswith('DELETE FROM `domains`')
    assert params == (1, 2)


# the delete pre-read: the transaction's first statement, and it decides what the delete targets

def test_delete_data_reads_the_targeted_rows_inside_the_transaction(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'recid': 7, 'domain': 'gone.example.com'}]
    assert database.delete_data('domains', [7], _USER) == 1
    sql, params = database.calls[0]
    assert 'FROM `domains`' in sql and '`recid` IN (%s)' in sql
    assert params == (7,)
    # read, delete and audit share one snapshot: the trail can only describe the version that was deleted
    assert database.events == ['begin', 'select', 'modify', 'modify', 'commit']


def test_delete_data_deletes_only_the_resolved_ids(database: _FakeW2UIDatabase) -> None:
    # 99 no longer exists, so the pre-read resolves it away and it never reaches the delete
    database.select_result = [{'recid': 5, 'domain': 'gone.example.com'}]
    assert database.delete_data('domains', ['5', '99'], _USER) == 1
    assert database.calls[1][1] == (5,)


def test_delete_data_unparsable_id_deletes_nothing(database: _FakeW2UIDatabase) -> None:
    # an id the search cannot parse matches no row, so it never reaches the delete as a bind value the
    # database would coerce numerically into a different row
    assert database.delete_data('domains', ['5abc'], _USER) == 0
    sql, params = database.calls[0]
    assert '0 = 1' in sql and params == ()
    assert database.events == ['begin', 'select', 'commit']  # nothing resolved, so nothing was deleted or audited


# the CRUD write and its audit rows commit as one unit, carrying the stored state on both sides

def test_save_insert_audits_the_stored_row_under_its_new_id(database: _FakeW2UIDatabase) -> None:
    database.insert_id = 42
    database.select_after = [{'recid': 42, 'domain': 'example.com', 'description': ''}]
    assert database.save_data('domains', 0, _USER, domain='example.com', description='') == 1
    # the generated key comes from the insert itself, so only the read-back costs a statement
    assert [sql.split(' (')[0].split(' FROM')[0] for sql, _ in database.calls] == [
        'INSERT INTO `domains`', 'SELECT `id` AS `recid`, `domain`, `description`', 'INSERT INTO `audit`']
    # an insert has no before state, and the trail records the real id the write generated
    assert database.calls[-1][1] == ('admin', '203.0.113.1', 'save', 'domains', 42, None,
                                     '{"recid":42,"domain":"example.com","description":""}')
    assert database.events == ['begin', 'modify', 'select', 'modify', 'commit']


def test_save_insert_without_a_generated_key_rolls_back(database: _FakeW2UIDatabase) -> None:
    # no key means no way to identify the written row, so the write is rejected rather than audited under a guess
    database.insert_id = 0
    with pytest.raises(RuntimeError, match='generated no key'):
        database.save_data('domains', 0, _USER, domain='example.com', description='')
    assert database.events[-1] == 'rollback'
    assert not [sql for sql, _ in database.calls if 'audit' in sql]


def test_save_of_a_vanished_row_is_rejected(database: _FakeW2UIDatabase) -> None:
    # the write counted, but the read-back finds nothing (the row was deleted meanwhile):
    # rejected rather than audited as a both-null row that the CHECK would bounce as 'internal error'
    database.select_after = []
    with pytest.raises(ValueError, match='not found'):
        database.save_data('domains', 7, _USER, domain='x', description='')
    assert database.events[-1] == 'rollback'
    assert not [sql for sql, _ in database.calls if 'audit' in sql]


def test_save_update_audits_the_row_before_and_after(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'recid': 7, 'domain': 'old.example.com', 'description': ''}]
    database.select_after = [{'recid': 7, 'domain': 'new.example.com', 'description': ''}]
    assert database.save_data('domains', 7, _USER, domain='new.example.com', description='') == 1
    # read, write, read back, audit: no LAST_INSERT_ID lookup, the update already knows its key
    assert database.events == ['begin', 'select', 'modify', 'select', 'modify', 'commit']
    assert database.calls[-1][1] == ('admin', '203.0.113.1', 'save', 'domains', 7,
                                     '{"recid":7,"domain":"old.example.com","description":""}',
                                     '{"recid":7,"domain":"new.example.com","description":""}')


def test_save_audits_the_stored_row_not_the_posted_fields(database: _FakeW2UIDatabase) -> None:
    # the posted fields need be neither complete nor what lands in the table; the trail takes the read-back row
    database.select_after = [{'recid': 42, 'domain': 'example.com', 'description': ''}]
    database.save_data('domains', 0, _USER, domain='example.com')
    assert database.calls[-1][1][-1] == '{"recid":42,"domain":"example.com","description":""}'


def test_save_of_a_writable_key_audits_the_posted_key(database: _FakeW2UIDatabase) -> None:
    # types supplies its own key, so the after read and the trail follow the posted value, not LAST_INSERT_ID
    database.select_after = [{'recid': 99, 'name_type': 'A', 'description': 'address'}]
    database.save_data('types', 0, _USER, recid='99', name_type='A', description='address')
    assert not [sql for sql, _ in database.calls if 'LAST_INSERT_ID' in sql]
    assert database.calls[-1][1][4] == 99


def test_delete_data_audits_inside_the_transaction(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'recid': 7, 'domain': 'gone.example.com'}]
    assert database.delete_data('domains', [7], _USER) == 1
    assert [sql.split(' (')[0] for sql, _ in database.calls][1:] == ['DELETE FROM `domains` WHERE `id` IN',
                                                                    'INSERT INTO `audit`']
    # the trail keeps the content the delete removed, read before it ran, and has no after state
    assert database.calls[-1][1] == ('admin', '203.0.113.1', 'delete', 'domains', 7,
                                     '{"recid":7,"domain":"gone.example.com"}', None)


def test_delete_data_audits_every_row_in_one_insert(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'recid': 1, 'domain': 'a'}, {'recid': 2, 'domain': 'b'}]
    database.affected = 2
    assert database.delete_data('domains', [1, 2], _USER) == 2
    sql, params = database.calls[-1]
    assert sql.startswith('INSERT INTO `audit`') and sql.count('(%s, %s, %s, %s, %s, %s, %s)') == 2
    assert params[4::7] == (1, 2)  # each row carries its own record id (field 5 of the 7 bound per row)


def test_audit_records_the_masked_password_the_read_returns(database: _FakeW2UIDatabase) -> None:
    # the users read path projects the mask (Users._projection), so the row read back for the trail already
    # carries '*****', never a hash; the trail serializes that row verbatim
    database.select_after = [{'recid': 3, 'user': 'bob', 'name': 'Bob', 'password': '*****'}]
    database.save_data('users', 0, _USER, user='bob', name='Bob', password='topsecret')
    record_json = database.calls[-1][1][-1]
    assert '"password":"*****"' in record_json and 'topsecret' not in record_json


def test_audit_serializes_a_datetime_row_value(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'recid': 3, 'logged': datetime.datetime(2026, 7, 18, 12, 34, 56)}]
    database.delete_data('domains', [3], _USER)
    assert database.calls[-1][1][-2] == '{"recid":3,"logged":"2026-07-18 12:34:56"}'


def test_audit_serialization_failure_rolls_the_write_back(database: _FakeW2UIDatabase) -> None:
    # a row the trail cannot serialize fails inside the transaction, so the write it describes never commits
    database.select_after = [{'recid': 42, 'domain': object()}]
    with pytest.raises(TypeError):
        database.save_data('domains', 0, _USER, domain='example.com', description='')
    assert database.events[-1] == 'rollback'
    assert not [sql for sql, _ in database.calls if 'audit' in sql]


def test_unchanged_write_is_not_audited(database: _FakeW2UIDatabase) -> None:
    # a zero rowcount changed nothing, so there is nothing to record; the committed transaction stays empty of audit
    database.affected = 0
    database.select_result = [{'recid': 7, 'domain': 'gone.example.com'}]
    assert database.save_data('domains', 1, _USER, domain='example.com', description='') == 0
    assert database.delete_data('domains', [7], _USER) == 0
    assert not [sql for sql, _ in database.calls if 'audit' in sql]


def test_failing_audit_insert_rolls_the_write_back(database: _FakeW2UIDatabase) -> None:
    # strict: an unauditable write is rejected, not committed untraceable
    database.select_after = [{'recid': 42, 'domain': 'example.com', 'description': ''}]
    database.raise_on_insert = 'INSERT INTO `audit`'
    with pytest.raises(RuntimeError, match='insert boom'):
        database.save_data('domains', 0, _USER, domain='example.com', description='')
    assert database.events == ['begin', 'modify', 'select', 'modify', 'rollback']
