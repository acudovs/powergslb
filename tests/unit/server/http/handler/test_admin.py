# pylint: disable=missing-function-docstring, protected-access, attribute-defined-outside-init

"""Tests for AdminRequestHandler.

Basic-auth checking, the WWW-Authenticate response, the route (auth wall, w2ui CRUD, static fall-through),
query/body parsing, the get/save/delete command handlers, the PageRequest plumbing into the SQL read
pipeline, the status style annotation, and content() dispatch including its error wrapping. The handler is
built with __new__ to skip the socket-opening __init__; the database is a fake exposing the token-dispatched
get_data/save_data/delete_data entry points, rejecting unregistered tokens like the real mixin.
"""

import base64
import email.utils
import gzip
import io
import json
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

import brotli
import netaddr
import pytest

from powergslb.database import PageRequest
from powergslb.monitor.status import StatusRegistry
from powergslb.server.http.handler import admin as admin_module
from powergslb.server.http.handler.admin import AdminRequestHandler

from .conftest import Recorder, build_recorder


class _FakeDatabase:
    """Record calls and return configured results for the token-dispatched CRUD entry points."""

    _tables = {'domains', 'monitors', 'records', 'routings', 'status', 'types', 'users', 'views'}

    def __init__(self) -> None:
        self.get_result: list[dict[str, Any]] = []
        self.get_total: int | None = None  # when set, overrides the returned total
        self.save_count = 1
        self.delete_count = 1
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.raise_on_get = False

    def _record(self, name: str, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((name, args, kwargs))

    def _check(self, data: Any) -> None:
        """Reject an unregistered token with ValueError, mirroring the real mixin's _table."""
        if data not in self._tables:
            raise ValueError(f"'{data}' not implemented")

    def _get(self) -> tuple[list[dict[str, Any]], int]:
        if self.raise_on_get:
            raise RuntimeError('db boom')
        total = self.get_total if self.get_total is not None else len(self.get_result)
        return self.get_result, total

    def get_data(self, data: str, recid: int = 0, page: PageRequest | None = None,
                 **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
        self._check(data)
        self._record('get_data', data, recid, page, **kwargs)
        return self._get()

    def save_data(self, data: str, recid: int, **kwargs: Any) -> int:
        self._check(data)
        self._record('save_data', data, recid, **kwargs)
        return self.save_count

    def delete_data(self, data: str, ids: list[Any]) -> int:
        self._check(data)
        self._record('delete_data', data, tuple(ids))
        return self.delete_count


def _handler(query: Any = None, body: bytes | None = None,
             status_registry: Any = None) -> AdminRequestHandler:
    """Build a handler without running __init__ (which would open a socket and call handle())."""
    handler = AdminRequestHandler.__new__(AdminRequestHandler)
    handler.body = body
    handler.database = _FakeDatabase()  # type: ignore[assignment]
    handler.dirs = ['admin', 'w2ui']
    handler.headers = {}  # type: ignore[assignment]
    handler.path = '/admin/w2ui'
    handler.remote_ip = netaddr.IPAddress('203.0.113.1')
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


# --- static asset encoding negotiation --------------------------------------------------------------------------


def _static_recorder(directory: Path, path: str, headers: dict[str, str] | None = None) -> _Recorder:
    handler = _recorder(headers)
    handler.directory = str(directory)  # type: ignore[attr-defined]  # set per request by stdlib __init__
    handler.path = path
    return handler


def _make_asset(directory: Path, name: str = 'app.js', body: bytes = b'console.log(42);') -> None:
    (directory / name).write_bytes(body)
    (directory / f'{name}.gz').write_bytes(gzip.compress(body, mtime=0))
    (directory / f'{name}.br').write_bytes(brotli.compress(body))


def test_send_head_serves_brotli_when_accepted(tmp_path: Path) -> None:
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br, gzip'})
    f = handler.send_head()
    try:
        headers = dict(handler.headers_sent)
        assert handler.responses_sent == [HTTPStatus.OK]
        assert headers['Content-Encoding'] == 'br'
        assert headers['Vary'] == 'Accept-Encoding'
        assert 'javascript' in headers['Content-Type']
        assert headers['Content-Length'] == str((tmp_path / 'app.js.br').stat().st_size)
        assert f is not None and f.read() == (tmp_path / 'app.js.br').read_bytes()
    finally:
        if f is not None:
            f.close()


def test_send_head_serves_gzip_when_only_gzip_offered(tmp_path: Path) -> None:
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'gzip'})
    f = handler.send_head()
    try:
        headers = dict(handler.headers_sent)
        assert headers['Content-Encoding'] == 'gzip'
        assert f is not None and f.read() == (tmp_path / 'app.js.gz').read_bytes()
    finally:
        if f is not None:
            f.close()


