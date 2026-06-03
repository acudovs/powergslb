# pylint: disable=missing-function-docstring

"""Tests for HttpCheck.execute() with the connection mocked."""

from typing import Any

import pytest

from powergslb.monitor.check import http as http_module
from powergslb.monitor.check.http import HttpCheck


class _FakeResponse:
    """Minimal response with a status and a body drained in chunks then EOF."""

    def __init__(self, status: int, chunks: int = 1, body: bytes = b'body') -> None:
        self.status = status
        self.read_calls = 0
        self._chunks = chunks
        self._body = body

    def read1(self, _amt: int = -1) -> bytes:
        self.read_calls += 1
        return self._body if self.read_calls <= self._chunks else b''  # b'' signals EOF


class _EndlessResponse(_FakeResponse):
    """A body that never reaches EOF, to exercise the read deadline."""

    def read1(self, _amt: int = -1) -> bytes:
        self.read_calls += 1
        return b'body'


class _TimeoutResponse(_FakeResponse):
    """A body whose read blocks past the per-read deadline, surfacing as TimeoutError."""

    def read1(self, _amt: int = -1) -> bytes:
        self.read_calls += 1
        raise TimeoutError


class _FakeConnection:
    """Stand-in for http.client.HTTP(S)Connection; captures the request and returns the supplied response."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.requests: list[dict[str, Any]] = []
        self.closed = False

    def request(self, method: str, url: str, headers: dict[str, str]) -> None:
        self.requests.append({'method': method, 'url': url, 'headers': headers})

    def getresponse(self) -> Any:
        return self._response

    def close(self) -> None:
        self.closed = True


def _check(**overrides: Any) -> HttpCheck:
    params: dict[str, Any] = {'interval': 10, 'timeout': 1, 'fall': 2, 'rise': 2, 'url': 'http://host/'}
    params.update(overrides)
    return HttpCheck(**params)


def _patch_connection(monkeypatch: pytest.MonkeyPatch, response: Any) -> _FakeConnection:
    connection = _FakeConnection(response)
    monkeypatch.setattr(http_module, 'HTTPConnection', lambda *a, **k: connection)
    monkeypatch.setattr(http_module, 'HTTPSConnection', lambda *a, **k: connection)
    return connection


def _patch_expired_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the deadline: the first monotonic() seeds it, every later call is already past it."""
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        calls += 1
        return 0.0 if calls == 1 else 100.0

    monkeypatch.setattr(http_module.time, 'monotonic', monotonic)


# status range (expected_status == 0): Route 53 default, 200..399 healthy

def test_2xx_is_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(200, chunks=2)
    _patch_connection(monkeypatch, response)
    assert _check().execute() is True
    assert response.read_calls == 3  # two body chunks then the EOF read


def test_3xx_is_healthy_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    # http.client never follows redirects, so a 3xx surfaces directly and falls in the default 200..399 range.
    _patch_connection(monkeypatch, _FakeResponse(302, chunks=0))
    assert _check().execute() is True


def test_4xx_is_unhealthy_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, _FakeResponse(404, chunks=0))
    assert _check().execute() is False


def test_connection_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    # execute() must close the connection so the socket is not leaked.
    connection = _patch_connection(monkeypatch, _FakeResponse(200, chunks=1))
    _check().execute()
    assert connection.closed is True


# expected_status: exact match

def test_expected_status_exact_match_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, _FakeResponse(401, chunks=0))
    assert _check(expected_status=401).execute() is True


def test_expected_status_mismatch_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, _FakeResponse(200, chunks=1))
    assert _check(expected_status=201).execute() is False


# method

