# pylint: disable=missing-function-docstring, redefined-outer-name, protected-access

"""Tests for HEAD dispatch through the request handler base.

HEAD is routed exactly like GET, so it stays behind the same routing and Basic Auth wall instead of reaching the
inherited SimpleHTTPRequestHandler.do_HEAD (which would serve static file metadata under the document root with no
routing and no auth). An unauthenticated HEAD is challenged with 401, an off-route HEAD is 404, and an
authenticated HEAD returns the static asset's headers (Content-Length and all) with no body. Exercised over a real
loopback socket because the dispatch lives in the stdlib do_<method> path in handle_one_request, which a
method-level fake cannot reach.
"""

import base64
import functools
import http.client
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import powergslb.database
from powergslb.monitor.status import StatusRegistry
from powergslb.server.http import server as server_module
from powergslb.server.http.handler import AdminRequestHandler

_INDEX_HTML = '<html>admin</html>'


class _FakeDatabase:
    """Context-manager stand-in for the real MySQL connection; authorizes one fixed credential."""

    def __init__(self, **_kwargs: Any) -> None:
        pass

    def __enter__(self) -> '_FakeDatabase':
        return self

    def __exit__(self, *_exc: Any) -> None:
        return None

    def check_user(self, user: str, password: str) -> list[dict[str, Any]]:
        return [{'valid': 1}] if (user, password) == ('admin', 'secret') else []


@pytest.fixture
def address(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, int]]:
    """Serve a throwaway document root on an ephemeral loopback port; yield the (host, port) to call."""
    monkeypatch.setattr(powergslb.database, 'Database', _FakeDatabase)
    (tmp_path / 'admin').mkdir()
    (tmp_path / 'admin' / 'index.html').write_text(_INDEX_HTML)
    (tmp_path / 'top-secret.txt').write_text('document root file outside admin/')

    handler = functools.partial(AdminRequestHandler, directory=str(tmp_path), database_config={},
                                status_registry=StatusRegistry(), timeout=5)
    httpd = server_module._ThreadingHTTPServer(('127.0.0.1', 0), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield '127.0.0.1', httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()


def _request(address: tuple[str, int], method: str, path: str,
             auth: tuple[str, str] | None = None) -> http.client.HTTPResponse:
    connection = http.client.HTTPConnection(*address, timeout=5)
    headers = {}
    if auth is not None:
        token = base64.b64encode(f'{auth[0]}:{auth[1]}'.encode()).decode()
        headers['Authorization'] = f'Basic {token}'
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        response.read()  # drain the body so the response object can report headers/status
        return response
    finally:
        connection.close()


def test_head_admin_static_unauthenticated_is_challenged(address: tuple[str, int]) -> None:
    # HEAD is behind the same auth wall as GET: an unauthenticated HEAD is challenged, never the file's metadata.
    response = _request(address, 'HEAD', '/admin/index.html')
    assert response.status == 401
    assert response.getheader('WWW-Authenticate', '').startswith('Basic ')


def test_head_outside_admin_is_404(address: tuple[str, int]) -> None:
    # Routing applies to HEAD too: a file under the document root but outside the routed /admin subtree is 404.
    response = _request(address, 'HEAD', '/top-secret.txt')
    assert response.status == 404


def test_head_admin_static_authenticated_returns_headers_without_body(address: tuple[str, int]) -> None:
    # An authenticated HEAD returns the same headers a GET would, including the file's Content-Length, but no body.
    response = _request(address, 'HEAD', '/admin/index.html', auth=('admin', 'secret'))
    assert response.status == 200
    assert response.getheader('Content-Length') == str(len(_INDEX_HTML))
    assert response.read() == b''  # already drained; a HEAD carries no body


def test_get_admin_static_still_requires_auth(address: tuple[str, int]) -> None:
    # The auth wall holds for GET as well: the same path answers 401, not the file, without credentials.
    response = _request(address, 'GET', '/admin/index.html')
    assert response.status == 401