def test_send_head_drops_q0_token(tmp_path: Path) -> None:
    # 'br;q=0' explicitly refuses brotli, so the gzip sibling is served instead.
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br;q=0, gzip'})
    f = handler.send_head()
    try:
        assert dict(handler.headers_sent)['Content-Encoding'] == 'gzip'
    finally:
        if f is not None:
            f.close()


def test_send_head_identity_without_accept_encoding(tmp_path: Path) -> None:
    # The identity file is served here too, so it carries Vary even though it has no Content-Encoding.
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {})
    f = handler.send_head()
    try:
        headers = dict(handler.headers_sent)
        assert 'Content-Encoding' not in headers
        assert headers['Vary'] == 'Accept-Encoding'
        assert f is not None and f.read() == b'console.log(42);'
    finally:
        if f is not None:
            f.close()


def test_send_head_identity_for_unsupported_encoding(tmp_path: Path) -> None:
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'deflate'})
    f = handler.send_head()
    try:
        headers = dict(handler.headers_sent)
        assert 'Content-Encoding' not in headers
        assert headers['Vary'] == 'Accept-Encoding'
    finally:
        if f is not None:
            f.close()


def test_send_head_identity_when_sibling_missing(tmp_path: Path) -> None:
    # An asset with no precompressed twin is served as identity, still carrying Vary, even when br is accepted.
    (tmp_path / 'plain.js').write_bytes(b'x')
    handler = _static_recorder(tmp_path, '/plain.js', {'Accept-Encoding': 'br'})
    f = handler.send_head()
    try:
        headers = dict(handler.headers_sent)
        assert 'Content-Encoding' not in headers
        assert headers['Vary'] == 'Accept-Encoding'
    finally:
        if f is not None:
            f.close()


def test_send_head_not_modified_uses_encoded_mtime(tmp_path: Path) -> None:
    _make_asset(tmp_path)
    ims = email.utils.formatdate((tmp_path / 'app.js.br').stat().st_mtime, usegmt=True)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br', 'If-Modified-Since': ims})
    f = handler.send_head()
    assert f is None
    assert handler.responses_sent == [HTTPStatus.NOT_MODIFIED]


def test_head_reaches_the_same_negotiation(tmp_path: Path) -> None:
    # do_HEAD routes through send_head too, so a HEAD negotiates and closes the encoded file with no body.
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br'})
    handler.command = 'HEAD'
    SimpleHTTPRequestHandler.do_HEAD(handler)
    assert dict(handler.headers_sent)['Content-Encoding'] == 'br'
    assert isinstance(handler.wfile, io.BytesIO)
    assert handler.wfile.getvalue() == b''  # HEAD writes no body


def test_send_head_malformed_qvalue_refuses_that_encoding(tmp_path: Path) -> None:
    # An unparseable q-value counts as a refusal (nginx-style), so 'br;q=bad' drops br and gzip is served.
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br;q=bad, gzip'})
    f = handler.send_head()
    try:
        assert dict(handler.headers_sent)['Content-Encoding'] == 'gzip'
    finally:
        if f is not None:
            f.close()


def test_send_head_ignores_malformed_if_modified_since(tmp_path: Path) -> None:
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br', 'If-Modified-Since': 'garbage'})
    f = handler.send_head()
    try:
        assert handler.responses_sent == [HTTPStatus.OK]
        assert dict(handler.headers_sent)['Content-Encoding'] == 'br'
    finally:
        if f is not None:
            f.close()


