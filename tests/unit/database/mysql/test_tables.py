# pylint: disable=missing-function-docstring, redefined-outer-name

"""Tests for the database tables.

Each table builds its SQL privately and executes through the executor passed in; a fake executor
records every (operation, params) and returns a canned value, so the tests assert the built SQL, bound
parameters and branch selection - no live database. Covers the base search/sort/paging pipeline and the
admin CRUD tables (incl. the records transaction, users hashing and the status CASE).
"""

from datetime import date
from typing import Any

import pytest

from powergslb.database import PageRequest, SearchClause, SortClause
from powergslb.database.mysql import tables as tables_module
from powergslb.database.mysql.masked import Masked
from powergslb.database.mysql.tables import AUDIT, DOMAINS, RECORDS, STATUS, TABLES, TYPES, USERS, AuditRow, Table
from powergslb.system.password import hash_password, verify_password


class _FakeExecutor:
    """Record calls; select returns select_result, modify returns affected."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.select_result: list[dict[str, Any]] = []
        self.affected = 1
        self.insert_id = 42  # the key an insert reports as generated; 0 means it generated none
        # when set, modify pops one count per call, so a transaction's per-statement rowcounts can be scripted
        self.affected_queue: list[int] | None = None
        # when set, select pops one result per call, so the page and COUNT queries can be scripted
        self.select_queue: list[list[dict[str, Any]]] | None = None

    def select(self, operation: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        self.calls.append((' '.join(operation.split()), params))
        if self.select_queue is not None:
            return self.select_queue.pop(0)
        return self.select_result

    def modify(self, operation: str, params: tuple[Any, ...] = ()) -> int:
        self.calls.append((' '.join(operation.split()), params))
        if self.affected_queue is not None:
            return self.affected_queue.pop(0)
        return self.affected

    def last_insert_id(self) -> int:
        return self.insert_id


@pytest.fixture
def db() -> _FakeExecutor:
    return _FakeExecutor()


def _last_sql(db: _FakeExecutor) -> str:
    return db.calls[-1][0]


def _last_params(db: _FakeExecutor) -> tuple[Any, ...]:
    return db.calls[-1][1]


# the registry holds every w2ui-token-addressable table, including the read-only status table

def test_registry_holds_the_w2ui_tokens() -> None:
    assert set(TABLES) == {'audit', 'domains', 'monitors', 'records', 'routings', 'status', 'types',
                           'users', 'views'}


# Users.check_user

def test_check_user_valid_password(db: _FakeExecutor) -> None:
    db.select_result = [{'id': 1, 'user': 'admin', 'name': 'Administrator', 'password': hash_password('secret')}]
    # a valid pair returns the identity row without the password
    assert USERS.check_user(db, 'admin', 'secret') == [{'id': 1, 'user': 'admin', 'name': 'Administrator'}]
    # only the user is bound; the salted hash is verified in Python, not in SQL
    assert _last_params(db) == ('admin',)
    assert 'PASSWORD' not in _last_sql(db)


def test_check_user_wrong_password(db: _FakeExecutor) -> None:
    db.select_result = [{'password': hash_password('secret')}]
    assert not USERS.check_user(db, 'admin', 'wrong')


def test_check_user_unknown_user(db: _FakeExecutor) -> None:
    db.select_result = []
    assert not USERS.check_user(db, 'ghost', 'secret')


def test_check_user_unknown_user_still_verifies(db: _FakeExecutor, monkeypatch: pytest.MonkeyPatch) -> None:
    # An unknown user must still run a verify so login timing does not reveal the user is absent. verify_password
    # owns the constant-time guarantee, so check_user hands it an empty stored hash and must call it regardless.
    verified: list[tuple[str, str]] = []

    def fake_verify(password: str, stored: str) -> bool:
        verified.append((password, stored))
        return False

    monkeypatch.setattr(tables_module, 'verify_password', fake_verify)
    db.select_result = []
    assert not USERS.check_user(db, 'ghost', 'secret')
    assert verified == [('secret', '')]


# Audit: read-only through the CRUD path, written only via record()

def test_audit_select_keeps_logged_datetime_and_exposes_columns(db: _FakeExecutor) -> None:
    AUDIT.get(db)
    sql = _last_sql(db)
    assert '`id` AS `recid`' in sql
    assert '`logged`' in sql
    for column in ('`user`', '`client_ip`', '`action`', '`data`', '`record_id`',
                   '`record_before`', '`record_after`'):
        assert column in sql
    assert 'FROM `audit`' in sql


def test_audit_record_inserts_one_row(db: _FakeExecutor) -> None:
    # an insert has no before state; the column takes NULL
    assert AUDIT.record(db, [AuditRow('admin', '203.0.113.1', 'save', 'domains', 7,
                                     None, '{"domain":"example.com"}')]) == 1
    sql, params = db.calls[-1]
    assert sql == ('INSERT INTO `audit` (`user`, `client_ip`, `action`, `data`, `record_id`, `record_before`, '
                   '`record_after`) VALUES (%s, %s, %s, %s, %s, %s, %s)')
    assert params == ('admin', '203.0.113.1', 'save', 'domains', 7, None, '{"domain":"example.com"}')


def test_audit_record_inserts_multiple_rows(db: _FakeExecutor) -> None:
    db.affected = 2
    rows = [AuditRow('admin', '203.0.113.1', 'delete', 'domains', 1, '{"recid":1}', None),
            AuditRow('admin', '203.0.113.1', 'delete', 'domains', 2, '{"recid":2}', None)]
    assert AUDIT.record(db, rows) == 2
    sql, params = db.calls[-1]
    assert sql == ('INSERT INTO `audit` (`user`, `client_ip`, `action`, `data`, `record_id`, `record_before`, '
                   '`record_after`) VALUES (%s, %s, %s, %s, %s, %s, %s), (%s, %s, %s, %s, %s, %s, %s)')
    assert params == ('admin', '203.0.113.1', 'delete', 'domains', 1, '{"recid":1}', None,
                      'admin', '203.0.113.1', 'delete', 'domains', 2, '{"recid":2}', None)


def test_audit_record_empty_is_noop(db: _FakeExecutor) -> None:
    assert AUDIT.record(db, []) == 0
    assert not db.calls


def test_audit_get_pages_by_recid_desc(db: _FakeExecutor) -> None:
    AUDIT.get(db, page=PageRequest(sorts=(SortClause(field='recid', direction='desc'),), limit=50, offset=0))
    sql = _last_sql(db)
    assert 'ORDER BY `recid` DESC' in sql
    assert 'LIMIT %s OFFSET %s' in sql


def test_audit_save_is_rejected(db: _FakeExecutor) -> None:
    with pytest.raises(ValueError, match='read-only'):
        AUDIT.save(db, 0, user='admin')
    assert not db.calls


def test_audit_remove_is_rejected(db: _FakeExecutor) -> None:
    with pytest.raises(ValueError, match='read-only'):
        AUDIT.remove(db, [1])
    assert not db.calls


# remove expands the IN clause to one placeholder per id

@pytest.mark.parametrize('data', ['domains', 'monitors', 'routings', 'types', 'users', 'views'])
def test_remove_expands_in_clause(db: _FakeExecutor, data: str) -> None:
    db.affected = 2
    assert TABLES[data].remove(db, [1, 2]) == 2
    assert 'IN (%s, %s)' in _last_sql(db)
    assert _last_params(db) == (1, 2)


def test_remove_records_expands_in_clause(db: _FakeExecutor) -> None:
    db.affected = 3
    assert RECORDS.remove(db, [10, 11, 12]) == 3
    # a single DELETE with one placeholder per record id
    assert len(db.calls) == 1
    assert 'IN (%s, %s, %s)' in _last_sql(db)
    assert _last_params(db) == (10, 11, 12)


def test_remove_empty_ids_is_noop(db: _FakeExecutor) -> None:
    # An empty id list must not build 'IN ()' (a MariaDB syntax error); short-circuit to zero rows.
    db.affected = 5
    assert RECORDS.remove(db, []) == 0
    assert db.calls == []  # no SQL executed


# Status.get

def test_status_get_selects(db: _FakeExecutor) -> None:
    db.select_result = [{'domain': 'example.com'}]
    assert STATUS.get(db) == ([{'domain': 'example.com'}], 1)
    assert _last_sql(db).startswith('SELECT')
    assert _last_params(db) == ()
    # the relative record name and ttl come from the rrsets level
    assert 'JOIN `rrsets` ON `records`.`rrset_id` = `rrsets`.`id`' in _last_sql(db)
    assert '`rrsets`.`name`' in _last_sql(db)
    # the rrset's routing policy is joined in and exposed by name
    assert 'JOIN `routings` ON `rrsets`.`routing_id` = `routings`.`id`' in _last_sql(db)
    assert '`routings`.`policy`' in _last_sql(db)


def test_status_get_computes_status_in_sql(db: _FakeExecutor) -> None:
    # the On/Off value is a SQL CASE over the records join (wrapped as `r`) so the paged pipeline can
    # search and sort it like any column; the record id rides along as recid (unshown, but the paging
    # tiebreaker matching the records grid)
    STATUS.get(db)
    sql = _last_sql(db)
    assert "CASE WHEN `r`.`disabled` THEN 'Off' ELSE 'On' END AS `status`" in sql
    assert '`records`.`id` AS `recid`' in sql
    assert _last_params(db) == ()


def test_status_get_expands_down_ids(db: _FakeExecutor) -> None:
    STATUS.get(db, down_ids=[7, 9])
    sql = _last_sql(db)
    assert ("CASE WHEN `r`.`disabled` OR `r`.`recid` IN (%s, %s) "
            "THEN 'Off' ELSE 'On' END AS `status`") in sql
    assert _last_params(db) == (7, 9)


def test_status_get_recid_binds_after_down_ids(db: _FakeExecutor) -> None:
    # the CASE's down-id placeholders precede the derived table's recid placeholder in the SQL text
    STATUS.get(db, 5, down_ids=[7])
    assert 'WHERE `records`.`id` = %s' in _last_sql(db)
    assert _last_params(db) == (7, 5)


# the records/status fast path: page records by primary key first, then join the lookup tables to the page

def test_records_fast_path_pages_records_then_joins(db: _FakeExecutor) -> None:
    # a local page selects its ids from records alone, counts records alone, then joins only the page ids
    db.select_queue = [
        [{'recid': 1}, {'recid': 2}],  # records-only page ids
        [{'total': 5}],  # records-only count
        [{'recid': 2, 'content': 'b'}, {'recid': 1, 'content': 'a'}],  # page join, returned unordered
    ]
    rows, total = RECORDS.get(db, page=PageRequest(limit=2, offset=0))
    assert total == 5
    # the page order (the id order) is preserved even though the page join came back reordered
    assert [row['recid'] for row in rows] == [1, 2]
    page_sql, _ = db.calls[0]
    count_sql, _ = db.calls[1]
    join_sql, join_params = db.calls[2]
    assert 'JOIN' not in page_sql and page_sql.endswith('LIMIT %s OFFSET %s')
    assert 'JOIN' not in count_sql and count_sql.startswith('SELECT COUNT(*)')
    assert 'JOIN `rrsets`' in join_sql and 'WHERE `records`.`id` IN (%s, %s)' in join_sql
    assert join_params == (1, 2)


def test_records_fast_path_search_on_local_field(db: _FakeExecutor) -> None:
    # a search on content (a records column) stays on the fast path
    db.select_queue = [[{'recid': 3}], [{'recid': 3, 'content': 'x'}]]  # short page skips the count
    rows, total = RECORDS.get(db, page=PageRequest(searches=(_search('content', value='x'),), limit=10, offset=0))
    assert (rows, total) == ([{'recid': 3, 'content': 'x'}], 1)
    page_sql, page_params = db.calls[0]
    assert 'JOIN' not in page_sql and 'WHERE `content` = %s' in page_sql
    assert page_params == ('x', 10, 0)
    assert 'JOIN `rrsets`' in db.calls[1][0]


def test_records_fast_path_empty_page_skips_join(db: _FakeExecutor) -> None:
    # no page ids means no page join and no count: a single records-only query
    db.select_result = []
    rows, total = RECORDS.get(db, page=PageRequest(limit=10, offset=0))
    assert (rows, total) == ([], 0)
    assert len(db.calls) == 1
    assert 'JOIN' not in _last_sql(db)


def test_records_falls_back_to_full_join_on_joined_field(db: _FakeExecutor) -> None:
    # a sort on domain (a joined column) needs the full join, so the base wrapped-join path runs
    RECORDS.get(db, page=PageRequest(sorts=(SortClause(field='domain', direction='asc'),), limit=5))
    assert len(db.calls) == 1
    sql = _last_sql(db)
    assert 'JOIN `rrsets`' in sql and 'ORDER BY `domain`, `recid` LIMIT %s' in sql


def test_status_fast_path_pages_records_then_joins(db: _FakeExecutor) -> None:
    # the default status/recid sort is local (status is computed from records columns), so it pages records first
    db.select_queue = [
        [{'recid': 1, 'status': 'On'}, {'recid': 2, 'status': 'Off'}],  # records-only page with the CASE
        [{'total': 5}],  # records-only count
        [{'recid': 2, 'content': 'b'}, {'recid': 1, 'content': 'a'}],  # page join, no status column
    ]
    page = PageRequest(sorts=(SortClause(field='status', direction='asc'), SortClause(field='recid', direction='asc')),
                       limit=2, offset=0)
    rows, total = STATUS.get(db, page=page, down_ids=[2])
    assert total == 5
    assert [row['recid'] for row in rows] == [1, 2]
    # the status computed on the records-only page is carried onto the joined page rows
    assert {row['recid']: row['status'] for row in rows} == {1: 'On', 2: 'Off'}
    page_sql, page_params = db.calls[0]
    assert 'JOIN' not in page_sql
    assert "CASE WHEN `records`.`disabled` OR `records`.`id` IN (%s) THEN 'Off' ELSE 'On' END AS `status`" in page_sql
    # the down id binds in the source, before the paging params
    assert page_params == (2, 2, 0)
    assert 'JOIN `rrsets`' in db.calls[2][0]


def test_status_fast_path_without_down_ids(db: _FakeExecutor) -> None:
    db.select_queue = [[{'recid': 1, 'status': 'On'}], [{'recid': 1, 'content': 'a'}]]
    rows, total = STATUS.get(db, page=PageRequest(sorts=(SortClause(field='status', direction='asc'),),
                                                  limit=10, offset=0))
    assert (total, rows[0]['status']) == (1, 'On')
    page_sql = db.calls[0][0]
    assert "CASE WHEN `records`.`disabled` THEN 'Off' ELSE 'On' END AS `status`" in page_sql
    assert 'IN (' not in page_sql


def test_status_falls_back_to_full_join_on_joined_field(db: _FakeExecutor) -> None:
    # a sort on name (a joined column) forces the CASE-over-wrapped-join full path
    STATUS.get(db, page=PageRequest(sorts=(SortClause(field='name', direction='desc'),), limit=5), down_ids=[7])
    sql = _last_sql(db)
    assert 'JOIN `rrsets`' in sql
    assert "CASE WHEN `r`.`disabled` OR `r`.`recid` IN (%s)" in sql


def test_status_save_is_rejected(db: _FakeExecutor) -> None:
    # the status grid is read-only; the inherited records writes must stay unreachable in code too
    with pytest.raises(ValueError, match='read-only'):
        STATUS.save(db, 0, **_record_kwargs())
    assert db.calls == []


def test_status_remove_is_rejected(db: _FakeExecutor) -> None:
    with pytest.raises(ValueError, match='read-only'):
        STATUS.remove(db, [1])
    assert db.calls == []


def test_records_get_joins_rrsets_and_exposes_relative_name(db: _FakeExecutor) -> None:
    RECORDS.get(db)
    sql = _last_sql(db)
    assert 'JOIN `rrsets` ON `records`.`rrset_id` = `rrsets`.`id`' in sql
    assert '`rrsets`.`name`' in sql and '`domains`.`domain`' in sql
    assert '`records`.`id` AS `recid`' in sql
    assert 'JOIN `routings` ON `rrsets`.`routing_id` = `routings`.`id`' in sql
    assert '`routings`.`policy`' in sql


# get with and without recid

@pytest.mark.parametrize('data', ['domains', 'monitors', 'records', 'routings', 'types', 'views'])
def test_get_all_has_no_recid_filter(db: _FakeExecutor, data: str) -> None:
    TABLES[data].get(db)
    assert _last_params(db) == ()
    assert 'WHERE' not in _last_sql(db)


@pytest.mark.parametrize('data', ['domains', 'monitors', 'records', 'routings', 'types', 'views'])
def test_get_one_filters_by_recid(db: _FakeExecutor, data: str) -> None:
    TABLES[data].get(db, 7)
    assert _last_params(db) == (7,)
    assert 'WHERE' in _last_sql(db)


def test_get_users_masks_password_as_literal(db: _FakeExecutor) -> None:
    USERS.get(db)
    assert _last_params(db) == ()
    assert "'*****' AS `password`" in _last_sql(db)


def test_get_users_with_recid_binds_only_the_id(db: _FakeExecutor) -> None:
    USERS.get(db, 3)
    assert _last_params(db) == (3,)
    assert "'*****' AS `password`" in _last_sql(db)


# the paged read: derived-table wrap and tuple return

def _search(field: str = 'domain', search_type: str = 'text', operator: str = 'is',
            value: Any = 'x') -> SearchClause:
    return SearchClause(field=field, type=search_type, operator=operator, value=value)


def test_get_none_page_runs_unwrapped(db: _FakeExecutor) -> None:
    db.select_result = [{'recid': 1}, {'recid': 2}]
    rows, total = DOMAINS.get(db)
    assert (rows, total) == (db.select_result, 2)
    assert 'SELECT * FROM (' not in _last_sql(db)


def test_get_page_wraps_as_derived_table(db: _FakeExecutor) -> None:
    db.select_result = [{'recid': 1}]
    rows, total = DOMAINS.get(db, page=PageRequest())
    assert (rows, total) == (db.select_result, 1)
    sql = _last_sql(db)
    assert sql.startswith('SELECT * FROM (SELECT')
    assert sql.endswith('AS `t`')
    assert 'WHERE' not in sql and 'ORDER BY' not in sql and 'LIMIT' not in sql


def test_get_recid_filter_stays_on_inner_query(db: _FakeExecutor) -> None:
    DOMAINS.get(db, 7, PageRequest())
    sql = _last_sql(db)
    assert 'WHERE `domains`.`id` = %s) AS `t`' in sql
    assert _last_params(db) == (7,)


# search operators

@pytest.mark.parametrize(('operator', 'condition', 'param'), [
    ('is', '`domain` = %s', 'x'),
    ('begins', '`domain` LIKE %s', 'x%'),
    ('contains', '`domain` LIKE %s', '%x%'),
    ('ends', '`domain` LIKE %s', '%x'),
])
def test_search_text_operators(db: _FakeExecutor, operator: str, condition: str, param: str) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search(operator=operator),)))
    assert f'AS `t` WHERE {condition}' in _last_sql(db)
    assert _last_params(db) == (param,)


def test_search_like_escapes_wildcards(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search(operator='contains', value='50%_\\'),)))
    assert _last_params(db) == ('%50\\%\\_\\\\%',)


def test_search_int_is_coerces_value(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search('recid', 'int', 'is', '5'),)))
    assert 'WHERE `recid` = %s' in _last_sql(db)
    assert _last_params(db) == (5,)


def test_search_int_in_expands_list(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search('recid', 'int', 'in', [1, '2']),)))
    assert 'WHERE `recid` IN (%s, %s)' in _last_sql(db)
    assert _last_params(db) == (1, 2)


def test_search_int_not_in_normalizes_scalar(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search('recid', 'int', 'not in', 3),)))
    assert 'WHERE `recid` NOT IN (%s)' in _last_sql(db)
    assert _last_params(db) == (3,)


def test_search_int_in_empty_list_matches_nothing(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search('recid', 'int', 'in', []),)))
    assert 'WHERE 0 = 1' in _last_sql(db)
    assert _last_params(db) == ()


def test_search_int_between(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search('recid', 'int', 'between', ['1', 9]),)))
    assert 'WHERE `recid` BETWEEN %s AND %s' in _last_sql(db)
    assert _last_params(db) == (1, 9)


@pytest.mark.parametrize('value', [5, [1], ['1', 'x'], None, '19'])  # a string must not index as '1'..'9'
def test_search_between_malformed_value_matches_nothing(db: _FakeExecutor, value: Any) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search('recid', 'int', 'between', value),)))
    assert 'WHERE 0 = 1' in _last_sql(db)
    assert _last_params(db) == ()


def test_search_int_uncoercible_value_matches_nothing(db: _FakeExecutor) -> None:
    # the Python engine's int('1x') raised -> no match; raw SQL coercion would fuzzy-match '1x' = 1
    DOMAINS.get(db, page=PageRequest(searches=(_search('recid', 'int', 'is', '1x'),)))
    assert 'WHERE 0 = 1' in _last_sql(db)
    assert _last_params(db) == ()


# date search: half-open day range, never equality (the column carries a time part)

def test_search_date_is_expands_to_day_range(db: _FakeExecutor) -> None:
    AUDIT.get(db, page=PageRequest(searches=(_search('logged', 'date', 'is', '2026-07-18'),)))
    assert 'WHERE `logged` >= %s AND `logged` < %s' in _last_sql(db)
    assert _last_params(db) == (date(2026, 7, 18), date(2026, 7, 19))


def test_search_date_between_includes_end_day(db: _FakeExecutor) -> None:
    AUDIT.get(db, page=PageRequest(searches=(_search('logged', 'date', 'between', ['2026-07-01', '2026-07-31']),)))
    assert 'WHERE `logged` >= %s AND `logged` < %s' in _last_sql(db)
    assert _last_params(db) == (date(2026, 7, 1), date(2026, 8, 1))


@pytest.mark.parametrize('value', ['not-a-date', '2026-13-01', ['2026-07-01'], '2026-07-01', None, [],
                                   ['2026-01-01', '9999-12-31']])  # the max date has no day after it
def test_search_date_malformed_value_matches_nothing(db: _FakeExecutor, value: Any) -> None:
    AUDIT.get(db, page=PageRequest(searches=(_search('logged', 'date', 'between', value),)))
    assert 'WHERE 0 = 1' in _last_sql(db)
    assert _last_params(db) == ()


def test_search_date_is_malformed_matches_nothing(db: _FakeExecutor) -> None:
    AUDIT.get(db, page=PageRequest(searches=(_search('logged', 'date', 'is', 'nope'),)))
    assert 'WHERE 0 = 1' in _last_sql(db)
    assert _last_params(db) == ()


# clause composition and fallbacks

def test_search_unknown_operator_is_dropped(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search(operator='wat'),)))
    assert 'WHERE' not in _last_sql(db)  # AND with zero usable clauses keeps the full set


def test_search_unknown_type_is_dropped(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search(search_type='float'),)))
    assert 'WHERE' not in _last_sql(db)


def test_search_unknown_field_matches_nothing(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search(field='absent'),)))
    assert 'WHERE 0 = 1' in _last_sql(db)
    assert _last_params(db) == ()


def test_search_and_composition(db: _FakeExecutor) -> None:
    page = PageRequest(searches=(_search(value='a'), _search('recid', 'int', 'is', 2)))
    DOMAINS.get(db, page=page)
    assert 'WHERE `domain` = %s AND `recid` = %s' in _last_sql(db)
    assert _last_params(db) == ('a', 2)


def test_search_or_composition(db: _FakeExecutor) -> None:
    page = PageRequest(searches=(_search(value='a'), _search(value='b')), or_logic=True)
    DOMAINS.get(db, page=page)
    assert 'WHERE `domain` = %s OR `domain` = %s' in _last_sql(db)
    assert _last_params(db) == ('a', 'b')


def test_search_or_zero_usable_clauses_matches_nothing(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(searches=(_search(operator='wat'),), or_logic=True))
    assert 'WHERE 0 = 1' in _last_sql(db)


def test_search_dropped_clause_binds_no_params(db: _FakeExecutor) -> None:
    page = PageRequest(searches=(_search(operator='wat'), _search(value='kept')))
    DOMAINS.get(db, page=page)
    assert 'WHERE `domain` = %s' in _last_sql(db)
    assert _last_params(db) == ('kept',)


# ORDER BY and the deterministic tiebreaker

def test_sort_order_by_whitelisted_fields_and_direction(db: _FakeExecutor) -> None:
    page = PageRequest(sorts=(SortClause(field='domain', direction='desc'), SortClause(field='recid', direction='asc')))
    DOMAINS.get(db, page=page)
    assert 'ORDER BY `domain` DESC, `recid`' in _last_sql(db)


def test_sort_unknown_field_is_skipped(db: _FakeExecutor) -> None:
    page = PageRequest(sorts=(SortClause(field='absent', direction='asc'),))
    DOMAINS.get(db, page=page)
    assert 'ORDER BY' not in _last_sql(db)


def test_sort_without_limit_appends_no_tiebreaker(db: _FakeExecutor) -> None:
    DOMAINS.get(db, page=PageRequest(sorts=(SortClause(field='domain', direction='asc'),)))
    assert _last_sql(db).endswith('ORDER BY `domain`')


def test_limit_appends_recid_tiebreaker(db: _FakeExecutor) -> None:
    # a LIMITed query without a deterministic order could overlap or skip rows across pages
    db.select_queue = [[{'recid': 1}], [{'total': 3}]]
    DOMAINS.get(db, page=PageRequest(sorts=(SortClause(field='domain', direction='asc'),), limit=1, offset=0))
    page_sql, _ = db.calls[0]
    assert 'ORDER BY `domain`, `recid` LIMIT %s OFFSET %s' in page_sql


@pytest.mark.parametrize(('direction', 'order_by'), [('asc', 'ORDER BY `recid`'), ('desc', 'ORDER BY `recid` DESC')])
def test_limit_tiebreaker_skips_already_sorted_field(db: _FakeExecutor, direction: str, order_by: str) -> None:
    db.select_queue = [[{'recid': 1}], [{'total': 3}]]
    DOMAINS.get(db, page=PageRequest(sorts=(SortClause(field='recid', direction=direction),), limit=1, offset=0))
    page_sql, _ = db.calls[0]
    assert f'{order_by} LIMIT %s OFFSET %s' in page_sql
    assert page_sql.count('`recid`') == 2  # the SELECT alias and the single ORDER BY term


def test_status_limit_appends_recid_tiebreaker(db: _FakeExecutor) -> None:
    # the status grid exposes recid too, so its paging order matches the records grid
    STATUS.get(db, page=PageRequest(sorts=(SortClause(field='name', direction='desc'),), limit=5))
    assert 'ORDER BY `name` DESC, `recid` LIMIT %s' in _last_sql(db)


# LIMIT/OFFSET and the COUNT total

def test_full_page_counts_total(db: _FakeExecutor) -> None:
    # a full page cannot determine the total, so the same filter re-runs as COUNT without order or paging
    db.select_queue = [[{'recid': 1}, {'recid': 2}], [{'total': 7}]]
    rows, total = DOMAINS.get(db, page=PageRequest(limit=2, offset=4))
    assert rows == [{'recid': 1}, {'recid': 2}]
    assert total == 7
    page_sql, page_params = db.calls[0]
    count_sql, count_params = db.calls[1]
    assert page_sql.endswith('LIMIT %s OFFSET %s')
    assert page_params == (2, 4)
    assert count_sql.startswith('SELECT COUNT(*) AS `total` FROM (')
    assert 'ORDER BY' not in count_sql and 'LIMIT' not in count_sql
    assert count_params == ()


def test_short_page_skips_count(db: _FakeExecutor) -> None:
    # a short page determines the total itself: offset + row count
    db.select_result = [{'recid': 1}]
    rows, total = DOMAINS.get(db, page=PageRequest(limit=10, offset=20))
    assert (rows, total) == ([{'recid': 1}], 21)
    assert len(db.calls) == 1


def test_empty_page_at_offset_zero_skips_count(db: _FakeExecutor) -> None:
    rows, total = DOMAINS.get(db, page=PageRequest(limit=10, offset=0))
    assert (rows, total) == ([], 0)
    assert len(db.calls) == 1


def test_empty_page_at_offset_counts_total(db: _FakeExecutor) -> None:
    # an empty page at a non-zero offset may be past the end; only COUNT can tell the real total
    db.select_queue = [[], [{'total': 3}]]
    rows, total = DOMAINS.get(db, page=PageRequest(limit=10, offset=100))
    assert (rows, total) == ([], 3)
    assert len(db.calls) == 2


def test_count_reuses_search_params(db: _FakeExecutor) -> None:
    db.select_queue = [[], [{'total': 0}]]
    DOMAINS.get(db, page=PageRequest(searches=(_search(value='a'),), limit=2, offset=4))
    page_params = db.calls[0][1]
    count_sql, count_params = db.calls[1]
    assert page_params == ('a', 2, 4)
    assert 'WHERE `domain` = %s' in count_sql
    assert count_params == ('a',)


def test_max_path_skips_count(db: _FakeExecutor) -> None:
    # get-items pages by max alone (offset None) and discards total, so it never pays the COUNT round trip
    db.select_result = [{'recid': 1}]
    rows, total = DOMAINS.get(db, page=PageRequest(limit=1))
    assert (rows, total) == ([{'recid': 1}], 1)
    assert len(db.calls) == 1
    assert _last_sql(db).endswith('LIMIT %s')
    assert _last_params(db) == (1,)


def test_paged_users_mask_binds_no_params(db: _FakeExecutor) -> None:
    # the mask is a SQL literal, so only the WHERE params and the paging params are bound
    db.select_queue = [[], [{'total': 0}]]
    USERS.get(db, page=PageRequest(searches=(_search('user', value='bob'),), limit=10, offset=10))
    assert db.calls[0][1] == ('bob', 10, 10)
    assert db.calls[1][1] == ('bob',)


# save insert vs update branch

def test_save_domains_insert(db: _FakeExecutor) -> None:
    DOMAINS.save(db, 0, domain='example.com', description='IANA example zone')
    assert _last_sql(db).startswith('INSERT')
    assert _last_params(db) == ('example.com', 'IANA example zone')


def test_save_domains_update(db: _FakeExecutor) -> None:
    DOMAINS.save(db, 5, domain='example.com', description='IANA example zone')
    assert _last_sql(db).startswith('UPDATE')
    assert _last_params(db) == ('example.com', 'IANA example zone', 5)


def test_save_domains_description_defaults_empty(db: _FakeExecutor) -> None:
    # the save override fills a column the posted record omits
    DOMAINS.save(db, 0, domain='example.com')
    assert _last_params(db) == ('example.com', '')


def test_save_extra_field_is_ignored(db: _FakeExecutor) -> None:
    # only the writable columns are bound; a stray posted field must not shift the values
    DOMAINS.save(db, 0, domain='example.com', description='', stray='x')
    assert _last_params(db) == ('example.com', '')


def test_get_domains_selects_description(db: _FakeExecutor) -> None:
    DOMAINS.get(db)
    assert '`description`' in _last_sql(db)


def test_save_monitors_insert_and_update(db: _FakeExecutor) -> None:
    TABLES['monitors'].save(db, 0, monitor='ping', monitor_json='{}')
    assert _last_sql(db).startswith('INSERT')
    assert _last_params(db) == ('ping', '{}')
    TABLES['monitors'].save(db, 9, monitor='ping', monitor_json='{}')
    assert _last_sql(db).startswith('UPDATE')
    assert _last_params(db) == ('ping', '{}', 9)


def test_save_routings_insert_and_update(db: _FakeExecutor) -> None:
    TABLES['routings'].save(db, 0, policy='Round robin', policy_json='{"type": "round-robin"}')
    assert _last_sql(db).startswith('INSERT')
    assert _last_params(db) == ('Round robin', '{"type": "round-robin"}')
    TABLES['routings'].save(db, 9, policy='Round robin', policy_json='{"type": "round-robin"}')
    assert _last_sql(db).startswith('UPDATE')
    assert _last_params(db) == ('Round robin', '{"type": "round-robin"}', 9)


def test_save_types_insert_and_update(db: _FakeExecutor) -> None:
    # the writable value key rides in columns as recid; name_type resolves to the type column
    TYPES.save(db, 0, description='desc', name_type='A', recid=1)
    assert _last_sql(db).startswith('INSERT')
    assert '`value`' in _last_sql(db) and '`type`' in _last_sql(db)
    assert _last_params(db) == (1, 'A', 'desc')
    TYPES.save(db, 1, description='desc', name_type='A', recid=1)
    assert _last_sql(db).startswith('UPDATE')
    assert _last_params(db) == (1, 'A', 'desc', 1)


# written_recid: the key of the row a save just wrote, for the audit trail's after read

def test_written_recid_of_an_update_is_the_targeted_key(db: _FakeExecutor) -> None:
    assert DOMAINS.written_recid(db, 7, domain='example.com') == 7
    assert not db.calls  # the key is known, so no lookup runs


def test_written_recid_of_an_insert_takes_the_generated_key(db: _FakeExecutor) -> None:
    db.insert_id = 7
    assert DOMAINS.written_recid(db, 0, domain='example.com') == 7
    assert not db.calls  # the insert already reported its key, so no statement runs


def test_written_recid_without_a_generated_key_raises(db: _FakeExecutor) -> None:
    # a table whose insert generates nothing would otherwise be audited under a wrong or absent id
    db.insert_id = 0
    with pytest.raises(RuntimeError, match="'domains' insert generated no key"):
        DOMAINS.written_recid(db, 0, domain='example.com')


def test_written_recid_of_a_writable_key_is_the_posted_value(db: _FakeExecutor) -> None:
    # types supplies its own key, so an insert has no AUTO_INCREMENT value to read back and an update
    # that renames the key lands on the posted one, not on the key it targeted
    assert TYPES.written_recid(db, 0, recid='99', name_type='A', description='desc') == 99
    assert TYPES.written_recid(db, 1, recid='99', name_type='A', description='desc') == 99
    assert not db.calls


def test_get_types_projects_aliased_column(db: _FakeExecutor) -> None:
    # the read path aliases the backing type column to its exposed name_type
    TYPES.get(db)
    assert '`type` AS `name_type`' in _last_sql(db)


def test_aliases_remap_column_both_directions(db: _FakeExecutor) -> None:
    # a pure column rename needs no subclass: aliases cover projection, insert and update
    aliased = Table(name='t', fields=('recid', 'shown'), columns=('shown',), aliases={'shown': 'stored'})
    aliased.get(db)
    assert '`stored` AS `shown`' in _last_sql(db)
    aliased.save(db, 0, shown='x')
    assert _last_sql(db).startswith('INSERT') and '`stored`' in _last_sql(db)
    aliased.save(db, 7, shown='x')
    assert _last_sql(db).startswith('UPDATE') and '`stored` = %s' in _last_sql(db)


def test_no_alias_projects_bare_column(db: _FakeExecutor) -> None:
    # an unaliased field is projected without an AS clause
    plain = Table(name='t', fields=('recid', 'plain'), columns=('plain',))
    plain.get(db)
    assert '`plain`' in _last_sql(db) and 'AS `plain`' not in _last_sql(db)


def test_save_views_insert_and_update(db: _FakeExecutor) -> None:
    TABLES['views'].save(db, 0, view='internal', rule='10.0.0.0/8')
    assert _last_params(db) == ('internal', '10.0.0.0/8')
    TABLES['views'].save(db, 4, view='internal', rule='10.0.0.0/8')
    assert _last_params(db) == ('internal', '10.0.0.0/8', 4)


def test_save_users_insert_hashes_password(db: _FakeExecutor) -> None:
    USERS.save(db, 0, user='bob', name='Bob', password='pw')
    assert _last_sql(db).startswith('INSERT')
    assert 'PASSWORD' not in _last_sql(db)
    user, name, stored = _last_params(db)
    assert (user, name) == ('bob', 'Bob')
    # the hash is Masked so it never lands in the query log; the wrapped value is the real hash
    assert isinstance(stored, Masked)
    assert verify_password('pw', stored.value)


def test_save_users_update_with_new_password_hashes_it(db: _FakeExecutor) -> None:
    USERS.save(db, 2, user='bob', name='Bob', password='newpw')
    assert 'PASSWORD' not in _last_sql(db)
    user, name, stored, recid = _last_params(db)
    assert (user, name, recid) == ('bob', 'Bob', 2)
    assert isinstance(stored, Masked)
    assert verify_password('newpw', stored.value)


def test_save_users_update_with_masked_password_keeps_existing(db: _FakeExecutor) -> None:
    USERS.save(db, 2, user='bob', name='Bob', password='*****')
    assert 'password' not in _last_sql(db).lower()
    assert _last_params(db) == ('bob', 'Bob', 2)


# Records.save: insert path and update path resolve domain/type/monitor/view names to ids

def _record_kwargs() -> dict[str, Any]:
    return {'domain': 'example.com', 'name': 'www', 'name_type': 'A', 'ttl': 60, 'content': '192.0.2.1',
            'monitor': 'ping', 'view': 'any', 'policy': 'Round robin', 'disabled': 0, 'weight': 0}


def test_save_records_insert_path(db: _FakeExecutor) -> None:
    # two statements in one transaction: upsert the rrset, then INSERT the record off LAST_INSERT_ID()
    db.affected_queue = [1, 1]
    count = RECORDS.save(db, 0, **_record_kwargs())
    assert count == 2
    assert len(db.calls) == 2
    rrset_sql, rrset_params = db.calls[0]
    record_sql, record_params = db.calls[1]
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


def test_save_records_update_path(db: _FakeExecutor) -> None:
    db.affected_queue = [1, 1]
    count = RECORDS.save(db, 9, **_record_kwargs())
    assert count == 2
    assert len(db.calls) == 2
    rrset_sql, _ = db.calls[0]
    record_sql, record_params = db.calls[1]
    assert rrset_sql.startswith('INSERT INTO `rrsets`')
    assert record_sql.startswith('UPDATE `records`')
    # the record UPDATE never references `rrsets` (the GC trigger fires AFTER and would raise error 1442)
    assert '`rrsets`' not in record_sql
    assert 'LAST_INSERT_ID()' in record_sql
    assert record_params == ('192.0.2.1', 'ping', 'any', 0, 0, 9)


def test_save_records_ttl_only_edit_reports_truthy(db: _FakeExecutor) -> None:
    # rrset ttl changes (1 row) but the record UPDATE is a no-op (0 rows); the summed count is still truthy
    db.affected_queue = [1, 0]
    assert RECORDS.save(db, 9, **_record_kwargs()) == 1


def test_save_records_content_only_edit_reports_truthy(db: _FakeExecutor) -> None:
    # rrset is unchanged (0 rows) but the record content changes (1 row); the summed count is still truthy
    db.affected_queue = [0, 1]
    assert RECORDS.save(db, 9, **_record_kwargs()) == 1


def test_save_records_true_noop_reports_falsy(db: _FakeExecutor) -> None:
    db.affected_queue = [0, 0]
    assert RECORDS.save(db, 9, **_record_kwargs()) == 0


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
def test_save_records_coerces_disabled_toggle(db: _FakeExecutor, disabled: Any, expected: int) -> None:
    db.affected_queue = [1, 1]
    kwargs = _record_kwargs() | {'disabled': disabled}
    RECORDS.save(db, 0, **kwargs)
    _, record_params = db.calls[1]
    # record params: content, monitor, view, disabled, weight
    assert record_params[3] == expected


def test_save_records_defaults_disabled_and_weight(db: _FakeExecutor) -> None:
    db.affected_queue = [1, 1]
    kwargs = _record_kwargs()
    del kwargs['disabled'], kwargs['weight']
    RECORDS.save(db, 0, **kwargs)
    _, record_params = db.calls[1]
    assert record_params[3:] == (0, 0)
