# pylint: disable=missing-function-docstring, redefined-outer-name, protected-access

"""Tests for cross-port request handler routing.

Each port physically serves one role: the DNS handler answers only /dns, the admin handler answers only /admin.
The split is the security boundary - it stops /dns being reachable (unauthenticated, source-IP-spoofable) on the
public admin port, and stops /admin answering on the DNS port. Exercised over a real loopback socket because the
routing runs inside the stdlib do_<method> dispatch, which a method-level fake cannot reach.
"""

import functools
import http.client
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Callable

import pytest

import powergslb.database
from powergslb.monitor.status import StatusRegistry
from powergslb.server.http import server as server_module
from powergslb.server.http.handler import AdminRequestHandler, HTTPRequestHandler, PowerDNSRequestHandler

from .conftest import FakeDatabase


@pytest.fixture
def serve(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[type[HTTPRequestHandler]],
                                                                                tuple[str, int]]]:
    """Yield a factory that serves a given handler class on an ephemeral loopback port and returns its address."""
    monkeypatch.setattr(powergslb.database, 'Database', FakeDatabase)
    (tmp_path / 'admin').mkdir()
    (tmp_path / 'admin' / 'index.html').write_text('<html>admin</html>')
    servers: list[Any] = []

    def factory(handler_class: type[HTTPRequestHandler]) -> tuple[str, int]:
        handler = functools.partial(handler_class, directory=str(tmp_path), database_config={},
                                    status_registry=StatusRegistry(), timeout=5)
        httpd = server_module._ThreadingHTTPServer(('127.0.0.1', 0), handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        return '127.0.0.1', httpd.server_address[1]

    try:
        yield factory
    finally:
        for httpd in servers:
            httpd.shutdown()
            httpd.server_close()


def _request(address: tuple[str, int], method: str, path: str) -> tuple[int, bytes]:
    connection = http.client.HTTPConnection(*address, timeout=5)
    try:
        connection.request(method, path)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_dns_port_serves_dns(serve: Callable[[type[HTTPRequestHandler]], tuple[str, int]]) -> None:
    address = serve(PowerDNSRequestHandler)
    status, body = _request(address, 'GET', '/dns')
    assert status == 200
    assert body == b'{"result":false}'  # the route is alive without touching the database


def test_dns_port_does_not_serve_admin(serve: Callable[[type[HTTPRequestHandler]], tuple[str, int]]) -> None:
    # Regression: the admin surface (auth wall, static files) must not exist on the DNS port.
    address = serve(PowerDNSRequestHandler)
    status, _ = _request(address, 'GET', '/admin/index.html')
    assert status == 404


def test_admin_port_walls_admin(serve: Callable[[type[HTTPRequestHandler]], tuple[str, int]]) -> None:
    address = serve(AdminRequestHandler)
    status, _ = _request(address, 'GET', '/admin/index.html')
    assert status == 401  # Basic Auth wall intact


def test_admin_port_does_not_serve_dns(serve: Callable[[type[HTTPRequestHandler]], tuple[str, int]]) -> None:
    # The disclosure this split closes: /dns must not answer (unauthenticated, source-IP-spoofable) on :443.
    address = serve(AdminRequestHandler)
    status, _ = _request(address, 'GET', '/dns/lookup/example.com/A')
    assert status == 404