def test_send_head_naive_if_modified_since_is_treated_as_utc(tmp_path: Path) -> None:
    # An obsolete tz-less If-Modified-Since is read as UTC; a 1990 date is stale, so the body is still sent.
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js',
                               {'Accept-Encoding': 'br', 'If-Modified-Since': 'Mon, 01 Jan 1990 00:00:00'})
    f = handler.send_head()
    try:
        assert handler.responses_sent == [HTTPStatus.OK]
    finally:
        if f is not None:
            f.close()


def test_send_static_head_missing_file_is_404(tmp_path: Path) -> None:
    # Direct call exercises the TOCTOU guard: send_head normally checks isfile first.
    (tmp_path / 'app.js').write_bytes(b'x')
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br'})
    result = handler._send_static_head(str(tmp_path / 'app.js'), str(tmp_path / 'missing.js.br'), 'br')
    assert result is None
    assert handler.errors_sent == [HTTPStatus.NOT_FOUND]


def test_send_static_head_closes_file_on_write_error(tmp_path: Path, monkeypatch: Any) -> None:
    # A failure after the sibling is opened closes the file and re-raises, so the descriptor never leaks.
    _make_asset(tmp_path)
    handler = _static_recorder(tmp_path, '/app.js', {'Accept-Encoding': 'br'})

    def boom(*_args: Any) -> None:
        raise RuntimeError('header failure')

    monkeypatch.setattr(handler, 'send_header', boom)
    with pytest.raises(RuntimeError, match='header failure'):
        handler._send_static_head(str(tmp_path / 'app.js'), str(tmp_path / 'app.js.br'), 'br')


def test_send_head_serves_compressed_index_for_directory(tmp_path: Path) -> None:
    # A browser hits /admin/ (a directory), so the resolved index.html must negotiate like any other asset.
    _make_asset(tmp_path, 'index.html', b'<!doctype html><title>admin</title>')
    handler = _static_recorder(tmp_path, '/', {'Accept-Encoding': 'br'})
    f = handler.send_head()
    try:
        headers = dict(handler.headers_sent)
        assert headers['Content-Encoding'] == 'br'
        assert 'html' in headers['Content-Type']
        assert f is not None and f.read() == (tmp_path / 'index.html.br').read_bytes()
    finally:
        if f is not None:
            f.close()


def test_static_file_path_directory_without_trailing_slash_defers(tmp_path: Path) -> None:
    # A directory URL missing the trailing slash defers to stdlib's 301 redirect, not compression negotiation.
    (tmp_path / 'sub').mkdir()
    handler = _static_recorder(tmp_path, '/sub')
    assert handler._static_file_path() is None


def test_static_file_path_directory_without_index_defers(tmp_path: Path) -> None:
    # A directory with no index page defers to stdlib's directory listing.
    (tmp_path / 'sub').mkdir()
    handler = _static_recorder(tmp_path, '/sub/')
    assert handler._static_file_path() is None


def test_send_head_directory_defers_to_stdlib(tmp_path: Path) -> None:
    # An unresolved path (directory without a trailing slash) hands off to stdlib's 301 redirect, unnegotiated.
    (tmp_path / 'sub').mkdir()
    handler = _static_recorder(tmp_path, '/sub', {'Accept-Encoding': 'br'})
    assert handler.send_head() is None
    assert handler.responses_sent == [HTTPStatus.MOVED_PERMANENTLY]
    assert ('Vary', 'Accept-Encoding') not in handler.headers_sent


# --- dynamic response encoding ----------------------------------------------------------------------------------

# A body at or above _min_encode_size so the encoding gate opens.
_BIG_JSON = json.dumps({'records': [{'id': i, 'name': f'record-{i}'} for i in range(50)]}, separators=(',', ':'))


def test_send_content_brotli_when_accepted() -> None:
    assert len(_BIG_JSON.encode()) >= AdminRequestHandler._min_encode_size
    handler = _recorder({'Accept-Encoding': 'br, gzip'})
    handler._send_content(_BIG_JSON)
    headers = dict(handler.headers_sent)
    assert headers['Content-Encoding'] == 'br'
    assert headers['Cache-Control'] == 'no-store'
    assert 'Vary' not in headers
    assert isinstance(handler.wfile, io.BytesIO)
    body = handler.wfile.getvalue()
    assert headers['Content-Length'] == str(len(body))
    assert brotli.decompress(body).decode() == _BIG_JSON


