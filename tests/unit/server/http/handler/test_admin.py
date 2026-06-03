# pylint: disable=missing-function-docstring, protected-access, attribute-defined-outside-init

"""Tests for AdminRequestHandler.

Basic-auth checking, the WWW-Authenticate response, the route (auth wall, w2ui CRUD, static fall-through),
query/body parsing, the get/save/delete command handlers, w2ui search/sort/limit post-processing, the admin status
decoration, and content() dispatch including its error wrapping. The handler is built with __new__ to skip the
socket-opening __init__; the database is a fake exposing the get_/save_/delete_ methods the handler dispatches to by
name.
"""

import base64
import io
import json
from typing import Any

import pytest

from powergslb.monitor.status import StatusRegistry
from powergslb.server.http.handler import admin as admin_module
from powergslb.server.http.handler.admin import AdminRequestHandler

from .conftest import Recorder, build_recorder


class _FakeDatabase:
    """Record calls and return configured results for the dynamically-dispatched CRUD methods."""

    def __init__(self) -> None:
        self.get_result: list[dict[str, Any]] = []
        self.save_count = 1
        self.delete_count = 1
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.raise_on_get = False

    def _record(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))

    def get_records(self, recid: int = 0) -> list[dict[str, Any]]:
        self._record('get_records', recid)
        if self.raise_on_get:
            raise RuntimeError('db boom')
        return self.get_result

    def get_status(self) -> list[dict[str, Any]]:
        self._record('get_status')
        return self.get_result

    def save_domains(self, recid: int, **kwargs: Any) -> int:
        self._record('save_domains', recid, **kwargs)
        return self.save_count

    def save_monitors(self, recid: int, **kwargs: Any) -> int:
        self._record('save_monitors', recid, **kwargs)
        return self.save_count

    def save_views(self, recid: int, **kwargs: Any) -> int:
        self._record('save_views', recid, **kwargs)
        return self.save_count

    def delete_records(self, ids: list[Any]) -> int:
        self._record('delete_records', tuple(ids))
        return self.delete_count


def _handler(query: Any = None, body: bytes | None = None, status_registry: Any = None) -> AdminRequestHandler:
    """Build a handler without running __init__ (which would open a socket and call handle())."""
    handler = AdminRequestHandler.__new__(AdminRequestHandler)
    handler.body = body
    handler.database = _FakeDatabase()  # type: ignore[assignment]
    handler.dirs = ['admin', 'w2ui']
    handler.headers = {}  # type: ignore[assignment]
    handler.path = '/admin/w2ui'
    handler.remote_ip = '203.0.113.1'
    handler.query = query
    handler.status_registry = status_registry or StatusRegistry()
    return handler


# --- auth and routing -------------------------------------------------------------------------------------------


class _Recorder(Recorder, AdminRequestHandler):  # pylint: disable=too-many-ancestors
    """A handler built without __init__, with the response primitives and streams stubbed for inspection."""


def _recorder(headers: dict[str, str] | None = None) -> _Recorder:
    handler = build_recorder(_Recorder, headers)
    handler.command = 'GET'
    return handler


class _AuthDatabase:
    def __init__(self, ok: bool) -> None:
        self.ok = ok
        self.checked: tuple[str, str] | None = None

    def check_user(self, user: str, password: str) -> list[dict[str, Any]]:
        self.checked = (user, password)
        return [{'1': 1}] if self.ok else []


def _basic(user: str, password: str) -> str:
    token = base64.b64encode(f'{user}:{password}'.encode()).decode()
    return f'Basic {token}'


# _is_authorized

def test_authorized_with_valid_credentials() -> None:
    handler = _recorder({'Authorization': _basic('admin', 'secret')})
    handler.database = _AuthDatabase(ok=True)  # type: ignore[assignment]
    assert handler._is_authorized() is True
    assert handler.database.checked == ('admin', 'secret')  # type: ignore[attr-defined]


def test_unauthorized_with_wrong_credentials() -> None:
    handler = _recorder({'Authorization': _basic('admin', 'wrong')})
    handler.database = _AuthDatabase(ok=False)  # type: ignore[assignment]
    assert handler._is_authorized() is False


