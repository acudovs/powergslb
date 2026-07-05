# pylint: disable=missing-function-docstring, redefined-outer-name, protected-access

"""Tests for HTTPServerManager.

Config unpacking, and the run()/shutdown() lifecycle of the threading HTTP server (plain and TLS). The server is
faked - no real socket - and its serve_forever() blocks until shutdown() like the real loop, so the handshake
(start, serve, stop) is exercised without binding a port.
"""

import os
import threading
from typing import Any

import pytest

from powergslb.monitor.status import StatusRegistry
from powergslb.server.http import server as server_module
from powergslb.server.http.handler import PowerDNSRequestHandler
from powergslb.server.http.server import HTTPServerManager


class _FakeServer:
    last: '_FakeServer | None' = None

    def __init__(self, address: tuple[str, int], handler: Any) -> None:
        self.address = address
        self.handler = handler
        self.daemon_threads = False
        self.socket: Any = 'plain-socket'
        self.serving = threading.Event()  # set while inside serve_forever()
        self.served = False
        self.shutdown_called = False
        self.closed = False
        self._stop = threading.Event()
        _FakeServer.last = self

    def __enter__(self) -> '_FakeServer':
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.closed = True  # the real server_close() on leaving the with-block

    def serve_forever(self) -> None:
        self.served = True
        self.serving.set()
        self._stop.wait()  # block like the real loop until shutdown() releases it

    def shutdown(self) -> None:
        self.shutdown_called = True
        self._stop.set()


class _FakeContext:
    def __init__(self) -> None:
        self.loaded: tuple[Any, Any] | None = None
        self.ciphers: str | None = None
        self.options = 0
        self.wrapped_server_side: bool | None = None

    def load_cert_chain(self, certfile: str, keyfile: str | None) -> None:
        self.loaded = (certfile, keyfile)

    def set_ciphers(self, ciphers: str) -> None:
        self.ciphers = ciphers

    def wrap_socket(self, _sock: Any, server_side: bool) -> str:
        self.wrapped_server_side = server_side
        return 'tls-socket'


@pytest.fixture
def fake_server(monkeypatch: pytest.MonkeyPatch) -> type[_FakeServer]:
    _FakeServer.last = None
    monkeypatch.setattr(server_module, '_ThreadingHTTPServer', _FakeServer)
    return _FakeServer


@pytest.fixture
def fake_context(monkeypatch: pytest.MonkeyPatch) -> _FakeContext:
    context = _FakeContext()
    monkeypatch.setattr(server_module.ssl, 'SSLContext', lambda _proto: context)
    return context


@pytest.fixture
def status_registry() -> StatusRegistry:
    return StatusRegistry()


def _config(**overrides: Any) -> dict[str, Any]:
    config = {'address': '127.0.0.1', 'port': 8080, 'root': '/srv'}
    config.update(overrides)
    return config


def _serve(thread: HTTPServerManager, fake: type[_FakeServer]) -> _FakeServer:
    """Start the thread and wait until it is inside serve_forever(); return the faked server."""
    thread.start()
    assert fake.last is not None
    assert fake.last.serving.wait(timeout=1)
    return fake.last


# __init__

def test_init_reads_plain_config(status_registry: StatusRegistry) -> None:
    thread = HTTPServerManager(_config(), {'host': 'db'},
                               status_registry, PowerDNSRequestHandler, name='Server')
    assert thread.address == '127.0.0.1'
    assert thread.port == 8080
    assert thread.root == '/srv'
    assert thread.ssl is False
    assert thread.cert is None and thread.key is None and thread.ciphers is None
    assert thread.daemon is True


def test_init_reads_ssl_config(status_registry: StatusRegistry) -> None:
    thread = HTTPServerManager(_config(ssl=True, cert='/c.pem', key='/k.pem', ciphers='HIGH'), {},
                               status_registry, PowerDNSRequestHandler, name='Admin')
    assert thread.ssl is True
    assert (thread.cert, thread.key, thread.ciphers) == ('/c.pem', '/k.pem', 'HIGH')