def test_send_content_gzip_when_only_gzip_offered() -> None:
    handler = _recorder({'Accept-Encoding': 'gzip'})
    handler._send_content(_BIG_JSON)
    headers = dict(handler.headers_sent)
    assert headers['Content-Encoding'] == 'gzip'
    assert headers['Cache-Control'] == 'no-store'
    assert 'Vary' not in headers
    assert isinstance(handler.wfile, io.BytesIO)
    assert gzip.decompress(handler.wfile.getvalue()).decode() == _BIG_JSON


def test_send_content_q0_refusal_falls_back_to_gzip() -> None:
    # 'br;q=0' refuses brotli, so gzip is the first accepted coding.
    handler = _recorder({'Accept-Encoding': 'br;q=0, gzip'})
    handler._send_content(_BIG_JSON)
    assert dict(handler.headers_sent)['Content-Encoding'] == 'gzip'


def test_send_content_identity_without_accept_encoding() -> None:
    # No acceptable coding: identity body, no Content-Encoding, but Cache-Control still applies and no Vary.
    handler = _recorder({})
    handler._send_content(_BIG_JSON)
    headers = dict(handler.headers_sent)
    assert 'Content-Encoding' not in headers
    assert headers['Cache-Control'] == 'no-store'
    assert 'Vary' not in headers
    assert isinstance(handler.wfile, io.BytesIO)
    assert handler.wfile.getvalue() == _BIG_JSON.encode()


def test_send_content_small_body_stays_identity() -> None:
    # Below the size gate the reply is sent identity even when br is accepted; Cache-Control still applies.
    small = json.dumps({'status': 'success'}, separators=(',', ':'))
    assert len(small.encode()) < AdminRequestHandler._min_encode_size
    handler = _recorder({'Accept-Encoding': 'br, gzip'})
    handler._send_content(small)
    headers = dict(handler.headers_sent)
    assert 'Content-Encoding' not in headers
    assert headers['Cache-Control'] == 'no-store'
    assert isinstance(handler.wfile, io.BytesIO)
    assert handler.wfile.getvalue() == small.encode()


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
    assert handler.database.calls[-1] == ('delete_data', ('records', (1, 2)), {})  # type: ignore[attr-defined]


def test_delete_records_wraps_single_selected() -> None:
    handler = _handler()
    handler.query = {'data': 'records', 'selected': 7}
    handler._delete_records()
    assert handler.database.calls[-1] == ('delete_data', ('records', (7,)), {})  # type: ignore[attr-defined]


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


def test_get_items_passes_max_as_limit() -> None:
    handler = _handler()
    handler.database.get_result = [{'name': 'a'}]  # type: ignore[attr-defined]
    handler.query = {'data': 'records', 'field': 'name', 'max': '250'}
    assert handler._get_items() == {'status': 'success', 'items': ['a']}
    _, args, _ = handler.database.calls[-1]  # type: ignore[attr-defined]
    assert args == ('records', 0, PageRequest(limit=250))


def test_get_items_pushes_contains_clause() -> None:
    # the w2ui combo posts get-items as flat search=<typed text> plus max; it always becomes a server-side clause
    handler = _handler()
    handler.database.get_result = [{'domain': 'a'}]  # type: ignore[attr-defined]
    handler.query = {'data': 'domains', 'field': 'domain', 'search': 'typed', 'max': '250'}
    assert handler._get_items()['items'] == ['a']
    _, args, _ = handler.database.calls[-1]  # type: ignore[attr-defined]
    assert args == ('domains', 0, PageRequest(
        searches=({'field': 'domain', 'type': 'text', 'operator': 'contains', 'value': 'typed'},), limit=250))


