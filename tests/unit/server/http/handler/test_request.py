# pylint: disable=missing-function-docstring, protected-access, disable=attribute-defined-outside-init

"""Tests for the HTTPRequestHandler base plumbing.

Exercised through a minimal concrete subclass (the base is
abstract and cannot be instantiated). Covers handle() connection-lifetime error handling (a vanished client ends
the connection quietly; any other error produces a single 500 once a request is parsed, then closes the
connection; a 500 that itself fails on a just-left client must not escape handle()), body reading, the response
writer, the base remote-IP resolution (the PowerDNS header is ignored here), URL splitting, the GET/HEAD/POST
dispatch, and the route skeleton. Handlers are built with __new__ to skip the socket-opening __init__, with the
response primitives and I/O streams stubbed.
"""

import io
from typing import Any

import netaddr
import pytest

import powergslb.database
from powergslb.monitor.status import StatusRegistry
from powergslb.server.http.handler import request as request_module
from powergslb.server.http.handler.request import HTTPRequestHandler

from .conftest import FakeDatabase, Recorder, build_recorder


class _Concrete(HTTPRequestHandler):
    """Minimal concrete handler: routes 'dns' and records that _handle_route ran."""
    route = 'dns'

    def _handle_route(self) -> None:
        self.routed = True


@pytest.fixture
def patched_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(powergslb.database, 'Database', FakeDatabase)


def _make_handler(send_error_exc: Exception | None = None) -> _Concrete:
    """Build a handler without running __init__ (which would open a socket and call handle())."""
    handler = _Concrete.__new__(_Concrete)
    handler.close_connection = False
    handler.database_config = {}
    handler.send_error_calls = []  # type: ignore[attr-defined]
    handler.status_registry = StatusRegistry()

    def fake_send_error(code: int, *_a: Any, **_k: Any) -> None:
        handler.send_error_calls.append(code)  # type: ignore[attr-defined]
        if send_error_exc is not None:
            raise send_error_exc

    handler.send_error = fake_send_error  # type: ignore[method-assign]
    return handler


def _drive(handler: _Concrete, request_exc: Exception, parse_request: bool = True) -> None:
    """Make the single handled request raise request_exc, optionally after a request is parsed."""

    def fake_handle_one_request() -> None:
        if parse_request:
            handler.command = 'GET'
        raise request_exc

    handler.handle_one_request = fake_handle_one_request  # type: ignore[method-assign]
    handler.handle()


@pytest.mark.usefixtures('patched_env')
def test_client_disconnect_is_quiet(caplog: pytest.LogCaptureFixture) -> None:
    handler = _make_handler()
    with caplog.at_level('DEBUG'):
        _drive(handler, BrokenPipeError(32, 'Broken pipe'))

    assert not handler.send_error_calls  # type: ignore[attr-defined]
    assert not any(r.levelname == 'ERROR' for r in caplog.records)
    assert any('connection closed' in r.getMessage() for r in caplog.records)


@pytest.mark.usefixtures('patched_env')
def test_connection_error_subclasses_are_quiet() -> None:
    # ConnectionResetError/ConnectionAbortedError subclass ConnectionError; TimeoutError is its own type.
    for exc in (ConnectionResetError(104, 'reset'), ConnectionAbortedError(103, 'abort'), TimeoutError()):
        handler = _make_handler()
        _drive(handler, exc)
        assert not handler.send_error_calls  # type: ignore[attr-defined]


@pytest.mark.usefixtures('patched_env')
def test_generic_error_sends_single_500_and_closes(caplog: pytest.LogCaptureFixture) -> None:
    handler = _make_handler()
    with caplog.at_level('ERROR'):
        _drive(handler, RuntimeError('boom'))

    assert handler.send_error_calls == [500]  # type: ignore[attr-defined]
    assert handler.close_connection is True
    assert any('RuntimeError: boom' in r.getMessage() for r in caplog.records)


@pytest.mark.usefixtures('patched_env')
def test_no_500_before_a_request_is_parsed() -> None:
    # e.g. the database connection fails before any request; self.command is unset, so there is no
    # client request to answer with 500, but the connection is still marked closed.
    handler = _make_handler()
    _drive(handler, RuntimeError('db down'), parse_request=False)

    assert not handler.send_error_calls  # type: ignore[attr-defined]
    assert handler.close_connection is True