def test_unauthorized_without_header() -> None:
    handler = _recorder({})
    assert handler._is_authorized() is False


def test_unauthorized_non_basic_scheme() -> None:
    handler = _recorder({'Authorization': _basic('admin', 'secret').replace('Basic', 'Digest')})
    handler.database = _AuthDatabase(ok=True)  # type: ignore[assignment]
    assert handler._is_authorized() is False


def test_authorized_basic_scheme_is_case_insensitive() -> None:
    # RFC 7617 makes the auth scheme token case-insensitive; a 'basic' (lowercase) scheme must still authorize.
    handler = _recorder({'Authorization': _basic('admin', 'secret').replace('Basic', 'basic')})
    handler.database = _AuthDatabase(ok=True)  # type: ignore[assignment]
    assert handler._is_authorized() is True


def test_unauthorized_malformed_header() -> None:
    handler = _recorder({'Authorization': 'Basic not-valid-base64!!'})
    handler.database = _AuthDatabase(ok=True)  # type: ignore[assignment]
    assert handler._is_authorized() is False


# _send_authenticate

def test_send_authenticate_sets_www_authenticate() -> None:
    handler = _recorder()
    handler._send_authenticate()
    assert handler.responses_sent == [401]
    assert any(k == 'WWW-Authenticate' for k, _ in handler.headers_sent)
    assert isinstance(handler.wfile, io.BytesIO)
    assert handler.wfile.getvalue()  # body written


def test_send_authenticate_head_suppresses_body() -> None:
    # A HEAD challenge sends the same 401 and headers (including Content-Length) but no body, to stay keep-alive safe.
    handler = _recorder()
    handler.command = 'HEAD'
    handler._send_authenticate()
    assert handler.responses_sent == [401]
    assert any(k == 'WWW-Authenticate' for k, _ in handler.headers_sent)
    assert any(k == 'Content-Length' for k, _ in handler.headers_sent)
    assert isinstance(handler.wfile, io.BytesIO)
    assert handler.wfile.getvalue() == b''  # no body on HEAD


# _handle_route

def test_handle_route_unauthorized_sends_authenticate(monkeypatch: Any) -> None:
    handler = _recorder()
    monkeypatch.setattr(handler, '_is_authorized', lambda: False)
    sent: list[str] = []
    monkeypatch.setattr(handler, '_send_authenticate', lambda *a, **k: sent.append('auth'))
    handler.dirs = ['admin', 'w2ui']
    handler.command = 'GET'
    handler._handle_route()
    assert sent == ['auth']


def test_handle_route_w2ui_sends_content(monkeypatch: Any) -> None:
    handler = _recorder()
    monkeypatch.setattr(handler, '_is_authorized', lambda: True)
    monkeypatch.setattr(handler, 'content', lambda: 'body')
    sent: list[str] = []
    monkeypatch.setattr(handler, '_send_content', lambda content, **k: sent.append(content))
    handler.dirs = ['admin', 'w2ui']
    handler.command = 'POST'
    handler._handle_route()
    assert sent == ['body']


def test_handle_route_static_delegates_to_stdlib(monkeypatch: Any) -> None:
    # Static admin assets are served by SimpleHTTPRequestHandler.do_GET; the call must name that class
    # explicitly, since super().do_GET() would re-enter HTTPRequestHandler routing and recurse.
    handler = _recorder()
    monkeypatch.setattr(handler, '_is_authorized', lambda: True)
    served: list[str] = []
    monkeypatch.setattr(admin_module.SimpleHTTPRequestHandler, 'do_GET', lambda self: served.append(self.path))
    handler.dirs = ['admin', 'index.html']
    handler.path = '/admin/index.html'
    handler.command = 'GET'
    handler._handle_route()
    assert served == ['/admin/index.html']


def test_handle_route_static_post_is_404(monkeypatch: Any) -> None:
    # Static assets are GET/HEAD-only; a POST to a static path must not be served via do_GET.
    handler = _recorder()
    monkeypatch.setattr(handler, '_is_authorized', lambda: True)
    served: list[str] = []
    monkeypatch.setattr(admin_module.SimpleHTTPRequestHandler, 'do_GET', lambda self: served.append(self.path))
    handler.dirs = ['admin', 'index.html']
    handler.path = '/admin/index.html'
    handler.command = 'POST'
    handler._handle_route()
    assert not served
    assert handler.errors_sent == [404]