def test_get_items_without_search_string_is_unfiltered() -> None:
    # a combo with no typed text (empty combo) still lists the capped page, no clause
    handler = _handler()
    handler.database.get_result = [{'domain': 'a'}]  # type: ignore[attr-defined]
    handler.query = {'data': 'domains', 'field': 'domain', 'max': '250'}
    assert handler._get_items()['items'] == ['a']
    _, args, _ = handler.database.calls[-1]  # type: ignore[attr-defined]
    assert args == ('domains', 0, PageRequest(limit=250))


def test_get_items_status_passes_snapshot() -> None:
    # the status token routes through get_data with the down-id snapshot as a keyword argument
    registry = StatusRegistry()
    registry.add(3)
    handler = _handler(status_registry=registry)
    handler.database.get_result = [{'domain': 'example.com'}]  # type: ignore[attr-defined]
    handler.query = {'data': 'status', 'field': 'domain'}
    assert handler._get_items() == {'status': 'success', 'items': ['example.com']}
    name, args, kwargs = handler.database.calls[-1]  # type: ignore[attr-defined]
    assert name == 'get_data'
    assert args == ('status', 0, PageRequest())
    assert kwargs == {'down_ids': [3]}


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


def test_get_record_status_passes_snapshot() -> None:
    # a single-row status read still needs the down ids, or a down record would report 'On'
    registry = StatusRegistry()
    registry.add(3)
    handler = _handler(status_registry=registry)
    handler.database.get_result = [{'recid': 3, 'status': 'Off'}]  # type: ignore[attr-defined]
    handler.query = {'data': 'status', 'recid': '3'}
    assert handler._get_record() == {'status': 'success', 'record': {'recid': 3, 'status': 'Off'}}
    name, args, kwargs = handler.database.calls[-1]  # type: ignore[attr-defined]
    assert name == 'get_data'
    assert args == ('status', 3, None)
    assert kwargs == {'down_ids': [3]}


# _get_records

def test_get_records_success() -> None:
    handler = _handler()
    handler.database.get_result = [{'recid': 1}, {'recid': 2}]  # type: ignore[attr-defined]
    handler.query = {'data': 'records'}
    result = handler._get_records()
    assert result['status'] == 'success'
    assert result['total'] == 2


def test_get_records_passes_page_request() -> None:
    handler = _handler()
    handler.query = {'data': 'records', 'limit': '5', 'offset': '10', 'searchLogic': 'OR',
                     'search': [{'field': 'domain'}], 'sort': [{'field': 'domain', 'direction': 'asc'}]}
    handler._get_records()
    name, args, _ = handler.database.calls[-1]  # type: ignore[attr-defined]
    assert name == 'get_data'
    assert args == ('records', 0, PageRequest(searches=({'field': 'domain'},), or_logic=True,
                                              sorts=({'field': 'domain', 'direction': 'asc'},), limit=5, offset=10))


def test_get_records_uses_returned_total() -> None:
    # total is the database's full match count, not the page length
    handler = _handler()
    handler.database.get_result = [{'recid': 1}]  # type: ignore[attr-defined]
    handler.database.get_total = 42  # type: ignore[attr-defined]
    handler.query = {'data': 'records', 'limit': '1', 'offset': '0'}
    result = handler._get_records()
    assert result['total'] == 42
    assert result['records'] == [{'recid': 1}]


def test_get_records_status_passes_snapshot_and_styles() -> None:
    registry = StatusRegistry()
    registry.add(5)
    handler = _handler(status_registry=registry)
    handler.database.get_result = [{'status': 'Off'}, {'status': 'On'}]  # type: ignore[attr-defined]
    handler.query = {'data': 'status'}
    result = handler._get_records()
    name, args, kwargs = handler.database.calls[-1]  # type: ignore[attr-defined]
    assert name == 'get_data'
    assert args == ('status', 0, PageRequest())
    assert kwargs == {'down_ids': [5]}
    assert result['records'][0]['style'] == 'color: red'
    assert result['records'][1]['style'] == 'color: green'


def test_get_records_not_implemented() -> None:
    handler = _handler()
    handler.query = {'data': 'bogus'}
    with pytest.raises(ValueError):
        handler._get_records()