def test_head_skips_body(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _FakeResponse(200, chunks=5)
    _patch_connection(monkeypatch, response)
    assert _check(method='HEAD').execute() is True
    assert response.read_calls == 0  # HEAD never reads the body


def test_head_uses_request_method(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _patch_connection(monkeypatch, _FakeResponse(200, chunks=0))
    _check(method='HEAD').execute()
    assert connection.requests[0]['method'] == 'HEAD'


def test_method_post_rejected() -> None:
    with pytest.raises(ValueError, match="check parameter 'method' unsupported"):
        _check(method='POST')


# request target: path and query

def test_request_target_includes_path_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _patch_connection(monkeypatch, _FakeResponse(200, chunks=0))
    _check(url='http://host/health?probe=1').execute()
    assert connection.requests[0]['url'] == '/health?probe=1'


def test_request_target_defaults_to_root(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _patch_connection(monkeypatch, _FakeResponse(200, chunks=0))
    _check(url='http://host').execute()
    assert connection.requests[0]['url'] == '/'


# __post_init__ validation

@pytest.mark.parametrize('url', ['host/', 'ftp://host/', 'http://', '//host/'])
def test_invalid_url_rejected(url: str) -> None:
    with pytest.raises(ValueError, match="check parameter 'url' invalid"):
        _check(url=url)


@pytest.mark.parametrize('status', [50, 99, 600, -1])
def test_out_of_range_expected_status_rejected(status: int) -> None:
    with pytest.raises(ValueError, match="check parameter 'expected_status' invalid"):
        _check(expected_status=status)


def test_invalid_body_match_regex_rejected() -> None:
    with pytest.raises(ValueError, match="check parameter 'body_match' invalid"):
        _check(body_match='[')


# body_match: regex via re.search

def test_body_match_found_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, _FakeResponse(200, chunks=1, body=b'service OK'))
    assert _check(body_match=r'OK').execute() is True


def test_body_match_not_found_unhealthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, _FakeResponse(200, chunks=1, body=b'service down'))
    assert _check(body_match=r'OK').execute() is False


def test_body_match_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_connection(monkeypatch, _FakeResponse(200, chunks=1, body=b'{"status":"ready"}'))
    assert _check(body_match=r'"status"\s*:\s*"ready"').execute() is True


def test_body_match_with_head_rejected() -> None:
    with pytest.raises(ValueError, match="check parameter 'body_match' unsupported for HEAD requests"):
        _check(method='HEAD', body_match='anything')


# tls_verify: connection built with an unverified SSL context

def test_tls_verify_false_disables_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_https(*_args: Any, **kwargs: Any) -> _FakeConnection:
        captured.update(kwargs)
        return _FakeConnection(_FakeResponse(200, chunks=0))

    monkeypatch.setattr(http_module, 'HTTPSConnection', fake_https)
    _check(url='https://host/', tls_verify=False).execute()
    assert captured['context'].verify_mode is http_module.ssl.CERT_NONE


def test_https_uses_https_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []

    def fake_https(*_args: Any, **_kwargs: Any) -> _FakeConnection:
        used.append('https')
        return _FakeConnection(_FakeResponse(200, chunks=0))

    def fake_http(*_args: Any, **_kwargs: Any) -> _FakeConnection:
        used.append('http')
        return _FakeConnection(_FakeResponse(200, chunks=0))

    monkeypatch.setattr(http_module, 'HTTPSConnection', fake_https)
    monkeypatch.setattr(http_module, 'HTTPConnection', fake_http)
    _check(url='https://host/').execute()
    assert used == ['https']


def test_http_uses_plain_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    used: list[str] = []

    def fake_https(*_args: Any, **_kwargs: Any) -> _FakeConnection:
        used.append('https')
        return _FakeConnection(_FakeResponse(200, chunks=0))

    def fake_http(*_args: Any, **_kwargs: Any) -> _FakeConnection:
        used.append('http')
        return _FakeConnection(_FakeResponse(200, chunks=0))

    monkeypatch.setattr(http_module, 'HTTPSConnection', fake_https)
    monkeypatch.setattr(http_module, 'HTTPConnection', fake_http)
    _check(url='http://host/').execute()
    assert used == ['http']


# host: overrides the HTTP Host header

def test_host_overrides_header(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _patch_connection(monkeypatch, _FakeResponse(200, chunks=0))
    _check(host='alt.example.com').execute()
    assert connection.requests[0]['headers'].get('Host') == 'alt.example.com'


def test_no_host_no_header_set(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _patch_connection(monkeypatch, _FakeResponse(200, chunks=0))
    _check().execute()
    assert 'Host' not in connection.requests[0]['headers']


# User-Agent: default PowerGSLB/<version>

def test_default_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _patch_connection(monkeypatch, _FakeResponse(200, chunks=0))
    _check().execute()
    assert connection.requests[0]['headers'].get('User-Agent') == http_module.HttpCheck.user_agent


# deadline still aborts an endless body

def test_read_deadline_aborts_endless_body(monkeypatch: pytest.MonkeyPatch) -> None:
    # A server that keeps sending without EOF must not loop forever: the deadline fires before the first read,
    # so execute() returns False (unhealthy) instead of draining indefinitely.
    response = _EndlessResponse(200)
    _patch_connection(monkeypatch, response)
    _patch_expired_clock(monkeypatch)
    assert _check().execute() is False
    assert response.read_calls == 0  # remaining <= 0 aborts before reading


def test_body_match_deadline_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    # body_match path: the deadline aborts before any read, so the regex is never reached.
    response = _EndlessResponse(200)
    _patch_connection(monkeypatch, response)
    _patch_expired_clock(monkeypatch)
    assert _check(body_match=r'OK').execute() is False
    assert response.read_calls == 0


def test_read_timeout_aborts(monkeypatch: pytest.MonkeyPatch) -> None:
    # A read that blocks past the per-read socket timeout surfaces as TimeoutError and aborts the check.
    response = _TimeoutResponse(200)
    _patch_connection(monkeypatch, response)
    assert _check().execute() is False
    assert response.read_calls == 1


def test_body_capped_at_chunk_size(monkeypatch: pytest.MonkeyPatch) -> None:
    # A body that fills 'body_chunk' stops the read loop without an EOF read; the kept bytes are matched.
    full = b'x' * HttpCheck.body_chunk
    response = _FakeResponse(200, chunks=1, body=full)
    _patch_connection(monkeypatch, response)
    assert _check(body_match=r'^x+$').execute() is True
    assert response.read_calls == 1  # the loop exits on length, not on an EOF read
