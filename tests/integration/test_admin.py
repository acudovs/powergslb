# pylint: disable=missing-function-docstring, redefined-outer-name, too-many-lines

"""Admin w2ui interface tests.

Covers the HTTPS management API at /admin/w2ui: HTTP Basic auth, the get-records / get-record / get-items / save-record
/ delete-records commands, pagination, search (text and int operators, AND/OR logic), sort, full CRUD lifecycles for
every table, static file serving (including precompressed-sibling negotiation), on-the-fly compression of the dynamic
grid responses, and malformed input that must return a JSON error with the connection left usable. All write tests
create their own rows and clean them up via try/finally.

Most tests drive the API through the w2ui client fixture; tests that exercise auth, static files, POST, or raw/prepared
requests use requests directly.
"""

import datetime
import gzip
import json
import re
from typing import Any

import brotli
import pytest
import requests

from .conftest import W2UIClient


@pytest.fixture(scope='session')
def admin_index_refs(admin_url: str) -> list[str]:
    """The href/src asset references parsed from the admin dashboard index.html."""
    index = requests.get(f'{admin_url}/admin/', auth=('admin', 'admin'), verify=False, timeout=10)
    assert index.status_code == 200
    refs = re.findall(r'(?:href|src)="([^"]+)"', index.text)
    assert refs, 'index.html references no assets'
    return refs


# auth

def test_no_auth_returns_401(admin_url: str) -> None:
    response = requests.get(f'{admin_url}/admin/w2ui',
                            params={'cmd': 'get-records', 'data': 'domains'},
                            verify=False, timeout=10)
    assert response.status_code == 401


def test_wrong_auth_returns_401(admin_url: str) -> None:
    response = requests.get(f'{admin_url}/admin/w2ui',
                            params={'cmd': 'get-records', 'data': 'domains'},
                            auth=('admin', 'wrongpassword'),
                            verify=False, timeout=10)
    assert response.status_code == 401


def test_write_ops_require_auth(admin_url: str) -> None:
    for params in (
            {'cmd': 'save-record', 'data': 'domains', 'recid': '0', 'record[domain]': 'x'},
            {'cmd': 'delete-records', 'data': 'domains', 'selected': '1'},
    ):
        r = requests.get(f'{admin_url}/admin/w2ui', params=params, verify=False, timeout=10)
        assert r.status_code == 401


# get-records (list)

def test_list_domains(w2ui: W2UIClient) -> None:
    response = w2ui.request('get-records', 'domains')
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'success'
    assert data['total'] == 3
    assert {r['domain'] for r in data['records']} == {'example.com', 'example.net', 'example.org'}


def test_list_monitors(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'monitors').json()
    assert data['status'] == 'success'
    assert data['total'] == 7
    assert all({'recid', 'monitor', 'monitor_json'}.issubset(r.keys()) for r in data['records'])
    assert 'No check' in {r['monitor'] for r in data['records']}


def test_list_routings(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'routings').json()
    assert data['status'] == 'success'
    assert data['total'] == 3
    assert all({'recid', 'policy', 'policy_json'}.issubset(r.keys()) for r in data['records'])
    assert {'Round robin', 'Weighted random', 'Sticky hash'} == {r['policy'] for r in data['records']}


def test_list_views(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'views').json()
    assert data['status'] == 'success'
    assert all({'recid', 'view', 'rule'}.issubset(r.keys()) for r in data['records'])
    assert {'Public', 'Private'}.issubset({r['view'] for r in data['records']})


def test_list_types(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'types').json()
    assert data['status'] == 'success'
    assert {'A', 'AAAA', 'NS', 'SOA', 'MX', 'TXT', 'CNAME', 'SRV', 'SVCB', 'HTTPS', 'CAA'}.issubset(
        {r['name_type'] for r in data['records']})


def test_list_users(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'users').json()
    assert data['status'] == 'success'
    assert any(r['user'] == 'admin' for r in data['records'])
    assert all(r['password'] == '*****' for r in data['records'])


def test_list_records(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'records').json()
    assert data['status'] == 'success'
    assert data['total'] > 0
    assert all({'recid', 'domain', 'name', 'name_type', 'content', 'monitor', 'view',
                'ttl', 'disabled', 'policy', 'weight'}.issubset(r.keys())
               for r in data['records'])


def test_list_status(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'status').json()
    assert data['status'] == 'success'
    assert data['total'] > 0
    assert all({'domain', 'name', 'content', 'monitor', 'status', 'view'}.issubset(r.keys())
               for r in data['records'])
    assert all(r['status'] == 'On' for r in data['records'])


def test_status_is_read_only(w2ui: W2UIClient) -> None:
    # the status grid mirrors records but rejects writes before any field matters
    save = w2ui.save('status', content='192.0.2.1').json()
    assert save['status'] == 'error'
    assert 'read-only' in save['message']
    assert w2ui.delete('status', 1).json()['status'] == 'error'


def test_unknown_command_and_data_return_error(w2ui: W2UIClient) -> None:
    for cmd, data, extra in (
            ('no-such-command', 'domains', {}),
            ('get-records', 'no_such_table', {}),
            ('get-items', 'no_such', {'field': 'id'}),
    ):
        assert w2ui.request(cmd, data, **extra).json()['status'] == 'error'


# get-record (single)

def test_get_record_domain_and_monitor(w2ui: W2UIClient) -> None:
    assert w2ui.record('domains', 1) == {'recid': 1, 'domain': 'example.com', 'description': 'IANA example zone (.com)'}

    record = w2ui.record('monitors', 1)
    assert record['recid'] == 1
    assert record['monitor'] == 'No check'
    assert 'monitor_json' in record


# get-items (dropdown)

def test_get_items_monitors_and_views(w2ui: W2UIClient) -> None:
    items = w2ui.items('monitors', 'monitor')
    assert 'No check' in items
    assert all(isinstance(i, str) for i in items)

    assert {'Public', 'Private'}.issubset(set(w2ui.items('views', 'view')))


# pagination

def test_pagination(w2ui: W2UIClient) -> None:
    # limit+offset: total reflects all rows, records is sliced
    data = w2ui.request('get-records', 'domains', limit='2', offset='0').json()
    assert data['total'] == 3
    assert len(data['records']) == 2

    # offset shifts the page
    page0 = w2ui.records('domains', limit='1', offset='0')
    page1 = w2ui.records('domains', limit='1', offset='1')
    assert page0[0]['domain'] != page1[0]['domain']

    # max caps the result
    assert len(w2ui.records('domains', max='1')) == 1