def test_unregistered_token_never_reaches_a_database_attribute() -> None:
    # A 'data' token that happens to name a real database attribute must still be rejected by the token
    # registry, so the w2ui dispatch cannot reach an arbitrary method.
    handler = _handler()
    handler.database.get_secret = lambda: [{'leaked': 1}]  # type: ignore[attr-defined]
    handler.query = {'data': 'secret'}
    with pytest.raises(ValueError):
        handler._get_records()


# _save_record

def test_save_record_success() -> None:
    handler = _handler()
    handler.query = {'data': 'domains', 'recid': '0', 'record': {'domain': 'example.com'}}
    assert handler._save_record() == {'status': 'success'}
    expected_call = ('save_data', ('domains', 0), {'domain': 'example.com'})
    assert handler.database.calls[-1] == expected_call  # type: ignore[attr-defined]


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
    assert handler.database.calls[-1][0] == 'save_data'  # type: ignore[attr-defined]


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


def test_validate_record_routing_valid_noop() -> None:
    handler = _handler()
    handler._validate_record('routings', {'policy_json': '{"type": "round-robin"}'})  # no raise


def test_validate_record_routing_invalid_raises() -> None:
    handler = _handler()
    with pytest.raises(ValueError):
        handler._validate_record('routings', {'policy_json': '{"type": "nope"}'})  # unknown policy type


@pytest.mark.parametrize('policy_json', ['[]', '"round-robin"', 'not json'])
def test_validate_record_routing_non_object_raises(policy_json: str) -> None:
    # resolve rejects malformed or non-object JSON with a clean ValueError, not an AttributeError.
    handler = _handler()
    with pytest.raises(ValueError):
        handler._validate_record('routings', {'policy_json': policy_json})


@pytest.mark.parametrize('rule', ['10.0.0.0/8 192.0.2.0/24', '2001:db8::/32'])
def test_validate_record_view_valid_noop(rule: str) -> None:
    handler = _handler()
    handler._validate_record('views', {'rule': rule})  # no raise


@pytest.mark.parametrize('rule', ['not-a-cidr', '10.0.0.0/8 garbage', '10.0.0.0/99', '   ', ''])
def test_validate_record_view_invalid_raises(rule: str) -> None:
    handler = _handler()
    with pytest.raises(ValueError):
        handler._validate_record('views', {'rule': rule})


# _validate_record: geo tokens

@pytest.mark.parametrize('rule', ['country:US', 'continent:EU', '10.0.0.0/8 country:DE', 'continent:NA country:US'])
def test_validate_record_view_geo_valid(rule: str) -> None:
    # Geo tokens are accepted regardless of whether a GeoIP database is configured.
    handler = _handler()
    handler._validate_record('views', {'rule': rule})  # no raise


@pytest.mark.parametrize('rule', ['country:USA', 'continent:XX'])
def test_validate_record_view_malformed_geo_token_raises(rule: str) -> None:
    handler = _handler()
    with pytest.raises(ValueError, match='geo token invalid'):
        handler._validate_record('views', {'rule': rule})


def test_validate_record_view_bad_cidr_still_rejected_with_geo_token() -> None:
    handler = _handler()
    with pytest.raises(ValueError, match='CIDR invalid'):
        handler._validate_record('views', {'rule': 'country:US not-a-cidr'})


def test_save_record_view_invalid_raises_and_skips_db() -> None:
    handler = _handler()
    handler.query = {'data': 'views', 'recid': '0', 'record': {'view': 'v', 'rule': 'not-a-cidr'}}
    with pytest.raises(ValueError):
        handler._save_record()
    assert not handler.database.calls  # type: ignore[attr-defined]


# _style_status

def test_style_status_off_is_red() -> None:
    records: list[dict[str, Any]] = [{'status': 'Off'}, {'status': 'On'}]
    AdminRequestHandler._style_status(records)
    assert records[0]['style'] == 'color: red'
    assert records[1]['style'] == 'color: green'


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


def test_content_non_int_paging_is_error() -> None:
    # PageRequest.from_query raises ValueError, surfaced as a w2ui error reply
    handler = _handler(query='cmd=get-records&data=records&limit=x&offset=0')
    payload = json.loads(handler.content())
    assert payload['status'] == 'error'