def test_handle_route_static_head_serves_headers(monkeypatch: Any) -> None:
    # A HEAD to a static asset is served by SimpleHTTPRequestHandler.do_HEAD (header-only), like do_GET for GET.
    handler = _recorder()
    monkeypatch.setattr(handler, '_is_authorized', lambda: True)
    headed: list[str] = []
    monkeypatch.setattr(admin_module.SimpleHTTPRequestHandler, 'do_HEAD', lambda self: headed.append(self.path))
    handler.dirs = ['admin', 'index.html']
    handler.path = '/admin/index.html'
    handler.command = 'HEAD'
    handler._handle_route()
    assert headed == ['/admin/index.html']
    assert not handler.errors_sent


def test_handle_route_w2ui_head_is_404(monkeypatch: Any) -> None:
    # The w2ui endpoint is a GET/POST command channel; a HEAD to it is 404, not a content response.
    handler = _recorder()
    monkeypatch.setattr(handler, '_is_authorized', lambda: True)
    sent: list[str] = []

    def fake_content() -> str:
        sent.append('content')
        return '{}'

    monkeypatch.setattr(handler, 'content', fake_content)
    handler.dirs = ['admin', 'w2ui']
    handler.command = 'HEAD'
    handler._handle_route()
    assert not sent
    assert handler.errors_sent == [404]


# --- w2ui CRUD --------------------------------------------------------------------------------------------------


# _parse_query

def test_parse_query_from_query_string() -> None:
    handler = _handler(query='cmd=get-records&data=records')
    handler._parse_query()
    assert handler.query == {'cmd': 'get-records', 'data': 'records'}


def test_parse_query_from_body_when_no_query() -> None:
    handler = _handler(query='', body=b'cmd=save-record&data=domains')
    handler._parse_query()
    assert handler.query == {'cmd': 'save-record', 'data': 'domains'}


def test_parse_query_empty_when_neither() -> None:
    handler = _handler(query='', body=None)
    handler._parse_query()
    assert handler.query == {}


def test_parse_query_malformed_becomes_empty() -> None:
    handler = _handler(query='[abc=v')  # QueryParserError -> {}
    handler._parse_query()
    assert handler.query == {}


# _delete_records

def test_delete_records_success() -> None:
    handler = _handler()
    handler.query = {'data': 'records', 'selected': [1, 2]}
    assert handler._delete_records() == {'status': 'success'}
    assert handler.database.calls[-1] == ('delete_records', ((1, 2),), {})  # type: ignore[attr-defined]


def test_delete_records_wraps_single_selected() -> None:
    handler = _handler()
    handler.query = {'data': 'records', 'selected': 7}
    handler._delete_records()
    assert handler.database.calls[-1] == ('delete_records', ((7,),), {})  # type: ignore[attr-defined]


def test_delete_records_not_implemented() -> None:
    handler = _handler()
    handler.query = {'data': 'bogus', 'selected': [1]}
    with pytest.raises(ValueError):
        handler._delete_records()


def test_delete_records_zero_count_is_error() -> None:
    handler = _handler()
    handler.database.delete_count = 0  # type: ignore[attr-defined]
    handler.query = {'data': 'records', 'selected': [1]}
    assert handler._delete_records() == {'status': 'error', 'message': 'records not deleted'}


# _get_items

def test_get_items_collects_non_null_field() -> None:
    handler = _handler()
    handler.database.get_result = [{'name': 'a'}, {'name': None}, {'other': 1}]  # type: ignore[attr-defined]
    handler.query = {'data': 'records', 'field': 'name'}
    assert handler._get_items() == {'status': 'success', 'items': ['a']}


def test_get_items_not_implemented() -> None:
    handler = _handler()
    handler.query = {'data': 'bogus', 'field': 'name'}
    with pytest.raises(ValueError):
        handler._get_items()


# _get_record