@pytest.mark.usefixtures('patched_env')
def test_send_error_failing_on_disconnect_does_not_escape(caplog: pytest.LogCaptureFixture) -> None:
    # The client RSTs between the original error and our 500; send_error's socket write raises an
    # OSError subclass, which must be swallowed rather than escaping handle().
    handler = _make_handler(send_error_exc=ConnectionResetError(104, 'reset'))
    with caplog.at_level('DEBUG'):
        _drive(handler, RuntimeError('boom'))  # must not raise

    assert handler.send_error_calls == [500]  # type: ignore[attr-defined]
    assert handler.close_connection is True
    assert any('send_error failed' in r.getMessage() for r in caplog.records)


# --- the rest of the base plumbing ------------------------------------------------------------------------------


class _Recorder(Recorder, _Concrete):  # pylint: disable=too-many-ancestors
    """A handler built without __init__, with the response primitives and streams stubbed for inspection."""


def _recorder(headers: dict[str, str] | None = None) -> _Recorder:
    return build_recorder(_Recorder, headers)


# _read_body

def test_read_body_reads_content_length() -> None:
    handler = _recorder({'Content-Length': '4'})
    handler.rfile = io.BytesIO(b'abcdEXTRA')
    handler._read_body()
    assert handler.body == b'abcd'


def test_read_body_defaults_to_zero_without_header() -> None:
    handler = _recorder({})
    handler.rfile = io.BytesIO(b'')
    handler._read_body()
    assert handler.body == b''


def test_read_body_invalid_length_raises() -> None:
    handler = _recorder({'Content-Length': 'oops'})
    handler.rfile = io.BytesIO(b'')
    with pytest.raises(ValueError, match='Content-Length'):
        handler._read_body()


def test_read_body_negative_length_raises() -> None:
    # A negative Content-Length must be rejected, not passed to rfile.read() (read(-1) drains until EOF).
    handler = _recorder({'Content-Length': '-1'})
    handler.rfile = io.BytesIO(b'payload')
    handler.body = None
    with pytest.raises(ValueError, match='Content-Length'):
        handler._read_body()
    assert handler.body is None  # nothing read


def test_read_body_over_max_length_raises() -> None:
    # An over-limit Content-Length is rejected before the body is buffered into memory.
    handler = _recorder({'Content-Length': '1048577'})  # 1 MiB + 1
    handler.rfile = io.BytesIO(b'payload')
    handler.body = None
    with pytest.raises(ValueError, match='Content-Length'):
        handler._read_body()
    assert handler.body is None  # body not buffered before the limit check


def test_read_body_at_max_length_is_allowed() -> None:
    # The exact limit is accepted; read() returns however many bytes the client actually sent.
    handler = _recorder({'Content-Length': '1048576'})  # 1 MiB exactly
    handler.rfile = io.BytesIO(b'small')
    handler._read_body()
    assert handler.body == b'small'


# _send_content

def test_send_content_writes_body_and_headers() -> None:
    handler = _recorder()
    handler._send_content('{"ok":true}')
    assert handler.responses_sent == [200]
    assert ('Content-Length', '11') in handler.headers_sent
    assert isinstance(handler.wfile, io.BytesIO)
    assert handler.wfile.getvalue() == b'{"ok":true}'


def test_send_content_custom_code_and_debug_off() -> None:
    handler = _recorder()
    handler._send_content('x', code=503, debug=False)
    assert handler.responses_sent == [503]


# log_message / log_error (thin wrappers over _log; stdlib access/error log routed through logging)

def test_log_message_logs_at_info_with_remote_ip(caplog: pytest.LogCaptureFixture) -> None:
    # The access log prefix is the resolved remote_ip (the recursor on the DNS interface), not the TCP peer.
    handler = _recorder()
    handler.client_address = ('127.0.0.1', 1234)
    handler.remote_ip = netaddr.IPAddress('203.0.113.7')
    with caplog.at_level('INFO', logger=request_module.logging.getLogger().name):
        handler.log_message('"%s" %s %s', 'GET /dns HTTP/1.1', '200', '-')
    record = next(r for r in caplog.records if 'GET /dns' in r.getMessage())
    assert record.levelname == 'INFO'
    assert record.getMessage() == '203.0.113.7 "GET /dns HTTP/1.1" 200 -'


def test_log_message_keeps_zero_remote_ip(caplog: pytest.LogCaptureFixture) -> None:
    # netaddr.IPAddress('0.0.0.0') is falsy; only a None remote_ip falls back to the TCP peer.
    handler = _recorder()
    handler.client_address = ('127.0.0.1', 1234)
    handler.remote_ip = netaddr.IPAddress('0.0.0.0')
    with caplog.at_level('INFO'):
        handler.log_message('"%s" %s %s', 'GET /dns HTTP/1.1', '200', '-')
    record = next(r for r in caplog.records if 'GET /dns' in r.getMessage())
    assert record.getMessage() == '0.0.0.0 "GET /dns HTTP/1.1" 200 -'


