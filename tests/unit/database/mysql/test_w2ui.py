# pylint: disable=missing-function-docstring, redefined-outer-name

"""Tests for the W2UIDatabaseMixIn SQL builders.

check_user and the delete_*/get_*/save_* admin CRUD methods. Fake _select/_modify methods record every (operation,
params) and return a canned value, so the tests assert the built SQL and bound parameters and the insert/update
branch selection - no live database.
"""

from typing import Any

import pytest

from powergslb.database.mysql import w2ui as w2ui_module
from powergslb.database.mysql.w2ui import W2UIDatabaseMixIn
from powergslb.system.password import hash_password, verify_password


class _FakeW2UIDatabase(W2UIDatabaseMixIn):
    """Record calls; _select returns select_result, _modify returns affected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.select_result: list[dict[str, Any]] = []
        self.affected = 1
        # when set, _modify pops one count per call, so a transaction's per-statement rowcounts can be scripted
        self.affected_queue: list[int] | None = None

    def _select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append((' '.join(operation.split()), params))
        return self.select_result

    def _modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        self.calls.append((' '.join(operation.split()), params))
        if self.affected_queue is not None:
            return self.affected_queue.pop(0)
        return self.affected

    def _execute_transaction(self, statements: list[tuple[str, tuple[Any, ...]]]) -> int:
        return sum(self._modify(operation, params) for operation, params in statements)


@pytest.fixture
def database() -> _FakeW2UIDatabase:
    return _FakeW2UIDatabase()


def _last_sql(database: _FakeW2UIDatabase) -> str:
    return database.calls[-1][0]


def _last_params(database: _FakeW2UIDatabase) -> tuple[Any, ...]:
    return database.calls[-1][1]


# check_user

def test_check_user_valid_password(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'password': hash_password('secret')}]
    assert database.check_user('admin', 'secret') == [{'valid': 1}]
    # only the user is bound; the salted hash is verified in Python, not in SQL
    assert _last_params(database) == ('admin',)
    assert 'PASSWORD' not in _last_sql(database)


def test_check_user_wrong_password(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'password': hash_password('secret')}]
    assert database.check_user('admin', 'wrong') == []


def test_check_user_unknown_user(database: _FakeW2UIDatabase) -> None:
    database.select_result = []
    assert database.check_user('ghost', 'secret') == []


def test_check_user_unknown_user_still_verifies(
        database: _FakeW2UIDatabase, monkeypatch: pytest.MonkeyPatch) -> None:
    # An unknown user must still run a verify so login timing does not reveal the user is absent. verify_password
    # owns the constant-time guarantee, so check_user hands it an empty stored hash and must call it regardless.
    verified: list[tuple[str, str]] = []

    def fake_verify(password: str, stored: str) -> bool:
        verified.append((password, stored))
        return False

    monkeypatch.setattr(w2ui_module, 'verify_password', fake_verify)
    database.select_result = []
    assert database.check_user('ghost', 'secret') == []
    assert verified == [('secret', '')]


# delete_* (via _delete) expand the IN clause to one placeholder per id

@pytest.mark.parametrize('method',
                         ['delete_domains', 'delete_monitors', 'delete_routings', 'delete_types', 'delete_users',
                          'delete_views'])
def test_delete_expands_in_clause(database: _FakeW2UIDatabase, method: str) -> None:
    database.affected = 2
    result = getattr(database, method)([1, 2])
    assert result == 2
    assert 'IN (%s, %s)' in _last_sql(database)
    assert _last_params(database) == (1, 2)


def test_delete_records_expands_in_clause(database: _FakeW2UIDatabase) -> None:
    database.affected = 3
    assert database.delete_records([10, 11, 12]) == 3
    # a single DELETE with one placeholder per record id
    assert len(database.calls) == 1
    assert 'IN (%s, %s, %s)' in _last_sql(database)
    assert _last_params(database) == (10, 11, 12)


def test_delete_empty_ids_is_noop(database: _FakeW2UIDatabase) -> None:
    # An empty id list must not build 'IN ()' (a MariaDB syntax error); short-circuit to zero rows.
    database.affected = 5
    assert database.delete_records([]) == 0
    assert database.calls == []  # no SQL executed


# get_status

def test_get_status_selects(database: _FakeW2UIDatabase) -> None:
    database.select_result = [{'domain': 'example.com'}]
    assert database.get_status() == [{'domain': 'example.com'}]
    assert _last_sql(database).startswith('SELECT')
    assert _last_params(database) == ()
    # the relative record name and ttl now come from the rrsets level; records.id stays unaliased
    assert 'JOIN `rrsets` ON `records`.`rrset_id` = `rrsets`.`id`' in _last_sql(database)
    assert '`rrsets`.`name`' in _last_sql(database)
    assert '`records`.`id` AS `recid`' not in _last_sql(database)
    # the rrset's routing policy is joined in and exposed by name
    assert 'JOIN `routings` ON `rrsets`.`routing_id` = `routings`.`id`' in _last_sql(database)
    assert '`routings`.`policy`' in _last_sql(database)


def test_get_records_joins_rrsets_and_exposes_relative_name(database: _FakeW2UIDatabase) -> None:
    database.get_records()
    sql = _last_sql(database)
    assert 'JOIN `rrsets` ON `records`.`rrset_id` = `rrsets`.`id`' in sql
    assert '`rrsets`.`name`' in sql and '`domains`.`domain`' in sql
    assert '`records`.`id` AS `recid`' in sql
    assert 'JOIN `routings` ON `rrsets`.`routing_id` = `routings`.`id`' in sql
    assert '`routings`.`policy`' in sql


# get_* with and without recid

@pytest.mark.parametrize('method',
                         ['get_domains', 'get_monitors', 'get_records', 'get_routings', 'get_types',
                          'get_views'])
def test_get_all_has_no_recid_filter(database: _FakeW2UIDatabase, method: str) -> None:
    getattr(database, method)()
    assert _last_params(database) == ()
    assert 'WHERE' not in _last_sql(database)


@pytest.mark.parametrize('method',
                         ['get_domains', 'get_monitors', 'get_records', 'get_routings', 'get_types',
                          'get_views'])
def test_get_one_filters_by_recid(database: _FakeW2UIDatabase, method: str) -> None:
    getattr(database, method)(7)
    assert _last_params(database) == (7,)
    assert 'WHERE' in _last_sql(database)


def test_get_users_masks_password_and_no_recid(database: _FakeW2UIDatabase) -> None:
    database.get_users()
    assert _last_params(database) == (database.password_mask,)
    assert '%s AS `password`' in _last_sql(database)


def test_get_users_with_recid_appends_id(database: _FakeW2UIDatabase) -> None:
    database.get_users(3)
    assert _last_params(database) == (database.password_mask, 3)


# save_* insert vs update branch

def test_save_domains_insert(database: _FakeW2UIDatabase) -> None:
    database.save_domains(0, 'example.com', 'IANA example zone')
    assert _last_sql(database).startswith('INSERT')
    assert _last_params(database) == ('example.com', 'IANA example zone')


def test_save_domains_update(database: _FakeW2UIDatabase) -> None:
    database.save_domains(5, 'example.com', 'IANA example zone')
    assert _last_sql(database).startswith('UPDATE')
    assert _last_params(database) == ('example.com', 'IANA example zone', 5)


def test_save_domains_description_defaults_empty(database: _FakeW2UIDatabase) -> None:
    database.save_domains(0, 'example.com')
    assert _last_params(database) == ('example.com', '')


def test_get_domains_selects_description(database: _FakeW2UIDatabase) -> None:
    database.get_domains()
    assert '`description`' in _last_sql(database)


def test_save_monitors_insert_and_update(database: _FakeW2UIDatabase) -> None:
    database.save_monitors(0, 'ping', '{}')
    assert _last_sql(database).startswith('INSERT')
    assert _last_params(database) == ('ping', '{}')
    database.save_monitors(9, 'ping', '{}')
    assert _last_sql(database).startswith('UPDATE')
    assert _last_params(database) == ('ping', '{}', 9)


def test_save_routings_insert_and_update(database: _FakeW2UIDatabase) -> None:
    database.save_routings(0, 'Round robin', '{"type": "round-robin"}')
    assert _last_sql(database).startswith('INSERT')
    assert _last_params(database) == ('Round robin', '{"type": "round-robin"}')
    database.save_routings(9, 'Round robin', '{"type": "round-robin"}')
    assert _last_sql(database).startswith('UPDATE')
    assert _last_params(database) == ('Round robin', '{"type": "round-robin"}', 9)


def test_save_types_insert_and_update(database: _FakeW2UIDatabase) -> None:
    database.save_types(0, 'desc', 'A', 1)
    assert _last_sql(database).startswith('INSERT')
    assert _last_params(database) == (1, 'A', 'desc')
    database.save_types(1, 'desc', 'A', 1)
    assert _last_sql(database).startswith('UPDATE')
    assert _last_params(database) == (1, 'A', 'desc', 1)


def test_save_views_insert_and_update(database: _FakeW2UIDatabase) -> None:
    database.save_views(0, 'internal', '10.0.0.0/8')
    assert _last_params(database) == ('internal', '10.0.0.0/8')
    database.save_views(4, 'internal', '10.0.0.0/8')
    assert _last_params(database) == ('internal', '10.0.0.0/8', 4)


def test_save_users_insert_hashes_password(database: _FakeW2UIDatabase) -> None:
    database.save_users(0, 'bob', 'Bob', 'pw')
    assert _last_sql(database).startswith('INSERT')
    assert 'PASSWORD' not in _last_sql(database)
    user, name, stored = _last_params(database)
    assert (user, name) == ('bob', 'Bob')
    assert verify_password('pw', stored)


def test_save_users_update_with_new_password_hashes_it(database: _FakeW2UIDatabase) -> None:
    database.save_users(2, 'bob', 'Bob', 'newpw')
    assert 'PASSWORD' not in _last_sql(database)
    user, name, stored, recid = _last_params(database)
    assert (user, name, recid) == ('bob', 'Bob', 2)
    assert verify_password('newpw', stored)


def test_save_users_update_with_masked_password_keeps_existing(database: _FakeW2UIDatabase) -> None:
    database.save_users(2, 'bob', 'Bob', database.password_mask)
    assert 'PASSWORD(%s)' not in _last_sql(database)
    assert 'password' not in _last_sql(database).lower()
    assert _last_params(database) == ('bob', 'Bob', 2)


# save_records: insert path and update path resolve domain/type/monitor/view names to ids

def _record_kwargs() -> dict[str, Any]:
    return {'domain': 'example.com', 'name': 'www', 'name_type': 'A', 'ttl': 60, 'content': '192.0.2.1',
            'monitor': 'ping', 'view': 'any', 'policy': 'Round robin', 'disabled': 0, 'weight': 0}


def test_save_records_insert_path(database: _FakeW2UIDatabase) -> None:
    # two statements in one transaction: upsert the rrset, then INSERT the record off LAST_INSERT_ID()
    database.affected_queue = [1, 1]
    count = database.save_records(0, **_record_kwargs())
    assert count == 2
    assert len(database.calls) == 2
    rrset_sql, rrset_params = database.calls[0]
    record_sql, record_params = database.calls[1]
    assert rrset_sql.startswith('INSERT INTO `rrsets`')
    assert 'ON DUPLICATE KEY UPDATE `id` = LAST_INSERT_ID(`id`)' in rrset_sql
    # the rrset upsert resolves the routing policy name to routing_id on both the insert and the update
    assert '`routing_id` = (SELECT `id` FROM `routings` WHERE `policy` = %s)' in rrset_sql
    # rrset params: domain, name, type, ttl, policy (insert), then ttl, policy (on duplicate update)
    assert rrset_params == ('example.com', 'www', 'A', 60, 'Round robin', 60, 'Round robin')
    assert record_sql.startswith('INSERT INTO `records`')
    assert 'LAST_INSERT_ID()' in record_sql and '`rrsets`' not in record_sql
    # record params: content, monitor, view, disabled, weight
    assert record_params == ('192.0.2.1', 'ping', 'any', 0, 0)


def test_save_records_update_path(database: _FakeW2UIDatabase) -> None:
    database.affected_queue = [1, 1]
    count = database.save_records(9, **_record_kwargs())
    assert count == 2
    assert len(database.calls) == 2
    rrset_sql, _ = database.calls[0]
    record_sql, record_params = database.calls[1]
    assert rrset_sql.startswith('INSERT INTO `rrsets`')
    assert record_sql.startswith('UPDATE `records`')
    # the record UPDATE never references `rrsets` (the GC trigger fires AFTER and would raise error 1442)
    assert '`rrsets`' not in record_sql
    assert 'LAST_INSERT_ID()' in record_sql
    assert record_params == ('192.0.2.1', 'ping', 'any', 0, 0, 9)


def test_save_records_ttl_only_edit_reports_truthy(database: _FakeW2UIDatabase) -> None:
    # rrset ttl changes (1 row) but the record UPDATE is a no-op (0 rows); the summed count is still truthy
    database.affected_queue = [1, 0]
    assert database.save_records(9, **_record_kwargs()) == 1


def test_save_records_content_only_edit_reports_truthy(database: _FakeW2UIDatabase) -> None:
    # rrset is unchanged (0 rows) but the record content changes (1 row); the summed count is still truthy
    database.affected_queue = [0, 1]
    assert database.save_records(9, **_record_kwargs()) == 1


def test_save_records_true_noop_reports_falsy(database: _FakeW2UIDatabase) -> None:
    database.affected_queue = [0, 0]
    assert database.save_records(9, **_record_kwargs()) == 0


@pytest.mark.parametrize(('disabled', 'expected'), [
    ('true', 1),  # w2ui toggle posts the JS boolean as 'true' / 'false'
    ('false', 0),
    ('1', 1),
    ('0', 0),
    (1, 1),
    (0, 0),
    (True, 1),
    (False, 0),
])
def test_save_records_coerces_disabled_toggle(database: _FakeW2UIDatabase, disabled: Any, expected: int) -> None:
    database.affected_queue = [1, 1]
    kwargs = _record_kwargs() | {'disabled': disabled}
    database.save_records(0, **kwargs)
    _, record_params = database.calls[1]
    # record params: content, monitor, view, disabled, weight
    assert record_params[3] == expected