def test_get_record_returns_first_row() -> None:
    handler = _handler()
    handler.database.get_result = [{'recid': 3, 'domain': 'example.com'}]  # type: ignore[attr-defined]
    handler.query = {'data': 'records', 'recid': '3'}
    assert handler._get_record() == {'status': 'success', 'record': {'recid': 3, 'domain': 'example.com'}}


def test_get_record_not_implemented() -> None:
    handler = _handler()
    handler.query = {'data': 'bogus', 'recid': '1'}
    with pytest.raises(ValueError):
        handler._get_record()


def test_get_record_not_found() -> None:
    handler = _handler()
    handler.database.get_result = []  # type: ignore[attr-defined]
    handler.query = {'data': 'records', 'recid': '99'}
    content = handler._get_record()
    assert content['status'] == 'error'
    assert 'not found' in content['message']


# _get_records

def test_get_records_success() -> None:
    handler = _handler()
    handler.database.get_result = [{'recid': 1}, {'recid': 2}]  # type: ignore[attr-defined]
    handler.query = {'data': 'records'}
    result = handler._get_records()
    assert result['status'] == 'success'
    assert result['total'] == 2


def test_get_records_status_decorates() -> None:
    handler = _handler()
    handler.database.get_result = [{'id': 1, 'disabled': 0}]  # type: ignore[attr-defined]
    handler.query = {'data': 'status'}
    result = handler._get_records()
    assert result['records'][0]['status'] == 'On'


def test_get_records_not_implemented() -> None:
    handler = _handler()
    handler.query = {'data': 'bogus'}
    with pytest.raises(ValueError):
        handler._get_records()


def test_database_method_whitelist_blocks_non_table_attribute() -> None:
    # A 'data' token that resolves to a real database attribute but is not a whitelisted table must be rejected,
    # so the w2ui dispatch cannot reach an arbitrary method through getattr.
    handler = _handler()
    handler.database.get_secret = lambda: [{'leaked': 1}]  # type: ignore[attr-defined]
    with pytest.raises(ValueError):
        handler._database_method('get_', 'secret')
    handler.query = {'data': 'secret'}
    with pytest.raises(ValueError):
        handler._get_records()


def test_database_method_resolves_whitelisted_table() -> None:
    handler = _handler()
    assert handler._database_method('get_', 'records') == handler.database.get_records


# _limit_records

def test_limit_with_offset() -> None:
    handler = _handler()
    handler.query = {'limit': '2', 'offset': '1'}
    assert handler._limit_records([{'i': 0}, {'i': 1}, {'i': 2}, {'i': 3}]) == [{'i': 1}, {'i': 2}]


def test_limit_with_max() -> None:
    handler = _handler()
    handler.query = {'max': '2'}
    assert handler._limit_records([{'i': 0}, {'i': 1}, {'i': 2}]) == [{'i': 0}, {'i': 1}]


def test_limit_without_params_returns_all() -> None:
    handler = _handler()
    handler.query = {}
    records = [{'i': 0}]
    assert handler._limit_records(records) == records


# _save_record

def test_save_record_success() -> None:
    handler = _handler()
    handler.query = {'data': 'domains', 'recid': '0', 'record': {'domain': 'example.com'}}
    assert handler._save_record() == {'status': 'success'}
    assert handler.database.calls[-1] == ('save_domains', (0,), {'domain': 'example.com'})  # type: ignore[attr-defined]


def test_save_record_not_implemented() -> None:
    handler = _handler()
    handler.query = {'data': 'bogus', 'recid': '0', 'record': {}}
    with pytest.raises(ValueError):
        handler._save_record()


def test_save_record_zero_count_is_error() -> None:
    handler = _handler()
    handler.database.save_count = 0  # type: ignore[attr-defined]
    handler.query = {'data': 'domains', 'recid': '1', 'record': {'domain': 'x'}}
    assert handler._save_record() == {'status': 'error', 'message': 'record not changed'}


_VALID_MONITOR_JSON = ('{"type": "tcp", "ip": "${content}", "port": 80, '
                       '"interval": 10, "timeout": 1, "fall": 2, "rise": 2}')