def test_log_error_falls_back_to_peer_before_routing(caplog: pytest.LogCaptureFixture) -> None:
    # remote_ip is unset for an error logged before routing, so the prefix falls back to the TCP peer.
    handler = _recorder()
    handler.client_address = ('203.0.113.7', 1234)
    with caplog.at_level('ERROR'):
        handler.log_error('code %d, message %s', 400, 'Bad request')
    record = next(r for r in caplog.records if 'Bad request' in r.getMessage())
    assert record.levelname == 'ERROR'
    assert record.getMessage() == '203.0.113.7 code 400, message Bad request'


def test_log_message_escapes_control_chars(caplog: pytest.LogCaptureFixture) -> None:
    # The stdlib guards against log injection via the request line; the override keeps that escaping.
    handler = _recorder()
    handler.client_address = ('203.0.113.7', 1234)
    with caplog.at_level('INFO'):
        handler.log_message('"%s"', 'GET /a\r\nINJECTED HTTP/1.1')
    record = next(r for r in caplog.records if 'INJECTED' in r.getMessage())
    assert '\n' not in record.getMessage()
    assert r'\x0d\x0a' in record.getMessage()  # stdlib escapes CR/LF to \xNN


def test_log_message_dumps_masked_headers_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    handler = _recorder({'Host': 'gslb.example', 'Authorization': 'Basic c2VjcmV0', 'Cookie': 'sid=abc'})
    handler.client_address = ('203.0.113.7', 1234)
    with caplog.at_level('DEBUG'):
        handler.log_message('"%s" %s %s', 'GET /admin HTTP/1.1', '200', '-')
    record = next(r for r in caplog.records if 'request headers' in r.getMessage())
    assert record.levelname == 'DEBUG'
    message = record.getMessage()
    assert "'Host': 'gslb.example'" in message
    assert "'Authorization': '***'" in message and 'c2VjcmV0' not in message  # masked, not leaked
    assert "'Cookie': '***'" in message and 'sid=abc' not in message


def test_log_error_skips_header_dump(caplog: pytest.LogCaptureFixture) -> None:
    # The dump belongs to log_message only, so an errored request dumps the headers once, not per log line.
    handler = _recorder({'Host': 'gslb.example'})
    handler.client_address = ('203.0.113.7', 1234)
    with caplog.at_level('DEBUG'):
        handler.log_error('code %d, message %s', 400, 'Bad request')
    assert not any('request headers' in r.getMessage() for r in caplog.records)


def test_log_message_without_headers_skips_dump(caplog: pytest.LogCaptureFixture) -> None:
    # 'if defined': no headers attribute set means no header dump, just the access line.
    handler = _recorder()
    del handler.headers
    handler.client_address = ('203.0.113.7', 1234)
    with caplog.at_level('DEBUG'):
        handler.log_message('"%s" %s %s', 'GET /dns HTTP/1.1', '200', '-')
    assert not any('request headers' in r.getMessage() for r in caplog.records)


# _set_remote_ip (base: peer address only, the PowerDNS header is ignored here)

def test_set_remote_ip_from_client_address() -> None:
    handler = _recorder({})
    handler.client_address = ('203.0.113.9', 4321)
    handler._set_remote_ip()
    assert handler.remote_ip.format() == '203.0.113.9'


def test_set_remote_ip_ignores_real_remote_header() -> None:
    # The base handler never trusts X-Remotebackend-Real-Remote, so a client cannot spoof its source IP.
    handler = _recorder({'X-Remotebackend-Real-Remote': '198.51.100.4/32'})
    handler.client_address = ('127.0.0.1', 1)
    handler._set_remote_ip()
    assert handler.remote_ip.format() == '127.0.0.1'


# _urlsplit

def test_urlsplit_extracts_query_and_dirs_leaving_path_raw() -> None:
    # self.path is left as the raw request target so the stdlib static-file path decodes it exactly once.
    handler = _recorder()
    handler.path = '/dns/lookup/example.com/A?includeDisabled=true'
    handler._urlsplit()
    assert handler.path == '/dns/lookup/example.com/A?includeDisabled=true'
    assert handler.query == 'includeDisabled=true'
    assert handler.dirs == ['dns', 'lookup', 'example.com', 'A']