# search

def test_search(w2ui: W2UIClient) -> None:
    # exact match → 1 result
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'is', 'search[0][value]': 'example.com'}).json()
    assert data['total'] == 1
    assert data['records'][0]['domain'] == 'example.com'

    # begins-with → all 3 match
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'begins', 'search[0][value]': 'example.'}).json()
    assert data['total'] == 3

    # no match → empty
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'is', 'search[0][value]': 'no.such.domain'}).json()
    assert data['total'] == 0
    assert data['records'] == []


# sort

def test_sort(w2ui: W2UIClient) -> None:
    asc = [r['domain'] for r in w2ui.records(
        'domains', **{'sort[0][field]': 'domain', 'sort[0][direction]': 'asc'})]
    assert asc == sorted(asc)

    desc = [r['domain'] for r in w2ui.records(
        'domains', **{'sort[0][field]': 'domain', 'sort[0][direction]': 'desc'})]
    assert desc == sorted(desc, reverse=True)


def test_multi_column_sort_primary_dominates(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    # w2ui sends its sort array primary-first; a stable sort must apply the keys
    # least-significant-first so the primary column dominates. Three records share one
    # name with weights chosen so the correct (weight primary, content secondary) order
    # differs from a content-dominant one: correct weights are [1, 1, 2], the wrong order
    # gives [2, 1, 1].
    name = 'multisort'
    specs = (('192.0.2.50', 2), ('192.0.2.51', 1), ('192.0.2.52', 1))
    for content, weight in specs:
        r = w2ui.save('records', **{**base_record, 'name': name, 'content': content, 'weight': weight})
        assert r.json()['status'] == 'success', content
        recid = w2ui.find_recid('records', name=name, content=content)
        assert recid is not None, content
        cleanup.append(('records', recid))

    rows = w2ui.records('records', searchLogic='AND',
                        **{'search[0][field]': 'name', 'search[0][type]': 'text',
                           'search[0][operator]': 'is', 'search[0][value]': name,
                           'sort[0][field]': 'weight', 'sort[0][direction]': 'asc',
                           'sort[1][field]': 'content', 'sort[1][direction]': 'asc'})

    assert [r['weight'] for r in rows] == [1, 1, 2], [(r['content'], r['weight']) for r in rows]
    assert [r['content'] for r in rows] == ['192.0.2.51', '192.0.2.52', '192.0.2.50']


# CRUD lifecycle

def test_crud_domain_lifecycle(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    assert w2ui.save('domains', domain='crud.integration.test').json()['status'] == 'success'

    recid = w2ui.find_recid('domains', domain='crud.integration.test')
    assert recid is not None
    cleanup.append(('domains', recid))

    assert w2ui.record('domains', recid) == {'recid': recid, 'domain': 'crud.integration.test', 'description': ''}

    assert w2ui.save('domains', recid=recid, domain='crud.updated.test',
                     description='updated by test').json()['status'] == 'success'
    assert w2ui.record('domains', recid) == {'recid': recid, 'domain': 'crud.updated.test',
                                             'description': 'updated by test'}


def test_crud_monitor_lifecycle(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    monitor_json = ('{"type": "exec", "args": ["/bin/true"], '
                    '"interval": 10, "timeout": 5, "fall": 3, "rise": 5}')
    assert w2ui.save('monitors', monitor='CRUD Test Monitor',
                     monitor_json=monitor_json).json()['status'] == 'success'

    recid = w2ui.find_recid('monitors', monitor='CRUD Test Monitor')
    assert recid is not None
    cleanup.append(('monitors', recid))

    record = w2ui.record('monitors', recid)
    assert record['monitor'] == 'CRUD Test Monitor'
    assert record['recid'] == recid


def test_save_monitor_invalid_json_rejected(w2ui: W2UIClient) -> None:
    # monitor_json is valid JSON (passes the DB CHECK) but a bad check spec; the admin builds the check before
    # writing and rejects it with the parameter error, so no row is created.
    for monitor_json in ('{"type": "tcp", "ip": "${content}"}',  # missing required port (timing fields default)
                         '{"type": "tcp", "ip": "nope", "port": 80, '
                         '"interval": 10, "timeout": 1, "fall": 2, "rise": 2}',  # bad ip
                         '{"type": "no-such-check"}'):  # unknown type
        r = w2ui.save('monitors', monitor='Invalid Monitor', monitor_json=monitor_json)
        assert r.json()['status'] == 'error'
        assert w2ui.find_recid('monitors', monitor='Invalid Monitor') is None


def test_crud_view_lifecycle(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    assert w2ui.save('views', view='CRUD Test View',
                     rule='192.168.99.0/24').json()['status'] == 'success'

    recid = w2ui.find_recid('views', view='CRUD Test View')
    assert recid is not None
    cleanup.append(('views', recid))

    record = w2ui.record('views', recid)
    assert record['view'] == 'CRUD Test View'
    assert record['rule'] == '192.168.99.0/24'
    assert record['recid'] == recid


def test_save_view_invalid_rule_rejected(w2ui: W2UIClient) -> None:
    # The rule is the space-separated CIDR list the DNS read path matches against; the admin parses every
    # token before writing and rejects a malformed one, so no row is created.
    for rule in ('not-a-cidr',  # not a CIDR at all
                 '10.0.0.0/8 garbage',  # second token invalid
                 '10.0.0.0/99',  # prefix out of range
                 ''):  # empty rule matches nothing
        r = w2ui.save('views', view='Invalid View', rule=rule)
        assert r.json()['status'] == 'error'
        assert w2ui.find_recid('views', view='Invalid View') is None


# POST

def test_post_list_domains(admin_url: str) -> None:
    response = requests.post(f'{admin_url}/admin/w2ui',
                             data={'cmd': 'get-records', 'data': 'domains'},
                             auth=('admin', 'admin'), verify=False, timeout=10)
    assert response.status_code == 200
    data = response.json()
    assert data['status'] == 'success'
    assert data['total'] == 3


# static file serving

def test_admin_index_and_redirect(admin_url: str) -> None:
    # /admin/ serves the HTML dashboard
    r = requests.get(f'{admin_url}/admin/',
                     auth=('admin', 'admin'), verify=False, timeout=10)
    assert r.status_code == 200
    assert 'text/html' in r.headers.get('Content-Type', '')
    assert '<html' in r.text.lower()

    # /admin (no trailing slash) redirects to /admin/
    r = requests.get(f'{admin_url}/admin',
                     auth=('admin', 'admin'), verify=False,
                     allow_redirects=False, timeout=10)
    assert r.status_code == 301
    assert r.headers['Location'].endswith('/admin/')


def test_admin_nonexistent_file_returns_404(admin_url: str) -> None:
    r = requests.get(f'{admin_url}/admin/no-such-file.txt',
                     auth=('admin', 'admin'), verify=False, timeout=10)
    assert r.status_code == 404


def test_admin_index_asset_references_resolve(admin_url: str, admin_index_refs: list[str]) -> None:
    # every asset referenced by the dashboard page is served with a matching content type
    content_types = {'.css': 'css', '.js': 'javascript', '.svg': 'svg'}
    for ref in admin_index_refs:
        r = requests.get(f'{admin_url}/admin/{ref}',
                         auth=('admin', 'admin'), verify=False, timeout=10)
        assert r.status_code == 200, ref
        suffix = ref[ref.rfind('.'):]
        assert content_types[suffix] in r.headers.get('Content-Type', ''), ref


# search OR logic

def test_search_or_logic(w2ui: W2UIClient) -> None:
    # OR: domain=example.com OR domain=example.net → 2 results
    data = w2ui.request('get-records', 'domains', searchLogic='OR',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'is', 'search[0][value]': 'example.com',
                           'search[1][field]': 'domain', 'search[1][type]': 'text',
                           'search[1][operator]': 'is', 'search[1][value]': 'example.net'}).json()
    assert data['total'] == 2
    assert {rec['domain'] for rec in data['records']} == {'example.com', 'example.net'}


# multi-ID delete

def test_delete_multiple_records(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    for domain in ('multi-delete-a.test', 'multi-delete-b.test'):
        assert w2ui.save('domains', domain=domain).json()['status'] == 'success'

    recids = [rec['recid'] for rec in w2ui.records('domains')
              if rec['domain'] in ('multi-delete-a.test', 'multi-delete-b.test')]
    assert len(recids) == 2
    cleanup.extend(('domains', recid) for recid in recids)

    assert w2ui.request('delete-records', 'domains',
                        **{'selected[]': recids}).json()['status'] == 'success'

    remaining = {rec['domain'] for rec in w2ui.records('domains')}
    assert 'multi-delete-a.test' not in remaining
    assert 'multi-delete-b.test' not in remaining


# POST write operations

def test_post_save_and_delete(admin_url: str) -> None:
    recid = None
    try:
        r = requests.post(f'{admin_url}/admin/w2ui',
                          data={'cmd': 'save-record', 'data': 'domains',
                                'recid': '0', 'record[domain]': 'post-write.test'},
                          auth=('admin', 'admin'), verify=False, timeout=10)
        assert r.json()['status'] == 'success'

        r = requests.get(f'{admin_url}/admin/w2ui',
                         params={'cmd': 'get-records', 'data': 'domains'},
                         auth=('admin', 'admin'), verify=False, timeout=10)
        recid = next((rec['recid'] for rec in r.json()['records']
                      if rec['domain'] == 'post-write.test'), None)
        assert recid is not None

    finally:
        if recid is not None:
            requests.post(f'{admin_url}/admin/w2ui',
                          data={'cmd': 'delete-records', 'data': 'domains',
                                'selected': recid},
                          auth=('admin', 'admin'), verify=False, timeout=10)


# status style field

def test_status_style_field(w2ui: W2UIClient) -> None:
    records = w2ui.records('status')
    assert all(r['style'] == 'color: green' for r in records if r['status'] == 'On')


# users CRUD

def test_crud_user_lifecycle(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    assert w2ui.save('users', user='crudtestuser', name='CRUD Test User',
                     password='testpassword123').json()['status'] == 'success'

    recid = w2ui.find_recid('users', user='crudtestuser')
    assert recid is not None
    cleanup.append(('users', recid))

    record = w2ui.record('users', recid)
    assert record['user'] == 'crudtestuser'
    assert record['name'] == 'CRUD Test User'
    assert record['password'] == '*****'


# regression: editing a user without changing the password must not reset it to the mask

def test_edit_user_keeps_password(admin_url: str, w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    user, password = 'keeppassuser', 'originalpassword123'
    assert w2ui.save('users', user=user, name='Keep Pass User',
                     password=password).json()['status'] == 'success'

    recid = w2ui.find_recid('users', user=user)
    assert recid is not None
    cleanup.append(('users', recid))

    # edit only the name, re-submitting the masked password as the admin UI does
    assert w2ui.save('users', recid=recid, user=user, name='Renamed User',
                     password='*****').json()['status'] == 'success'
    assert w2ui.record('users', recid)['name'] == 'Renamed User'

    # the original password must still authenticate; the mask must not
    ok = requests.get(f'{admin_url}/admin/w2ui',
                      params={'cmd': 'get-records', 'data': 'domains'},
                      auth=(user, password), verify=False, timeout=10)
    assert ok.status_code == 200
    assert ok.json()['status'] == 'success'

    masked = requests.get(f'{admin_url}/admin/w2ui',
                          params={'cmd': 'get-records', 'data': 'domains'},
                          auth=(user, '*****'), verify=False, timeout=10)
    assert masked.status_code == 401


# audit trail

def test_audit_logs_save_and_delete(w2ui: W2UIClient) -> None:
    domain = 'audit-trail.test'
    assert w2ui.save('domains', domain=domain).json()['status'] == 'success'
    recid = w2ui.find_recid('domains', domain=domain)
    assert recid is not None
    assert w2ui.delete('domains', recid).json()['status'] == 'success'

    rows = [row for row in w2ui.records('audit') if row['data'] == 'domains'
            and domain in (row['record_before'] or '') + (row['record_after'] or '')]
    by_action = {row['action']: row for row in rows}
    assert set(by_action) == {'save', 'delete'}
    assert all(row['user'] == 'admin' for row in rows)
    assert by_action['save']['record_id'] == recid        # the insert is audited under the id it generated
    assert by_action['delete']['record_id'] == recid
    assert by_action['delete']['recid'] > by_action['save']['recid']  # newest-first: delete logged after save

    # an insert has no before state, a delete no after state, and each stored side is the row itself
    assert by_action['save']['record_before'] is None
    assert json.loads(by_action['save']['record_after']) == {'recid': recid, 'domain': domain, 'description': ''}
    assert json.loads(by_action['delete']['record_before'])['domain'] == domain
    assert by_action['delete']['record_after'] is None


def test_audit_update_records_both_states(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    domain = 'audit-update.test'
    assert w2ui.save('domains', domain=domain, description='before').json()['status'] == 'success'
    recid = w2ui.find_recid('domains', domain=domain)
    assert recid is not None
    cleanup.append(('domains', recid))
    assert w2ui.save('domains', recid=recid, domain=domain, description='after').json()['status'] == 'success'

    rows = [row for row in w2ui.records('audit') if row['data'] == 'domains'
            and row['action'] == 'save' and row['record_id'] == recid]
    updates = [row for row in rows if row['record_before'] is not None]
    assert len(updates) == 1
    assert json.loads(updates[0]['record_before'])['description'] == 'before'
    assert json.loads(updates[0]['record_after'])['description'] == 'after'


def test_audit_record_insert_is_logged_under_the_id_it_generated(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    # a record insert writes the rrset first, and that statement pins the session id to the rrset
    # (ON DUPLICATE KEY UPDATE `id` = LAST_INSERT_ID(`id`)). The record id the trail resolves must be the one the
    # second statement generated: an rrset id is a valid records id too, so a mix-up would audit another record.
    content = '192.0.2.235'
    assert w2ui.save('records', **base_record, name='audit-insert', content=content).json()['status'] == 'success'
    recid = w2ui.find_recid('records', name='audit-insert', content=content)
    assert recid is not None
    cleanup.append(('records', recid))

    rows = [row for row in w2ui.records('audit') if row['data'] == 'records'
            and row['action'] == 'save' and row['record_id'] == recid]
    assert len(rows) == 1
    assert rows[0]['record_before'] is None  # an insert has no before state
    payload = json.loads(rows[0]['record_after'])
    assert (payload['recid'], payload['content'], payload['name']) == (recid, content, 'audit-insert')


def test_audit_delete_records_captures_joined_content(w2ui: W2UIClient, base_record: dict[str, Any]) -> None:
    # deleting a record audits the joined row, not just the records-table fields.
    content = '192.0.2.234'
    assert w2ui.save('records', **base_record, name='audit-record', content=content).json()['status'] == 'success'
    recid = w2ui.find_recid('records', name='audit-record', content=content)
    assert recid is not None
    assert w2ui.delete('records', recid).json()['status'] == 'success'  # the delete GCs the rrset too

    rows = [row for row in w2ui.records('audit') if row['data'] == 'records'
            and row['action'] == 'delete' and row['record_id'] == recid]
    assert len(rows) == 1
    payload = json.loads(rows[0]['record_before'])
    assert payload['content'] == content
    assert (payload['domain'], payload['name'], payload['name_type']) == ('example.com', 'audit-record', 'A')
    assert (payload['monitor'], payload['view'], payload['policy']) == ('No check', 'Public', 'Round robin')


def test_audit_delete_ignores_an_unparsable_id(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    # the delete targets the ids the pre-read resolved, so an id the database would coerce numerically
    # ('<recid>abc' compares equal to <recid>) cannot remove a row behind the audit trail's back
    domain = 'audit-coerce.test'
    assert w2ui.save('domains', domain=domain).json()['status'] == 'success'
    recid = w2ui.find_recid('domains', domain=domain)
    assert recid is not None
    cleanup.append(('domains', recid))

    assert w2ui.delete('domains', f'{recid}abc').json()['status'] == 'error'
    assert w2ui.find_recid('domains', domain=domain) == recid


def test_audit_is_read_only(w2ui: W2UIClient) -> None:
    # write is rejected before any field matters, so post a minimal record
    save = w2ui.save('audit', user='x', record_after='{}').json()
    assert save['status'] == 'error'
    assert 'read-only' in save['message']
    assert w2ui.delete('audit', 1).json()['status'] == 'error'


def test_audit_masks_user_password(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    assert w2ui.save('users', user='audituser', name='Audit User',
                     password='sup3rsecret').json()['status'] == 'success'
    recid = w2ui.find_recid('users', user='audituser')
    assert recid is not None
    cleanup.append(('users', recid))

    rows = [row for row in w2ui.records('audit') if row['data'] == 'users'
            and json.loads(row['record_after'] or '{}').get('user') == 'audituser']
    assert rows
    # the stored row is read back for the trail, so neither the posted password nor its hash can appear
    assert json.loads(rows[0]['record_after'])['password'] == '*****'
    assert 'sup3rsecret' not in rows[0]['record_after'] and '$6$' not in rows[0]['record_after']


def test_audit_search_by_logged_date(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    # the frontend posts a date search as an ISO yyyy-mm-dd string; the server compares it against the
    # real datetime column as a half-open day range, so a write logged today is matched by today's date
    domain = 'audit-date.test'
    assert w2ui.save('domains', domain=domain).json()['status'] == 'success'
    recid = w2ui.find_recid('domains', domain=domain)
    assert recid is not None
    cleanup.append(('domains', recid))

    today = datetime.date.today().isoformat()
    data = w2ui.request('get-records', 'audit', searchLogic='AND',
                        **{'search[0][field]': 'logged', 'search[0][type]': 'date',
                           'search[0][operator]': 'is', 'search[0][value]': today}).json()
    assert data['status'] == 'success'
    assert data['total'] >= 1
    assert all(row['logged'].startswith(today) for row in data['records'])
    assert any(row['data'] == 'domains' and row['record_id'] == recid for row in data['records'])


def test_audit_paging_reports_total(w2ui: W2UIClient) -> None:
    # the audit grid pages through the base limit/offset pipeline; a full page pays the second COUNT(*)
    # for the total, which must cover at least the rows returned
    page = w2ui.request('get-records', 'audit', limit=2, offset=0).json()
    assert page['status'] == 'success'
    assert len(page['records']) <= 2
    assert page['total'] >= len(page['records'])


# types CRUD

def test_crud_type_lifecycle(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    # the types form sends record[recid] (the type value) alongside the w2ui
    # recid, so go through request() rather than save(**fields)
    type_value = 999
    cleanup.append(('types', type_value))
    r = w2ui.request('save-record', 'types', recid='0',
                     **{'record[description]': 'CRUD Test Type',
                        'record[name_type]': 'CRUDTEST',
                        'record[recid]': str(type_value)})
    assert r.json()['status'] == 'success'

    created = next((t for t in w2ui.records('types') if t['name_type'] == 'CRUDTEST'), None)
    assert created is not None
    assert created['description'] == 'CRUD Test Type'
    assert created['recid'] == type_value

    assert w2ui.record('types', type_value)['name_type'] == 'CRUDTEST'


# save-record tolerates an extra unexpected field (save reads only the writable columns)

def test_save_types_with_extra_field(w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    type_value = 998
    cleanup.append(('types', type_value))
    r = w2ui.request('save-record', 'types', recid='0',
                     **{'record[description]': 'Extra Field Type',
                        'record[name_type]': 'EXTRATEST',
                        'record[recid]': str(type_value),
                        'record[unexpected]': 'ignored'})
    # the extra field is ignored: save binds only the table's writable columns
    assert r.json()['status'] == 'success'

    created = next((t for t in w2ui.records('types') if t['name_type'] == 'EXTRATEST'), None)
    assert created is not None
    assert created['recid'] == type_value


# the same relative name + type can be added under two different domains: the rrset upsert keys on
# (domain_id, name, type_value), so each save resolves to its own domain's rrset.

def test_save_record_same_name_two_domains(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    name = 'shared-name'

    for i, domain in enumerate(('example.com', 'example.net')):
        r = w2ui.save('records', **{**base_record, 'domain': domain,
                                    'name': name, 'content': f'192.0.2.{i + 1}'})
        assert r.json()['status'] == 'success', domain

        recid = w2ui.find_recid('records', domain=domain, name=name)
        assert recid is not None, domain
        cleanup.append(('records', recid))


# record update: save-record with an existing recid drives the rrset upsert + record UPDATE transaction

def test_crud_record_update_lifecycle(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """Record-level and rrset-level edits of one record all persist under the same recid.

    Records.save always runs the rrset upsert first and the record write second, linked via LAST_INSERT_ID().
    A content-only edit makes the rrset upsert a complete no-op, so it must still pin the existing rrset id for
    the record UPDATE (name and ttl surviving proves rrset_id stayed correct); ttl and routing-policy edits are
    the mirror cases where only the rrset row changes; a rename re-keys the record onto a freshly upserted rrset
    (the emptied old rrset is garbage-collected by trigger).
    """
    fields = {**base_record, 'name': 'crud-update'}
    assert w2ui.save('records', **fields, content='192.0.2.60').json()['status'] == 'success'

    recid = w2ui.find_recid('records', name='crud-update', content='192.0.2.60')
    assert recid is not None
    cleanup.append(('records', recid))

    # content-only edit (record level): the rrset upsert changes nothing
    assert w2ui.save('records', recid=recid, **fields, content='192.0.2.61').json()['status'] == 'success'
    record = w2ui.record('records', recid)
    assert record['content'] == '192.0.2.61'
    assert (record['domain'], record['name'], record['ttl']) == ('example.com', 'crud-update', 300)

    # ttl-only edit (rrset level): the rrset row changes, the record UPDATE is the no-op side
    fields['ttl'] = 600
    assert w2ui.save('records', recid=recid, **fields, content='192.0.2.61').json()['status'] == 'success'
    record = w2ui.record('records', recid)
    assert (record['ttl'], record['content']) == (600, '192.0.2.61')

    # routing-policy edit (rrset level): the upsert's ON DUPLICATE KEY UPDATE re-resolves routing_id
    fields['policy'] = 'Weighted random'
    assert w2ui.save('records', recid=recid, **fields, content='192.0.2.61').json()['status'] == 'success'
    record = w2ui.record('records', recid)
    assert (record['policy'], record['ttl']) == ('Weighted random', 600)

    # rename (rrset re-key): the upsert creates the new rrset and the record moves onto it
    fields['name'] = 'crud-renamed'
    assert w2ui.save('records', recid=recid, **fields, content='192.0.2.61').json()['status'] == 'success'
    record = w2ui.record('records', recid)
    assert (record['name'], record['content']) == ('crud-renamed', '192.0.2.61')
    # the record moved, not copied: the old name is gone from the grid
    assert w2ui.find_recid('records', name='crud-update') is None


# delete non-existent record

def test_delete_nonexistent_returns_error(w2ui: W2UIClient) -> None:
    r = w2ui.delete('domains', '99999')
    assert r.status_code == 200
    assert r.json()['status'] == 'error'


# search with unknown operator

def test_search_unknown_operator_returns_all(w2ui: W2UIClient) -> None:
    # unknown operator is silently skipped; with AND logic all records are returned
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'no-such-operator',
                           'search[0][value]': 'example.com'}).json()
    assert data['status'] == 'success'
    assert data['total'] == 3


# search on a field that is not a column

def test_search_unknown_field_matches_nothing(w2ui: W2UIClient) -> None:
    # a search field absent from every row matches no record rather than erroring the request
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'no_such_field', 'search[0][type]': 'text',
                           'search[0][operator]': 'is', 'search[0][value]': 'example.com'}).json()
    assert data['status'] == 'success'
    assert data['total'] == 0


# POST with malformed Content-Length

def test_post_malformed_content_length_returns_400(admin_url: str) -> None:
    req = requests.Request('POST', f'{admin_url}/admin/w2ui',
                           data={'cmd': 'get-records', 'data': 'domains'},
                           auth=('admin', 'admin'))
    prepared = req.prepare()
    prepared.headers['Content-Length'] = 'not-a-number'
    session = requests.Session()
    session.verify = False
    response = session.send(prepared, timeout=10)
    assert response.status_code == 400


# regression: a POST with no Content-Length header must not crash

def test_post_without_content_length_returns_error(admin_url: str) -> None:
    # absent Content-Length is treated as an empty body
    req = requests.Request('POST', f'{admin_url}/admin/w2ui', auth=('admin', 'admin'))
    prepared = req.prepare()
    prepared.headers.pop('Content-Length', None)
    session = requests.Session()
    session.verify = False
    response = session.send(prepared, timeout=10)
    assert response.status_code == 200
    assert response.json()['status'] == 'error'


# regression: sort on a field that does not exist must not crash

def test_sort_unknown_field_does_not_crash(w2ui: W2UIClient) -> None:
    r = w2ui.request('get-records', 'domains',
                     **{'sort[0][field]': 'no_such_field', 'sort[0][direction]': 'asc'})
    assert r.status_code == 200
    data = r.json()
    assert data['status'] == 'success'
    assert data['total'] == 3


# regression: malformed query bracket must not crash the connection

def test_malformed_query_bracket_does_not_crash(admin_url: str, w2ui: W2UIClient) -> None:
    # 'record[' has an opening bracket with no closing ']' -> QueryParserError
    url = f'{admin_url}/admin/w2ui?cmd=get-records&data=domains&record[=x'
    r = requests.get(url, auth=('admin', 'admin'), verify=False, timeout=10)
    assert r.status_code == 200
    # query is discarded -> cmd becomes None -> error status, connection survives
    assert r.json()['status'] == 'error'

    # connection still usable for a normal follow-up request
    assert w2ui.request('get-records', 'domains').json()['status'] == 'success'


# regression: empty POST body must not crash

def test_empty_post_body_returns_error(admin_url: str) -> None:
    r = requests.post(f'{admin_url}/admin/w2ui',
                      auth=('admin', 'admin'), verify=False, timeout=10)
    assert r.status_code == 200
    assert r.json()['status'] == 'error'


# regression: a password containing a colon must authenticate (split once)

def test_colon_password_authenticates(admin_url: str, w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    user, password = 'colonuser', 'pa:ss:word'
    assert w2ui.save('users', user=user, name='Colon User',
                     password=password).json()['status'] == 'success'

    recid = w2ui.find_recid('users', user=user)
    assert recid is not None
    cleanup.append(('users', recid))

    # authenticate with the colon password -> server splits on first colon only
    ok = requests.get(f'{admin_url}/admin/w2ui',
                      params={'cmd': 'get-records', 'data': 'domains'},
                      auth=(user, password), verify=False, timeout=10)
    assert ok.status_code == 200
    assert ok.json()['status'] == 'success'

    # wrong password for the same user is still rejected
    bad = requests.get(f'{admin_url}/admin/w2ui',
                       params={'cmd': 'get-records', 'data': 'domains'},
                       auth=(user, 'pa:ss'), verify=False, timeout=10)
    assert bad.status_code == 401


# search: integer operators in / not in / between

def test_search_int_operators(w2ui: W2UIClient) -> None:
    recids = sorted(r['recid'] for r in w2ui.records('domains'))
    assert len(recids) == 3

    # in: recid in [recids[0]] -> exactly one row
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'recid', 'search[0][type]': 'int',
                           'search[0][operator]': 'in', 'search[0][value]': recids[0]}).json()
    assert data['total'] == 1
    assert data['records'][0]['recid'] == recids[0]

    # not in: recid not in [recids[0]] -> the other two rows
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'recid', 'search[0][type]': 'int',
                           'search[0][operator]': 'not in', 'search[0][value]': recids[0]}).json()
    assert data['total'] == 2
    assert recids[0] not in {rec['recid'] for rec in data['records']}

    # between: min..max recid spans all three rows
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'recid', 'search[0][type]': 'int',
                           'search[0][operator]': 'between',
                           'search[0][value][]': [recids[0], recids[-1]]}).json()
    assert data['total'] == 3


# search: integer field with a non-numeric value is silently excluded

def test_search_int_non_numeric_value_excluded(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'recid', 'search[0][type]': 'int',
                           'search[0][operator]': 'is', 'search[0][value]': 'not-a-number'}).json()
    assert data['status'] == 'success'
    assert data['total'] == 0


# search: text operators contains / ends

def test_search_text_operators(w2ui: W2UIClient) -> None:
    # contains: every seed domain contains 'ample'
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'contains', 'search[0][value]': 'ample'}).json()
    assert data['total'] == 3

    # ends: only example.net ends with '.net'
    data = w2ui.request('get-records', 'domains', searchLogic='AND',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'ends', 'search[0][value]': '.net'}).json()
    assert data['total'] == 1
    assert data['records'][0]['domain'] == 'example.net'


# get-items with an absent field yields an empty item list

def test_get_items_absent_field_returns_empty(w2ui: W2UIClient) -> None:
    assert w2ui.items('domains', 'no_such_field') == []


# pagination offset past the end of the list returns no rows

def test_pagination_offset_past_end(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'domains', limit='10', offset='100').json()
    assert data['total'] == 3
    assert data['records'] == []


# static asset serving from the admin docroot

def test_admin_static_js_asset(admin_url: str) -> None:
    r = requests.get(f'{admin_url}/admin/src/jquery-3.7.1.min.js',
                     auth=('admin', 'admin'), verify=False, timeout=10)
    assert r.status_code == 200
    assert 'javascript' in r.headers.get('Content-Type', '').lower()
    assert len(r.content) > 0


# precompressed sibling negotiation (built into the image by the in-tree backend)

_COMPRESSIBLE_SUFFIXES = ('.js', '.css', '.svg', '.html')


def _get_asset(admin_url: str, path: str, accept_encoding: str) -> requests.Response:
    # stream=True leaves the body undecoded so Content-Encoding and the raw bytes can be inspected.
    return requests.get(f'{admin_url}{path}', headers={'Accept-Encoding': accept_encoding},
                        auth=('admin', 'admin'), verify=False, timeout=10, stream=True)


def test_admin_static_assets_precompressed(admin_url: str, admin_index_refs: list[str]) -> None:
    # The shipped image must carry the identity, .gz and .br of every compressible asset - verify all three
    # for each asset, that each sibling is smaller than the identity, and that it decodes back to it.
    assets = [f'/admin/{ref}' for ref in admin_index_refs if ref.endswith(_COMPRESSIBLE_SUFFIXES)]
    assert assets, 'index.html references no compressible assets'
    for path in assets:
        identity = _get_asset(admin_url, path, 'identity')
        assert identity.status_code == 200, path
        assert 'Content-Encoding' not in identity.headers, path
        # The identity representation is still negotiable, so a shared cache must key it on Accept-Encoding too.
        assert identity.headers.get('Vary') == 'Accept-Encoding', path
        body = identity.content

        brotli_resp = _get_asset(admin_url, path, 'br')
        assert brotli_resp.status_code == 200, path
        assert brotli_resp.headers['Content-Encoding'] == 'br', path
        assert brotli_resp.headers.get('Vary') == 'Accept-Encoding', path
        br_body = brotli_resp.raw.read()
        assert len(br_body) < len(body), path
        assert brotli.decompress(br_body) == body, path

        gzip_resp = _get_asset(admin_url, path, 'gzip')
        assert gzip_resp.status_code == 200, path
        assert gzip_resp.headers['Content-Encoding'] == 'gzip', path
        gz_body = gzip_resp.raw.read()
        assert len(gz_body) < len(body), path
        assert gzip.decompress(gz_body) == body, path


def test_admin_directory_index_precompressed(admin_url: str) -> None:
    # The dashboard is reached at /admin/ (a directory), so the resolved index.html must negotiate too.
    identity = _get_asset(admin_url, '/admin/', 'identity')
    assert identity.status_code == 200
    assert 'Content-Encoding' not in identity.headers
    body = identity.content

    brotli_resp = _get_asset(admin_url, '/admin/', 'br')
    assert brotli_resp.status_code == 200
    assert brotli_resp.headers['Content-Encoding'] == 'br'
    assert 'html' in brotli_resp.headers['Content-Type']
    assert brotli.decompress(brotli_resp.raw.read()) == body


# dynamic w2ui response compression (on the fly at request time, distinct from the static precompressed siblings)

def _w2ui_get(admin_url: str, accept_encoding: str, **params: Any) -> requests.Response:
    # stream=True leaves the body undecoded so Content-Encoding and the raw bytes can be inspected.
    return requests.get(f'{admin_url}/admin/w2ui', params=params, headers={'Accept-Encoding': accept_encoding},
                        auth=('admin', 'admin'), verify=False, timeout=10, stream=True)


def test_w2ui_grid_compressed_on_the_fly(admin_url: str) -> None:
    # A full grid page trips the size gate, so it is compressed per request. Unlike the static path the dynamic
    # response carries Cache-Control: no-store and no Vary; both codings decode back to the identity body.
    identity = _w2ui_get(admin_url, 'identity', cmd='get-records', data='records')
    assert identity.status_code == 200
    body = identity.content
    assert len(body) >= 256
    assert 'Content-Encoding' not in identity.headers
    assert identity.headers.get('Cache-Control') == 'no-store'
    assert 'Vary' not in identity.headers

    brotli_resp = _w2ui_get(admin_url, 'br, gzip', cmd='get-records', data='records')
    assert brotli_resp.headers['Content-Encoding'] == 'br'  # br preferred over gzip
    assert brotli_resp.headers.get('Cache-Control') == 'no-store'
    assert 'Vary' not in brotli_resp.headers
    br_body = brotli_resp.raw.read()
    assert len(br_body) < len(body)
    assert brotli.decompress(br_body) == body

    gzip_resp = _w2ui_get(admin_url, 'gzip', cmd='get-records', data='records')
    assert gzip_resp.headers['Content-Encoding'] == 'gzip'
    gz_body = gzip_resp.raw.read()
    assert len(gz_body) < len(body)
    assert gzip.decompress(gz_body) == body


def test_w2ui_q0_refusal_falls_back_to_gzip(admin_url: str) -> None:
    # 'br;q=0' explicitly refuses brotli, so the grid is served gzip instead.
    resp = _w2ui_get(admin_url, 'br;q=0, gzip', cmd='get-records', data='records')
    assert resp.headers['Content-Encoding'] == 'gzip'


def test_w2ui_small_response_not_compressed(admin_url: str) -> None:
    # A tiny reply (below the size gate) is sent identity even when br is accepted, but still no-store.
    resp = _w2ui_get(admin_url, 'br', cmd='no-such-command', data='domains')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'error'
    assert len(resp.content) < 256
    assert 'Content-Encoding' not in resp.headers
    assert resp.headers.get('Cache-Control') == 'no-store'
    assert 'Vary' not in resp.headers


# regression: malformed get-record / save-record must return an error, not crash

def test_malformed_admin_requests_return_error(w2ui: W2UIClient) -> None:
    """Each malformed request comes back as a JSON error with status 200.

    The connection must stay usable for the next request.
    """
    cases: tuple[tuple[str, str, dict[str, str]], ...] = (
        # get-record for a recid that does not exist
        ('get-record', 'domains', {'recid': '99999'}),
        # get-record with no recid at all
        ('get-record', 'domains', {}),
        # save-record missing the required 'domain' field
        ('save-record', 'domains', {'recid': '0'}),
    )
    for cmd, data, extra in cases:
        r = w2ui.request(cmd, data, **extra)
        assert r.status_code == 200, (cmd, data, extra)
        assert r.json()['status'] == 'error', (cmd, data, extra)

        # connection still usable for a normal follow-up request
        assert w2ui.request('get-records', 'domains').json()['status'] == 'success'


# referential integrity: name resolution and foreign keys

def test_save_record_unknown_reference_rejected(
        w2ui: W2UIClient, base_record: dict[str, Any]) -> None:
    """An unknown domain/view/monitor name is rejected as a JSON error (HTTP 200) with no row inserted.

    Records.save resolves those names to ids via scalar subqueries; an unknown name yields NULL against a NOT NULL
    foreign-key column.
    """
    cases = (
        ('domain', {'domain': 'no-such-domain.invalid'}),
        ('view', {'view': 'No Such View'}),
        ('monitor', {'monitor': 'No Such Monitor'}),
    )
    for i, (field, override) in enumerate(cases):
        name, content = f'orphan-{field}.example.com', f'192.0.2.{120 + i}'
        r = w2ui.save('records', name=name, content=content, **{**base_record, **override})
        assert r.status_code == 200, field
        assert r.json()['status'] == 'error', f'{field}: expected error, got {r.json()}'
        assert w2ui.find_recid('records', name=name, content=content) is None, \
            f'{field}: a row was inserted despite the unknown reference'


def test_delete_rows_referenced_by_record_rejected(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A domain / view / monitor still referenced by a record cannot be deleted.

    The records foreign keys have no ON DELETE CASCADE, so each delete returns a JSON error and the row survives.
    The audit row is written in the same transaction, so a rejected delete leaves no trail either.
    Teardown removes the record first (LIFO), after which the parent rows delete cleanly.
    """
    w2ui.save('domains', domain='ref-int.example')
    w2ui.save('views', view='Ref Int View', rule='198.51.100.0/24')
    w2ui.save('monitors', monitor='Ref Int Monitor',
              monitor_json='{"type": "exec", "args": ["/bin/true", "ref-int"], '
                           '"interval": 3600, "timeout": 1, "fall": 1, "rise": 1}')

    dom = w2ui.find_recid('domains', domain='ref-int.example')
    view = w2ui.find_recid('views', view='Ref Int View')
    mon = w2ui.find_recid('monitors', monitor='Ref Int Monitor')
    assert dom and view and mon
    cleanup += [('domains', dom), ('views', view), ('monitors', mon)]

    r = w2ui.save('records', name='r.ref-int.example', content='192.0.2.124',
                  **{**base_record, 'domain': 'ref-int.example', 'view': 'Ref Int View',
                     'monitor': 'Ref Int Monitor'})
    assert r.json()['status'] == 'success', r.json()
    rec = w2ui.find_recid('records', name='r.ref-int.example', content='192.0.2.124')
    assert rec is not None
    cleanup.append(('records', rec))

    for data, recid in (('domains', dom), ('views', view), ('monitors', mon)):
        r = w2ui.delete(data, recid)
        assert r.json()['status'] == 'error', f'{data}: delete of a referenced row should fail: {r.json()}'
        assert not [row for row in w2ui.records('audit')
                    if row['data'] == data and row['action'] == 'delete' and row['record_id'] == recid], \
            f'{data}: the rolled-back delete left an audit row'

    assert w2ui.find_recid('domains', domain='ref-int.example') is not None
    assert w2ui.find_recid('views', view='Ref Int View') is not None
    assert w2ui.find_recid('monitors', monitor='Ref Int Monitor') is not None


# special-character round-trip through the admin API (GET query and POST body decode paths)

_SPECIAL = 'a&b=c% d#e+f"g/h:i?j'


def test_record_content_special_chars_round_trip_get_and_post(
        w2ui: W2UIClient, base_record: dict[str, Any], cleanup: list[tuple[str, int]]) -> None:
    """A TXT content with '&', '=', '%', space, '#', '+', '"', '/', ':' and '?' survives both decode paths intact.

    The two paths are the GET query string (_urlsplit keeps it percent-encoded for parse_qsl) and the POST body. A
    regression in either decoder (e.g. truncating at an encoded '&', or '+' decoding to a space) shows up as a content
    mismatch.
    """
    txt = {**base_record, 'name_type': 'TXT', 'ttl': 3600}

    for label, save in (('get', w2ui.save), ('post', w2ui.save_post)):
        name = f'special-{label}.example.com'
        r = save('records', name=name, content=_SPECIAL, **txt)
        assert r.json()['status'] == 'success', f'{label}: {r.json()}'
        recid = w2ui.find_recid('records', name=name, content=_SPECIAL)
        assert recid is not None, f'{label}: content did not round-trip (truncated or altered on save)'
        cleanup.append(('records', recid))
        assert w2ui.record('records', recid)['content'] == _SPECIAL, f'{label}: get-record content mismatch'


def test_monitor_json_special_chars_round_trip(
        w2ui: W2UIClient, cleanup: list[tuple[str, int]]) -> None:
    """monitor_json containing a shell redirect ('1>&2') round-trips unchanged.

    This is the field that first exposed the query-decode truncation bug, so it gets a dedicated guard via both GET
    and POST.
    """
    for label, save in (('get', w2ui.save), ('post', w2ui.save_post)):
        monitor = f'Special JSON {label}'
        monitor_json = ('{"type": "exec", "args": ["/bin/sh", "-c", "echo ' + label + ' 1>&2"], '
                        '"interval": 3600, "timeout": 1, "fall": 1, "rise": 1}')
        r = save('monitors', monitor=monitor, monitor_json=monitor_json)
        assert r.json()['status'] == 'success', f'{label}: {r.json()}'
        recid = w2ui.find_recid('monitors', monitor=monitor)
        assert recid is not None, f'{label}: monitor not found after save'
        cleanup.append(('monitors', recid))
        assert w2ui.record('monitors', recid)['monitor_json'] == monitor_json, \
            f'{label}: monitor_json mangled in round-trip'


# SQL-side paging: search combined with limit/offset

def test_search_with_paging(w2ui: W2UIClient) -> None:
    # total reflects all matching rows, records is the requested page
    data = w2ui.request('get-records', 'domains', searchLogic='AND', limit='2', offset='0',
                        **{'search[0][field]': 'domain', 'search[0][type]': 'text',
                           'search[0][operator]': 'begins', 'search[0][value]': 'example.'}).json()
    assert data['total'] == 3
    assert len(data['records']) == 2


# the status grid's On/Off value is computed in SQL and searchable like any other column

def test_status_search_by_status_value(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-records', 'status', searchLogic='AND',
                        **{'search[0][field]': 'status', 'search[0][type]': 'text',
                           'search[0][operator]': 'is', 'search[0][value]': 'On'}).json()
    assert data['status'] == 'success'
    assert data['total'] > 0
    assert all(r['status'] == 'On' for r in data['records'])

    # the seed data has no disabled or down records (as test_list_status asserts)
    data = w2ui.request('get-records', 'status', searchLogic='AND',
                        **{'search[0][field]': 'status', 'search[0][type]': 'text',
                           'search[0][operator]': 'is', 'search[0][value]': 'Off'}).json()
    assert data['total'] == 0


# the status grid exposes recid and pages by it, so its order matches the records grid

def test_status_and_records_share_order(w2ui: W2UIClient) -> None:
    records = w2ui.records('records', limit='100', offset='0')
    status = w2ui.records('status', limit='100', offset='0')
    assert [r['recid'] for r in records] == [s['recid'] for s in status]


# sort desc combined with offset pages the sorted set

def test_sort_desc_with_offset(w2ui: W2UIClient) -> None:
    all_desc = [r['domain'] for r in w2ui.records(
        'domains', **{'sort[0][field]': 'domain', 'sort[0][direction]': 'desc'})]
    assert all_desc == sorted(all_desc, reverse=True)

    page = w2ui.request('get-records', 'domains', limit='2', offset='1',
                        **{'sort[0][field]': 'domain', 'sort[0][direction]': 'desc'}).json()
    assert page['total'] == 3
    assert [r['domain'] for r in page['records']] == all_desc[1:3]


# get-items: the w2ui combo posts a flat search=<typed text> plus max, which must not become a clause

def test_get_items_flat_search_and_max(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-items', 'domains', field='domain', search='exa', max='250').json()
    assert data['status'] == 'success'
    assert 'example.com' in data['items']


# get-items on the status grid keeps working (down_ids is a required parameter of the status read)

def test_get_items_status(w2ui: W2UIClient) -> None:
    data = w2ui.request('get-items', 'status', field='domain').json()
    assert data['status'] == 'success'
    assert 'example.com' in data['items']