def test_save_record_monitor_valid_is_saved() -> None:
    handler = _handler()
    handler.query = {'data': 'monitors', 'recid': '0',
                     'record': {'monitor': 'm', 'monitor_json': _VALID_MONITOR_JSON}}
    assert handler._save_record() == {'status': 'success'}
    assert handler.database.calls[-1][0] == 'save_monitors'  # type: ignore[attr-defined]


def test_save_record_monitor_invalid_raises_and_skips_db() -> None:
    handler = _handler()
    handler.query = {'data': 'monitors', 'recid': '0',
                     'record': {'monitor': 'm', 'monitor_json': '{"type": "tcp", "ip": "nope"}'}}
    with pytest.raises(ValueError):
        handler._save_record()
    assert not handler.database.calls  # type: ignore[attr-defined]


# _validate_record

def test_validate_record_monitor_valid_noop() -> None:
    handler = _handler()
    handler._validate_record('monitors', {'monitor_json': _VALID_MONITOR_JSON})  # no raise


def test_validate_record_monitor_invalid_raises() -> None:
    handler = _handler()
    with pytest.raises(ValueError):
        handler._validate_record('monitors', {'monitor_json': '{"type": "tcp"}'})  # missing ip/port


def test_validate_record_non_monitors_noop() -> None:
    handler = _handler()
    handler._validate_record('domains', {'domain': 'example.com'})  # no monitor_json needed, no raise


@pytest.mark.parametrize('rule', ['10.0.0.0/8 192.0.2.0/24', '2001:db8::/32'])
def test_validate_record_view_valid_noop(rule: str) -> None:
    handler = _handler()
    handler._validate_record('views', {'rule': rule})  # no raise


@pytest.mark.parametrize('rule', ['not-a-cidr', '10.0.0.0/8 garbage', '10.0.0.0/99', '   ', ''])
def test_validate_record_view_invalid_raises(rule: str) -> None:
    handler = _handler()
    with pytest.raises(ValueError):
        handler._validate_record('views', {'rule': rule})


def test_save_record_view_invalid_raises_and_skips_db() -> None:
    handler = _handler()
    handler.query = {'data': 'views', 'recid': '0', 'record': {'view': 'v', 'rule': 'not-a-cidr'}}
    with pytest.raises(ValueError):
        handler._save_record()
    assert not handler.database.calls  # type: ignore[attr-defined]


# _search_records

def _records() -> list[dict[str, Any]]:
    return [{'name': 'alpha', 'n': 1}, {'name': 'beta', 'n': 2}]


def test_search_absent_returns_all() -> None:
    handler = _handler()
    handler.query = {}
    records = _records()
    assert handler._search_records(records) == records


def test_search_and_filters() -> None:
    handler = _handler()
    handler.query = {'search': [{'type': 'text', 'operator': 'is', 'field': 'name', 'value': 'alpha'}],
                     'searchLogic': 'AND'}
    assert handler._search_records(_records()) == [{'name': 'alpha', 'n': 1}]


def test_search_or_unions() -> None:
    handler = _handler()
    handler.query = {'search': [{'type': 'text', 'operator': 'is', 'field': 'name', 'value': 'alpha'},
                                {'type': 'int', 'operator': 'is', 'field': 'n', 'value': 2}],
                     'searchLogic': 'OR'}
    result = handler._search_records(_records())
    assert {r['name'] for r in result} == {'alpha', 'beta'}


def test_search_or_preserves_input_order() -> None:
    handler = _handler()
    handler.query = {'search': [{'type': 'text', 'operator': 'is', 'field': 'name', 'value': 'alpha'},
                                {'type': 'int', 'operator': 'is', 'field': 'n', 'value': 2}],
                     'searchLogic': 'OR'}
    # both records match; the result keeps the order they were given in, not set-iteration order
    assert handler._search_records(_records()) == _records()


def test_search_missing_logic_defaults_to_and() -> None:
    handler = _handler()
    # No searchLogic sent: it must default to AND and return the matching subset, not an empty list.
    handler.query = {'search': [{'type': 'text', 'operator': 'is', 'field': 'name', 'value': 'alpha'}]}
    assert handler._search_records(_records()) == [{'name': 'alpha', 'n': 1}]