def test_urlsplit_unquotes_dirs_but_leaves_path_and_query_encoded() -> None:
    # dirs are unquoted (for the DNS name); self.path and the query stay percent-encoded. Pre-unquoting the
    # query would truncate a value at an encoded '&'; pre-unquoting the path would make the static handler
    # decode it twice.
    handler = _recorder()
    handler.path = '/dns/lookup/a%20b/A?record%5Bx%5D=1%3E%262'
    handler._urlsplit()
    assert handler.path == '/dns/lookup/a%20b/A?record%5Bx%5D=1%3E%262'
    assert handler.query == 'record%5Bx%5D=1%3E%262'
    assert handler.dirs == ['dns', 'lookup', 'a b', 'A']


# do_GET / do_POST

def test_do_get_dispatches_to_handle_request(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _recorder()
    called = []
    monkeypatch.setattr(handler, '_handle_request', lambda: called.append(True))
    handler.do_GET()
    assert called == [True]


def test_do_post_reads_body_then_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _recorder()
    order = []
    monkeypatch.setattr(handler, '_read_body', lambda: order.append('read'))
    monkeypatch.setattr(handler, '_handle_request', lambda: order.append('dispatch'))
    handler.do_POST()
    assert order == ['read', 'dispatch']


def test_get_after_post_does_not_see_stale_body(monkeypatch: pytest.MonkeyPatch) -> None:
    # One handler instance serves a whole keep-alive connection. A POST sets self.body; a following GET
    # must not inherit that body (the admin handler falls back to self.body when the query is empty).
    handler = _recorder({'Content-Length': '5'})
    handler.rfile = io.BytesIO(b'stale')
    seen: list[bytes | None] = []
    monkeypatch.setattr(handler, '_handle_request', lambda: seen.append(handler.body))

    handler.do_POST()
    assert handler.body == b'stale'

    handler.do_GET()
    assert seen[-1] is None


def test_do_post_invalid_body_sends_400(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _recorder()
    dispatched = []

    def bad_body() -> None:
        raise ValueError('bad length')

    monkeypatch.setattr(handler, '_read_body', bad_body)
    monkeypatch.setattr(handler, '_handle_request', lambda: dispatched.append(True))
    handler.do_POST()
    assert handler.errors_sent == [400]
    assert not dispatched


def test_do_head_clears_body_and_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    # HEAD routes like GET (so auth and routing still apply); it clears any leftover keep-alive body first.
    handler = _recorder()
    handler.body = b'stale'
    seen: list[bytes | None] = []
    monkeypatch.setattr(handler, '_handle_request', lambda: seen.append(handler.body))
    handler.do_HEAD()
    assert seen == [None]
    assert not handler.errors_sent


# _handle_request route skeleton

def _prepare_dispatch(handler: _Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(handler, '_set_remote_ip', lambda: None)


def test_handle_request_on_route_calls_handle_route(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _recorder()
    _prepare_dispatch(handler, monkeypatch)
    routed = []
    monkeypatch.setattr(handler, '_handle_route', lambda: routed.append(True))
    handler.path = '/dns/lookup/example.com/A'
    handler.command = 'GET'
    handler._handle_request()
    assert routed == [True]
    assert not handler.errors_sent


def test_handle_request_off_route_sends_404(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = _recorder()
    _prepare_dispatch(handler, monkeypatch)
    routed = []
    monkeypatch.setattr(handler, '_handle_route', lambda: routed.append(True))
    handler.path = '/admin/index.html'  # not this handler's route ('dns')
    handler.command = 'GET'
    handler._handle_request()
    assert not routed
    assert handler.errors_sent == [404]


def test_handle_request_empty_path_sends_404(monkeypatch: pytest.MonkeyPatch) -> None:
    # A malformed target with no usable path leaves dirs empty; it is a 404 like any other unrouted path.
    # '//x' parses to an empty path, exercising the guard via _urlsplit.
    handler = _recorder()
    _prepare_dispatch(handler, monkeypatch)
    routed = []
    monkeypatch.setattr(handler, '_handle_route', lambda: routed.append(True))
    handler.path = '//x'
    handler.command = 'GET'
    handler._handle_request()
    assert handler.dirs == []
    assert not routed
    assert handler.errors_sent == [404]


# __init__ (with the socket-opening base __init__ stubbed out)

def test_init_sets_per_request_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(request_module.SimpleHTTPRequestHandler, '__init__', lambda self, *a, **k: None)
    registry = StatusRegistry()
    handler = _Concrete(database_config={'host': 'db'}, status_registry=registry, timeout=30)
    assert handler.body is None
    assert handler.close_connection is False
    assert handler.database is None
    assert handler.database_config == {'host': 'db'}
    assert handler.dirs == []
    assert handler.path == ''
    assert handler.remote_ip is None
    assert handler.query is None
    assert handler.status_registry is registry
    assert handler.timeout == 30