def test_init_defaults_root_to_bundled_resources(status_registry: StatusRegistry) -> None:
    config = _config()
    del config['root']
    thread = HTTPServerManager(config, {},
                               status_registry, PowerDNSRequestHandler, name='Admin')
    assert thread.root == server_module._default_root()
    assert os.path.isfile(os.path.join(thread.root, 'admin', 'index.html'))
    assert os.path.isfile(os.path.join(thread.root, 'admin', 'src', 'favicon.svg'))


# run + shutdown: plain HTTP

def test_run_serves_plain_then_shutdown_stops_it(
        fake_server: type[_FakeServer], status_registry: StatusRegistry) -> None:
    thread = HTTPServerManager(_config(), {'host': 'db'},
                               status_registry, PowerDNSRequestHandler, name='Server')
    server = _serve(thread, fake_server)

    assert server.address == ('127.0.0.1', 8080)
    assert server.daemon_threads is True
    assert server.served is True
    assert server.socket == 'plain-socket'  # untouched, no TLS wrap
    assert server.handler.func is PowerDNSRequestHandler  # the configured role handler serves this port
    assert server.handler.keywords['database_config'] == {'host': 'db'}  # threaded into the request handler
    assert thread._server is server

    thread.shutdown(timeout=1)
    assert not thread.is_alive()
    assert server.shutdown_called is True
    assert server.closed is True  # the with-block closed the socket after serve_forever returned


# run: TLS

def test_run_wraps_socket_with_tls_and_ciphers(
        fake_server: type[_FakeServer], fake_context: _FakeContext, status_registry: StatusRegistry) -> None:
    thread = HTTPServerManager(_config(ssl=True, cert='/c.pem', key='/k.pem', ciphers='HIGH'), {},
                               status_registry, PowerDNSRequestHandler, name='Admin')
    server = _serve(thread, fake_server)
    try:
        assert fake_context.loaded == ('/c.pem', '/k.pem')
        assert fake_context.ciphers == 'HIGH'
        assert fake_context.wrapped_server_side is True
        assert fake_context.options & server_module.ssl.OP_CIPHER_SERVER_PREFERENCE
        assert server.socket == 'tls-socket'
    finally:
        thread.shutdown(timeout=1)


@pytest.mark.usefixtures('fake_context')
def test_run_tls_without_ciphers_skips_set_ciphers(
        fake_server: type[_FakeServer], fake_context: _FakeContext,
        status_registry: StatusRegistry) -> None:
    thread = HTTPServerManager(_config(ssl=True, cert='/c.pem'), {},
                               status_registry, PowerDNSRequestHandler, name='Admin')
    _serve(thread, fake_server)
    try:
        assert fake_context.ciphers is None  # set_ciphers not called
        assert fake_context.loaded == ('/c.pem', None)
    finally:
        thread.shutdown(timeout=1)


def test_init_tls_without_cert_raises(status_registry: StatusRegistry) -> None:
    # TLS enabled with no certificate is a config error caught at construction, not a stripped-by-O assert in run().
    with pytest.raises(ValueError, match='certificate'):
        HTTPServerManager(_config(ssl=True), {},
                          status_registry, PowerDNSRequestHandler, name='Admin')


# shutdown / stopping guard

def test_shutdown_before_serving_takes_stopping_path(
        fake_server: type[_FakeServer], status_registry: StatusRegistry) -> None:
    # shutdown() arrived first: run() must close the freshly-bound socket and never serve.
    thread = HTTPServerManager(_config(), {'host': 'db'},
                               status_registry, PowerDNSRequestHandler, name='Server')
    thread._stopping = True
    thread.run()  # synchronous: returns at the guard, never reaches serve_forever

    assert fake_server.last is not None
    assert fake_server.last.served is False
    assert fake_server.last.closed is True
    assert thread._server is None


def test_shutdown_when_not_serving_skips_server_shutdown(
        fake_server: type[_FakeServer], status_registry: StatusRegistry) -> None:
    # a thread that stopped before serving has no server to stop; shutdown() must not raise or call server.shutdown().
    thread = HTTPServerManager(_config(), {'host': 'db'},
                               status_registry, PowerDNSRequestHandler, name='Server')
    thread._stopping = True
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert thread._server is None

    thread.shutdown(timeout=1)
    assert fake_server.last is not None
    assert fake_server.last.shutdown_called is False
    assert thread._stopping is True