def test_search_unknown_operator_is_skipped() -> None:
    handler = _handler()
    handler.query = {'search': [{'type': 'text', 'operator': 'wat', 'field': 'name', 'value': 'x'}],
                     'searchLogic': 'AND'}
    # the unusable search is skipped, so AND keeps the full set
    assert handler._search_records(_records()) == _records()


def test_search_missing_field_is_ignored() -> None:
    handler = _handler()
    handler.query = {'search': [{'type': 'text', 'operator': 'is', 'field': 'absent', 'value': 'x'}],
                     'searchLogic': 'AND'}
    assert not handler._search_records(_records())


def test_search_between_malformed_value_is_no_match() -> None:
    handler = _handler()
    # 'between' expects a 2-element value; a scalar must count as no-match, not crash the whole request.
    handler.query = {'search': [{'type': 'int', 'operator': 'between', 'field': 'n', 'value': 5}],
                     'searchLogic': 'AND'}
    assert not handler._search_records(_records())


def test_search_int_over_none_field_is_no_match() -> None:
    handler = _handler()
    # int(None) raises TypeError; the record must be skipped, not abort the request.
    records = [{'n': None}]
    handler.query = {'search': [{'type': 'int', 'operator': 'is', 'field': 'n', 'value': 1}],
                     'searchLogic': 'AND'}
    assert not handler._search_records(records)


# _sort_records

def test_sort_ascending() -> None:
    handler = _handler()
    handler.query = {'sort': [{'field': 'n', 'direction': 'asc'}]}
    records = [{'n': 2}, {'n': 1}]
    handler._sort_records(records)
    assert [r['n'] for r in records] == [1, 2]


def test_sort_descending() -> None:
    handler = _handler()
    handler.query = {'sort': [{'field': 'n', 'direction': 'desc'}]}
    records = [{'n': 1}, {'n': 2}]
    handler._sort_records(records)
    assert [r['n'] for r in records] == [2, 1]


def test_sort_unknown_field_is_skipped() -> None:
    handler = _handler()
    handler.query = {'sort': [{'field': 'absent', 'direction': 'asc'}]}
    records = [{'n': 2}, {'n': 1}]
    handler._sort_records(records)
    assert [r['n'] for r in records] == [2, 1]  # unchanged


# _update_status

def test_update_status_off_when_disabled() -> None:
    handler = _handler()
    records = [{'id': 1, 'disabled': 1}]
    handler._update_status(records)
    assert records[0]['status'] == 'Off' and records[0]['style'] == 'color: red'
    assert 'id' not in records[0]


def test_update_status_off_when_down() -> None:
    registry = StatusRegistry()
    registry.add(5)
    handler = _handler(status_registry=registry)
    records = [{'id': 5, 'disabled': 0}]
    handler._update_status(records)
    assert records[0]['status'] == 'Off'


def test_update_status_on_when_healthy() -> None:
    handler = _handler()
    records = [{'id': 9, 'disabled': 0}]
    handler._update_status(records)
    assert records[0]['status'] == 'On' and records[0]['style'] == 'color: green'


# content dispatch

def test_content_unknown_command() -> None:
    handler = _handler(query='cmd=frobnicate')
    payload = json.loads(handler.content())
    assert payload['status'] == 'error'
    assert 'frobnicate' in payload['message']


def test_content_dispatches_known_command() -> None:
    handler = _handler(query='cmd=get-records&data=records')
    handler.database.get_result = [{'recid': 1}]  # type: ignore[attr-defined]
    payload = json.loads(handler.content())
    assert payload['status'] == 'success'
    assert payload['total'] == 1


def test_content_hides_internal_exception() -> None:
    handler = _handler(query='cmd=get-records&data=records')
    handler.database.raise_on_get = True  # type: ignore[attr-defined]
    payload = json.loads(handler.content())
    assert payload == {'status': 'error', 'message': 'internal error'}


def test_content_surfaces_validation_error() -> None:
    handler = _handler(query='cmd=get-records&data=bogus')
    payload = json.loads(handler.content())
    assert payload['status'] == 'error'
    assert "'bogus' not implemented" in payload['message']
